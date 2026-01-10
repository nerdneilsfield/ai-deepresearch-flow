## ADDED Requirements

### Requirement: Database web viewer
The system SHALL provide a `deepresearch-flow paper db serve` command to start a local, read-only web UI for a paper database JSON file.
The server SHALL load and index the database at startup for responsiveness.

#### Scenario: Serve command starts server
- **WHEN** the user runs `deepresearch-flow paper db serve --input paper_infos.json`
- **THEN** the command starts a local HTTP server that serves a web UI

### Requirement: Web UI filtering and infinite scroll
The web UI SHALL provide filtering by title query, tags, authors, year/month, and venue.
The paper list UI SHALL support infinite scrolling using paginated API requests.

#### Scenario: Filter list by tag
- **WHEN** the user selects a tag filter in the UI
- **THEN** the list shows only matching papers

### Requirement: Template-based rendering
The detail view SHALL render paper content from JSON using a render template.
The default template selection SHALL use the paper's `prompt_template` when present.
The rendered output SHALL support Mermaid diagrams and KaTeX math.
If the selected template cannot be resolved, the renderer SHALL fall back to the built-in default template and display a warning in the UI.
The HTML output SHALL be rendered safely by default by disabling raw HTML in Markdown and/or sanitizing the HTML.

#### Scenario: Detail view uses per-paper template
- **WHEN** a paper record has `prompt_template: eight_questions`
- **THEN** the detail view uses the matching render template

#### Scenario: Missing template falls back
- **WHEN** a paper record has a missing or unknown template name
- **THEN** the detail view renders using the built-in default template and indicates the fallback

### Requirement: Statistics dashboard
The web UI SHALL provide a stats page that visualizes the database using charts.

#### Scenario: Stats page renders charts
- **WHEN** the user opens `/stats`
- **THEN** the page displays charts for year/month and top tags/authors/venues
