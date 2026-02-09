## ADDED Requirements

### Requirement: DOI in paper detail API
The paper detail API SHALL include persisted DOI metadata in the response payload.

#### Scenario: Paper detail returns DOI
- **WHEN** a client requests `/api/v1/papers/{paper_id}` for a paper with stored DOI
- **THEN** the response includes `doi` with the persisted canonical DOI value

#### Scenario: Legacy snapshot fallback for DOI
- **WHEN** the API serves a snapshot DB built before this change where `paper.doi` is unavailable
- **THEN** paper detail responses still include the `doi` key with `null` value

### Requirement: Paper BibTeX endpoint
The API SHALL provide a BibTeX detail endpoint at `GET /api/v1/papers/{paper_id}/bibtex`.
The endpoint SHALL return persisted BibTeX metadata for that paper.
The endpoint `doi` field SHALL be sourced from `paper.doi` (canonical persisted DOI) and SHALL NOT require reparsing `bibtex_raw`.

#### Scenario: Return BibTeX payload
- **WHEN** a client requests `/api/v1/papers/{paper_id}/bibtex` for a paper with stored BibTeX
- **THEN** the response includes `paper_id`, `doi`, `bibtex_raw`, `bibtex_key`, and `entry_type`

#### Scenario: Paper not found
- **WHEN** a client requests `/api/v1/papers/{paper_id}/bibtex` for a non-existing paper
- **THEN** the API returns `404` with `error = "paper_not_found"`

#### Scenario: BibTeX missing for existing paper
- **WHEN** a client requests `/api/v1/papers/{paper_id}/bibtex` for an existing paper that has no BibTeX row
- **THEN** the API returns `404` with `error = "bibtex_not_found"`
