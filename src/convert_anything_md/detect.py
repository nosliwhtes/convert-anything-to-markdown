"""File-type detection.

Two layers of decision-making:

1.  `detect_kind(path)` returns a normalized `FileKind` enum based on a
    combination of extension, magic-byte sniffing, and content inspection.
    This is what the router switches on.
2.  `is_scanned_pdf(path)` decides whether a PDF should go through the OCR
    chain. It samples a handful of pages with PyMuPDF and checks the
    average character count per page. Below the threshold → scanned.

Both are pure(ish) — the only side effect is opening the file for reading.
"""

from __future__ import annotations

import mimetypes
from enum import Enum
from pathlib import Path


class FileKind(str, Enum):
    """Normalized document kinds the router dispatches on."""

    PDF_TEXT = "pdf_text"          # PDF with embedded text layer
    PDF_SCANNED = "pdf_scanned"    # PDF that needs OCR
    DOCX = "docx"                 # .docx, .doc, .docm
    PPTX = "pptx"                 # .pptx, .ppt, .pps, .pot, .pptm, .ppsx, .ppsm
    XLSX = "xlsx"                 # .xlsx, .xls, .xlsm, .xlsb
    ODT = "odt"                   # .odt (OpenDocument Text)
    ODS = "ods"                   # .ods (OpenDocument Spreadsheet)
    ODP = "odp"                   # .odp (OpenDocument Presentation)
    CSV = "csv"
    HTML = "html"
    EPUB = "epub"
    RTF = "rtf"
    MARKDOWN = "markdown"
    PLAINTEXT = "plaintext"
    IMAGE = "image"                # PNG / JPG / JPEG / TIFF / BMP / WEBP
    VCF = "vcf"                    # vCard
    UNKNOWN = "unknown"


# Extension → FileKind table. Lower-case keys, include the leading dot.
_EXT_MAP: dict[str, FileKind] = {
    ".pdf": FileKind.PDF_TEXT,      # may get flipped to PDF_SCANNED after sniff
    ".docx": FileKind.DOCX,
    ".doc": FileKind.DOCX,          # best-effort; MarkItDown handles .doc too
    ".docm": FileKind.DOCX,
    ".pptx": FileKind.PPTX,
    ".ppt": FileKind.PPTX,
    ".pps": FileKind.PPTX,
    ".pot": FileKind.PPTX,
    ".pptm": FileKind.PPTX,
    ".ppsx": FileKind.PPTX,
    ".ppsm": FileKind.PPTX,
    ".xlsx": FileKind.XLSX,
    ".xls": FileKind.XLSX,
    ".xlsm": FileKind.XLSX,
    ".xlsb": FileKind.XLSX,
    ".odt": FileKind.ODT,           # OpenDocument Text
    ".ods": FileKind.ODS,           # OpenDocument Spreadsheet
    ".odp": FileKind.ODP,           # OpenDocument Presentation
    ".csv": FileKind.CSV,
    ".tsv": FileKind.CSV,
    ".html": FileKind.HTML,
    ".htm": FileKind.HTML,
    ".xhtml": FileKind.HTML,
    ".epub": FileKind.EPUB,
    ".rtf": FileKind.RTF,
    ".md": FileKind.MARKDOWN,
    ".markdown": FileKind.MARKDOWN,
    ".mdown": FileKind.MARKDOWN,
    ".txt": FileKind.PLAINTEXT,
    ".text": FileKind.PLAINTEXT,
    ".log": FileKind.PLAINTEXT,
    ".png": FileKind.IMAGE,
    ".jpg": FileKind.IMAGE,
    ".jpeg": FileKind.IMAGE,
    ".tif": FileKind.IMAGE,
    ".tiff": FileKind.IMAGE,
    ".bmp": FileKind.IMAGE,
    ".webp": FileKind.IMAGE,
    ".gif": FileKind.IMAGE,
    ".vcf": FileKind.VCF,
}

# Magic byte signatures for extension-less or mis-named files.
# Limited to the formats where extension spoofing is common (PDF, Office, EPUB).
_MAGIC_SIGNATURES: list[tuple[bytes, FileKind]] = [
    (b"%PDF-", FileKind.PDF_TEXT),
    (b"PK\x03\x04", FileKind.DOCX),   # OOXML zip — disambiguated below
    (b"{\\rtf", FileKind.RTF),
    (b"\x89PNG\r\n\x1a\n", FileKind.IMAGE),
    (b"\xff\xd8\xff", FileKind.IMAGE),  # JPEG
    (b"GIF87a", FileKind.IMAGE),
    (b"GIF89a", FileKind.IMAGE),
    (b"II*\x00", FileKind.IMAGE),      # TIFF little-endian
    (b"MM\x00*", FileKind.IMAGE),      # TIFF big-endian
    (b"RIFF", FileKind.IMAGE),         # WebP (further check below)
    (b"<html", FileKind.HTML),
    (b"<!DOCTYPE", FileKind.HTML),
]


