# Change: Improve deep_read diagram prompting

## Why
Current deep_read diagram prompts are overly rigid with keyword triggers and forbid inferred diagrams, causing missing structure/flow diagrams when the content implies them.

## What Changes
- Update deep_read prompt guidance to allow model judgment instead of hard keyword triggers.
- Permit inferred diagrams when content implies a structure/flow, with clear labeling.
- Add Mermaid syntax safety guidance (simple node IDs + label text).
- Emphasize diagrams as supplements to text, not replacements.
- Improve Module A output depth (more specific coverage and checks).

## Impact
- Affected specs: paper
- Affected code: paper deep_read prompt template
