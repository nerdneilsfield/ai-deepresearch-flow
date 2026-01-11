# Change: Add homepage filters and summary template indicators to db serve list

## Why
Users need to quickly filter papers by multiple availability signals and template tags, while also seeing which summary templates exist before opening a detail page.

## What Changes
- Add multi-select filters for PDF/Source/Summary availability and summary template tags on the papers homepage.
- Add a filter query input that can express the same filters.
- Show available summary template tags on each paper list item.
- Document the filters and template indicators in README.

## Impact
- Affected specs: paper
- Affected code: python/deepresearch_flow/paper/web/app.py, README.md
