## 1. Implementation
- [x] 1.1 Add `translator` command group and wire into `deepresearch-flow` CLI.
- [x] 1.2 Implement markdown input discovery (file/dir + `--glob`) and output naming with language suffixes (`.zh.md`, `.ja.md`).
- [x] 1.3 Add OCR markdown fixers (`--fix-level off|moderate|aggressive`) for:
  - references (`[1]`, `[1-5]`, `[1,2,3]`) → footnote style (`[^1] ...`)
  - bare links/emails/phones → autolink brackets (`<...>`)
  - algorithm/pseudocode blocks → fenced code wrapping
- [x] 1.4 Implement placeholder store and Markdown protector (block-level + inline-level freezing) with strict placeholder preservation rules.
- [x] 1.5 Implement node splitting, grouping, and reconstruction that preserves original separators/whitespace.
- [x] 1.6 Implement provider translation calls reusing `config.toml` providers and forcing unstructured output (`structured_mode="none"`).
- [x] 1.7 Implement validation + retries:
  - marker pairing (`NODE_START/END`) and ordering
  - placeholder multiset equality checks
  - retry failed nodes with smaller chunks; fallback to original node after max retries
- [x] 1.8 Add optional debug outputs: protected markdown, placeholder database, per-node JSON diagnostics.
- [x] 1.9 Extend `config.toml` provider schema with optional `max_tokens` (used by Claude); update `config.example.toml` and README.
- [x] 1.10 Document translator usage in `README.md` with examples for `.zh.md` / `.ja.md`.

## 2. Validation
- [x] 2.1 Run `openspec validate add-translator-translate --strict` and address any findings.
