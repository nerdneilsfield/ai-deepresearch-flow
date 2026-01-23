# Change: Add extract timeout configuration

## Why
Long-running model calls can exceed the fixed 60s timeout, causing avoidable extraction failures. Users need a configurable timeout for `paper extract`.

## What Changes
- Add `extract.timeout` to config to set the default request timeout in seconds.
- Add `--timeout` to the `paper extract` CLI to override the config value per run.
- Wire the timeout through to provider calls.
- Document the config and CLI option in the example config.

## Impact
- Affected specs: paper
- Affected code: paper extract CLI, config loading, request pipeline
