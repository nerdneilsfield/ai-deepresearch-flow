## 1. Implementation
- [x] 1.1 Add `recognize fix-math` CLI with shared flags (`--input`, `--in-place`, `--output`, `--recursive`, `--model`, `--max-retries`, `--timeout`, `--dry-run`).
- [x] 1.2 Implement formula extraction for markdown and JSON inputs with file/line metadata.
- [x] 1.3 Add cleanup + validation pipeline (regex cleanup → pylatexenc parse → optional KaTeX validation via Node helper).
- [x] 1.4 Implement batched LLM repair (default 10 formulas) with per-formula context windows and strict JSON output format.
- [x] 1.5 Write outputs preserving JSON structure and emit an errors report file with file + line numbers (plus item/field path for JSON).
- [x] 1.6 Add validation-only mode (`--only-show-error`) to count invalid formulas without repairs.
- [x] 1.7 Auto-detect JSON inputs when `--json` is omitted.

## 2. Validation
- [ ] 2.1 Run `recognize fix-math` on sample markdown and JSON to confirm repairs and error reporting.
