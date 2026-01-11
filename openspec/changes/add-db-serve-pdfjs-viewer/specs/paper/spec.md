## MODIFIED Requirements

### Requirement: Database web viewer
The system SHALL provide a `deepresearch-flow paper db serve` command to start a local, read-only web UI for a paper database JSON file.
The server SHALL load and index the database at startup for responsiveness.
The web UI SHALL provide unified search using a single query input.
The search input SHALL support field filters (title/author/tag/venue/year/month), quoted phrases, negation, and term-level OR.
The UI SHALL provide an Advanced Search helper to build queries.
The server SHALL optionally accept a BibTeX input to enrich metadata, and the merge SHALL be field-level with fallback to extracted values.
The server SHALL optionally accept one or more markdown roots to locate original markdown sources for papers.
The server SHALL optionally accept one or more PDF roots to locate PDFs and display them in-page via pdf.js.
The paper detail page SHALL expose both a built-in PDF.js canvas view and an embedded PDF.js viewer (iframe) when PDFs are available.
The paper detail page SHALL provide a dual-pane split view where users choose the left and right content independently (summary, source, PDF canvas, or PDF.js viewer).
In split view, each pane SHALL be rendered in an iframe to allow independent scrolling.
The server SHALL serve the PDF.js viewer static assets locally to avoid cross-origin issues when loading PDFs.
The web UI SHALL reflect split view state (left/right selections) in URL query parameters.
On narrow screens, the split view SHALL stack panes or allow toggling between panes instead of squeezing two columns.
The summary view SHALL provide a collapsible outline panel anchored at the top-left.
The summary view SHALL provide a bottom-left control to scroll back to the top of the page.

#### Scenario: Unified query search
- **WHEN** the user searches using a query like `tag:fpga year:2023..2025 -survey`
- **THEN** the UI filters papers accordingly

#### Scenario: BibTeX enrichment improves stats
- **WHEN** the user provides a BibTeX file
- **THEN** stats and per-paper metadata prefer BibTeX year/month/venue fields when available and fall back otherwise

#### Scenario: Source markdown view
- **WHEN** the user provides one or more markdown roots
- **THEN** the paper detail page can switch between rendered summary and the original markdown source when present

#### Scenario: PDF view
- **WHEN** the user provides one or more PDF roots
- **THEN** the paper detail page can display the matched PDF using pdf.js

#### Scenario: PDF.js viewer iframe
- **WHEN** the user switches to the PDF.js viewer mode
- **THEN** the paper detail page embeds the PDF.js viewer UI in an iframe for the matched PDF

#### Scenario: Dual-pane split view
- **WHEN** the user selects split view and chooses different left/right content
- **THEN** the page displays two independently scrollable iframe panes with the selected views

#### Scenario: Local PDF.js viewer assets
- **WHEN** the user opens the PDF.js viewer mode
- **THEN** the viewer assets are served from the local server and can load the local PDF API without CORS errors

#### Scenario: Split view deep link
- **WHEN** the user refreshes a split view URL with left/right parameters
- **THEN** the UI restores the same left/right selections

#### Scenario: Split view on small screens
- **WHEN** the viewport is narrow
- **THEN** the UI stacks panes or lets the user toggle between panes instead of rendering two cramped columns

#### Scenario: Summary outline panel
- **WHEN** the user views a paper summary
- **THEN** a collapsible outline panel is available at the top-left for navigating headings

#### Scenario: Summary back-to-top control
- **WHEN** the user is scrolled down in the summary view
- **THEN** a bottom-left control allows jumping back to the top
