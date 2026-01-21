# Change: update translation prompt

## Why
We want translation prompts aligned with TranslateGemma's recommended structure while preserving existing XML constraints and formatting rules.

## What Changes
- Extend translator prompt to include the professional translator framing and blank-line notice.
- Keep XML constraints intact for formatting/marker safety.

## Impact
- Affected specs: translator
- Affected code: python/deepresearch_flow/translator/prompts.py
