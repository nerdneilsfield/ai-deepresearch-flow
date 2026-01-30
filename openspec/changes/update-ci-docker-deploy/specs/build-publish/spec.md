## ADDED Requirements
### Requirement: Deploy runtime env vars for API and static base
The system SHALL read `PAPER_DB_API_BASE` and `PAPER_DB_STATIC_BASE` from the runtime environment, and the deploy nginx configuration SHALL use these exact names for proxying `/api` and serving static assets.

#### Scenario: Env var names are honored
- **WHEN** the deploy container starts with `PAPER_DB_API_BASE` and `PAPER_DB_STATIC_BASE` set
- **THEN** nginx routes `/api` to `PAPER_DB_API_BASE` and serves static content using `PAPER_DB_STATIC_BASE`

### Requirement: Frontend assets bundling
The deploy Dockerfile SHALL build or copy frontend static assets into the image.

#### Scenario: Frontend build in deploy image
- **GIVEN** the deploy image build starts
- **WHEN** building the frontend or copying pre-built assets
- **THEN** the final image contains all static assets served by nginx at `/`

### Requirement: Docker workflow triggers
The Docker workflow SHALL support manual trigger via `workflow_dispatch` and release tag builds; PR builds are not required.

#### Scenario: Manual trigger
- **WHEN** a user triggers the Docker workflow manually
- **THEN** it runs the Docker build and publish steps

#### Scenario: Release tag trigger
- **WHEN** a release tag `vX.X.X` is pushed
- **THEN** the Docker workflow runs and publishes images with the release tags

### Requirement: Docker images built via Docker workflow
The system SHALL build and publish Docker images only in the dedicated Docker workflow, not in the PyPI workflow. The deploy image build SHALL depend on successful frontend asset generation.

#### Scenario: PyPI workflow does not build Docker images
- **WHEN** the PyPI workflow runs
- **THEN** it skips all Docker build and push steps

#### Scenario: Docker workflow builds base and deploy images
- **WHEN** the Docker workflow runs on release tags
- **THEN** it builds the base image and the deploy image with the required tag scheme

#### Scenario: Deploy build waits on frontend assets
- **GIVEN** frontend asset generation fails
- **WHEN** the deploy image build would start
- **THEN** the deploy image build does not run and the workflow fails

### Requirement: Base and deploy image tagging
The system SHALL tag base images as `:latest` and `:vX.X.X`, and deploy images as `:deploy-latest` and `:deploy-vX.X.X`.

#### Scenario: Base tags on release
- **WHEN** a release tag `vX.X.X` is built
- **THEN** the base image is tagged with `:latest` and `:vX.X.X`

#### Scenario: Deploy tags on release
- **WHEN** a release tag `vX.X.X` is built
- **THEN** the deploy image is tagged with `:deploy-latest` and `:deploy-vX.X.X`
