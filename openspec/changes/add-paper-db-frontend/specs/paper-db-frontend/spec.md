## ADDED Requirements

### Requirement: Frontend App Bootstrap
The system SHALL provide a Vue 3 SPA in `frontend/` built with Vite and TypeScript, and it SHALL be buildable to static assets.

#### Scenario: Build succeeds
- **WHEN** the user runs `npm run build` in `frontend/`
- **THEN** the app outputs static assets for deployment

### Requirement: API Configuration
The frontend SHALL accept an API base URL via environment configuration and default to `/api/v1` when none is provided.

#### Scenario: Default API base
- **WHEN** no explicit API base URL is configured
- **THEN** the app requests endpoints under `/api/v1`

### Requirement: Component Management
The frontend SHALL store shadcn-vue components under `frontend/src/components/ui/` and track component provenance in `frontend/components.json`.

#### Scenario: New component added
- **WHEN** a shadcn-vue component is added
- **THEN** it is placed under `frontend/src/components/ui/` and recorded in `components.json`

### Requirement: URL State Synchronization
The frontend SHALL synchronize search state (query, filters, page) to URL query parameters and restore state from URL on initial load.

#### Scenario: Shareable search URL
- **WHEN** a user performs a search with filters
- **THEN** the URL reflects the search state and can be shared or bookmarked
- **AND** loading that URL restores the same search results

### Requirement: Search Input Throttling
The frontend SHALL debounce search input with a 300ms delay and cancel in-flight search requests when new input arrives.

#### Scenario: Fast typing
- **WHEN** a user types quickly in the search box
- **THEN** API requests are debounced and only the latest request is executed

### Requirement: API Error Handling
The frontend SHALL handle API failures with user-friendly error messages and implement exponential backoff retry for transient failures.

#### Scenario: Network failure
- **WHEN** a request fails due to a network error
- **THEN** the UI shows a retry option and preserves user input

#### Scenario: API timeout
- **WHEN** a search request exceeds 10 seconds
- **THEN** the UI cancels the request and suggests refining the query

### Requirement: Query Cache Strategy
The frontend SHALL configure Vue Query caching with endpoint-specific `staleTime` and `cacheTime` values optimized for snapshot data:
- Search: `staleTime=30m`, `cacheTime=60m`
- Paper detail: `staleTime=60m`, `cacheTime=120m`
- Stats/Facets: `staleTime=10m`, `cacheTime=30m`

#### Scenario: Cached search results
- **WHEN** a user repeats the same search within the configured `staleTime`
- **THEN** the UI renders cached results without a network round-trip

### Requirement: Search + Snippet Highlight
The frontend SHALL query `/api/v1/search` and render snippets using the `[[[` `]]]` markers as visual highlights.

#### Scenario: Highlight markers rendered
- **WHEN** the API returns `snippet_markdown` containing `[[[` and `]]]`
- **THEN** the UI highlights the marked text in the search results

### Requirement: Search Result Summary Preview
The frontend SHALL show a summary preview (when available) in search results and allow the preview to be expanded or collapsed per item.

#### Scenario: Toggle summary preview
- **WHEN** a user toggles the summary preview control
- **THEN** the preview expands to show the full summary content or collapses to a truncated view

### Requirement: Facets and Stats
The frontend SHALL surface facet data from `/api/v1/facets/*` and overview data from `/api/v1/stats`.

#### Scenario: Facet filters applied
- **WHEN** a user selects a facet value (e.g., author or year)
- **THEN** the UI filters results by calling the corresponding facet endpoint

### Requirement: Large Dataset Rendering
The frontend SHALL use virtual scrolling for lists exceeding 50 items and paginate facet values when counts exceed 100.

#### Scenario: Large author list
- **WHEN** the author facet contains 5000+ entries
- **THEN** the UI shows a searchable paginated list, not a dropdown

### Requirement: Paper Detail with Multi-Summary
The frontend SHALL show paper details from `/api/v1/papers/{paper_id}` and expose all available summaries from `summary_urls`. When summary JSON includes metadata fields (title/authors/venue/institutions/abstract/keywords), the UI SHALL render those fields in the summary view.

#### Scenario: Multi-summary selection
- **WHEN** a paper provides multiple `summary_urls`
- **THEN** the UI allows switching between templates and renders the selected summary

#### Scenario: Summary metadata rendering
- **WHEN** a summary JSON contains metadata fields (title, authors, venue, institutions, abstract, keywords)
- **THEN** the summary view renders those fields above the summary content

### Requirement: Detail Split View and PDF Viewer
The frontend SHALL provide a detail split view with independently selectable left/right panes.
The default split layout SHALL use Translated (left) and Summary (right) when available.
The split panes SHALL scroll independently (no synchronized scrolling).
The frontend SHALL integrate a PDF viewer using a client-side library and the API-provided `pdf_url`.

#### Scenario: Split view switching
- **WHEN** a user opens the detail split view
- **THEN** each pane can be switched independently among Summary, Source, Translated, and PDF

#### Scenario: Default split layout
- **WHEN** translated markdown is available
- **THEN** the split view opens with Translated on the left and Summary on the right

#### Scenario: Independent scrolling
- **WHEN** a user scrolls the left pane
- **THEN** the right pane scroll position remains unchanged

### Requirement: Split View Constraints
The frontend SHALL disable split view on narrow screens and fall back to tabbed views.

#### Scenario: Split view on mobile
- **WHEN** the viewport width is below 1024px
- **THEN** the split view option is hidden and the user switches content via tabs

#### Scenario: PDF viewer
- **WHEN** a paper has a `pdf_url`
- **THEN** the detail view can render the PDF inline using the integrated viewer

