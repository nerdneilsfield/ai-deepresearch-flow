# Change: update extract module sequencing and resume

## Why
Multi-stage templates (e.g., deep_read) need clearer control over scheduling, persistence, and resume behavior. Users want a task-queue execution model (doc+module as a unit), immediate persistence after each module, resume that skips completed modules, and better iteration when prompts change.

## What Changes
- Execute doc+module tasks from a single linear queue (e.g., doc1-moduleA, doc1-moduleB, ... docN-moduleH) with concurrency over that queue.
- Persist outputs after each module completes (including per-module stage outputs and aggregated output JSON).
- On start, load existing outputs and skip modules already completed for a document.
- Track prompt template hashes per module and re-run modules when prompts change.
- Add a CLI option to force re-running specific modules (e.g., `--force-stage`).

## Impact
- Affected spec: paper.
- Affected code: `python/deepresearch_flow/paper/extract.py`, `python/deepresearch_flow/paper/cli.py`.
- Data: stage output files and aggregate JSON become sources for resume.
