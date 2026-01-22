# Change: Add extract range filtering

## Why
Users need to rerun extraction on a subset of files in a directory without editing the input set manually.

## What Changes
- Add `--start-idx`/`--end-idx` to the extract CLI to slice the resolved input list.
- Treat `end-idx = -1` as "to the last file".
- Apply slicing before retry-failed filtering for stable indices.
- Log slicing results and warn when the range yields no files.
- Document the new flags.

## Impact
- Affected specs: paper
- Affected code: paper extract CLI + input resolution
