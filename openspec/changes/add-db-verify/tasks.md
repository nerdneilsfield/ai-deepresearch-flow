## 1. Implementation
- [x] 1.1 Add `paper db verify` CLI options (input JSON, prompt template/schema, output JSON) and wire into the CLI.
- [x] 1.2 Implement verify logic to detect empty fields, emit rich console summary + per-item details, and write the JSON report.
- [x] 1.3 Add `--retry-list-json` to `paper extract` to filter inputs and retry missing stages or full docs based on the report.
