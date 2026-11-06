"""Plain-text, Markdown, and RTF extractors.

* TXT / log  → passthrough inside a minimal `# Filename` heading.
* MD         → passthrough (already Markdown).
* RTF        → pandoc if available, else the pure-Python `striprtf` fallback.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

from convert_anything_md.extractors.base import (
    ExtractionResult,
    ExtractorError,
    ExtractorUnavailable,
    word_count,
)


class PlainTextExtractor:
    """TXT / log → Markdown by wrapping in a minimal heading."""

    name = "plaintext"

    def extract(self, path: Path) -> ExtractionResult:
        start = time.perf_counter()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ExtractorError(f"cannot read {path.name}: {exc}") from exc

        # A source backtick run must never close the surrounding code block.
        longest_run = max((len(match[0]) for match in re.finditer(r"`+", text)), default=0)
        fence = "`" * max(3, longest_run + 1)
        markdown = f"# {path.stem}\n\n{fence}\n{text.rstrip()}\n{fence}\n"
        duration_ms = int((time.perf_counter() - start) * 1000)
        return ExtractionResult(
            markdown=markdown,
            engine=self.name,
            word_count=word_count(text),
            duration_ms=duration_ms,
            fallback_chain=[self.name],
        )


class MarkdownPassthroughExtractor:
    """Markdown source → Markdown (normalization only)."""

    name = "markdown-passthrough"

    def extract(self, path: Path) -> ExtractionResult:
        start = time.perf_counter()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ExtractorError(f"cannot read {path.name}: {exc}") from exc

        duration_ms = int((time.perf_counter() - start) * 1000)
        return ExtractionResult(
            markdown=text.rstrip() + "\n",
            engine=self.name,
            word_count=word_count(text),
            duration_ms=duration_ms,
            fallback_chain=[self.name],
        )


class RtfPandocExtractor:
    """RTF → Markdown via pandoc (best fidelity) if the binary is installed."""

    name = "pandoc"

    def extract(self, path: Path) -> ExtractionResult:
        pandoc = shutil.which("pandoc")
        if not pandoc:
            raise ExtractorUnavailable(
                "pandoc is not on PATH (install: macOS `brew install pandoc`, "
                "Linux via apt/dnf/pacman, Windows via https://pandoc.org)"
            )

        start = time.perf_counter()
        try:
            completed = subprocess.run(
                [pandoc, "-f", "rtf", "-t", "gfm", str(path)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise ExtractorError(f"pandoc failed on {path.name}: {exc}") from exc

        if completed.returncode != 0:
            raise ExtractorError(
                f"pandoc exited {completed.returncode}: "
                f"{completed.stderr.strip()[:500]}"
            )

        markdown = completed.stdout.strip() + "\n"
        duration_ms = int((time.perf_counter() - start) * 1000)
        return ExtractionResult(
            markdown=markdown,
            engine=self.name,
            word_count=word_count(markdown),
            duration_ms=duration_ms,
            fallback_chain=[self.name],
        )


class RtfStripExtractor:
    """Pure-Python RTF fallback via `striprtf` — works on all OSes."""

    name = "striprtf"

    def extract(self, path: Path) -> ExtractionResult:
        try:
            from striprtf.striprtf import rtf_to_text
        except ImportError as exc:
            raise ExtractorUnavailable("striprtf is not installed") from exc

        start = time.perf_counter()
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ExtractorError(f"cannot read {path.name}: {exc}") from exc

        try:
            text = rtf_to_text(raw)
        except Exception as exc:  # noqa: BLE001
            raise ExtractorError(f"striprtf failed on {path.name}: {exc}") from exc

        markdown = f"# {path.stem}\n\n{text.strip()}\n"
        duration_ms = int((time.perf_counter() - start) * 1000)
        return ExtractionResult(
            markdown=markdown,
            engine=self.name,
            word_count=word_count(text),
            duration_ms=duration_ms,
            fallback_chain=[self.name],
        )

