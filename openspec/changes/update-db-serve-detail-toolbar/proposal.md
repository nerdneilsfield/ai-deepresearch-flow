# Change: Compact paper detail toolbar and add fullscreen mode

## Why
The paper detail view consumes too much vertical space, especially in split view. Users want a compact toolbar and a fullscreen mode to focus on content.

## What Changes
- Move the paper title into the top header bar.
- Consolidate view tabs, split controls, and width controls into a single toolbar row.
- Add a fullscreen toggle available for all paper views, with a visible exit control.
- Preserve summary outline/back-to-top in fullscreen.

## Impact
- Affected specs: paper
- Affected code: python/deepresearch_flow/paper/web/app.py, README.md
