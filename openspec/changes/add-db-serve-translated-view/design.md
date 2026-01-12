## Context
The db serve UI currently supports Summary, Source, and PDF views. Users are now generating translated markdown files using the translator workflow, following a language suffix naming convention (e.g., `paper.zh.md`, `paper.ja.md`).

## Goals / Non-Goals
- Goals:
  - Provide a Translated view next to Summary/Source/PDF.
- Detect translated markdown files based on the source markdown filename and language suffixes under `--md-translated-root`.
  - Use Source-style rendering (same Markdown rules, outline, back-to-top control).
  - Allow language switching via dropdown.
- Non-Goals:
  - Automatic machine translation within db serve.
  - Changing the input JSON schema or data format.

## Decisions
- Translation discovery:
  - Use the source markdown filename (without `.md`) as the base.
  - Detect files named `<base>.<lang>.md` (e.g., `paper.zh.md`, `paper.ja.md`) under `--md-translated-root`.
  - Derive language keys from the suffix between the base name and `.md`.
- Default language:
  - Prefer `zh` if present; otherwise choose the first available language in sorted order.
- Rendering:
  - Reuse the Source rendering pipeline and UI helpers (outline panel + back-to-top control).
  - Apply the same HTML sanitization and markdown-it options as Source.
- Empty state:
  - If no translations exist, show a friendly message and disable the dropdown.

## Risks / Trade-offs
- Translation files might be stored outside the source directory if users move files manually.
  - Mitigation: rely on the same `--md-root` path resolution used by Source; show empty-state if not found.

## Open Questions
- Whether to support a configurable translation-root directory separate from `--md-root` (not required for v1).
