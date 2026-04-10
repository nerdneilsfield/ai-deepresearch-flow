# Web Upload Pipeline Design Spec

**Date:** 2026-03-29
**Status:** Draft
**Depends on:** OCR module (Spec 2026-03-27), WebDAV static push (Spec 2026-03-29)

## Overview

Full pipeline: user uploads PDF + BibTeX via web UI → OCR → fix → fix-math → organize → extract → fix → fix-math → fix-mermaid → translate → fix → fix-math → push_static → merge_db. All API endpoints under `/api/v1/admin/` with Bearer auth. Vue 3 + Vite frontend with simple form upload, step-level polling, and browser notifications.

## Pipeline Steps

13 logical steps. Steps marked `(loop)` iterate up to `max_fix_rounds` times, re-feeding remaining errors each round until error count reaches 0 or rounds exhausted.

| # | Step | Type | Description |
|---|------|------|-------------|
| 1 | `ocr` | fast | PaddleOCR backend, outputs `full.md` + `images/` |
| 2 | `fix` | LLM | Fix OCR text artifacts |
| 3 | `fix_math` | LLM (loop) | Fix LaTeX formulas, iterate on remaining errors |
| 4 | `organize` | fast | Organize output directory structure |
| 5 | `extract` | LLM | Extract structured paper metadata |
| 6 | `fix_extract` | LLM | Fix extraction artifacts |
| 7 | `fix_extract_math` | LLM (loop) | Fix formulas in extraction output |
| 8 | `fix_extract_mermaid` | LLM (loop) | Fix mermaid diagrams in extraction output |
| 9 | `translate` | LLM | Translate markdown |
| 10 | `fix_translate` | LLM | Fix translation artifacts |
| 11 | `fix_translate_math` | LLM (loop) | Fix formulas in translated output |
| 12 | `push_static` | I/O | Push static files to WebDAV |
| 13 | `merge_db` | local | Merge paper into snapshot database |

### Loop Steps Detail

```
fix_math / fix_extract_math / fix_translate_math:
  round = 0
  while round < max_fix_rounds:
    result = run_fix_math(input, previous_errors)
    if result.remaining_errors == 0:
      break
    previous_errors = result.remaining_errors
    round += 1

fix_extract_mermaid:
  same logic, using fix_mermaid instead
```

## Architecture

### Job Queue

SQLite `pipeline_job` table + asyncio workers.

```sql
CREATE TABLE pipeline_job (
  job_id        TEXT PRIMARY KEY,   -- UUID
  status        TEXT NOT NULL,      -- pending | running | done | failed | cancelled
  current_step  INTEGER DEFAULT 0,  -- 0-based step index
  total_steps   INTEGER NOT NULL,   -- number of enabled steps
  step_name     TEXT,               -- current step name for display
  skip_steps    TEXT,               -- JSON array of skipped step names
  error_message TEXT,               -- last error if failed
  created_at    TEXT NOT NULL,      -- ISO 8601
  updated_at    TEXT NOT NULL,
  completed_at  TEXT,
  work_dir      TEXT NOT NULL,      -- {temp_dir}/{job_id}/
  -- input metadata
  pdf_filename  TEXT NOT NULL,
  bibtex_raw    TEXT NOT NULL,
  bibtex_key    TEXT,               -- parsed BibTeX entry key
  -- pipeline config snapshot
  config_json   TEXT NOT NULL       -- frozen copy of pipeline config at submission time
);
```

### Worker Pool

```python
class PipelineWorkerPool:
    """Manages concurrent pipeline job execution."""

    def __init__(
        self,
        *,
        max_workers: int,
        llm_semaphore: asyncio.Semaphore,
        db_path: Path,
        temp_dir: Path,
        pipeline_config: PipelineConfig,
    ): ...

    async def submit(self, job_id: str) -> None:
        """Pick up a pending job and start processing."""

    async def run_pipeline(self, job: PipelineJob) -> None:
        """Execute pipeline steps sequentially, updating job state after each step."""

    async def shutdown(self) -> None:
        """Wait for running jobs to complete, cancel pending."""
```

Workers are asyncio Tasks managed by the pool. Each worker:
1. Loads job from SQLite
2. Resumes from `current_step` (breakpoint resume)
3. For each enabled step:
   - Update job `step_name` and `current_step` in DB
   - Acquire `llm_semaphore` if step is LLM-type
   - Execute step function
   - Release semaphore
   - On success: increment `current_step`, commit
   - On failure: set `status = "failed"`, record `error_message`, commit
