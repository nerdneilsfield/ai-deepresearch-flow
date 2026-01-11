## 1. Implementation
- [ ] 1.1 Add recognize command group and wire into CLI entrypoint.
- [ ] 1.2 Refactor markdown discovery to support non-recursive search while preserving paper behavior.
- [ ] 1.3 Implement markdown input discovery and flattened output naming with collision suffixes.
- [ ] 1.4 Implement `recognize md embed` for local/data image embedding and optional HTTP fetching (timeout, user-agent, warn-and-skip on failures).
- [ ] 1.5 Implement `recognize md unpack` to extract data URLs into output images, using MIME-derived extensions.
- [ ] 1.6 Implement concurrency controls (`--workers`) for recognize md and organize commands.
- [ ] 1.7 Implement `recognize organize` with `mineru` layout, output-simple/output-base64, and image copy/rename.
- [ ] 1.8 Update README with recognize command usage and examples.
