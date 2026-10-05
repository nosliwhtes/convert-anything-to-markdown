# convert-anything-md

Convert PDFs, Office files, OpenDocument files, images, and web pages to clean
GitHub-Flavored Markdown. Chooses the best extractor per format automatically.
Runs offline with no API keys. Cross-platform (macOS, Linux, Windows).

## Why

Every document-to-markdown tool has different strengths. Docling is great at
complex PDFs. MarkItDown handles broad Office formats. Tesseract does OCR.
Trafilatura extracts clean article text from HTML. [anydoc](https://github.com/firecrawl/anydoc)
(Firecrawl, Rust) converts office documents in single-digit milliseconds with
a unified Markdown serializer.

This project routes each file to the best available extractor and falls back
automatically when a tool is missing or fails. One CLI, one consistent output,
every format.

## Install

```bash
# Recommended: uv
uv tool install ./src

# Or: pipx
pipx install ./src

# Or: pip
pip install --user ./src
```

**Optional system binaries** (detected at runtime, not required):

- [`anydoc`](https://github.com/firecrawl/anydoc) (npm: `npm install -g @firecrawl/anydoc`)
  enables Rust-fast office document conversion (4ms median)
- `tesseract` on PATH enables OCR for scanned PDFs and image files
- `pandoc` on PATH improves RTF extraction fidelity

## Usage

```bash
# Single file — output to desktop (auto-detected per OS)
convert-anything-md report.pdf

# Multiple files + custom output dir
convert-anything-md -o ~/md *.pdf notes.docx

# Walk a directory recursively
convert-anything-md -r ~/Documents/inbox -o ~/md

# Force a specific engine
convert-anything-md --engine markitdown fast-preview.pdf

# List every extractor name the --engine flag accepts
convert-anything-md --list-engines

# Machine-readable output for scripts / agents
convert-anything-md --json file.pdf
```

## Supported formats

| Format | Extensions | Best engine | Fallback chain |
|--------|-----------|-------------|----------------|
| PDF (text) | .pdf | anydoc | docling, markitdown, tesseract OCR |
| PDF (scanned) | .pdf | tesseract OCR | docling, markitdown |
| Word | .docx, .doc, .docm | anydoc | docling, markitdown, python-docx |
| PowerPoint | .pptx, .ppt, .pps, .pot, .pptm, .ppsx, .ppsm | anydoc | docling, markitdown, python-pptx |
| Excel | .xlsx, .xls, .xlsm, .xlsb | anydoc | docling, markitdown, openpyxl |
| OpenDocument Text | .odt | anydoc | markitdown |
| OpenDocument Spreadsheet | .ods | anydoc | markitdown |
| OpenDocument Presentation | .odp | anydoc | markitdown |
| CSV / TSV | .csv, .tsv | csv (stdlib) | anydoc, markitdown |
| HTML | .html, .htm, .xhtml | trafilatura | markitdown, beautifulsoup |
| EPUB | .epub | anydoc | ebooklib, markitdown |
| RTF | .rtf | anydoc | pandoc, striprtf, markitdown |
| Markdown | .md, .markdown, .mdown | passthrough | — |
| Plain text | .txt, .text, .log | passthrough | — |
| Images | .png, .jpg, .jpeg, .tif, .tiff, .bmp, .webp, .gif | tesseract OCR | — |
| vCard | .vcf | vobject | — |

## How it works

1. **Detect** the file type from extension + magic bytes
2. **Route** to the best extractor for that format
3. **Fall back** through a chain if the first extractor is unavailable or fails
4. **Write** clean Markdown with optional YAML front-matter (source path, SHA-256,
   engine used, word count, duration)

Each converted `.md` includes a YAML front-matter block with provenance metadata.
Pass `--no-frontmatter` for a clean file.

## Agent skill

This project ships as an [agent skill](https://github.com/skills-sh) that teaches
any compatible agent (Claude Code, Codex, Cursor, OpenCode, Hermes, etc.) to convert
documents with the CLI. See `skills/SKILL.md`.

## Running the tests

```bash
cd src
uv run pytest          # or: python -m pytest
```

The PDF-specific tests automatically skip when PyMuPDF (`fitz`) isn't installed;
every other test runs regardless.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines
on adding extractors, reporting bugs, and submitting pull requests.

### Good first issues

New to the project? Check the [good first issues](https://github.com/nosliwhtes/convert-anything-to-markdown/labels/good%20first%20issue)
label for small, well-scoped tasks. Each issue includes step-by-step instructions
and points to the relevant files.

## License

MIT. See [LICENSE](LICENSE).
