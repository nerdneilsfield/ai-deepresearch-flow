## 1. Implementation
- [x] 1.1 Add recognize command group and wire into CLI entrypoint.
- [x] 1.2 Refactor markdown discovery to support non-recursive search while preserving paper behavior.
- [x] 1.3 Implement markdown input discovery and flattened output naming with collision suffixes.
- [x] 1.4 Implement `recognize md embed` for local/data image embedding and optional HTTP fetching (timeout, user-agent, warn-and-skip on failures).
- [x] 1.5 Implement `recognize md unpack` to extract data URLs into output images, using MIME-derived extensions.
- [x] 1.6 Implement concurrency controls (`--workers`) for recognize md and organize commands.
- [x] 1.7 Implement `recognize organize` with `mineru` layout, output-simple/output-base64, and image copy/rename.
- [x] 1.8 Update README with recognize command usage and examples.
