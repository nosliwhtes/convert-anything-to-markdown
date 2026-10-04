# Contributing to convert-anything-md

Thanks for your interest in improving this project. This document covers the
basics of contributing code, reporting bugs, and suggesting new extractors.

## Quick start

```bash
# Clone the repo
git clone https://github.com/nosliwhtes/convert-anything-to-markdown.git
cd convert-anything-to-markdown

# Install the locked development environment
cd src
uv sync --locked --extra dev

# Run offline tests, lint, and package builds
uv run --no-sync pytest
uv run --no-sync ruff check .
uv run --no-sync python -m build --installer uv
```

## Project structure

```
convert-anything-to-markdown/
├── src/                          # Python package
│   ├── pyproject.toml            # Package metadata + dependencies
│   ├── convert_anything_md/
│   │   ├── cli.py                 # CLI entry point (argparse)
│   │   ├── router.py              # Format detection + fallback chains
│   │   ├── detect.py             # File type detection (extension + magic bytes)
│   │   ├── frontmatter.py         # YAML provenance header
│   │   ├── paths.py               # Cross-platform path helpers
│   │   └── extractors/
│   │       ├── base.py            # Extractor protocol + result type
│   │       ├── anydoc.py           # anydoc (Rust, Firecrawl) - office docs
│   │       ├── pdf_docling.py     # Docling - PDF + Office
│   │       ├── pdf_markitdown.py  # MarkItDown - broad format fallback
│   │       ├── pdf_ocr.py         # Tesseract OCR - scanned PDFs
│   │       ├── office.py          # Pure-Python DOCX/PPTX/XLSX/CSV fallbacks
│   │       ├── html.py            # Trafilatura + BeautifulSoup
│   │       ├── epub.py            # Ebooklib
│   │       ├── image.py           # Tesseract OCR for images
│   │       └── text.py            # Markdown/plain text passthrough + RTF
│   ├── tests/
│   └── scripts/
│       └── install.py             # Cross-platform installer (uv -> pipx -> pip)
├── skills/
│   └── SKILL.md                   # Agent skill definition
├── tools/                         # Documentation (architecture, troubleshooting)
├── examples/                      # Usage examples
├── LICENSE
├── README.md
├── CONTRIBUTING.md
└── .gitignore
```

## Adding a new extractor

Extractors are the core extension point. Each one implements the `Extractor`
protocol from `extractors/base.py`:

1. **Create a new file** in `src/convert_anything_md/extractors/` (e.g.
   `myengine.py`).
2. **Implement the `Extractor` protocol:**
   - Class attribute `name` (short identifier for front-matter)
   - Method `extract(self, path: Path) -> ExtractionResult`
   - Raise `ExtractorUnavailable` if the backing tool isn't installed
   - Raise `ExtractorError` if extraction fails
3. **Wire it into the chain** in `router.py`:
   - Import your extractor class
   - Add it to the `_CHAINS` dict for the appropriate `FileKind`(s)
   - Order matters: best engine first, fallbacks after
4. **Add tests** in `src/tests/`
5. **Update the supported formats table** in `README.md` if you added a new
   format or a new engine option

### Example extractor

```python
from convert_anything_md.extractors.base import (
    ExtractionResult, ExtractorError, ExtractorUnavailable, word_count
)

class MyExtractor:
    name = "myengine"

    def extract(self, path: Path) -> ExtractionResult:
        try:
            import my_library
        except ImportError as exc:
            raise ExtractorUnavailable("my_library is not installed") from exc

        # ... do extraction ...
        markdown = my_library.convert(path)

        return ExtractionResult(
            markdown=markdown,
            engine=self.name,
            word_count=word_count(markdown),
            duration_ms=42,
            fallback_chain=[self.name],
        )
```

## Adding a new file format

1. **Add a `FileKind`** enum value in `detect.py`
2. **Add extension mappings** in `_EXT_MAP` (and magic byte signatures if
   the format has a detectable header)
3. **Add a chain** in `router.py` mapping the new `FileKind` to extractors
4. **Update the error message** in `router.py` listing supported formats
5. **Update `README.md`** supported formats table

## Reporting bugs

Use the [GitHub issue tracker](https://github.com/nosliwhtes/convert-anything-to-markdown/issues).
Include:

- The file type and extension you were converting
- The command you ran (include `--verbose --json` output)
- The engine that was used (from the JSON `engine` field)
- Your OS and Python version
- The error message, if any

## Pull requests

1. **Fork** the repo and create a branch from `main`
2. **Make your changes** following the code style (enforced by `ruff`)
3. **Add or update tests** for your changes
4. **Run tests locally:** `cd src && pytest`
5. **Run the linter:** `ruff check .`
6. **Open a pull request** with a clear description of what and why

### PR checklist

- [ ] Tests pass (`pytest`)
- [ ] Linter passes (`ruff check .`)
- [ ] New extractors raise `ExtractorUnavailable` when their tool is missing
- [ ] New extractors are wired into the appropriate chain in `router.py`
- [ ] README.md updated if formats or engines changed
- [ ] No hardcoded paths, secrets, or personal info in the code

## Code style

- Python 3.10+ (uses `from __future__ import annotations`)
- Line length: 100 chars (configured in `pyproject.toml`)
- Linter: `ruff` with `E, F, W, I, B, UP, SIM` rules
- Type hints everywhere; the codebase is fully typed

## Releasing

Releases are tagged via git tags (`v1.5.0`, etc.) and published as GitHub
Releases with auto-generated changelog notes.

## License

By contributing, you agree your contributions are licensed under the MIT
license that covers this project.
## Reproducible verification and dependency maintenance

The development dependency graph is recorded in `src/uv.lock`. From `src/`,
run `uv sync --locked --extra dev`, then `uv run --no-sync ruff check .`,
`uv run --no-sync pytest -ra -q`, and `uv run --no-sync python -m build --installer uv`.
CI uses these same commands. No static type checker is currently configured.
To refresh compatible releases, use `uv lock --upgrade` and commit the
lockfile together with any requirement changes.

The Pillow minimum is 12.3.0 to include the fixes documented in the
[upstream advisory](https://github.com/python-pillow/Pillow/security/advisories/GHSA-9hw9-ch79-4vh6);
that release retains Python 3.10 support. EbookLib 0.20 is the tested EPUB
baseline. CI uses current action majors on GitHub-hosted runners; consult the
[checkout](https://github.com/actions/checkout/releases),
[setup-python](https://github.com/actions/setup-python/releases), and
[setup-uv](https://github.com/astral-sh/setup-uv/releases) migration notes before
using older self-hosted runners.
