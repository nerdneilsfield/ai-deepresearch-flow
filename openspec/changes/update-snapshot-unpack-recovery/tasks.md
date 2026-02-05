## 1. Implementation
- [ ] 1.1 Replace legacy `paper db snapshot unpack` entry with a subcommand group (`md`, `info`).
- [ ] 1.2 Refactor snapshot unpacker into `unpack_md()` and `unpack_info()` with shared DB access.
- [ ] 1.3 Implement PDF hash indexing and output naming (PDF basename, title fallback, collision suffix).
- [ ] 1.4 Implement aggregated `paper_infos.json` recovery for a selected template with fallback behavior.
- [ ] 1.5 Add Rich summary tables for md/info commands (success/failure/missing PDF).
- [ ] 1.6 Update `README.md` and `README_ZH.md` with new commands and breaking change note.
