## 1. Implementation
- [x] 1.1 Add `recognize fix-mermaid` CLI with shared flags (`--input`, `--in-place`, `--output`, `--recursive`, `--model`, `--max-retries`, `--timeout`, `--dry-run`).
- [x] 1.2 Implement Mermaid block extraction for markdown and JSON inputs with file/line metadata.
- [x] 1.3 Add validation pipeline using `mmdc` and `/tmp/mermaid` working directory.
- [x] 1.4 Implement batched LLM repair (default 10 diagrams) with per-diagram context windows and strict JSON output.
- [x] 1.5 Write outputs preserving JSON structure and emit an errors report file with file + line numbers (plus item/field path for JSON).
- [x] 1.6 Add validation-only mode (`--only-show-error`) to count invalid diagrams without repairs.
- [x] 1.7 Auto-detect JSON inputs when `--json` is omitted.
- [x] 1.8 Add per-file and per-diagram progress reporting.

## 2. Validation
- [ ] 2.1 Run `recognize fix-mermaid` on sample markdown and JSON to confirm repairs and error reporting.
