## 1. Implementation
- [x] 1.1 Extend stage definitions to accept optional depends_on list
- [x] 1.2 Add extract.stage_dag config + --stage-dag CLI flag
- [x] 1.3 Implement ready-only DAG scheduler for multi-stage extract
- [x] 1.4 Enforce deterministic previous_outputs in DAG mode (dependencies only)
- [x] 1.5 Add dependency cycle detection and fail-fast validation
- [x] 1.6 Update retry/skip logic to enqueue dependencies in DAG mode
- [x] 1.7 Add DAG plan preview to --dry-run output
- [x] 1.8 Add scheduler mode logging and update docs
