## ADDED Requirements
### Requirement: Paper DB data-layer module
The system SHALL provide `deepresearch_flow.paper.db_ops` as the canonical data-layer for loading, merging, and indexing paper databases without importing web framework modules.

#### Scenario: CLI uses data layer
- **WHEN** a CLI command loads paper JSON and scans roots
- **THEN** it can call `deepresearch_flow.paper.db_ops` without importing `deepresearch_flow.paper.web` or Starlette.

#### Scenario: Web uses data layer
- **WHEN** the web app initializes the db serve index
- **THEN** it uses `deepresearch_flow.paper.db_ops` helpers for load/merge/index.

#### Scenario: No circular imports
- **WHEN** `deepresearch_flow.paper.db_ops` is imported
- **THEN** it SHALL NOT import any module from `deepresearch_flow.paper.web`.

### Requirement: Web layer modularization
The system SHALL split db serve web implementation into focused modules under `deepresearch_flow.paper.web` while keeping `app.py` as a thin `create_app` entrypoint.

#### Scenario: App entrypoint
- **WHEN** `deepresearch_flow.paper.web.app.create_app` is imported
- **THEN** it wires routes and middleware and delegates rendering/handlers to module helpers.

### Requirement: Web constants module
The system SHALL centralize shared db serve web constants (CDN URLs and PDFJS paths) in `deepresearch_flow.paper.web.constants`.

#### Scenario: Shared constants
- **WHEN** web modules need CDN or PDFJS constants
- **THEN** they import them from `deepresearch_flow.paper.web.constants`.

### Requirement: Behavior parity for db serve
The refactor SHALL preserve db serve behavior for paper loading, PDF/markdown resolution, and search filtering.

#### Scenario: Index parity
- **WHEN** db serve runs with the same inputs before and after refactor
- **THEN** the resolved paper count and associations (pdf/md/translated) remain consistent.
