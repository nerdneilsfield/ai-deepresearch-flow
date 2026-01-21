# Change: update paper templates diagrams

## Why
Users want institutional affiliation extracted (optional for backward compatibility) and richer diagram outputs in deep_read and eight_questions outputs.

## What Changes
- Add optional institution field to simple/deep_read/eight_questions schemas and prompts.
- Add optional diagram fields (architecture/flow/data/hardware + markmap mindmap) to deep_read.
- Add optional architecture diagram field to eight_questions.
- Render the new fields in markdown outputs when present.

## Impact
- Affected specs: paper
- Affected code: python/deepresearch_flow/paper/schemas/*.json, python/deepresearch_flow/paper/prompt_templates/*.j2, python/deepresearch_flow/paper/templates/*.j2
