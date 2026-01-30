# Change: Update CI workflows and Docker image strategy

## Why
CI/CD and Docker images are being restructured to separate PyPI publishing from Docker builds and to introduce a deployable Nginx + API image built on the base image.

## What Changes
- Split responsibilities: PyPI workflow only publishes packages; Docker workflow handles all Docker builds.
- Base Dockerfile moves to `scripts/docker/Dockerfile.base` (adds sqlite packages) and remains the primary runtime base.
- Deploy Dockerfile `scripts/docker/Dockerfile.deploy` builds on the base image, installs nginx, bundles frontend assets, and runs nginx + API via supervisord.
- Docker workflow builds base + frontend in parallel, then builds deploy image with explicit dependency on frontend artifacts.
- Docker workflow supports manual trigger (workflow_dispatch) and release tag builds; PR builds are not required.
- Tagging rules: `:latest`/`:vX.X.X` for base; `:deploy-latest`/`:deploy-vX.X.X` for deploy.

## Impact
- Affected specs: build-publish
- Affected code: `Dockerfile`, `scripts/docker/Dockerfile.base`, `scripts/docker/Dockerfile.deploy`, `.github/workflows/push-to-pypi.yml`, new docker workflow file(s)
