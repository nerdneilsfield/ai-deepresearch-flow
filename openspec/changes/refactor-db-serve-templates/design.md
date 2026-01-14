## Context
The db serve UI currently assembles large HTML/CSS/JS strings inside Python handlers. This blocks reuse, makes diff review hard, and prevents browser caching of assets.

## Goals / Non-Goals
- Goals:
  - Move all page HTML into Jinja2 templates under `paper/web/templates/`.
  - Serve CSS/JS as static files under `/static` for cache efficiency.
  - Keep UI behavior and data payloads unchanged.
- Non-Goals:
  - No changes to API endpoints or data schema.
  - No visual redesign.

## Decisions
- Decision: Use Jinja2 templates for index/detail/stats pages with a shared base layout.
  - Rationale: Keeps templates close to web code and matches existing dependency.
- Decision: Serve CSS/JS from `paper/web/static/` and mount `/static` in Starlette.
  - Rationale: Enables browser caching and smaller handler code.

## Risks / Trade-offs
- Risk: Packaging misses new template/static assets.
  - Mitigation: Add explicit package data configuration and verify build.
- Risk: Behavior drift if inline JS is altered during move.
  - Mitigation: Copy JS verbatim into static files and keep template variables minimal.

## Migration Plan
1. Scaffold templates and static assets.
2. Replace page handlers with Jinja2 rendering.
3. Mount `/static` and ensure assets are packaged.
4. Run db serve on sample data to verify parity (visual + basic interactions).

## Open Questions
- None.
