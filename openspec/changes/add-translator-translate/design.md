## Context
The repository already supports OCR workflows (via `deepresearch-flow recognize`) and has a configurable provider system (`config.toml` with `[[providers]]`).
However, there is no built-in translation workflow for OCR-produced Markdown.

Separate sibling projects have demonstrated that OCR Markdown translation needs:
- placeholder masking for non-translatable spans (images, formulas, code, HTML, URLs),
- strict validation to detect model formatting drift, and
- targeted retries on failures (node-based retranslation).

## Goals / Non-Goals
- Goals:
  - Provide a first-class `deepresearch-flow translator translate` command.
  - Reuse the existing provider + config system (no new provider config format).
  - Preserve Markdown structure and non-translatable content using placeholders.
  - Provide OCR-specific repair options (`--fix-level`) to improve translation stability.
  - Produce deterministic translated filenames using target-language suffixes.
  - Make failures diagnosable via optional debug dumps.
- Non-Goals:
  - Translating table cell content (tables remain frozen as a whole).
  - Translating image alt text by default.
  - Adding a GUI or web-based translation UI.

## Decisions
- CLI:
  - Add a new command group: `deepresearch-flow translator`.
  - Add `translate` subcommand that accepts file(s)/dir(s) + `--glob`.
- Output naming:
  - Write translated files with a language suffix before `.md` (e.g., `paper.md` → `paper.zh.md`, `paper.ja.md`).
  - Language suffix is derived from `--target-lang` using a small normalization map (`zh* → zh`, `ja/jp → ja`).
- Provider reuse:
  - Use `config.toml` `[[providers]]` and `--model provider/model` selection.
  - Force translation calls to use unstructured output (`structured_mode="none"`) regardless of provider config.
  - Add optional `max_tokens` to provider config; used for Claude translation calls.
- Prompts:
  - Use an MT-style prompt ("professional translation engine") that requests translation-only output and forbids explanations/notes.
  - Include strict invariants for placeholders and Markdown formatting; preserve formulas/references/code/URLs by relying on the placeholder protector.
- Translation pipeline:
  - Optional OCR repair step before translation (`--fix-level`).
  - Protect Markdown using placeholders:
    - block-level freezing (code fences, block math, HTML blocks, tables, footnote defs, indented code)
    - inline freezing (images, links/URLs, inline code, inline math, footnote refs, inline HTML)
  - Split protected markdown into nodes for translation and group nodes into provider-sized chunks.
  - Wrap nodes using explicit markers (`<NODE_START_0001>...</NODE_END_0001>`) and require the model to output only these blocks.
  - Validate output by:
    - marker pairing and id matching
    - placeholder multiset equality between source and translated node
  - Retry only failed nodes with smaller chunk sizes; final fallback is to keep the original node.

## Risks / Trade-offs
- Some providers/models may still drift formatting under heavy constraints.
  - Mitigation: strict placeholder/marker validation, smaller retry chunks, and safe fallback to original nodes.
- Over-aggressive OCR fixes can alter document structure.
  - Mitigation: `--fix-level` is explicit and defaults to a conservative mode; repairs are targeted and reversible where possible.

## Open Questions
- Whether to support optional translation of link anchor text and/or image alt text (default remains frozen for stability).
