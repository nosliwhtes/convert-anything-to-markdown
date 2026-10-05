"""Format-aware dispatcher with graceful-fallback chains.

Public entry points:

* `convert_file(path, **options) -> ConversionOutcome` - convert one file,
  write the Markdown (with optional front-matter) to disk, return a rich
  outcome object the CLI / skill can format for the user.
* `convert_batch(paths, **options) -> list[ConversionOutcome]` - same for
  multiple files.

Each `FileKind` maps to an **ordered list of extractor factories**. The
router walks the list, catching `ExtractorUnavailable` (silent skip) and
`ExtractorError` (append to warnings and keep going). The first success
wins; if every extractor fails the outcome is marked failed.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from convert_anything_md.detect import FileKind, detect_kind, is_scanned_pdf
from convert_anything_md.extractors.anydoc import AnyDocExtractor
from convert_anything_md.extractors.base import (
    ExtractionResult,
    Extractor,
    ExtractorError,
    ExtractorUnavailable,
)
from convert_anything_md.extractors.epub import EpubExtractor
from convert_anything_md.extractors.html import (
    BeautifulSoupHtmlExtractor,
    TrafilaturaExtractor,
)
from convert_anything_md.extractors.image import ImageOcrExtractor
from convert_anything_md.extractors.office import (
    CsvExtractor,
    DocxExtractor,
    PptxExtractor,
    XlsxExtractor,
)
from convert_anything_md.extractors.pdf_docling import DoclingExtractor
from convert_anything_md.extractors.pdf_markitdown import MarkItDownExtractor
from convert_anything_md.extractors.pdf_ocr import PdfOcrExtractor
from convert_anything_md.extractors.text import (
    MarkdownPassthroughExtractor,
    PlainTextExtractor,
    RtfPandocExtractor,
    RtfStripExtractor,
)
from convert_anything_md.extractors.vcf import VCFExtractor
from convert_anything_md.frontmatter import build_frontmatter
from convert_anything_md.paths import (
    conflict_safe_name,
    desktop_dir,
    ensure_dir,
    safe_stem,
)

# ---------------------------------------------------------------------------
# Extractor chains
# ---------------------------------------------------------------------------
#
# Each kind maps to a list of extractor *factories* (zero-arg callables
# returning a fresh `Extractor`). The first one that returns a result wins.
# Factories lazy-instantiate so we don't pay import cost for formats the
# user never converts.
# ---------------------------------------------------------------------------

_CHAINS: dict[FileKind, list[type[Extractor]]] = {
    # PDF: anydoc handles text-based PDFs in Rust (4ms vs 500ms Docling).
    # Scanned PDFs still need Tesseract OCR (anydoc can't do OCR).
    FileKind.PDF_TEXT: [AnyDocExtractor, DoclingExtractor, MarkItDownExtractor, PdfOcrExtractor],
    FileKind.PDF_SCANNED: [PdfOcrExtractor, DoclingExtractor, MarkItDownExtractor],
    # Office documents: anydoc first (Rust, 4ms, unified serializer),
    # then Docling/MarkItDown as fallback, then pure-Python last resort.
    FileKind.DOCX: [AnyDocExtractor, DoclingExtractor, MarkItDownExtractor, DocxExtractor],
    FileKind.PPTX: [AnyDocExtractor, DoclingExtractor, MarkItDownExtractor, PptxExtractor],
    FileKind.XLSX: [AnyDocExtractor, DoclingExtractor, MarkItDownExtractor, XlsxExtractor],
    FileKind.ODT: [AnyDocExtractor, MarkItDownExtractor],
    FileKind.ODS: [AnyDocExtractor, MarkItDownExtractor],
    FileKind.ODP: [AnyDocExtractor, MarkItDownExtractor],
    FileKind.CSV: [CsvExtractor, AnyDocExtractor, MarkItDownExtractor],
    FileKind.HTML: [TrafilaturaExtractor, MarkItDownExtractor, BeautifulSoupHtmlExtractor],
    FileKind.EPUB: [AnyDocExtractor, EpubExtractor, MarkItDownExtractor],
    FileKind.RTF: [AnyDocExtractor, RtfPandocExtractor, RtfStripExtractor, MarkItDownExtractor],
    FileKind.MARKDOWN: [MarkdownPassthroughExtractor],
    FileKind.PLAINTEXT: [PlainTextExtractor],
    FileKind.IMAGE: [ImageOcrExtractor],
    FileKind.VCF: [VCFExtractor],
}


def _all_extractor_classes() -> list[type[Extractor]]:
    """Every extractor class wired into a chain, de-duplicated."""
    seen: dict[str, type[Extractor]] = {}
    for chain in _CHAINS.values():
        for cls in chain:
            seen.setdefault(cls.name, cls)
    return list(seen.values())


#: Every extractor name the CLI's --engine flag will accept. Built from
#: `_CHAINS` so adding a new extractor automatically widens the surface.
EXTRACTOR_NAMES: frozenset[str] = frozenset(
    cls.name for cls in _all_extractor_classes()
)


# ---------------------------------------------------------------------------
# Public result types
# ---------------------------------------------------------------------------


@dataclass
class ConversionOutcome:
    """Per-file result returned by convert_file / convert_batch."""

    source: Path
    kind: FileKind
    ok: bool
    output: Path | None = None
    engine: str = ""
    pages: int | None = None
    word_count: int = 0
    duration_ms: int = 0
    warnings: list[str] = field(default_factory=list)
    fallback_chain: list[str] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


def convert_file(
    path: Path,
    *,
    output_dir: Path | None = None,
    include_frontmatter: bool = True,
    engine_override: str | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
) -> ConversionOutcome:
    """Convert `path` to Markdown and write it to `output_dir` (default: Desktop).

    Arguments:
        path:                 The input document.
        output_dir:           Destination directory. Defaults to the OS desktop.
        include_frontmatter:  Prepend a YAML provenance header.
        engine_override:      Force a specific extractor by name (e.g. "docling").
                              If unknown or incompatible with the file kind, the
                              full chain is used anyway with a warning.
        overwrite:            Overwrite a colliding filename instead of using
                              `name (1).md`.
        dry_run:              Run detection + extraction but do not write output.
    """
    kind = FileKind.UNKNOWN
    try:
        path = path.expanduser().resolve()
        if not path.is_file():
            return ConversionOutcome(
                source=path,
                kind=FileKind.UNKNOWN,
                ok=False,
                error=f"file not found: {path}",
            )

        kind = detect_kind(path)
        # Refine PDF kind using the scanned heuristic.
        if kind == FileKind.PDF_TEXT and is_scanned_pdf(path):
            kind = FileKind.PDF_SCANNED
    except OSError as exc:
        return ConversionOutcome(
            source=path, kind=kind, ok=False, error=f"cannot read input: {exc}",
        )

    chain = _CHAINS.get(kind)
    if not chain:
        return ConversionOutcome(
            source=path,
            kind=kind,
            ok=False,
            error=(
                f"unsupported file kind: {kind.value!r} for {path.name}. "
                "Supported: PDF, DOCX, PPTX, XLSX, ODT, ODS, ODP, "
                "CSV, HTML, EPUB, RTF, MD/TXT, PNG/JPG/TIFF/BMP/WEBP."
            ),
        )

    extractor_classes, override_warnings = _reorder_for_override(
        chain, engine_override
    )

    # Create the output directory BEFORE invoking the (possibly expensive)
    # extractor chain - failing fast on a read-only desktop beats throwing
    # away a 30s docling run because the destination was never writable.
    if not dry_run:
        try:
            out_dir = ensure_dir(
                (output_dir or desktop_dir()).expanduser().resolve()
            )
        except OSError as exc:
            return ConversionOutcome(
                source=path,
                kind=kind,
                ok=False,
                error=(
                    f"cannot prepare output directory "
                    f"{output_dir or '(desktop)'}: {exc}"
                ),
            )
    else:
        out_dir = (output_dir or desktop_dir()).expanduser().resolve()

    result, warnings, attempted = _run_chain(path, extractor_classes)
    warnings = override_warnings + warnings
    if result is None:
        return ConversionOutcome(
            source=path,
            kind=kind,
            ok=False,
            error=(
                "all extractors failed or were unavailable: "
                + "; ".join(warnings)
            ),
            fallback_chain=attempted,
            warnings=warnings,
        )

    # `result.fallback_chain` only holds the successful engine name; overwrite
    # with the full attempted list so the user sees the skipped extractors.
    result = dataclasses.replace(
        result,
        warnings=list(result.warnings) + warnings,
        fallback_chain=attempted,
    )

    try:
        out_path = _pick_output_path(out_dir, path, overwrite=overwrite)

        body = result.markdown
        if include_frontmatter:
            header = build_frontmatter(
                source=path,
                engine=result.engine,
                fallback_chain=result.fallback_chain,
                pages=result.page_count,
                word_count=result.word_count,
                duration_ms=result.duration_ms,
                warnings=result.warnings,
                extra=result.extra or None,
            )
            body = header + body

        if not dry_run:
            out_path.write_text(body, encoding="utf-8", newline="\n")
    except OSError as exc:
        return ConversionOutcome(
            source=path, kind=kind, ok=False,
            engine=result.engine, warnings=result.warnings,
            fallback_chain=result.fallback_chain,
            error=f"cannot write output: {exc}",
        )

    return ConversionOutcome(
        source=path,
        kind=kind,
        ok=True,
        output=out_path,
        engine=result.engine,
        pages=result.page_count,
        word_count=result.word_count,
        duration_ms=result.duration_ms,
        warnings=result.warnings,
        fallback_chain=result.fallback_chain,
    )


def convert_batch(
    paths: Iterable[Path],
    **options,  # noqa: ANN003 - forwards to convert_file
) -> list[ConversionOutcome]:
    """Convert many files. Errors on one file never abort the rest."""
    outcomes = []
    for path in paths:
        try:
            outcomes.append(convert_file(path, **options))
        except OSError as exc:
            outcomes.append(ConversionOutcome(
                source=path, kind=FileKind.UNKNOWN, ok=False,
                error=f"filesystem error: {exc}",
            ))
    return outcomes


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_chain(
    path: Path,
    extractor_classes: list[type[Extractor]],
) -> tuple[ExtractionResult | None, list[str], list[str]]:
    """Walk the extractor chain until one succeeds.
    Returns (result, warnings, attempted)."""
    warnings: list[str] = []
    attempted: list[str] = []

    for cls in extractor_classes:
        extractor = cls()
        attempted.append(extractor.name)
        try:
            result = extractor.extract(path)
            # A successful extraction that yields *no* output is useless — an
            # empty .md gives the user nothing and hides the real reason. Treat
            # it like a failure and let the chain fall back to the next engine
            # (e.g. `anydoc` can "convert" a valid but empty document to an
            # empty body; `markdown-passthrough` on a blank file).
            if not result.markdown.strip():
                warnings.append(
                    f"{extractor.name} returned empty output; trying next extractor"
                )
                continue
            return result, warnings, attempted
        except ExtractorUnavailable as exc:
            warnings.append(f"{extractor.name} unavailable: {exc}")
        except ExtractorError as exc:
            warnings.append(f"{extractor.name} failed: {exc}")
        except Exception as exc:  # noqa: BLE001 - defensive catch-all
            warnings.append(f"{extractor.name} crashed: {exc!r}")

    return None, warnings, attempted


def _reorder_for_override(
    chain: list[type[Extractor]], override: str | None
) -> tuple[list[type[Extractor]], list[str]]:
    """Move the override engine to the front of the chain if present.

    Returns:
        (reordered_chain, warnings)
        warnings contains a single message when the override was specified
        but does not belong to this file's chain - earlier versions silently
        swallowed that mismatch, leaving users guessing why their engine
        choice had no effect.
    """
    if not override:
        return list(chain), []
    for cls in chain:
        if cls.name == override:  # use class attribute - no instantiation
            reordered = [cls] + [c for c in chain if c is not cls]
            return reordered, []
    # Override is not in this chain - tell the user instead of silently
    # ignoring their flag.
    chain_names = ", ".join(cls.name for cls in chain)
    return list(chain), [
        f"engine override {override!r} is not applicable to this file kind "
        f"(applicable extractors: {chain_names}); falling back to default order"
    ]


def _pick_output_path(out_dir: Path, source: Path, *, overwrite: bool) -> Path:
    """Resolve the final .md path, honoring the overwrite flag.

    `safe_stem` keeps the user-visible name; `conflict_safe_name` also
    strips any trailing `(n)` from re-converted outputs so we don't
    accumulate `foo (1) (1).md`. We share the strip-then-build path with
    --overwrite so the two code paths converge on the same canonical name.
    """
    stem = safe_stem(source)
    if overwrite:
        # Reuse the strip logic so `report (1).pdf --overwrite` -> report.md,
        # not `report (1).md`. Without --overwrite we then walk the (n)
        # ladder; with --overwrite we just clobber the stripped name.
        return _strip_counter_and_build(out_dir, stem)
    return conflict_safe_name(out_dir, stem, ".md")


def _strip_counter_and_build(out_dir: Path, stem: str) -> Path:
    """Strip a trailing ' (n)' from `stem` and return `<out_dir>/<stem>.md`."""
    import re

    match = re.match(r"^(.*) \((\d+)\)$", stem)
    if match:
        stem = match.group(1)
    return out_dir / f"{stem}.md"
