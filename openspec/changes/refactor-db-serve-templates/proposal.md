# Change: Move db serve HTML/CSS/JS into Jinja2 templates and static assets

## Why
`paper/web/handlers/pages.py` currently embeds large HTML/CSS/JS strings in Python. This makes layout changes hard to maintain and prevents browser caching of static assets. The project already uses Jinja2, so we can move page markup to templates and serve CSS/JS as static files.

## What Changes
- Add Jinja2 templates under `python/deepresearch_flow/paper/web/templates/` for index/detail/stats pages.
- Move page CSS/JS into `python/deepresearch_flow/paper/web/static/` (with `/static` mount) for cache-friendly delivery.
- Replace inline HTML generation in `paper/web/handlers/pages.py` with template rendering using a shared Jinja2 environment.
- Keep the existing page behavior and data payloads unchanged.

## Impact
- Affected specs: `paper-db-serve-web`
- Affected code: `python/deepresearch_flow/paper/web/handlers/pages.py`, `python/deepresearch_flow/paper/web/templates.py`, `python/deepresearch_flow/paper/web/app.py`
- New assets: `python/deepresearch_flow/paper/web/templates/*`, `python/deepresearch_flow/paper/web/static/*`
- Packaging: ensure templates/static are included in the built distribution.
