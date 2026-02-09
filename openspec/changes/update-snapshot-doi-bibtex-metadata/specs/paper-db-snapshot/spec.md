## ADDED Requirements

### Requirement: DOI persistence in snapshot paper metadata
The snapshot database SHALL persist a nullable `doi` field for each paper row.
The persisted DOI value SHALL be canonicalized by the same logic as identity normalization (`canonicalize_doi`).

#### Scenario: Persist DOI from input
- **WHEN** a paper input provides DOI metadata
- **THEN** snapshot build writes the canonical DOI into `paper.doi`

#### Scenario: Paper without DOI
- **WHEN** a paper input has no DOI
- **THEN** snapshot build stores `NULL` (or empty equivalent) in `paper.doi`

### Requirement: Dedicated BibTeX table
The snapshot database SHALL persist BibTeX metadata in a dedicated per-paper table.
The table SHALL store at least deterministic BibTeX entry text and optional key/type metadata.
`entry_type` SHALL use lowercased pybtex entry type values.

#### Scenario: Persist BibTeX raw entry
- **WHEN** a paper input has matched BibTeX metadata
- **THEN** snapshot build writes one row in `paper_bibtex` for that `paper_id` including `bibtex_raw`

#### Scenario: Paper without BibTeX
- **WHEN** a paper input has no matched BibTeX metadata
- **THEN** snapshot build writes no `paper_bibtex` row for that `paper_id`

### Requirement: BibTeX text source for persistence
The snapshot pipeline SHALL retain deterministic per-entry BibTeX text during parse/enrichment so that `bibtex_raw` can be persisted in snapshot DB.
The persisted text MAY be normalized by parser/writer and is not required to be byte-identical to the original `.bib` source segment.

#### Scenario: Parser-generated entry text is persisted
- **WHEN** BibTeX metadata is parsed for a matched paper
- **THEN** the pipeline persists deterministic entry text to `paper_bibtex.bibtex_raw`

### Requirement: Rebuild inheritance for DOI and BibTeX
When `--previous-snapshot-db` is provided, snapshot rebuild SHALL inherit DOI and BibTeX from previous snapshot rows with the same resolved `paper_id` when current inputs do not provide these values.
Current-input metadata SHALL take precedence over inherited values.

#### Scenario: Inherit missing metadata from previous snapshot
- **WHEN** current inputs produce a matched `paper_id` but omit DOI/BibTeX
- **AND** previous snapshot contains DOI/BibTeX for that `paper_id`
- **THEN** rebuild writes inherited DOI/BibTeX into the new snapshot

#### Scenario: Current metadata overrides inherited metadata
- **WHEN** current inputs provide DOI or BibTeX for a matched `paper_id`
- **THEN** rebuild uses current input values instead of previous snapshot values

#### Scenario: DOI and inherited BibTeX DOI may diverge
- **WHEN** current input provides DOI but does not provide BibTeX
- **AND** rebuild inherits BibTeX from previous snapshot
- **THEN** the system accepts possible DOI mismatch between `paper.doi` and inherited `bibtex_raw` and records a warning

### Requirement: DOI/BibTeX mismatch observability
When DOI and BibTeX-derived DOI are inconsistent during build/update, the system SHALL provide aggregated observability output for the run.

#### Scenario: Mismatch summary is aggregated
- **WHEN** multiple papers in a run have DOI/BibTeX mismatch
- **THEN** the run emits an aggregated mismatch summary (count and optional samples) instead of one warning log per paper

### Requirement: BibTeX key consistency with identity aliases
When `paper_bibtex.bibtex_key` exists and a bib identity alias is used, alias key formatting SHALL be consistent with `bib:{bibtex_key}` in `paper_key_alias.paper_key`.

#### Scenario: Bib alias consistency
- **WHEN** a paper has `paper_bibtex.bibtex_key = smith2024foo`
- **THEN** the bib identity alias key uses `bib:smith2024foo`

### Requirement: DOI searchable in snapshot full-text index
The snapshot full-text metadata corpus SHALL include canonical DOI text when available.

#### Scenario: Search by DOI text
- **WHEN** a client searches with DOI text such as `10.1145/xxxx`
- **THEN** the snapshot search index can match papers containing that DOI
