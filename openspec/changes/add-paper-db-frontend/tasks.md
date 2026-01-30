## 1. Foundation
- [x] 1.1 Scaffold `frontend/` with Vite + Vue 3 + TypeScript
- [ ] 1.2 Configure ESLint + Prettier
- [x] 1.3 Add Tailwind CSS and shadcn-vue setup
- [x] 1.4 Establish component layout under `frontend/src/components/ui/`

## 2. Core Infrastructure
- [x] 2.1 Add routing with URL state sync (Search, Paper Detail, Batch Download)
- [x] 2.2 Add API client with error handling, retries, and timeouts
- [x] 2.3 Add i18n infrastructure (en/zh) with persistence
- [x] 2.4 Add global loading/error UI patterns
- [x] 2.5 Adopt Vue Query for data fetching (search, detail, stats, facets)
- [x] 2.6 Define shared domain types + runtime validation (e.g., Zod)
- [x] 2.7 Extract composables (search/detail/facet/split state)
- [x] 2.8 Define Vue Query cache policy per endpoint type

## 3. Features
- [x] 3.1 Build search UI with facets, pagination, and snippet highlight
- [x] 3.2 Build detail UI with summary tabs and asset links
- [x] 3.3 Build batch download flow with progress and size estimates
- [ ] 3.4 Refactor SearchView and PaperDetailView into modular components
- [x] 3.4a Add tests for refactored composables and critical UI (smoke)
- [x] 3.5 Add detail split view with independent panes and default translated/summary layout
- [x] 3.6 Integrate `@tuttarealstep/vue-pdf.js` viewer for `pdf_url`
- [x] 3.7 Make metadata chips (author/institution/venue/keywords/tags/year/month/template) navigable to facet views
- [x] 3.8 Add facet stats views for authors, institutions, and venues with related-metadata lists
- [x] 3.9 Unify markdown pipeline via `markstream-vue` (math/diagrams/footnotes/tables) with caching
- [x] 3.10 Add outline drawer + floating top button for summary/markdown views
- [x] 3.11 Render summary metadata fields from summary JSON
- [x] 3.12 Add search result summary preview expand/collapse
- [x] 3.13 Convert stats summaries to expandable tables (single-column layout)
- [x] 3.14 Defer heavy markdown render (Mermaid/Markmap) until visible
- [x] 3.15 Add split-view mobile fallback (tabs-only)
- [x] 3.16 Add facet stats lazy-load + pagination
- [x] 3.17 Add error boundary wrapper(s) with fallback UI
- [x] 3.18 Add offline/online status indicator

## 4. Polish
- [x] 4.1 Mobile responsiveness pass
- [x] 4.2 Accessibility checks and ARIA labels
- [x] 4.3 Performance optimization (code split + lazy imports)
- [ ] 4.4 Add tests (unit/component/E2E)
- [ ] 4.5 Add README for local dev + build/deploy
