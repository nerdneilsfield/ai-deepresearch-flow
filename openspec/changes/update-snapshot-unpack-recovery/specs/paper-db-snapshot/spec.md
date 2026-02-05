## ADDED Requirements
### Requirement: Snapshot unpack command shape
The system SHALL expose `paper db snapshot unpack` as a command group with `md` and `info` subcommands.
The legacy options-only form SHALL NOT be supported.

#### Scenario: Unpack requires subcommand
- **WHEN** the user runs `paper db snapshot unpack` without a subcommand
- **THEN** the CLI returns a usage error listing the available subcommands

### Requirement: Snapshot unpack md recovery
The system SHALL provide `paper db snapshot unpack md` to recover source and translated Markdown from a snapshot DB and static export.
The command SHALL accept `--snapshot-db`, `--static-export-dir`, and one or more `--pdf-root` inputs.
The command SHALL write source Markdown to `--md-output-dir` and translated Markdown to `--md-translated-output-dir`.

#### Scenario: Align output names to PDF base
- **WHEN** a paper has `pdf_content_hash` and a matching PDF under `--pdf-root`
- **THEN** the output filenames use the PDF basename as the base (e.g., `<base>.md` and `<base>.<lang>.md`)

#### Scenario: Fallback to paper title when PDF missing
- **WHEN** no matching PDF is found for a paper
- **THEN** the output base name is derived from a sanitized `paper_title`

#### Scenario: Translation filename format
- **WHEN** a translated Markdown exists for language `zh`
- **THEN** the output is named `<base>.zh.md`

#### Scenario: Summary table for md
- **WHEN** the md recovery finishes
- **THEN** the command prints a Rich table summarizing total, succeeded, failed, and missing-PDF counts

### Requirement: Snapshot unpack info recovery
The system SHALL provide `paper db snapshot unpack info` to recover an aggregated `paper_infos.json` from a snapshot DB and static export.
The command SHALL require `--template` to select the summary template.
The command SHALL write a single JSON file to `--output-json`.

#### Scenario: Prefer template summary
- **WHEN** `summary/<paper_id>/<template>.json` exists
- **THEN** the aggregated output uses that template summary

#### Scenario: Template fallback
- **WHEN** the template summary is missing
- **THEN** the output falls back to `summary/<paper_id>.json` and records a failure count

#### Scenario: Summary table for info
- **WHEN** info recovery finishes
- **THEN** the command prints a Rich table summarizing total, succeeded, failed, and missing-PDF counts
