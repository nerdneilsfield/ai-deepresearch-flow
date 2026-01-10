## ADDED Requirements

### Requirement: Multi-input db serve format
The system SHALL accept one or more `--input` values for `paper db serve`, where each input JSON is an object with `template_tag` and `papers` fields.

#### Scenario: Start server with multiple inputs
- **WHEN** the user runs `paper db serve` with multiple `--input` files
- **THEN** the server loads each input as `{template_tag, papers}` and aggregates them

### Requirement: Input validation and template tag inference
The system SHALL reject legacy array-only input. When `template_tag` is missing, the system SHALL infer it by matching paper keys against known template schema keys while ignoring metadata fields (`source_path`, `source_hash`, `provider`, `model`, `extracted_at`, `truncation`, `output_language`, `prompt_template`), selecting the highest match with ties broken by preferring `simple` and then registry order.

#### Scenario: Missing template tag is inferred
- **WHEN** an input JSON omits `template_tag` but provides `papers`
- **THEN** the server infers the template tag from schema key matches

### Requirement: Title-based paper merging
The system SHALL merge papers across inputs when their titles are similar with a ratio of at least 0.95 after normalization (trim + lower). The system SHALL prefer `bibtex.fields.title` when present on both papers; otherwise it SHALL use `paper_title`. If a title is missing on either side, the papers SHALL NOT be merged.

#### Scenario: Merge by BibTeX title similarity
- **WHEN** two papers have BibTeX titles with similarity >= 0.95
- **THEN** the server merges them into a single paper with multiple template variants

#### Scenario: Merge falls back to paper_title
- **WHEN** two papers lack BibTeX titles but have `paper_title` similarity >= 0.95
- **THEN** the server merges them into a single paper with multiple template variants

### Requirement: Summary template selection
The system SHALL allow Summary rendering to switch among the templates available for that paper, showing only available templates in the selector. The default template SHALL be `simple` when available, otherwise the first available template by input order. Source and PDF views SHALL remain unchanged by Summary template selection.

#### Scenario: Summary template dropdown
- **WHEN** a paper has multiple templates available
- **THEN** Summary displays a dropdown listing only those templates and renders the selected template

### Requirement: db serve cache directory
The system SHALL accept an optional `--cache-dir` for `paper db serve` to reuse merged inputs across runs, and SHALL accept `--no-cache` to bypass cache reads and writes.

#### Scenario: Cache reused across runs
- **WHEN** the user runs `paper db serve` with `--cache-dir` and unchanged input files
- **THEN** the server reuses cached merged inputs instead of rebuilding them
