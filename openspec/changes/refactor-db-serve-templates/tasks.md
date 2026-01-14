## 1. Implementation
### Phase 1: Template and Static Scaffolding
- [ ] 1.1 Add `paper/web/templates/` Jinja2 templates for index, detail, stats, and shared base layout.
- [ ] 1.2 Add `paper/web/static/` CSS/JS files for page styles and behaviors.
- [ ] 1.3 Wire a shared Jinja2 environment for web handlers.

### Phase 2: Handler Migration
- [ ] 1.4 Migrate index page HTML to Jinja2 template with static asset links.
- [ ] 1.5 Migrate detail page HTML to Jinja2 template with static asset links.
- [ ] 1.6 Migrate stats page HTML to Jinja2 template with static asset links.

### Phase 3: App Integration & Packaging
- [ ] 1.7 Mount `/static` in `paper/web/app.py` for assets.
- [ ] 1.8 Ensure templates/static are included in package data (pyproject/manifest update).

### Phase 4: Validation
- [ ] 1.9 Confirm rendered HTML output and behavior are unchanged (manual parity check on sample db).
