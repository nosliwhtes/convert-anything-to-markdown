---
name: convert-to-markdown
description: >-
  Convert any document (PDF, DOCX, PPTX, XLSX, ODT, ODS, ODP, CSV, HTML,
  EPUB, RTF, images) into a clean Markdown file saved to the user's desktop.
  Use when the user says "convert this to markdown", "turn these into .md",
  "extract text from this PDF", "make a markdown copy of this document",
  "OCR this image/scan", or similar. Cross-platform (macOS, Linux, Windows).
  Routes each file to the best available extractor (anydoc, Docling,
  MarkItDown, Trafilatura, Tesseract, pandoc) and falls back automatically
  when tools are missing or fail.
triggers:
  - convert .+ to markdown
  - make .+ markdown
  - extract text from (this|the|a|these) (pdf|doc|docx|file|document)
  - turn .+ into (a )?\\.md
  - markdown (copy|version) of .+
  - ocr (this|the|these|it)
model:
  provider: any
  hosting: any
tools:
  - terminal
  - filesystem
---

# Convert Anything to Markdown

## What this skill does

Given one or more file paths (absolute, relative, or containing globs), this
skill converts each file to a high-quality Markdown (.md) file and saves it to
the user's desktop by default. It is **agent-agnostic** — any harness with
shell access can run it.

Supported inputs:

* **PDF** — text-embedded (anydoc or Docling preferred) and scanned (Tesseract OCR)
* **Word** — DOCX, DOC, DOCM
* **PowerPoint** — PPTX, PPT, PPS, POT, PPTM, PPSX, PPSM
* **Excel** — XLSX, XLS, XLSM, XLSB
* **OpenDocument** — ODT, ODS, ODP
* **Tabular** — CSV, TSV
* **Web** — HTML, XHTML
* **eBooks** — EPUB
* **Rich text** — RTF
* **Markup** — MD, TXT, log
* **Images** — PNG, JPG, JPEG, TIFF, BMP, WEBP, GIF (OCR)

## Trigger phrases to recognize

Activate this skill when the user's message matches any of:

* "convert this/these to markdown"
* "make a markdown copy of ..."
* "turn this PDF into a .md file"
* "extract the text from this document"
* "OCR this scan / image"
* "I want this as markdown on my desktop"
* Any natural variation where the intent is "document → .md file".

If the user is clearly asking for something else (summarize, translate,
rewrite), do NOT invoke this skill.

## How to invoke

This skill drives a single CLI command, `convert-anything-md`, that is
installed once via `uv tool install ./src` from the repo root. The CLI is
cross-platform and works identically on macOS, Linux, and Windows.

### Step 1 — Verify the CLI is installed

Run:

```
convert-anything-md --version
```

If it prints a version (e.g. `convert-anything-md 1.5.0`), skip to Step 3.
If the command is not found, proceed to Step 2.

### Step 2 — Install (first-time only)

Prefer `uv`. If it isn't installed, follow the official Astral
instructions at https://docs.astral.sh/uv/getting-started/installation/
(covers macOS, Linux, and Windows).

Then, from the repo root:

```
uv tool install ./src
```

If `uv` is unavailable, the bundled cross-platform installer detects
`uv` → `pipx` → `pip --user` automatically:

```
python3 ./src/scripts/install.py
```

After install, re-check with `convert-anything-md --version`.

### Step 3 — Resolve input paths

* If the user referenced files by absolute path, use them as-is.
* If they referenced relative paths, resolve against the current working
  directory of the agent session.
* If they used globs (`*.pdf`, `docs/*.pdf`), the CLI itself expands them
  — pass through verbatim (but still `ls` first to confirm you got what
  you expected).
* If they pointed at a directory and want everything inside converted,
  pass `--recursive`. The CLI walks the directory, skipping dotfiles,
  `.git`, `node_modules`, `.venv`, and other build dirs.
* If the user attached files through the harness and the files are on
  disk, pass those paths.
* Verify each path exists **before** invoking the CLI. Report any
  missing files to the user clearly.

### Step 4 — Run the CLI with `--json`

Always use `--json` when invoking from a skill. It gives you a stable,
machine-readable envelope to summarize back to the user. Example:

```
convert-anything-md --json "/path/to/report.pdf" "/path/to/minutes.docx"
```

The CLI writes output to the user's Desktop by default. If the user
specified a different destination, pass `-o /custom/dir`.

### Step 5 — Parse the JSON and report

The CLI emits a JSON object like:

