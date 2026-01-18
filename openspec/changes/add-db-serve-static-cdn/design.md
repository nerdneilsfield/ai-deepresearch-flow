## Context
The web UI needs to be CDN-friendly on Cloudflare. Static assets (PDF, images, Markdown) are public and mostly immutable. We want a production static domain (e.g. `static.site.com`) and a dev mode that can run locally.

## Goals / Non-Goals
- Goals:
  - Serve PDFs, images, and Markdown from a configurable static base URL.
  - Use content-hash versioned URLs to enable long cache lifetimes.
  - Render Markdown on the client using Marked + DOMPurify with an allowlist that includes `sup` and table tags.
  - Ensure original and translated Markdown reuse the same image URLs when image bytes are identical.
  - Support both organize-time and app-startup asset generation.
- Non-Goals:
  - Add authentication for PDFs or images.
  - Introduce server-side Markdown rendering in production mode.
  - Provide PDF transformations or thumbnails.

## Decisions
- Provide a static asset base URL via configuration (e.g. `PAPER_DB_STATIC_URL`).
- Development mode always uses local asset routes and ignores the static base URL.
- Define production path layout:
  - `/pdf/<hash>.pdf`
  - `/images/<hash>.<ext>`
  - `/md/<hash>.md`
  - `/md_translate/<lang>/<hash>.md`
- Use content-hash naming for PDFs, Markdown, and images to ensure URL versioning.
- Extract base64 images from Markdown and emit image files; Markdown references use relative image URLs.
- Frontend resolves relative image URLs against `images_base_url` derived from the static base.
- API returns canonical asset URLs (pdf_url, md_url, md_translated_url per language, images_base_url).
- Render Markdown in the browser with Marked + DOMPurify in **both** dev and production modes to ensure rendering consistency.
- Keep `viewer.html` served by the app (local origin) to avoid cross-origin iframe issues, but load PDF.js library assets from CDN (or local fallback).
- Frontend Markdown renderer MUST implement an image hook to prefix relative image paths with the appropriate base URL (static CDN or local API).
- Production static server MUST serve CORS headers (`Access-Control-Allow-Origin: *`) to allow frontend XHR/fetch of Markdown and PDFs.
- Add a dev-only API endpoint (`/api/dev/markdown/{source_hash}`) to serve raw Markdown content locally.
- Use long cache headers for hashed assets (`Cache-Control: public, max-age=31536000, immutable`).


## Risks / Trade-offs
- Client-side rendering increases JS work; mitigate with caching and lazy rendering.
- Incomplete asset export can break image links; mitigate by generating at organize or startup and validating references.
- CDN cache invalidation depends on correct hashing; hash mismatches can cause stale assets.

## Migration Plan
- Add optional export step in organize pipeline or on app startup.
- Update deployment to mount static directories into Nginx and serve under the static domain.
- Set `PAPER_DB_STATIC_BASE_URL` and related flags in production.
- Update frontend to fetch raw Markdown and render client-side.

## Open Questions
- Final environment variable names and CLI flags.
- Exact image path shape (`/images/<hash>.ext` vs `/images/<hash>/<filename>`).
