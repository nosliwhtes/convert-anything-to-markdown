# Architecture

## Request flow

```
user speech / CLI args
        │
        ▼
    ┌─────────┐   resolves globs, expands ~, verifies existence
    │   cli   │
    └────┬────┘
         │
         ▼
    ┌─────────┐   classifies by extension + magic bytes; flips PDF_TEXT
    │ detect  │   to PDF_SCANNED when PyMuPDF sampling says so
    └────┬────┘
         │
         ▼
    ┌─────────┐   picks an extractor chain from a static dict;
    │ router  │   runs each in order, catching Unavailable / Error
    └────┬────┘
         │
    ┌────┼────────────┬──────────┬──────────┐
    ▼    ▼            ▼          ▼          ▼
 docling markitdown tesseract trafilatura ebooklib ... (one module per tool)
         │
         ▼  (each returns an ExtractionResult dataclass)
    ┌──────────────────┐
    │   frontmatter    │   prepends YAML provenance header
    └────────┬─────────┘
             ▼
    ┌──────────────────┐
    │  paths.py        │   resolves OS desktop, picks conflict-free name
    └────────┬─────────┘
             ▼
     ~/Desktop/<stem>.md
```

## Why a router?

Every document-extraction library has a sweet spot:

* **Docling** — unbeatable layout / table fidelity, but heavy (downloads
  ML models) and slower than text-only tools.
* **MarkItDown** — very broad format support, fast, but weaker on complex
  PDF tables.
* **Trafilatura** — the best HTML boilerplate remover, but it's
  specialized: don't feed it a PDF.
* **Tesseract** — the only reliable option for pure-image input, but
  pointless for text-bearing documents.
* **pandoc** — the universal translator, but an external binary that's
  not always installed on Windows.
* **pure-Python fallbacks** (python-docx, openpyxl, ...) — wheel-only,
  works on the most restricted systems, but least rich output.

The router encodes "which tool do you want first, and what's the
sensible fallback if it's missing or fails" as a simple dict of ordered
class lists. Extractors are dead-cheap to instantiate (lazy imports keep
cold-start fast), and the framework only pays the cost of backends it
actually uses.

## Why frozen dataclasses?

`ExtractionResult` is a frozen dataclass because the router sometimes
needs to add warnings from the fallback chain after the extractor
returned. `dataclasses.replace()` creates a cheap copy with new fields
instead of mutating shared state — easier to reason about across the
extractor layer.

## Why a dedicated `paths` module?

Desktop location is the single most platform-specific thing about this
kit. Keeping it in its own module (with unit tests that mock
`sys.platform`) lets us verify Windows / macOS / Linux logic without
needing three CI runners. `conflict_safe_name()` is also a popular
reimplementation target — isolating it makes it easy to swap the
strategy (timestamp? UUID? lockfile?) if users ever ask.

`conflict_safe_name()` is a preview for dry runs: existing entries, including
dangling symlinks, occupy a name. Actual writes exclusively create candidates
from `output_candidates()` and retry collisions so concurrent conversions keep
separate outputs. Exhausting the numbered names is a reported write failure.
With `--overwrite`, the router writes a temporary file in the output directory
and replaces the canonical output entry after closing it. An output symlink is
replaced rather than followed; its target is untouched. These guarantees apply
to output entries within a trusted destination directory, not to replacement of
the directory or its ancestors by another process.

## Why lazy imports inside each extractor?

A fresh `python -m convert_anything_md` invocation takes ~200 ms on a
modern laptop because the main entry point only imports argparse,
pathlib, and the router. The heavy ML imports (Docling) happen only if
Docling is actually going to run. This matters for quick conversions
(plaintext, MD passthrough) where the user expects sub-second latency.

## Why JSON output?

The skill layer needs a contract. The human-readable Rich table is
optimized for terminals; the JSON envelope is optimized for agents
parsing stdout. Both carry the same data; only the rendering differs.

## Error model

Three exception classes make the control flow explicit:

| Exception             | When raised              | Router behavior |
|-----------------------|--------------------------|-----------------|
| `ExtractorUnavailable`| backing lib/binary missing| silent skip → try next |
| `ExtractorError`      | ran but failed            | record warning → try next |
| anything else         | programming bug           | record warning → try next (defensive) |

If every extractor fails, the `ConversionOutcome` is marked `ok=False`
with the aggregated warnings in its `error` field.