```json
{
  "tool": "convert-anything-md",
  "version": "1.5.0",
  "output_dir": "/Users/example/Desktop",
  "summary": { "total": 2, "succeeded": 2, "failed": 0 },
  "results": [
    {
      "source": "/path/to/report.pdf",
      "kind": "pdf_text",
      "ok": true,
      "output": "/Users/example/Desktop/report.md",
      "engine": "anydoc",
      "pages": 42,
      "word_count": 9847,
      "duration_ms": 32,
      "warnings": [],
      "fallback_chain": ["anydoc"]
    }
  ]
}
```

Summarize concisely to the user:

* For each successful conversion, name the output path, the engine used,
  and the word count (and page count if present).
* If any file failed, quote the `error` field and suggest a remedy (e.g.
  "install Tesseract for OCR" when the error mentions `tesseract`).
* If fallbacks were used, mention it briefly ("anydoc unavailable — used
  docling instead") so the user knows why quality may vary.
* If a per-file warning mentions an engine override that didn't apply,
  pass it along — earlier versions silently swallowed that mismatch.

## Options you can pass to the CLI

| Flag | Meaning |
|------|---------|
| `-o <dir>` / `--output-dir <dir>` | Write .md files to `<dir>` instead of the desktop. |
| `-r` / `--recursive` | Walk every directory argument recursively. |
| `--no-frontmatter` | Omit the YAML provenance header. |
| `--overwrite` | Replace an existing .md instead of creating `name (1).md`. |
| `--dry-run` | Detect + extract but don't write output (useful for probing). |
| `--engine <name>` | Pin a specific extractor (advanced — only when the user asks). |
| `--list-engines` | Print every extractor name the `--engine` flag accepts. |
| `-q` / `--quiet` | Suppress the human-readable table (exit code still reflects success). |
| `-v` / `--verbose` | Include warnings + full fallback chain in the human output. |
| `--json` | **Always pass this from skill invocations.** |

## Examples

**User:** "Convert ~/Downloads/contract.pdf to markdown"

Agent runs:

```
convert-anything-md --json ~/Downloads/contract.pdf
```

Reports:

> Converted `contract.pdf` → `~/Desktop/contract.md` (anydoc, 18 pages, 4,120
> words, 32ms).

**User:** "Turn everything in this folder into .md files on my desktop"

Agent runs (after confirming the folder path):

```
convert-anything-md --json --recursive /path/to/folder
```

Reports:

> Converted 6 of 7 files:
>   • report.pdf → ~/Desktop/report.md (anydoc, 9 pages)
>   • memo.docx → ~/Desktop/memo.md (anydoc)
>   • ... (4 more)
>
> Failed: corrupted.xlsx — "openpyxl failed: invalid workbook header". You may
> want to open it in Excel first to re-save a clean copy.

**User:** "OCR this scan: /tmp/receipt.png"

Agent runs:

```
convert-anything-md --json /tmp/receipt.png
```

If Tesseract is missing, the CLI returns `ok: false` with a clear install
hint. Forward that hint to the user and offer to install Tesseract for them
(one command per OS).

## Safety & constraints

* The CLI writes files to disk. Confirm the destination with the user if
  the output directory isn't their desktop.
* The CLI never removes or modifies the source files — it only reads them.
* The CLI never sends file contents over the network.
* First-time Docling use downloads ML models (~1-2 GB) to the user's
  Hugging Face cache. Mention this if you see a long first-run delay.
* Every converted .md includes a YAML front-matter block with the source
  path, SHA-256, timestamp, and engine used. If the SHA-256 cannot be
  computed (file deleted mid-run, permission denied), the front-matter
  surfaces a `source_sha256_error` field instead of silently emitting an
  empty hash. Pass `--no-frontmatter` if the user wants a clean file.

## When NOT to use this skill

* The user asked for a summary, translation, or rewrite (LLM task, not a
  format conversion).
* The file is already Markdown and the user wants to edit it (use normal
  file-editing tools).
* The user wants to convert Markdown **out** to another format like PDF
  or DOCX (that's a different tool — reach for `pandoc` directly).
## Machine-readable failure handling

Pass `--json` and an explicit `-o` directory for automation. Parse stdout as
one JSON envelope, and read diagnostics from stderr separately. `summary.failed`
includes missing paths, unmatched globs, directories requiring `--recursive`,
and conversion/write failures. Exit status is 0 for all successes, 1 for mixed
results, and 2 when nothing succeeds. Inspect each result's `ok` and `error`;
a failed write has no successful `output` path. Repeated existing input files
are deduplicated, including overlapping globs.

`--dry-run` runs extraction; it is not an offline guarantee. Use text/CSV
fixtures for offline checks. Engine overrides retain the fallback chain.
