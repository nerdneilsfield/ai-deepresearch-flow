# Change: Add static CDN delivery for db serve web

## Why
The current web UI serves PDFs/Markdown/images from the app origin, which is CDN-unfriendly and limits long-lived caching. We want static assets to be served from a dedicated static host (Cloudflare) with content-hash URLs so they can be cached aggressively.

## What Changes
- Introduce dev/prod static asset modes; production uses a configurable static base URL.
- Serve PDFs, images, and Markdown as static files with content-hash names under `/pdf`, `/images`, `/md`, `/md_translate/<lang>`.
- Move Markdown rendering to the browser (marked + DOMPurify), including image URL rewrite.
- Keep images shared across original and translated Markdown.
- Allow PDF.js viewer assets to load from a CDN (jsDelivr) with local fallback.
- Document cache strategy: immutable assets with long TTL.

## Impact
- Affected specs: `paper-db-serve-web`
- Affected code: `python/deepresearch_flow/paper/web/*`, organize output pipeline, env/config handling, templates/static JS
