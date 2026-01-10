## 1. Implementation
- [ ] 1.1 Update `paper db serve` to accept multiple `--input` values and pass the list through.
- [ ] 1.2 Enforce input JSON format `{template_tag, papers}` and infer missing template tags via schema key matching.
- [ ] 1.3 Implement merge logic using title similarity (>= 0.95), preferring BibTeX title and falling back to `paper_title`.
- [ ] 1.4 Store per-paper template variants and default template selection (prefer `simple`, else input order).
- [ ] 1.5 Add Summary template dropdown and render by selected template only; keep Source/PDF behavior unchanged.
- [ ] 1.6 Add optional cache directory support for merged inputs with a `--no-cache` override.
- [ ] 1.7 Update `README.md` with multi-input usage and JSON format notes.
- [ ] 1.8 Write Notion dev log entry (reverse timestamp order).
