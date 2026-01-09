# Change: Rename seven_questions to eight_questions and update workflows

## Why
Users need the seven-questions workflow expanded to eight questions (including datasets/settings and metrics/results), and the template name should match its actual behavior. The update also requires stronger metadata fields and render output that localizes headings based on output language.

## What Changes
- Rename the built-in template from `seven_questions` to `eight_questions` (breaking change).
- Update built-in templates so the eight-questions flow uses eight questions and two-stage grouping (1-4, 5-8).
- Update the simple template to produce a single summary covering the eight-question aspects.
- Require `publication_date` and `publication_venue` across built-in schemas; require `abstract`, `keywords`, and `summary` in the simple schema.
- Localize render headings based on `output_language` (zh/en).

## Impact
- Affected specs: `paper` capability
- Affected code: prompt templates, JSON schemas, render templates, template registry, extraction stage validation, README
