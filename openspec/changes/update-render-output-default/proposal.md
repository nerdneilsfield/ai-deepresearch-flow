# Change: Default extract render output directory

## Why
Users want extract-time rendering to place markdown outputs alongside the aggregated JSON output when `--output` is specified, and want clearer examples in README.

## What Changes
- Default `paper extract --render-md` output directory to the parent directory of `--output` when provided.
- Keep explicit `--render-output-dir` as an override.
- Update README with more render examples and clarify output locations.

## Impact
- Affected specs: `paper` capability
- Affected code: extract CLI, README
