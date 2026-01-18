## ADDED Requirements

### Requirement: Static asset base configuration
The system SHALL support a production mode that serves PDFs, images, and Markdown from a configured static asset base URL outside the app domain.
The system SHALL support a development mode that serves assets locally when a static base URL is not configured.
The static asset base URL SHALL be configurable via environment variable or CLI flag (e.g. `PAPER_DB_STATIC_BASE_URL`).

#### Scenario: Production static base
- **WHEN** a static base URL is configured and production mode is enabled
- **THEN** the UI and API reference static URLs for PDFs, images, and Markdown

#### Scenario: Development fallback
- **WHEN** no static base URL is configured or dev mode is enabled
- **THEN** the UI and API reference local asset routes

### Requirement: Static path layout and content hashing
The system SHALL publish static assets using content-hash versioned URLs to support long-lived caching.
PDFs SHALL be available under `/pdf/<hash>.pdf`.
Markdown sources SHALL be available under `/md/<hash>.md`.
Translated Markdown SHALL be available under `/md_translate/<lang>/<hash>.md`.
Images extracted from Markdown SHALL be available under `/images/<hash>.<ext>` or `/images/<hash>/<filename>`.

#### Scenario: Content hash versioning
- **WHEN** a Markdown file changes
- **THEN** its URL changes because the content hash changes

#### Scenario: Translation path
- **WHEN** a translation exists for a paper in language `zh`
- **THEN** the API returns `/md_translate/zh/<hash>.md` for that paper

### Requirement: Shared image URLs across source and translation
The system SHALL reuse a single canonical image URL when original and translated Markdown reference identical image bytes.
Markdown SHALL reference images via relative URLs so the frontend can resolve them against the static images base URL.

#### Scenario: Shared image URL
- **WHEN** the same image appears in both source and translated Markdown
- **THEN** both Markdown files reference the same exported image URL

#### Scenario: Relative image resolution
- **WHEN** a Markdown image uses a relative path
- **THEN** the frontend resolves it under the configured images base URL

### Requirement: Asset URLs in API responses
The API SHALL return canonical asset URLs for each paper (pdf_url, md_url, md_translated_url per language, images_base_url).

#### Scenario: List API includes URLs
- **WHEN** a client requests `/api/papers`
- **THEN** each item includes URLs for available assets

### Requirement: Client-side Markdown rendering and sanitization
The web UI SHALL fetch raw Markdown and render it in the browser using Marked in both development and production modes.
Rendered HTML SHALL be sanitized with DOMPurify while allowing `sup` and table-related tags.
The Markdown renderer SHALL rewrite relative image URLs by prefixing them with the configured static base URL (prod) or local asset path (dev).

#### Scenario: Consistent rendering
- **WHEN** viewing a paper in development mode
- **THEN** it renders identically to production mode using client-side logic

#### Scenario: Image URL rewriting
- **WHEN** Markdown contains `![](../images/foo.png)`
- **THEN** the renderer rewrites the `src` to `https://static.example.com/images/foo.png` (prod) or `/static/images/foo.png` (dev)

### Requirement: Dev-mode raw content API
The system SHALL provide an API endpoint in development mode to serve raw Markdown content for a given paper hash.

#### Scenario: Fetch raw markdown
- **WHEN** frontend requests content for a paper in dev mode
- **THEN** the API returns the raw Markdown string from the local file system

### Requirement: PDF.js viewer CDN support
The web UI SHALL serve the PDF.js `viewer.html` from the application origin but load the PDF.js library assets (JS/CSS) from a configured CDN (default jsDelivr) with a local fallback.

#### Scenario: CDN library assets
- **WHEN** a PDF.js CDN base URL is configured
- **THEN** the local viewer.html loads script tags pointing to the CDN

### Requirement: Public, cacheable static assets
Static assets SHALL be publicly accessible without authentication.
Static assets served via content-hash URLs SHALL be cacheable with long-lived cache headers (e.g. `max-age=31536000, immutable`).

#### Scenario: Long-lived caching
- **WHEN** a client requests a hashed static asset
- **THEN** the response is cacheable for at least one year and treated as immutable
