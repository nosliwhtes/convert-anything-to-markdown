"""Cross-platform path resolution.

Two responsibilities:

1.  `desktop_dir()` finds the user's Desktop on macOS, Windows, and Linux
    (including distros where Desktop is localized or disabled via XDG).
2.  `output_candidates()` and `conflict_safe_name()` enumerate and preview
    output filenames using the familiar `name.md`, `name (1).md`,
    `name (2).md` pattern. Writers must claim names with exclusive creation.

The helpers are unit-testable in isolation.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path


def desktop_dir() -> Path:
    """Return the user's Desktop directory for the current OS.

    Resolution order:

    * **Windows** - read `Shell Folders\\Desktop` from HKCU; fall back to
      `%USERPROFILE%\\Desktop`, then the home dir.
    * **macOS** - `~/Desktop` (always exists on standard installs).
    * **Linux / other POSIX** - parse `XDG_DESKTOP_DIR` out of
      `$XDG_CONFIG_HOME/user-dirs.dirs` (honors localization and custom
      Desktop paths); fall back to `~/Desktop`, then the home dir.

    Never raises. Always returns a `Path` that either exists or can be
    created with `.mkdir(parents=True, exist_ok=True)`.
    """
    home = Path.home()

    if sys.platform == "win32":
        desktop = _windows_desktop(home)
    elif sys.platform == "darwin":
        desktop = home / "Desktop"
    else:
        desktop = _linux_desktop(home)

    if not desktop.exists() and not home.exists():
        # Prefer Desktop even if missing (user may want us to create it);
        # but if the home dir itself is missing we fall back to CWD.
        return Path.cwd()
    return desktop


def _windows_desktop(home: Path) -> Path:
    """Windows-specific Desktop lookup with registry + env fallbacks."""
    # Preferred: registry lookup (handles OneDrive redirection, custom
    # Desktop paths, and non-English Windows installations where "Desktop"
    # is translated).
    try:  # pragma: no cover - Windows-only branch
        import winreg  # type: ignore[import-not-found]

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            raw, _ = winreg.QueryValueEx(key, "Desktop")
        expanded = os.path.expandvars(raw)
        if expanded:
            return Path(expanded)
    except Exception:  # noqa: BLE001 - any registry failure means fall back
        pass

    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        return Path(userprofile) / "Desktop"
    return home / "Desktop"


_XDG_DESKTOP_LINE = re.compile(r'^\s*XDG_DESKTOP_DIR\s*=\s*"([^"]+)"\s*$')


def _linux_desktop(home: Path) -> Path:
    """Linux Desktop lookup via XDG user-dirs config."""
    xdg_config_home = Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config"))
    user_dirs = xdg_config_home / "user-dirs.dirs"
    if user_dirs.is_file():
        # Catch BOTH OSError (filesystem) AND UnicodeDecodeError /
        # general ValueError (text decoding) - the docstring promises
        # never to raise, and a corrupt user-dirs.dirs file would
        # otherwise propagate a decode error.
        try:
            for line in user_dirs.read_text(encoding="utf-8").splitlines():
                match = _XDG_DESKTOP_LINE.match(line)
                if match:
                    raw = match.group(1)
                    # Expand $HOME references written by xdg-user-dirs.
                    expanded = raw.replace("$HOME", str(home))
                    return Path(os.path.expandvars(expanded))
        except (OSError, UnicodeDecodeError, ValueError):
            pass
    return home / "Desktop"


# `(n)` suffix pattern - matches macOS/Windows "Keep Both" behavior.
_COUNTER_SUFFIX = re.compile(r"^(.*) \((\d+)\)$")


def conflict_safe_name(directory: Path, stem: str, ext: str = ".md") -> Path:
    """Return `directory/stem.ext` if free, else `stem (1).ext`, etc.

    * Treats every existing entry, including dangling symlinks, as occupied.
    * Uses the `(n)` increment convention familiar from macOS and Windows.
    * Strips an existing ` (n)` suffix from `stem` before counting so that
      re-running with a previous output doesn't produce ``foo (1) (1).md``.
    * `ext` must include the leading dot (or be ``""`` for no extension).

    This is only a preview, not a reservation. Writers must use exclusive
    creation and retry collisions, since another writer may claim the name.
    """
    for candidate in output_candidates(directory, stem, ext):
        if not os.path.lexists(candidate):
            return candidate
    raise FileExistsError(f"no available output name for {stem}{ext} in {directory}")


def output_candidates(directory: Path, stem: str, ext: str = ".md") -> Iterator[Path]:
    """Yield the canonical name then numbered alternatives, up to 10,000 names."""
    if ext and not ext.startswith("."):
        ext = "." + ext

    # Normalize: strip any trailing " (n)" so we don't stack suffixes.
    match = _COUNTER_SUFFIX.match(stem)
    if match:
        stem = match.group(1)

    yield directory / f"{stem}{ext}"

    # Cap at 9999 to avoid pathological infinite loops.
    for i in range(1, 10_000):
        yield directory / f"{stem} ({i}){ext}"


def ensure_dir(path: Path) -> Path:
    """Create `path` (and parents) if missing; return the resolved path."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_stem(input_path: Path) -> str:
    """Return a Markdown-safe stem for an input path.

    Strips the extension, replaces characters that are illegal or awkward
    on Windows filesystems (`<>:"/\\|?*`), collapses whitespace, and trims
    to a reasonable length. The resulting stem is safe to use on any of
    macOS, Linux, or Windows.
    """
    stem = input_path.stem
    # Replace Windows-reserved and shell-awkward characters.
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    # Trim trailing dots + spaces (Windows explorer dislikes both) and
    # leading dots (so a bare ".pdf" or Unix dot-file like ".bashrc"
    # doesn't become a visually-hidden output file).
    stem = stem.strip(". ")
    if not stem:
        stem = "converted"
    return stem[:200]  # keep total name comfortably under OS limits
