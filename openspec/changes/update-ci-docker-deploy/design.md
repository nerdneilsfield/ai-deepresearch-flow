## Context
We are restructuring CI workflows and Docker images to split PyPI publishing from Docker builds and to introduce a deploy image that bundles nginx + frontend assets on top of the base image.

## Goals / Non-Goals
- Goals:
  - Establish a base image (`Dockerfile.base`) for runtime dependencies (including sqlite packages).
  - Establish a deploy image (`Dockerfile.deploy`) that installs nginx, bundles frontend assets, and uses supervisord to run nginx + API.
  - Ensure environment variables `PAPER_DB_API_BASE` and `PAPER_DB_STATIC_BASE` are used by nginx config.
  - Docker workflow supports manual trigger and release tag builds with correct tag scheme.
- Non-Goals:
  - Redesign the application runtime or API behavior.
  - Introduce new deployment targets beyond Docker.

## Decisions
- Decision: Use `scripts/docker/Dockerfile.base` as the base runtime image and install sqlite packages via apt.
  - Why: sqlite is required at runtime; base image should include it to keep deploy image thin.
- Decision: Use `scripts/docker/Dockerfile.deploy` built FROM the base image and install nginx in it.
  - Why: keep deploy image aligned with base runtime dependencies.
- Decision: Use `supervisord` to run nginx and the API process in a single container.
  - Why: deploy image must serve both API (internal 127.0.0.1:8000) and frontend via nginx on 8899.
- Decision: nginx configuration uses env vars `PAPER_DB_API_BASE` and `PAPER_DB_STATIC_BASE` as the canonical names.

## Risks / Trade-offs
- Running multiple processes in one container increases operational complexity; mitigated via supervisord config and clear runtime env var docs.

## Migration Plan
- Rename `Dockerfile.depoly` to `Dockerfile.deploy` and update references.
- Update CI workflows and docs to follow the new tag scheme and build order.

## Open Questions
- None.
