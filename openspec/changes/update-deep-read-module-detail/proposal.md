# Change: Expand deep_read module detail requirements

## Why
Current deep_read module guidance is too terse, leading to shallow outputs across modules.

## What Changes
- Expand module A-H guidance with detailed checklists and required elements.
- Add per-module examples where appropriate (without changing schemas).
- In stage mode, show detailed instructions only for the active module and brief summaries for others.
- Add dynamic Jinja2 blocks to hide non-active module details in stage mode.
- Add end-of-prompt recency instructions to reinforce format/completeness for the active stage.

## Impact
- Affected specs: paper
- Affected code: deep_read prompt template
