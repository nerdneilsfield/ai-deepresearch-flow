# Change: Add selectable prompt templates and output language

## Why
Users want multiple built-in prompt templates for paper reading workflows and a way to specify output language without editing templates.

## What Changes
- Add built-in prompt templates (deep reading, seven questions, three-pass reading).
- Allow selecting a built-in template by name.
- Allow passing an output language parameter into templates.
- Document template names and usage.

## Impact
- Affected specs: `paper` capability
- Affected code: `paper db render-md` command, template loader, built-in templates, README