def detect_kind(path: Path) -> FileKind:
    """Classify `path` into a `FileKind` used by the router.

    Uses extension first (fast, correct ~99% of the time) and falls back
    to magic-byte sniffing for extension-less or ambiguous files. For PDFs,
    the caller should additionally run `is_scanned_pdf()` to refine
    `PDF_TEXT` into `PDF_SCANNED` where appropriate.
    """
    if not path.is_file():
        return FileKind.UNKNOWN

    ext = path.suffix.lower()
    if ext in _EXT_MAP:
        kind = _EXT_MAP[ext]
        # Disambiguate OOXML containers — .docx/.xlsx/.pptx all share
        # PK\x03\x04 magic but the extension already nailed it.
        return kind

    # Extension-less or unknown extension: try magic bytes.
    try:
        with path.open("rb") as f:
            head = f.read(512)
    except OSError:
        return FileKind.UNKNOWN

    for signature, kind in _MAGIC_SIGNATURES:
        if head.startswith(signature):
            if kind == FileKind.DOCX:
                return _classify_ooxml(path)
            if kind == FileKind.HTML:
                return FileKind.HTML
            return kind

    # Final fallback: mimetypes.
    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        if mime.startswith("text/"):
            return FileKind.PLAINTEXT
        if mime.startswith("image/"):
            return FileKind.IMAGE

    # Heuristic: UTF-8 / ASCII text → plaintext.
    if _looks_like_text(head):
        return FileKind.PLAINTEXT

    return FileKind.UNKNOWN


def _classify_ooxml(path: Path) -> FileKind:
    """Distinguish DOCX vs XLSX vs PPTX for a generic OOXML zip file."""
    try:
        import zipfile

        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
    except (OSError, zipfile.BadZipFile):
        return FileKind.UNKNOWN

    if any(n.startswith("word/") for n in names):
        return FileKind.DOCX
    if any(n.startswith("ppt/") for n in names):
        return FileKind.PPTX
    if any(n.startswith("xl/") for n in names):
        return FileKind.XLSX
    if any(n.startswith("OEBPS/") or n == "mimetype" for n in names):
        return FileKind.EPUB
    return FileKind.UNKNOWN


def _looks_like_text(head: bytes) -> bool:
    """Rough heuristic: printable ASCII/UTF-8 dominates the first chunk."""
    if not head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return False
    # Require that at least 90% of bytes are printable-ish.
    printable = sum(1 for b in head if 0x20 <= b < 0x7f or b in (0x09, 0x0a, 0x0d))
    return printable / len(head) >= 0.9


# --- PDF scanned-vs-text heuristic --------------------------------------

#: Min avg printable chars per page to consider the PDF "text-bearing".
#: Chosen conservatively: well-extracted pages yield thousands of chars;
#: scanned PDFs yield 0–20 of incidental junk (metadata, form fields).
PDF_TEXT_THRESHOLD_CHARS_PER_PAGE = 100

#: Number of pages to sample (spread evenly through the document).
PDF_SAMPLE_PAGES = 5


def is_scanned_pdf(
    path: Path,
    *,
    sample_pages: int = PDF_SAMPLE_PAGES,
    threshold: int = PDF_TEXT_THRESHOLD_CHARS_PER_PAGE,
) -> bool:
    """Return True if `path` is a PDF that needs OCR (no embedded text).

    Opens the PDF with PyMuPDF, samples up to `sample_pages` evenly-spaced
    pages, and measures average printable characters per page. Below
    `threshold` → scanned. Gracefully returns False if PyMuPDF is absent
    or the PDF can't be opened (the router will then attempt the text
    extraction path and let that fail visibly).
    """
    try:
        import pymupdf  # pymupdf
    except ImportError:
        return False

    try:
        doc = pymupdf.open(str(path))
    except Exception:  # noqa: BLE001 - any open failure → let caller handle
        return False

    try:
        page_count = doc.page_count
        if page_count == 0:
            return False

        if page_count <= sample_pages:
            indices = list(range(page_count))
        else:
            step = page_count / sample_pages
            indices = [int(step * i) for i in range(sample_pages)]

        total_chars = 0
        for i in indices:
            try:
                text = doc.load_page(i).get_text("text") or ""
            except Exception:  # noqa: BLE001
                text = ""
            # Count only printable chars to ignore metadata noise.
            total_chars += sum(
                1 for c in text if c.isprintable() and not c.isspace()
            )

        avg = total_chars / len(indices)
        return avg < threshold
    finally:
        doc.close()

