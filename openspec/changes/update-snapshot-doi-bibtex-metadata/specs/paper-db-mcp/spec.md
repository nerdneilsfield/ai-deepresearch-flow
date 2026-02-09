## ADDED Requirements

### Requirement: MCP metadata DOI passthrough
The MCP `get_paper_metadata` tool SHALL return persisted DOI metadata from snapshot DB when available.

#### Scenario: DOI returned by MCP metadata
- **WHEN** a client calls `get_paper_metadata` for a paper with stored DOI
- **THEN** the response `doi` field equals the persisted canonical DOI value

#### Scenario: DOI absent remains null
- **WHEN** a client calls `get_paper_metadata` for a paper without stored DOI
- **THEN** the response `doi` field is null or empty by existing compatibility rules

#### Scenario: Legacy snapshot fallback for DOI/BibTeX metadata
- **WHEN** MCP serves a snapshot DB built before this change where `paper.doi` and `paper_bibtex` are unavailable
- **THEN** `get_paper_metadata` returns `doi = null` and `has_bibtex = false`

### Requirement: MCP BibTeX availability signal
The MCP `get_paper_metadata` tool SHALL expose whether BibTeX metadata exists for the paper.

#### Scenario: has_bibtex true
- **WHEN** a client calls `get_paper_metadata` for a paper with persisted `paper_bibtex` row
- **THEN** the response includes `has_bibtex = true`

#### Scenario: has_bibtex false
- **WHEN** a client calls `get_paper_metadata` for a paper without persisted `paper_bibtex` row
- **THEN** the response includes `has_bibtex = false`

### Requirement: MCP BibTeX retrieval tool
The MCP server SHALL provide `get_paper_bibtex(paper_id)` to return persisted BibTeX content for a paper.
The tool `doi` field SHALL be sourced from `paper.doi` and MAY be `null` when canonical DOI is unavailable.

#### Scenario: Return BibTeX via MCP tool
- **WHEN** a client calls `get_paper_bibtex` for a paper with persisted BibTeX
- **THEN** the tool returns `paper_id`, `doi`, `bibtex_raw`, `bibtex_key`, and `entry_type`

#### Scenario: Paper not found via MCP tool
- **WHEN** a client calls `get_paper_bibtex` with a non-existing `paper_id`
- **THEN** the tool returns `paper_not_found`

#### Scenario: BibTeX missing via MCP tool
- **WHEN** a client calls `get_paper_bibtex` for an existing paper without persisted BibTeX
- **THEN** the tool returns `bibtex_not_found`
