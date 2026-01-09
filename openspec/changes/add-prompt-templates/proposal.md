# Change: Add selectable extract/render templates and output language

## Why
Users want multiple built-in templates for both extraction prompts and render output, plus a way to specify output language without editing templates.

## What Changes
- Add built-in extract prompt templates (deep reading, seven questions, three-pass reading).
- Add built-in render templates that correspond to the extract templates.
- Allow selecting built-in templates by name for both extract and render workflows.
- Allow passing an output language parameter into templates.
- Document template names and usage for extract/render.

## Impact
- Affected specs: `paper` capability
- Affected code: `paper extract` and `paper db render-md`, template loaders, built-in templates, README
