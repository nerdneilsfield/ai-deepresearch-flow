# Change: Add Paper DB Frontend (Vue3 + Tailwind + shadcn-vue)

## Why
The snapshot API is ready for production use, but there is no first-party UI to browse, search, and download papers. A lightweight frontend is required to validate UX flows (search, facets, multi-summary, batch download) and enable standalone static hosting. Updated proposal sets performance goals for perceived latency (target <100ms UI response after API data is received) to keep interactions snappy on top of typical sub-200ms API responses. The initial implementation needs a deeper refactor to improve maintainability, performance, and UX parity with the legacy `db serve` UI.

## What Changes
- Add a new Vue 3 + Vite + TypeScript frontend under `frontend/`.
- Use Tailwind CSS and shadcn-vue components for the UI.
- Integrate API endpoints: `/api/v1/search`, `/api/v1/papers/{paper_id}`, `/api/v1/facets/*`, `/api/v1/stats`.
- Provide multi-language UI (en/zh) via `vue-i18n`.
- Implement batch download (manifest + static assets) with client-side ZIP.
- Add error handling, loading states, and mobile-first responsive layout.
- Refactor large views into composables + modular components for maintainability.
- Standardize markdown rendering (summary/source/translated) with `markstream-vue` and unified enhancement pipeline.
- Adopt a query client for data fetching (retry, cache, cancellation) to reduce manual state handling.
- Integrate a PDF viewer library (`@tuttarealstep/vue-pdf.js`) and ensure split panes scroll independently.

## Impact
- Affected specs: `paper-db-frontend` (new).
- Affected code: new `frontend/` directory, build assets, and docs.
- Affected operations: deployment pipeline will include a frontend build step and static hosting.
