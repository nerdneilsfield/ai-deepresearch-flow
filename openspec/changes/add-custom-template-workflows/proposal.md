# Change: Custom template workflows and multi-stage extraction

## Why
Users need to supply their own prompt/schema/render templates and run multi-stage extraction for complex reading templates (deep_read, seven_questions, three_pass) with stage-level persistence.

## What Changes
- Allow custom prompt templates + JSON schema + render Jinja2 template to be supplied via CLI.
- Add multi-stage extraction for complex templates, invoking the model per module and merging results.
- Persist intermediate stage outputs for resume/debugging.
- Add CLI flags for language and custom template paths.

## Impact
- Affected specs: `paper` capability
- Affected code: `paper extract`, template registry, extraction pipeline, render-md, README
