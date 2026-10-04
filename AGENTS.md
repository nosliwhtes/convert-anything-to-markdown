# Working on this repository

The Python project lives in `src/`. Read `CONTRIBUTING.md` for extractor
interfaces and `tools/architecture.md` for request flow. Keep repairs focused,
reuse the standard library or existing dependencies, and preserve fallback
behavior and Python 3.10 compatibility.

## Noninteractive checks

From the repository root:

```sh
cd src
uv sync --locked --extra dev
uv run --no-sync ruff check .
uv run --no-sync pytest -ra -q
uv run --no-sync python -m build --installer uv
```

Tests use temporary files and mocks; do not download models or invoke live
providers to validate a change. There is currently no configured static type
checker. CI runs the checks on Linux and macOS with Python 3.10–3.12, then
publishes the required aggregate check named `CI`. Keep that name stable and
require every matrix job to succeed.

When changing dependencies, update `src/pyproject.toml` and regenerate
`src/uv.lock` together with `uv lock --upgrade`, then run the checks. Avoid
editing generated lock entries manually. Preserve user changes and use a
feature branch; do not merge or release without explicit authorization.

## Machine use

Use `cam --json -o OUTPUT -- INPUT...` and inspect both the exit status and
`results`. Exit 0 means all outcomes succeeded, 1 means mixed success/failure,
and 2 means none succeeded. Missing paths, unmatched globs, and directories
without `--recursive` are failed outcomes, even alongside successful inputs.
Existing files are deduplicated after expansion. JSON goes to stdout;
input diagnostics go to stderr. Failed writes have `ok: false`, `output: null`,
and a readable `error`. Do not infer success from an output filename alone.

`--dry-run` still performs extraction and can load/download models. For an
offline smoke check use a local text/CSV file; do not treat dry-run as a
network sandbox. `--engine` prioritizes an extractor and does not disable
fallback engines.
