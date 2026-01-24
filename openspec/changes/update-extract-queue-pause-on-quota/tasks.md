## 1. Implementation
- [x] 1.1 Add key pool availability helpers on the key rotator (availability check + next wake delay)
- [x] 1.2 Add a shared pause gate for extract scheduling (async event + watcher)
- [x] 1.3 Add a pause threshold to avoid short-cooldown flicker
- [x] 1.4 Harden the watcher against exceptions and add a safety timeout for workers
- [x] 1.5 Integrate pause gate into sequential and DAG schedulers before dequeuing new work
- [x] 1.6 Add pause/resume logging with timestamps and reasons (and optional tqdm status)
- [ ] 1.7 Update extract summary metrics (optional) if a new pause counter is tracked
- [x] 1.8 Update config/example docs if any new settings are introduced
