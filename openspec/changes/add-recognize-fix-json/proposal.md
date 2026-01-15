# Change: add recognize fix support for JSON outputs

## Why
Users need to apply the same OCR markdown fixes to paper JSON outputs (summary and template results) so downstream rendering is consistent without manually reformatting each item.

## What Changes
- Add `recognize fix --json` to process JSON files (summary/template outputs) and apply the markdown fix pipeline to template result fields.
- Detect JSON structures (`[{...}]` or `{ "papers": [...] }`) and preserve the original JSON layout on output.
- Apply rumdl formatting to fixed markdown fields unless `--no-format` is supplied.

## Impact
- Affected spec: recognize-fix.
- Affected code: `python/deepresearch_flow/recognize/cli.py`, `python/deepresearch_flow/recognize/organize.py` (or new helper), `python/deepresearch_flow/paper/template_registry.py` (template field map reuse), plus JSON handling helpers.
