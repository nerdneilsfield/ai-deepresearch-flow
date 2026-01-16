## Context
The project already includes OCR fixes for markdown, but math formulas in `$...$`/`$$...$$` can still be malformed and break rendering. We need a targeted fixer that validates formulas without rendering and optionally repairs them with an LLM.

## Goals / Non-Goals
- Goals:
  - Detect broken formulas in markdown and JSON outputs.
  - Validate formulas via `pylatexenc` and KaTeX (Node helper when available).
  - Repair formulas via provider/model and emit a report with file/line context.
- Non-Goals:
  - Supporting additional math delimiters beyond `$...$` and `$$...$$`.
  - Rendering output or changing db serve rendering logic.

## Decisions
- Decision: Add a new `recognize fix-math` command.
  - Reason: separation from general OCR fixes and explicit opt-in.
- Decision: Validation pipeline = regex cleanup → `pylatexenc` parse → KaTeX validation via Node helper (optional).
  - Reason: quick cleanup catches common issues; pylatexenc finds structural errors; KaTeX enforces strict syntax when available.
- Decision: KaTeX validation is optional and gracefully skipped when Node is unavailable.
  - Reason: avoid requiring Node.js in minimal environments.
- Decision: Use existing provider config (`--model provider/model`) with retry/timeout controls.
  - Reason: consistent with other LLM-backed commands.
- Decision: Batch LLM repairs (default 10 formulas) with per-formula context windows.
  - Reason: reduce token overhead and improve repair quality.
- Decision: Preserve JSON structure and only update fields containing formulas.

## Risks / Trade-offs
- Risk: KaTeX is stricter than LaTeX; some valid LaTeX might be flagged.
  - Mitigation: keep both pylatexenc and KaTeX errors in report; only replace when model returns valid output.
- Risk: Line number mapping for JSON values may be approximate.
  - Mitigation: compute line numbers from raw JSON text and record item index + field name/path alongside the line number.

## Migration Plan
- Add dependencies and command without changing existing `recognize fix` behavior.
- Validate on sample markdown and JSON outputs.
