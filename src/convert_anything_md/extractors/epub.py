"""EPUB → Markdown via ebooklib + BeautifulSoup.

EPUB is fundamentally a zip of XHTML documents plus a spine that orders
them. We walk the spine, strip each chapter's HTML to a simple
heading + paragraph structure, and concatenate.
"""

from __future__ import annotations

import time
from pathlib import Path

from convert_anything_md.extractors.base import (
    ExtractionResult,
    ExtractorError,
    ExtractorUnavailable,
    word_count,
)


class EpubExtractor:
    """EPUB → Markdown. Walks the spine; one chapter per logical section."""

    name = "ebooklib"

    def extract(self, path: Path) -> ExtractionResult:
        try:
            from ebooklib import ITEM_DOCUMENT, epub
        except ImportError as exc:
            raise ExtractorUnavailable("ebooklib is not installed") from exc

        try:
            from bs4 import BeautifulSoup
        except ImportError as exc:
            raise ExtractorUnavailable("beautifulsoup4 is not installed") from exc

        start = time.perf_counter()
        try:
            book = epub.read_epub(str(path))
        except Exception as exc:  # noqa: BLE001
            raise ExtractorError(f"ebooklib failed on {path.name}: {exc}") from exc

        title = _first_metadata(book, "title") or path.stem
        author = _first_metadata(book, "creator") or ""

        parts: list[str] = [f"# {title}"]
        if author:
            parts.append(f"_by {author}_")

        chapter_count = 0
        warnings: list[str] = []
        items = []
        for idref, _linear in book.spine:
            item = book.get_item_with_id(idref)
            if item is None or item.get_type() != ITEM_DOCUMENT:
                warnings.append(f"invalid EPUB spine reference: {idref}")
                continue
            items.append(item)
        if not items:
            warnings.append("EPUB spine has no readable documents; using manifest order")
            items = list(book.get_items_of_type(ITEM_DOCUMENT))

        for item in items:
            try:
                raw = item.get_content().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                continue
            soup = BeautifulSoup(raw, "lxml")
            # Drop scripts/styles.
            for t in soup(["script", "style"]):
                t.decompose()

            chapter_text = []
            for element in soup.find_all(
                ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote"]
            ):
                text = element.get_text(" ", strip=True)
                if not text:
                    continue
                if element.name.startswith("h"):
                    level = int(element.name[1])
                    chapter_text.append(f"{'#' * (level + 1)} {text}")
                elif element.name == "li":
                    chapter_text.append(f"- {text}")
                elif element.name == "blockquote":
                    chapter_text.append(f"> {text}")
                else:
                    chapter_text.append(text)

            if chapter_text:
                chapter_count += 1
                parts.append("\n\n".join(chapter_text))

        if chapter_count == 0:
            raise ExtractorError(
                f"no readable chapters found in {path.name}"
            )

        markdown = "\n\n".join(parts).strip() + "\n"
        duration_ms = int((time.perf_counter() - start) * 1000)
        return ExtractionResult(
            markdown=markdown,
            engine=self.name,
            page_count=chapter_count,
            word_count=word_count(markdown),
            duration_ms=duration_ms,
            fallback_chain=[self.name],
            warnings=warnings,
            extra={"author": author} if author else {},
        )


def _first_metadata(book, field: str) -> str:  # type: ignore[no-untyped-def]
    """Best-effort lookup of a single Dublin Core metadata value."""
    try:
        values = book.get_metadata("DC", field)
    except Exception:  # noqa: BLE001
        return ""
    if not values:
        return ""
    value = values[0]
    if isinstance(value, tuple):
        value = value[0]
    return str(value).strip()