4. On all steps complete: set `status = "done"`, set `completed_at`

### Concurrency Control

Two levels:
- **Job concurrency**: `max_workers` (e.g., 3) — max simultaneous pipeline jobs
- **LLM concurrency**: `max_llm_concurrency` (e.g., 20) — global semaphore shared across all jobs for LLM API calls. Prevents rate limit exhaustion.

```python
# All LLM steps acquire from the same semaphore
async with llm_semaphore:
    result = await run_fix_math(...)
```

### Breakpoint Resume

Job records `current_step` (0-based index into the list of **enabled** steps). On retry:
1. Client calls `POST /api/v1/admin/pipeline/jobs/{job_id}/retry`
2. Server sets `status = "pending"`, keeps `current_step` unchanged
3. Worker picks up job, skips completed steps, resumes from `current_step`

Intermediate outputs persist in `{temp_dir}/{job_id}/`:
```
{temp_dir}/{job_id}/
├── input.pdf
├── bibtex.bib
├── 01_ocr/
│   ├── full.md
│   └── images/
├── 02_fix/
│   └── full.md
├── 03_fix_math/
│   ├── round_0.md
│   └── round_1.md    # if multi-round
├── 04_organize/
│   └── ...
├── 05_extract/
│   └── output.json
├── ...
└── 12_push_static/
    └── stats.json
```

Each step reads from the previous step's output directory and writes to its own.

## Configuration

### config.toml `[pipeline]` section

```toml
[pipeline]
max_workers = 3
max_llm_concurrency = 20
max_fix_rounds = 3
keep_days = 7

# OCR
ocr_config = "ocr.toml"

# LLM steps share the same config.toml providers
# Model references for each LLM step (provider/model format)
fix_model = "doubao/doubao-seed-2.0-pro"
fix_math_model = "doubao/doubao-seed-2.0-pro"
fix_mermaid_model = "doubao/doubao-seed-2.0-pro"
extract_model = "doubao/doubao-seed-2.0-pro"
extract_template = "default"
translate_model = "doubao/doubao-seed-2.0-pro"
translate_target_lang = "zh"

# Workers per step (internal concurrency)
fix_workers = 4
fix_math_workers = 12
fix_mermaid_workers = 4
extract_workers = 1
translate_max_concurrency = 4

# Static push uses remote.toml [remote.storage]
```

### Startup

```python
# CLI or programmatic
def start_api(
    *,
    config_path: Path = Path("config.toml"),
    snapshot_db: Path,
    admin_token: str,
    temp_dir: Path,           # pipeline working directory
    pipeline_db: Path | None, # SQLite for job queue, default: temp_dir/pipeline.db
    host: str = "0.0.0.0",
    port: int = 8000,
): ...
```

`temp_dir` is specified at API startup. Pipeline SQLite DB lives at `{temp_dir}/pipeline.db` by default, or a custom path.

## API Endpoints

All under `/api/v1/admin/pipeline/`. Auth: Bearer token (same `admin_token` as existing admin API).

### `POST /jobs` — Submit new job

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `pdf` | file | yes | PDF file |
| `bibtex` | text | yes | BibTeX entry text |
| `skip_steps` | text | no | JSON array of step names to skip, e.g. `["translate","fix_translate","fix_translate_math"]` |

**Processing:**
1. Validate PDF (non-empty, valid header)
2. Parse BibTeX text — reject with 400 if invalid
3. Create job record in SQLite
4. Save PDF + BibTeX to `{temp_dir}/{job_id}/`
5. Submit to worker pool
6. Return job ID

**Response:** `201 Created`
```json
{
  "job_id": "a1b2c3d4-...",
  "status": "pending",
  "bibtex_key": "smith2024attention"
}
```

**Errors:**
- `400` — Invalid PDF, invalid BibTeX, empty fields
- `401` — Unauthorized
- `413` — PDF too large (configurable limit)

### `GET /jobs` — List jobs

**Query params:** `?status=running&limit=20&offset=0`

**Response:** `200 OK`
```json
{
  "jobs": [
    {
      "job_id": "a1b2c3d4-...",
      "status": "running",
      "current_step": 5,
      "step_name": "extract",
      "total_steps": 13,
      "pdf_filename": "attention.pdf",
      "bibtex_key": "smith2024attention",
      "created_at": "2026-03-29T10:00:00Z",
      "updated_at": "2026-03-29T10:05:30Z"
    }
  ],
  "total": 42,
  "limit": 20,
  "offset": 0
}
```

