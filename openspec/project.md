# Project Context

## Purpose
This repo is the Python-based scaffold for the "ai-deepresearch-flow" project.
It focuses on establishing workflow conventions and tooling while core features
are defined.

## Tech Stack
- Python (>=3.14)
- uv for dependency management and running the project (pyproject.toml)
- No runtime dependencies yet

## Project Conventions

### Code Style
- Prefer small, single-purpose modules and functions.
- Use explicit names; avoid cleverness.
- Add type hints when it improves clarity.

### Architecture Patterns
- Start simple: flat modules in the repo root or a single package when code
  grows.
- Favor the standard library and minimal dependencies; introduce new deps only
  when justified.

### Testing Strategy
- No tests yet; use pytest once behaviors are defined.
- Keep tests close to the code they cover.

### Git Workflow
- Conventional commits required (e.g., `feat:`, `fix:`, `doc:`, `refactor:`,
  `test:`, `chore:`).
- Prefer small, focused commits.

## Domain Context
- Project docs and the development log are maintained in Notion; treat Notion as
  the source of truth for progress records.

## Important Constraints
- All development records must be written to the Notion dev log page in reverse
  chronological order.
- Each entry format: `## YYYY-MM-DD HH:MM - Summary` followed by detail lines.

## External Dependencies
- Notion project page: https://www.notion.so/nerdneils/ai-deepresearch-flow-2e30f931dcb280709bcfe39ab7de3382
- Notion dev log page: https://www.notion.so/nerdneils/ai-deepresearch-flow-2e30f931dcb2800199f7e324f89676a5
