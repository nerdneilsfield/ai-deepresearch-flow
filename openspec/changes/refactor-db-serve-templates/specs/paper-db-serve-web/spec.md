## ADDED Requirements
### Requirement: Jinja2 page templates
The system SHALL render db serve pages (index, detail, stats) using Jinja2 templates located under `deepresearch_flow.paper.web.templates`.

#### Scenario: Index page renders from template
- **WHEN** the index page is requested
- **THEN** the handler renders a Jinja2 template with the provided data context.

#### Scenario: Detail page renders from template
- **WHEN** the detail page is requested
- **THEN** the handler renders a Jinja2 template with the paper context and view parameters.

#### Scenario: Stats page renders from template
- **WHEN** the stats page is requested
- **THEN** the handler renders a Jinja2 template with chart scaffolding.

### Requirement: Static assets for CSS/JS
The system SHALL serve page CSS and JavaScript from `deepresearch_flow.paper.web.static` via a `/static` route.

#### Scenario: Static assets are accessible
- **WHEN** a db serve page loads
- **THEN** its CSS/JS assets are requested from `/static/*` URLs.

### Requirement: Packaging includes web assets
The system SHALL include Jinja2 templates and static files in the built distribution.

#### Scenario: Installed package serves web assets
- **WHEN** the project is installed from a package build
- **THEN** db serve can load templates and static assets without missing-file errors.

### Requirement: Behavior parity
The refactor SHALL preserve db serve page behavior (navigation, filters, outline, split view) while moving markup to templates.

#### Scenario: Page behavior remains unchanged
- **WHEN** a user interacts with filters and views before and after the change
- **THEN** the same navigation and results are observed.
