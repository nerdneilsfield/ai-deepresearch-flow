## Context
The backend API is a read-only JSON service backed by a snapshot SQLite DB. The UI will be a static SPA hosted separately, consuming API responses and static asset URLs.

## Goals / Non-Goals
- Goals:
  - Provide a minimal, fast UI for search, detail, and batch download.
  - Keep dependencies small and avoid server-side rendering.
  - Support English and Chinese strings from day one.
  - Performance target: Lighthouse performance score >= 90 on desktop.
- Non-Goals:
  - SSR/SSG.
  - User accounts, authentication, or write operations.
  - PWA/offline support (future consideration).

## Decisions
- Framework: Vue 3 + Vite + TypeScript for fast iteration and low maintenance.
- Styling: Tailwind CSS + shadcn-vue (Reka UI) for consistent components with code ownership.
- State: URL-first state persistence for search filters and pagination; Pinia for cross-page selection state.
- Data fetching: use TanStack Vue Query for request caching, retries, and cancellation.
- Error handling: user-facing toasts + inline error blocks; retry for transient failures.
- Markdown rendering: `markstream-vue` for summary/source/translated display, with normalization for math tokens and outline generation from DOM headings.
- Snippet highlight: replace `[[[` / `]]]` markers with `<mark>` at render time.
- Packaging: npm scripts for install/build (Deno is acceptable later, but shadcn-vue tooling is npm-first today).
- Detail layout: split view with independently selectable panes; panes scroll independently; default translated/summary when available.
- PDF rendering: use `@tuttarealstep/vue-pdf.js` and render from `pdf_url`.
- Component architecture: split large views into focused components and extract domain logic into composables.
- Outline + Top affordances: outline drawer on the right; top button as a floating icon in the bottom-right.
- Detail header layout: place the paper title/subtitle in the top nav on detail pages; keep view + split controls in a single row.
- Vue Query cache strategy: favor aggressive caching for snapshot data with tuned `staleTime`/`cacheTime` by endpoint type.
  - Search: `staleTime=30m`, `cacheTime=60m`
  - Paper detail: `staleTime=60m`, `cacheTime=120m`
  - Stats/Facets: `staleTime=10m`, `cacheTime=30m`
- Markdown enhancement priority: render math/tables/footnotes synchronously; defer Mermaid/Markmap work until visible.
- Split view constraints: disable split view on narrow screens (tabs only).
- Facet stats loading: load primary charts first and lazy-load related facet lists on demand.
- Legacy UI coexistence: keep `paper db serve` for local dev; new frontend targets snapshot API.

## Component Management Strategy
- All shadcn-vue components live in `frontend/src/components/ui/`.
- Local modifications MUST be annotated with `// CUSTOM:` comments.
- Prefer extension via props/slots; avoid rewriting component logic directly.
- Track component provenance in `frontend/components.json`.

## Performance Targets
- First Contentful Paint < 1.5s on 4G.
- Time to Interactive < 3s.

### Optimization Strategies
- Route-based code splitting.
- Dynamic imports for snippet rendering and `jszip`.
- Preconnect to API and static asset domains.

## Risks / Trade-offs
- shadcn-vue is community-maintained; components are copied locally to avoid upstream breakage.
- Client-side ZIP for large batches may be heavy; cap selection size and provide progress feedback.
- Markdown XSS: always sanitize rendered markdown and escape HTML; keep a strict allowlist for tags/attrs.
- PDF rendering can be heavy for large files; ensure worker configuration is correct to avoid blocking the UI thread.

## Migration Plan
- Phase 1: MVP pages (search, detail, download) wired to API.
- Phase 2: polish, i18n, and UX improvements.

## Open Questions
- Max batch download size limit (default 50?).
- Preferred theme density (compact vs spacious).
- Dark mode support (yes/no for MVP).
- Frontend deployment mode (standalone CDN vs bundled with API reverse proxy).
- Relationship with legacy `paper db serve` UI (coexist vs replace).