### `GET /jobs/{job_id}` — Job detail

**Response:** `200 OK`
```json
{
  "job_id": "a1b2c3d4-...",
  "status": "running",
  "current_step": 5,
  "step_name": "extract",
  "total_steps": 13,
  "steps": [
    {"name": "ocr", "status": "done"},
    {"name": "fix", "status": "done"},
    {"name": "fix_math", "status": "done", "rounds": 2},
    {"name": "organize", "status": "done"},
    {"name": "extract", "status": "running"},
    {"name": "fix_extract", "status": "pending"},
    {"name": "fix_extract_math", "status": "pending"},
    {"name": "fix_extract_mermaid", "status": "pending"},
    {"name": "translate", "status": "skipped"},
    {"name": "fix_translate", "status": "skipped"},
    {"name": "fix_translate_math", "status": "skipped"},
    {"name": "push_static", "status": "pending"},
    {"name": "merge_db", "status": "pending"}
  ],
  "pdf_filename": "attention.pdf",
  "bibtex_key": "smith2024attention",
  "error_message": null,
  "created_at": "2026-03-29T10:00:00Z",
  "updated_at": "2026-03-29T10:05:30Z",
  "completed_at": null
}
```

### `POST /jobs/{job_id}/retry` — Retry failed job

Resumes from the failed step (breakpoint resume).

**Response:** `200 OK`
```json
{
  "job_id": "a1b2c3d4-...",
  "status": "pending",
  "resume_from_step": 5,
  "step_name": "extract"
}
```

**Errors:**
- `400` — Job not in `failed` status
- `404` — Job not found

### `POST /jobs/{job_id}/cancel` — Cancel running job

**Response:** `200 OK`
```json
{
  "job_id": "a1b2c3d4-...",
  "status": "cancelled"
}
```

### `DELETE /jobs/{job_id}` — Delete job and cleanup

Deletes job record and removes `{temp_dir}/{job_id}/` directory.

**Response:** `200 OK`
```json
{
  "deleted": true,
  "job_id": "a1b2c3d4-..."
}
```

## Frontend

### Tech Stack

Vue 3 + Vite. Mounted at `/admin/` path on the web app. Protected by the same Bearer token (stored in localStorage after login prompt).

### Pages

#### 1. Login Page (`/admin/login`)
- Single token input field
- Validates against `GET /api/v1/admin/pipeline/jobs` (401 = wrong token)
- Stores token in localStorage

#### 2. Upload Page (`/admin/upload`)
- PDF file picker (drag zone optional, but simple input is fine)
- BibTeX text area (paste content)
- Pipeline step checkboxes (13 steps, all checked by default)
  - Grouped: OCR group, Extract group, Translate group, Push group
  - Unchecking a group unchecks all sub-steps
- Submit button → `POST /jobs`
- On success: redirect to job detail page

#### 3. Job List Page (`/admin/jobs`)
- Table: job_id (short), PDF filename, BibTeX key, status badge, progress bar (step/total), created_at, actions
- Filter by status (tabs: All / Running / Pending / Done / Failed)
- Auto-refresh every 3s for running jobs
- Actions: View detail, Retry (if failed), Cancel (if running), Delete

#### 4. Job Detail Page (`/admin/jobs/{job_id}`)
- Header: PDF filename, BibTeX key, status badge
- Step progress: vertical timeline showing each step with status icon
  - Done: green check
  - Running: spinner
  - Pending: gray circle
  - Skipped: dash
  - Failed: red X with error message
- For loop steps: show round count (e.g., "fix_math — 2 rounds")
- Auto-refresh every 2s while running
- Browser Notification API: request permission on page load, fire notification on status change to `done` or `failed`
- Actions: Retry (if failed), Cancel (if running)

### Browser Notifications

```javascript
// On page load
if (Notification.permission === 'default') {
  Notification.requestPermission()
}

// On poll detecting status change
if (newStatus === 'done' || newStatus === 'failed') {
  new Notification(`Pipeline ${newStatus}`, {
    body: `${job.pdf_filename} — ${newStatus === 'done' ? 'completed successfully' : job.error_message}`
  })
}
```

## Job Cleanup

Background task runs periodically (e.g., every hour) or on startup:
1. Query jobs where `status IN ('done', 'failed', 'cancelled')` and `completed_at < now - keep_days`
2. Delete `{temp_dir}/{job_id}/` directory
3. Delete job record from SQLite

```python
async def cleanup_expired_jobs(db_path: Path, temp_dir: Path, keep_days: int) -> int:
    """Delete expired job records and their working directories. Returns count deleted."""
```

