## 1. Implementation
- [x] 1.1 Add static-asset configuration (mode + base URL) and pass into templates/API.
- [x] 1.2 Export Markdown and images into the static layout with content-hash naming (organize or app-startup).
- [x] 1.3 Add dev-only API endpoint to serve raw Markdown content locally.
- [x] 1.4 Update API payloads to include asset URLs and update frontend to fetch raw Markdown.
- [x] 1.5 Implement client-side Markdown rendering (Marked + DOMPurify) with image URL rewriting and allowed tags (`sup`, table).
- [x] 1.6 Update PDF.js viewer template to load library assets from CDN/local based on config (keep viewer.html local).
- [x] 1.6 Add tests for URL generation and sanitizer allowlist (if tests are present).
