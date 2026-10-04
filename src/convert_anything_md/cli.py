"""`convert-anything-md` command-line entry point.

Two output modes:

* **Human** (default) - Rich-rendered progress + summary table for
  interactive use.
* **JSON** (``--json``) - machine-readable envelope intended for the
  calling agent / skill to parse and report to the user. The envelope
  includes per-file outcomes plus an aggregate summary.

Cross-platform by construction: every path is resolved with pathlib, every
subprocess call lives behind an extractor abstraction, and Rich falls back
to plain ANSI-free output on dumb terminals.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from convert_anything_md.detect import FileKind
from convert_anything_md.paths import desktop_dir
from convert_anything_md.router import (
    EXTRACTOR_NAMES,
    ConversionOutcome,
    convert_batch,
)
from convert_anything_md.version import __version__


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.list_engines:
        print("Available extractors (use with --engine):")
        for name in sorted(EXTRACTOR_NAMES):
            print(f"  {name}")
        return 0

    if not args.paths:
        parser.error("the following arguments are required: paths")

    # Resolve + glob-expand every input path BEFORE calling the router so
    # users get immediate feedback on typos / missing files.
    paths, missing, directories = _resolve_inputs(
        args.paths, recursive=args.recursive
    )
    if missing:
        for m in missing:
            _stderr(f"[error] not found: {m}")
    for d in directories:
        _stderr(
            f"[error] {d} is a directory - pass --recursive to convert "
            "every supported file inside it, or use a glob like "
            f"'{d}/*.pdf'."
        )

    output_dir = (
        Path(args.output_dir).expanduser().resolve() if args.output_dir else None
    )

    outcomes = convert_batch(
        paths,
        output_dir=output_dir,
        include_frontmatter=not args.no_frontmatter,
        engine_override=args.engine,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
    )

    outcomes.extend(
        ConversionOutcome(source=Path(raw), kind=FileKind.UNKNOWN, ok=False,
                          error=f"not found: {raw}")
        for raw in missing
    )
    outcomes.extend(
        ConversionOutcome(source=directory, kind=FileKind.UNKNOWN, ok=False,
                          error=f"{directory} is a directory - pass --recursive")
        for directory in directories
    )

    if args.json:
        _emit_json(outcomes, output_dir=output_dir or desktop_dir())
    elif not args.quiet:
        _emit_human(outcomes, verbose=args.verbose)

    # Exit code: 0 if everything converted; 1 if at least one failed but
    # others succeeded; 2 if nothing succeeded (hard failure).
    ok = sum(1 for o in outcomes if o.ok)
    if ok == 0:
        return 2
    if ok < len(outcomes):
        return 1
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="convert-anything-md",
        description=(
            "Convert PDFs, Office files, web pages, eBooks, and images into "
            "high-quality Markdown files on your desktop. Hybrid router "
            "picks the best extractor per format with graceful fallbacks."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="One or more files (or globs) to convert.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        help=(
            "Destination directory for the .md files. "
            "Defaults to the OS desktop (auto-detected on macOS / Linux / Windows)."
        ),
    )
    parser.add_argument(
        "--engine",
        choices=sorted(EXTRACTOR_NAMES),
        help="Force a specific extractor (advanced; default routes automatically).",
    )
    parser.add_argument(
        "--no-frontmatter",
        action="store_true",
        help="Omit the YAML front-matter provenance header.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing .md files instead of using `name (1).md`.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect and extract, but don't write any .md files.",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help=(
            "When an input is a directory, walk it recursively and "
            "convert every supported file inside."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON envelope instead of a human table.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show per-file warnings and the full fallback chain.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help=(
            "Suppress the human-readable table. Exit code still reflects "
            "success/failure. Has no effect with --json."
        ),
    )
    parser.add_argument(
        "--list-engines",
        action="store_true",
        help="List every extractor name accepted by --engine and exit.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"convert-anything-md {__version__}",
    )
    return parser


# ---------------------------------------------------------------------------
# Input resolution
# ---------------------------------------------------------------------------


def _resolve_inputs(
    raw_paths: list[str], *, recursive: bool = False
) -> tuple[list[Path], list[str], list[Path]]:
    """Expand globs, deduplicate, and separate found vs missing vs directories.

    Returns:
        (resolved_files, missing_inputs, unresolved_directories)
        - resolved_files:  absolute paths that exist and are files (deduped).
        - missing_inputs:  raw inputs that matched nothing on disk.
        - unresolved_directories: paths that resolved to a directory when
          ``recursive`` is False (so the caller can emit a helpful error).
          When ``recursive`` is True this list is always empty - directories
          are expanded inline and contribute to ``resolved_files``.
    """
    resolved: list[Path] = []
    missing: list[str] = []
    directories: list[Path] = []
    seen: set[Path] = set()

    for raw in raw_paths:
        expanded = Path(raw).expanduser()

        # Treat a literal directory differently from a file. We don't want
        # `Path.exists()` to gobble directories into the file list silently.
        if expanded.is_dir():
            if recursive:
                _collect_directory_files(expanded, resolved, seen)
            else:
                directories.append(expanded.resolve())
            continue

        if expanded.is_file():
            resolved_path = expanded.resolve()
            if resolved_path not in seen:
                seen.add(resolved_path)
                resolved.append(resolved_path)
            continue

        # Not a literal file/dir - try glob expansion. Use the original
        # ``raw`` so multi-segment patterns like ``docs/*.pdf`` keep their
        # directory component (the previous implementation lost subdir).
        matched_any = False
        if any(ch in raw for ch in "*?[]"):
            # First try the path as given (handles absolute globs cleanly).
            for match_str in sorted(glob.glob(str(expanded), recursive=True)):
                matched_any = _absorb_match(
                    Path(match_str), resolved, seen, directories, recursive
                ) or matched_any


        if not matched_any:
            missing.append(raw)

    return resolved, missing, directories


def _absorb_match(
    match: Path,
    resolved: list[Path],
    seen: set[Path],
    directories: list[Path],
    recursive: bool,
) -> bool:
    """Classify a glob match, returning True even when already collected."""
    if match.is_dir():
        if recursive:
            _collect_directory_files(match, resolved, seen)
            return True
        directories.append(match.resolve())
        return True
    if match.is_file():
        resolved_path = match.resolve()
        if resolved_path in seen:
            return True
        seen.add(resolved_path)
        resolved.append(resolved_path)
        return True
    return False


_RECURSIVE_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)


def _collect_directory_files(
    directory: Path, resolved: list[Path], seen: set[Path]
) -> None:
    """Walk `directory` recursively, appending supported files to `resolved`.

    Recursive walks intentionally use the *extension* table (not content
    sniffing) to decide whether to include a file - a random `note.xyz`
    full of ASCII would otherwise sneak in. Users who actually want an
    extension-less file converted can name it explicitly. Skips dotfiles
    and well-known SCM / build directories so the user does not
    accidentally convert their `.git/` objects. Symlink loops are
    short-circuited via the ``seen`` set.
    """
    # Local import to avoid pulling the extractor namespace at module load.
    from convert_anything_md.detect import _EXT_MAP

    for child in sorted(directory.rglob("*")):
        # Skip if any path component is hidden or a known build dir.
        try:
            relative = child.relative_to(directory)
        except ValueError:
            relative = child
        if any(
            part in _RECURSIVE_SKIP_DIRS or part.startswith(".")
            for part in relative.parts
        ):
            continue
        if not child.is_file():
            continue
        if child.suffix.lower() not in _EXT_MAP:
            continue
        try:
            resolved_path = child.resolve()
        except OSError:
            continue
        if resolved_path in seen:
            continue
        seen.add(resolved_path)
        resolved.append(resolved_path)


# ---------------------------------------------------------------------------
# Output: human-readable
# ---------------------------------------------------------------------------


def _emit_human(outcomes: list[ConversionOutcome], *, verbose: bool) -> None:
    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:  # pragma: no cover - rich is a hard dep
        _emit_plain(outcomes, verbose=verbose)
        return

    console = Console()
    table = Table(
        title=f"convert-anything-md v{__version__}",
        title_justify="left",
        show_lines=False,
        expand=False,
    )
    table.add_column("Source", overflow="fold", no_wrap=False)
    table.add_column("Kind", no_wrap=True)
    table.add_column("Engine", no_wrap=True)
    table.add_column("Pages", justify="right", no_wrap=True)
    table.add_column("Words", justify="right", no_wrap=True)
    table.add_column("Time", justify="right", no_wrap=True)
    table.add_column("Output", overflow="fold")

    for o in outcomes:
        if o.ok:
            table.add_row(
                o.source.name,
                o.kind.value,
                o.engine,
                str(o.pages) if o.pages is not None else "-",
                f"{o.word_count:,}",
                f"{o.duration_ms / 1000:.1f}s",
                str(o.output) if o.output else "-",
            )
        else:
            table.add_row(
                f"[red]{o.source.name}[/red]",
                o.kind.value,
                "[red]FAILED[/red]",
                "-",
                "-",
                "-",
                f"[red]{o.error or 'unknown error'}[/red]",
            )

    console.print(table)

    if verbose:
        for o in outcomes:
            if o.warnings or (verbose and o.fallback_chain):
                console.print(f"[dim]# {o.source.name}[/dim]")
                if o.fallback_chain:
                    console.print(
                        f"[dim]  chain: {' -> '.join(o.fallback_chain)}[/dim]"
                    )
                for w in o.warnings:
                    console.print(f"[yellow]  ! {w}[/yellow]")


def _emit_plain(outcomes: list[ConversionOutcome], *, verbose: bool) -> None:
    """Dumb-terminal fallback when Rich is missing or output is redirected."""
    for o in outcomes:
        if o.ok:
            print(
                f"OK  {o.source.name} -> {o.output}  "
                f"({o.engine}, {o.word_count:,} words, "
                f"{o.duration_ms / 1000:.1f}s)"
            )
        else:
            print(f"ERR {o.source.name}: {o.error}", file=sys.stderr)
        if verbose:
            for w in o.warnings:
                print(f"    ! {w}")


# ---------------------------------------------------------------------------
# Output: machine-readable JSON
# ---------------------------------------------------------------------------


def _emit_json(outcomes: list[ConversionOutcome], *, output_dir: Path) -> None:
    envelope: dict[str, Any] = {
        "tool": "convert-anything-md",
        "version": __version__,
        "output_dir": str(output_dir),
        "summary": {
            "total": len(outcomes),
            "succeeded": sum(1 for o in outcomes if o.ok),
            "failed": sum(1 for o in outcomes if not o.ok),
        },
        "results": [_outcome_to_dict(o) for o in outcomes],
    }
    json.dump(envelope, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def _outcome_to_dict(o: ConversionOutcome) -> dict[str, Any]:
    d = asdict(o)
    # Path -> str, Enum -> str, None stays None.
    d["source"] = str(o.source)
    d["output"] = str(o.output) if o.output else None
    d["kind"] = o.kind.value
    return d


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
