## 1. Custom templates
- [x] 1.1 Add CLI flags: `--schema-json`, `--prompt-system`, `--prompt-user`, `--markdown-template`, `--template-dir`.
- [x] 1.2 Implement custom prompt loader from explicit system/user files.
- [x] 1.3 Implement template directory loader (`system.j2`, `user.j2`, `schema.json`, `render.j2`).
- [x] 1.4 Allow custom schema with custom prompt and validate against JSON Schema.
- [x] 1.5 Allow render-md to use `--markdown-template`.

## 2. Multi-stage extraction
- [x] 2.1 Define stage maps for `deep_read`, `seven_questions`, `three_pass`.
- [x] 2.2 Implement per-stage extraction pipeline with stage-level retries.
- [x] 2.3 Add per-stage validation (JSON + required stage keys).
- [x] 2.4 Persist intermediate stage outputs per document in `paper_stage_outputs/`.
- [x] 2.5 Merge stage outputs into final JSON result.

## 3. Documentation
- [x] 3.1 Document custom template flags and stage-output behavior in README.