## Step Implementation Mapping

Each pipeline step maps to existing CLI functions, called as library functions (not subprocess):

| Step | Module | Function | Key Args |
|------|--------|----------|----------|
| `ocr` | `deepresearch_flow.ocr.runner` | `run_ocr()` | backend, input_path, output_dir, max_retries |
| `fix` | `deepresearch_flow.recognize.organize` | `fix_markdown_text()` | fix_level, format_enabled |
| `fix_math` | `deepresearch_flow.recognize.math` | `fix_math_text()` | model, config, workers, max_retries |
| `organize` | `deepresearch_flow.recognize.organize` | `organize_mineru_dir()` | input_dir, bibtex |
| `extract` | `deepresearch_flow.paper.extract` | (extraction pipeline) | model, config, template |
| `fix_extract` | `deepresearch_flow.recognize.organize` | `fix_markdown_text()` | on JSON fields |
| `fix_extract_math` | `deepresearch_flow.recognize.math` | `fix_math_text()` | on JSON fields |
| `fix_extract_mermaid` | `deepresearch_flow.recognize.mermaid` | `fix_mermaid_text()` | on JSON fields |
| `translate` | `deepresearch_flow.translator.engine` | `MarkdownTranslator` | model, target_lang |
| `fix_translate` | `deepresearch_flow.recognize.organize` | `fix_markdown_text()` | on translated md |
| `fix_translate_math` | `deepresearch_flow.recognize.math` | `fix_math_text()` | on translated md |
| `push_static` | `deepresearch_flow.paper.snapshot.push_static` | `push_static_files()` | storage, export_dir |
| `merge_db` | `deepresearch_flow.paper.snapshot.admin` | `_insert_paper_metadata()` | conn, paper_dict |

## Module Structure

```
python/deepresearch_flow/pipeline/
├── __init__.py
├── config.py          # PipelineConfig dataclass, load from [pipeline] in config.toml
├── job.py             # PipelineJob dataclass, SQLite job CRUD
├── schema.py          # init_pipeline_db(), job table DDL
├── steps.py           # PIPELINE_STEPS registry, StepDef dataclass
├── runner.py          # PipelineWorkerPool, run_pipeline(), per-step execution
├── cleanup.py         # cleanup_expired_jobs()
└── api.py             # Starlette sub-app: /jobs CRUD + /jobs/{id}/retry|cancel

python/deepresearch_flow/pipeline/frontend/
├── dist/              # Built Vue 3 + Vite assets (gitignored during dev)
└── ...                # Vue source (or separate repo/dir)
```

### Integration with Existing Admin App

```python
# In paper/snapshot/api.py create_app()
if admin_token:
    from deepresearch_flow.paper.snapshot.admin import create_admin_app
    from deepresearch_flow.pipeline.api import create_pipeline_app

    admin_app = create_admin_app(snapshot_db=snapshot_db, admin_token=admin_token)
    routes.append(Mount("/api/v1/admin", app=admin_app))

    if pipeline_config:
        pipeline_app = create_pipeline_app(
            admin_token=admin_token,
            pipeline_config=pipeline_config,
            snapshot_db=snapshot_db,
            temp_dir=temp_dir,
        )
        routes.append(Mount("/api/v1/admin/pipeline", app=pipeline_app))
        routes.append(Mount("/admin", app=StaticFiles(directory=frontend_dist, html=True)))
```

## Error Handling

- **BibTeX parse error**: Reject at upload time (400).
- **Step failure**: Set job `status = "failed"`, record step name + error message. User can retry.
- **LLM rate limit / timeout**: Individual step handles retries internally (existing `max_retries` + backoff logic). If all retries exhausted, step fails.
- **Storage auth error**: `push_static` step catches `StorageAuthError`, marks job failed with clear message.
- **Worker crash**: On startup, scan for jobs in `running` status — reset to `pending` for auto-resume.

## Security

- All endpoints require Bearer token (same `admin_token`).
- PDF upload size limit: configurable (`max_upload_mb`, default 100 MB).
- BibTeX text limit: 64 KB.
- No path traversal: job_id is UUID, temp_dir is fixed at startup.
- Rate limit: optional, via middleware.

## Testing Strategy

- **Unit**: Job CRUD, step registry, config parsing, BibTeX validation, cleanup logic.
- **Integration**: API endpoints with test client (Starlette TestClient), mock pipeline steps.
- **E2E**: Full pipeline with a small PDF (1-2 pages), mock LLM responses.
