## 1. Implementation
- [ ] 1.1 Add compare dataset builder that reuses `db_ops` matching logic for JSON, PDF, Markdown, and translated Markdown inputs.
- [ ] 1.2 Implement A/B input parsing and validation, including `--lang` enforcement for translated roots.
- [ ] 1.3 Build file indexes via `_build_file_index`/`_build_translated_index` and resolve JSON via `_resolve_source_md`/`_resolve_pdf`.
- [ ] 1.4 Emit rich summary tables and sample lists; export full CSV results to user-specified path.
- [ ] 1.5 Document usage and examples.
