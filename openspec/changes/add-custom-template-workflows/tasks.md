## 1. Custom templates
- [ ] 1.1 Add CLI flags: `--schema-json`, `--prompt-system`, `--prompt-user`, `--markdown-template`, `--template-dir`.
- [ ] 1.2 Implement custom prompt loader from explicit system/user files.
- [ ] 1.3 Implement template directory loader (`system.j2`, `user.j2`, `schema.json`, `render.j2`).
- [ ] 1.4 Allow custom schema with custom prompt and validate against JSON Schema.
- [ ] 1.5 Allow render-md to use `--markdown-template`.

## 2. Multi-stage extraction
- [ ] 2.1 Define stage maps for `deep_read`, `seven_questions`, `three_pass`.
- [ ] 2.2 Implement per-stage extraction pipeline with stage-level retries.
- [ ] 2.3 Persist intermediate stage outputs to a stage-output file.
- [ ] 2.4 Merge stage outputs into final JSON result.

## 3. Documentation
- [ ] 3.1 Document custom template flags and stage-output behavior in README.
