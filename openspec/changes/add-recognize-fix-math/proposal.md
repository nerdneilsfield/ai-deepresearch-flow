# Change: add recognize fix-math command

## Why
OCR and LLM outputs often contain malformed LaTeX in inline/block math, causing render failures downstream. A dedicated fix command should detect broken formulas, validate them without rendering, and repair them via the configured LLM provider.

## What Changes
- Add `recognize fix-math` to scan markdown and JSON outputs for `$...$` and `$$...$$` formulas.
- Use `pylatexenc` to parse formulas and surface syntax errors, apply a regex cleanup pass, then validate with KaTeX via a Node helper (optional, requires `node` + `katex`).
- Send failed formulas in batches (default 10) with error details and per-formula context to the configured provider/model for repair, and write an error report for any remaining failures.

## Impact
- Affected spec: recognize-fix.
- Affected code: `python/deepresearch_flow/recognize/cli.py`, new math-fix helpers under `python/deepresearch_flow/recognize/`, provider call reuse in `python/deepresearch_flow/paper/llm.py`.
- Dependencies: add `pylatexenc` (Python), KaTeX validation uses Node runtime when available.
