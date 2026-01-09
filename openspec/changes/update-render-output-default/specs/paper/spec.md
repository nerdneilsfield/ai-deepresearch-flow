## ADDED Requirements

### Requirement: Default extract render output directory
When running `paper extract --render-md`, the default render output directory SHALL be the parent directory of the aggregated JSON output when `--output` is provided.
The CLI SHALL allow overriding this directory via `--render-output-dir`.

#### Scenario: Default render directory follows output
- **WHEN** the user runs `deepresearch-flow paper extract --output ./out/papers.json --render-md`
- **THEN** rendered markdown files are written under `./out/` by default

#### Scenario: Override render output directory
- **WHEN** the user runs `deepresearch-flow paper extract --render-md --render-output-dir ./md`
- **THEN** rendered markdown files are written under `./md/`
