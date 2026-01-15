## 1. Implementation
- [x] 1.1 Add JSON mode flag to `recognize fix` CLI and validation for `--in-place`/`--output`.
- [x] 1.2 Implement JSON loader/writer that preserves `{papers: [...]}` vs list layout.
- [x] 1.3 Derive markdown-fix field list per template (simple/deep_read/eight_questions/three_pass) and apply `fix_markdown_text` on those fields.
- [x] 1.4 Apply rumdl formatting to fixed JSON fields unless `--no-format` is set.
- [x] 1.5 Add summary reporting for JSON mode (items processed, fields updated, output target).

## 2. Validation
- [x] 2.1 Run `recognize fix --json` against sample JSON to confirm fields are fixed and JSON structure is preserved.
