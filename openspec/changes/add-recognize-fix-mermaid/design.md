## Context
Mermaid diagrams are commonly embedded in markdown and JSON outputs. OCR and LLM transformations can introduce syntax errors that prevent rendering. We need a command that validates Mermaid blocks using mermaid-cli and repairs invalid diagrams via an LLM while preserving document structure.

## Goals / Non-Goals
- Goals:
  - Detect Mermaid diagrams in markdown and JSON outputs.
  - Validate Mermaid diagrams using mermaid-cli (`mmdc`).
  - Repair invalid diagrams via provider/model and emit a report with file/line context.
  - Use `/tmp/mermaid` as the working directory for validation artifacts.
- Non-Goals:
  - Supporting diagram languages other than Mermaid.
  - Rendering outputs or changing db serve rendering logic.

## Decisions
- Decision: Add a new `recognize fix-mermaid` command mirroring `fix-math` flags and behavior.
  - Reason: consistent UX and shared workflow for validation + repair.
- Decision: Validation pipeline = extract Mermaid code blocks → run `mmdc` → parse stderr for errors.
  - Reason: `mmdc` is the authoritative parser and indicates syntax failures via exit code.
- Decision: Only invoke the LLM for diagrams that fail `mmdc` validation.
  - Reason: avoids unnecessary LLM calls for valid diagrams.
- Decision: Use `/tmp/mermaid` (per-process subdir) for validation inputs/outputs.
  - Reason: isolates transient files and keeps working directories clean.

## Risks / Trade-offs
- Risk: `mmdc` is a Node dependency and may not be installed.
  - Mitigation: fail fast with a clear error message instructing how to install mermaid-cli.
- Risk: Large repositories may contain many diagrams and slow validation.
  - Mitigation: batch LLM repairs and provide per-diagram progress in addition to file progress.

## Migration Plan
- Add command and helpers without changing existing recognize behaviors.
- Validate on sample markdown and JSON outputs.
