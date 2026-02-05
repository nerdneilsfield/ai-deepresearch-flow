## Context
- Snapshot builds export content-hashed assets under the static export directory and store hashes in the snapshot DB.
- The current unpack flow is monolithic and does not align output names to PDF basenames or allow template-targeted info recovery.
- This change introduces explicit subcommands and a breaking CLI shape update.

## Goals / Non-Goals
- Goals:
  - Provide `paper db snapshot unpack md` and `paper db snapshot unpack info` subcommands.
  - Align output names to PDF basenames using `pdf_content_hash` matching from `--pdf-root`.
  - Output translations as `<base>.<lang>.md` in a separate directory.
  - Aggregate template summary JSONs into a single `paper_infos.json` output.
  - Emit Rich summary tables with success/failure counts.
- Non-Goals:
  - Do not rebuild the snapshot DB or rerun extraction.
  - Do not keep backward compatibility for the legacy unpack entry.
  - Do not change static asset formats or summary schema beyond aggregation.

## Decisions
- Build a PDF hash index by hashing PDFs under `--pdf-root` using the same SHA256 logic as snapshot build.
- Use PDF basename as the primary filename base; fallback to a sanitized `paper_title` when missing.
- Use `paper_id` as a collision suffix when `<base>.md` already exists.
- For `unpack info`, prefer `summary/<paper_id>/<template>.json`, fallback to `summary/<paper_id>.json`.
- Print Rich tables summarizing totals, successes, failures, and missing-PDF counts.

## Alternatives Considered
- Add new flags to the legacy command (rejected: ambiguous UX and breaking requirements).
- Use `paper_id` as the primary basename (rejected: output must align to PDF names with title fallback).
- Output translations as `.json` (rejected: requirement is `<base>.<lang>.md`).

## Risks / Trade-offs
- Hashing large PDF roots is slower but provides exact matching with `pdf_content_hash`.
- Filename collisions may still occur; suffixing with `paper_id` reduces collisions but can be noisy.
- Breaking change requires documentation updates and migration guidance.

## Migration Plan
- Replace the legacy unpack entry with subcommands.
- Update documentation with new commands and breaking change notice.
- Provide examples for md/info recovery workflows.

## Open Questions
- None.
