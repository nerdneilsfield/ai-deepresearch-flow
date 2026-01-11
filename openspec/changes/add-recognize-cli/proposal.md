# Change: Add recognize CLI for OCR markdown processing

## Why
Users need a dedicated `recognize` command group to post-process OCR outputs, including embedding images into Markdown, unpacking data URLs, and organizing OCR result folders into consumable markdown files.

## What Changes
- Add a top-level `deepresearch-flow recognize` command group.
- Implement `recognize md embed` and `recognize md unpack` for Markdown image embedding/unpacking.
- Implement `recognize organize` with a `mineru` layout handler that outputs flat markdown files and (optionally) embedded images.
- Update README with recognize usage examples and flags.

## Impact
- Affected specs: new `recognize` capability.
- Affected code: new `python/deepresearch_flow/recognize/` module(s) and CLI wiring.
- CLI behavior: new command group and options for markdown post-processing.