### Requirement: Markdown Enhancements
The frontend SHALL render markdown with support for KaTeX math, Mermaid diagrams, Markmap mindmaps, footnotes, tables, and inline `sup`/`sub` tags.

#### Scenario: Math + diagram rendering
- **WHEN** markdown includes KaTeX/Math blocks and Mermaid code fences
- **THEN** the UI renders the math and diagrams correctly

#### Scenario: Markmap rendering
- **WHEN** markdown includes a markmap code fence
- **THEN** the UI renders an interactive mindmap

### Requirement: Markdown Render Budget
The frontend SHALL keep synchronous markdown rendering fast and defer heavy diagram rendering until visible.

#### Scenario: Deferred diagrams
- **WHEN** a page contains Mermaid or Markmap blocks below the fold
- **THEN** the UI defers their rendering until they enter the viewport

### Requirement: Outline and Top Controls
The frontend SHALL provide an outline drawer toggle for summary/markdown views and a floating "back to top" button.

#### Scenario: Outline toggle
- **WHEN** a user clicks the outline toggle
- **THEN** a right-side outline drawer opens or closes

#### Scenario: Back to top
- **WHEN** the content is scrolled down
- **THEN** a floating icon button appears that scrolls back to the top

### Requirement: Clickable Metadata Links
The frontend SHALL render metadata facets (authors, institutions, venues, keywords, tags, years, months, and summary templates) as clickable elements that navigate to the corresponding facet browse view.

#### Scenario: Author facet navigation
- **WHEN** a user clicks an author name in the detail view
- **THEN** the app navigates to the author facet listing for that author

#### Scenario: Venue facet navigation
- **WHEN** a user clicks a venue name in the detail view
- **THEN** the app navigates to the venue facet listing for that venue

### Requirement: Facet Stats Pages
The frontend SHALL provide stats views for authors, institutions, and venues using facet-scoped stats endpoints.
The stats views SHALL surface related metadata facets (coauthors, venues, institutions, keywords, tags, years, months, templates, output_language, provider, model, prompt_template, translation languages).

#### Scenario: Author stats view
- **WHEN** a user opens an author detail page
- **THEN** the UI displays year/month breakdowns scoped to that author

#### Scenario: Author knowledge graph links
- **WHEN** a user opens an author stats view
- **THEN** the UI displays related facet lists (coauthors, venues, institutions, keywords, tags) with navigation links

### Requirement: Facet Stats Loading Strategy
The frontend SHALL load primary stats immediately and lazy-load related facet lists, with pagination for large lists.

#### Scenario: Lazy facet lists
- **WHEN** a user opens a stats page
- **THEN** the year/month charts render first and related facet lists load on demand

### Requirement: Stats Tables
The stats views SHALL provide table-based summaries with expandable rows for detailed values.

#### Scenario: Expandable stats rows
- **WHEN** a user expands a stats row
- **THEN** the UI reveals a detailed list of items and counts

### Requirement: Batch Download
The frontend SHALL allow users to select multiple papers and create a ZIP based on `manifest_url` responses.

#### Scenario: Generate ZIP from manifests
- **WHEN** the user starts a batch download
- **THEN** the UI fetches each manifest and packages the referenced assets into a ZIP

#### Scenario: Download progress
- **WHEN** the batch download is in progress
- **THEN** the UI shows progress and estimated size

### Requirement: XSS Prevention
The frontend SHALL sanitize all rendered markdown content and escape HTML in user-generated content before rendering.

#### Scenario: Malicious snippet content
- **WHEN** `snippet_markdown` contains HTML script tags
- **THEN** the HTML is escaped and not executed

### Requirement: Error Boundaries
The frontend SHALL implement error boundaries to prevent rendering failures from crashing the app and to present a fallback UI.

#### Scenario: Render error fallback
- **WHEN** a component throws during render
- **THEN** the UI shows a fallback panel and logs the error

### Requirement: Internationalization
The frontend SHALL support English and Chinese UI strings via `vue-i18n`, detect browser language on first visit, and persist user preference in localStorage.

#### Scenario: Language persistence
- **WHEN** a user switches to Chinese
- **THEN** the preference is saved and applied on next visit
- **AND** the preference can be overridden via URL parameter `?lang=en`

### Requirement: Loading States
The frontend SHALL show loading indicators for data fetches and preserve user input during loading.

#### Scenario: Search loading
- **WHEN** a search request is in flight
- **THEN** the UI shows a loading indicator and keeps the current query visible

### Requirement: Network Status Awareness
The frontend SHALL detect offline/online state and display appropriate messaging when the network is unavailable.

#### Scenario: Offline banner
- **WHEN** the browser goes offline
- **THEN** the UI displays a non-blocking offline notice

### Requirement: Mobile Responsiveness
The frontend SHALL provide a responsive layout that remains usable on mobile screen sizes.

#### Scenario: Mobile search
- **WHEN** the viewport width is below 768px
- **THEN** search filters and results remain accessible without horizontal scrolling

### Requirement: Accessibility
The frontend SHALL support keyboard navigation, provide ARIA labels for interactive elements, and target WCAG 2.1 AA compliance.

#### Scenario: Keyboard navigation
- **WHEN** a user navigates with the keyboard
- **THEN** all interactive elements are reachable and usable

### Requirement: Testing
The frontend SHALL include unit tests for utilities/composables, component tests for critical UI components, and E2E tests for core flows.

#### Scenario: Search flow test
- **WHEN** a user types a query and presses enter
- **THEN** the search API is called with correct parameters
- **AND** results are displayed
