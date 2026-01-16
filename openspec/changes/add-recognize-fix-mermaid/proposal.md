# Change: add recognize fix-mermaid command

## Why
Mermaid blocks in OCR/LLM outputs often contain syntax drift that breaks rendering. A dedicated fix command should validate Mermaid diagrams with mermaid-cli and repair them via the configured LLM provider while preserving file structure.

## What Changes
- Add `recognize fix-mermaid` to scan markdown and JSON outputs for Mermaid code blocks and repair invalid diagrams.
- Validate Mermaid via `mmdc` (mermaid-cli) and only invoke the LLM for diagrams that fail validation.
- Write error reports and surface per-file/per-diagram progress similar to `fix-math`.
- Use a temporary working directory under `/tmp/mermaid` for mmdc input/output files.

## Impact
- Affected spec: recognize-fix.
- Affected code: `python/deepresearch_flow/recognize/cli.py`, new helpers under `python/deepresearch_flow/recognize/`.
- Dependencies: requires `mermaid-cli` (`mmdc`) available on PATH.
