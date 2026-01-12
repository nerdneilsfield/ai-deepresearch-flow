# Change: Add translator CLI for OCR markdown translation

## Why
`deepresearch-flow recognize` can produce OCR-derived Markdown, but the project currently has no first-class way to translate that Markdown while preserving OCR-specific structure (images, formulas, links, tables, code blocks).

We want an official `deepresearch-flow translator translate` workflow that reuses the existing `config.toml` provider system and applies proven placeholder + restore techniques to keep the Markdown stable during machine translation.

## What Changes
- Add a new CLI command group `deepresearch-flow translator` with a `translate` subcommand.
- Reuse the existing provider configuration in `config.toml` (`[[providers]]`) and explicit routing via `--model provider/model`.
- Implement an OCR-safe Markdown translation pipeline:
  - Optional OCR repair (`--fix-level`) for references/links/pseudocode patterns before translation.
  - Markdown protection using placeholders for non-translatable blocks (tables, images, code, LaTeX, HTML, URLs, etc.).
  - Node-based chunking with strict validation + retries on failures (missing markers/placeholders).
  - Restore placeholders after translation to reconstruct the original Markdown structure.
- Output translated markdown using language suffix naming (e.g., `.zh.md`, `.ja.md`) for easy downstream serving/rendering.
- Extend provider config with optional `max_tokens` (used by Claude translation calls); default behavior remains unchanged when omitted.
- Add debug flags to dump protected markdown, placeholder map, and node diagnostics for troubleshooting.

## Impact
- Affected specs: `translator` (new)
- Affected code: new package modules under `python/deepresearch_flow/translator/`, CLI wiring in `python/deepresearch_flow/cli.py`
- Config compatibility: backward-compatible; `max_tokens` is optional.
