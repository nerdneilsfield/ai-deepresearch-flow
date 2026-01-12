# Change: Add Source outline and image sizing in db serve

## Why
Source view can be long and hard to navigate, and base64 images can overflow the layout. Users need the same navigation aids as Summary and safer image sizing.

## What Changes
- Add a collapsible outline panel and back-to-top control to the Source view
- Constrain Source-rendered images to fit the content width
- Update README to document Source view navigation aids

## Impact
- Affected specs: paper
- Affected code: `python/deepresearch_flow/paper/web/app.py`, `README.md`
