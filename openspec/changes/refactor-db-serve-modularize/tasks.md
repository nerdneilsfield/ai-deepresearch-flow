## 1. Implementation
### Phase 1: Data Layer
- [ ] 1.1 Rename `paper/db_match.py` to `paper/db_ops.py` and update imports.
- [ ] 1.2.1 Move `scan_pdf_roots`, `PaperIndex` to `paper/db_ops.py`.
- [ ] 1.2.2 Move title normalization + similarity helpers (`_normalize_title_key`, `_title_similarity`, etc.) to `paper/db_ops.py`.
- [ ] 1.2.3 Move load/merge/cache helpers (`_load_paper_inputs`, `_infer_template_tag`, `_load_or_merge_papers`, cache helpers) to `paper/db_ops.py`.
- [ ] 1.2.4 Move PDF resolution helpers (`_resolve_pdf`, `_read_pdf_metadata_title`) to `paper/db_ops.py`.
- [ ] 1.3 Update `paper/web/app.py` to use `db_ops` for loading and indexing.

### Phase 2: Web Modules
- [ ] 1.4 Extract constants into `paper/web/constants.py`.
- [ ] 1.5 Extract markdown rendering helpers into `paper/web/markdown.py`.
- [ ] 1.6 Extract HTML shell/template helpers into `paper/web/templates.py`.
- [ ] 1.7 Extract filter/query/stat helpers into `paper/web/filters.py`.
- [ ] 1.8 Split route handlers into `paper/web/handlers/*` and keep `create_app` thin.

### Phase 3: Validation
- [ ] 1.9 Verify `paper db serve` behavior remains consistent (JSON diff + counts).
