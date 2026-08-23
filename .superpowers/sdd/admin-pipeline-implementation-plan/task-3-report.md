# Task 3 report — resumable processing Worker

## Outcome

Implemented fixed-step, resumable processing Worker and protected preview
generation. Worker has no HTTP dependency and exposes synchronous and async
Supervisor entrypoints. Fake adapters remain available only as test seams;
production construction now loads real OCR and Paper provider configuration.

Fixed sequence:

`ocr → source_repair → math_repair → organize → extract → validation → summary_repair → translate → translation_repair → preview`

## Real adapter mapping

`ProductionAdapters.from_config` in `pipeline/adapters.py` accepts
`paper_config_path` and `ocr_config_path`; no extractor/translator callable is
required.

| Worker step | Existing public seam | Construction/use |
| --- | --- | --- |
| OCR | `ocr.config.load_ocr_config`, `ocr.factory.create_backend`, `backend.ocr` | OCR backend is built from `ocr_config_path`. |
| Source repair | `recognize.organize.fix_markdown_text` | `fix_level="standard"`, formatting off. |
| Math repair | `recognize.math.extract_math_spans`, `cleanup_formula` | Formula spans are repaired in-place. |
| Markdown organization | `recognize.organize.fix_markdown_text` | `fix_level="standard"`, formatting on. |
| Extract | `paper.config.load_config`, `paper.routing.parse_model_selector`, `resolve_model_capability`, `RoutePool.from_selector`, `paper.extract.extract_documents` | Uploader-selected `provider/model` resolves against declared provider/model allowlist; one Markdown input and JSON/error outputs live in per-job staging. |
| Validation | `paper.schema.validate_schema` | Schema errors become bounded Worker validation retries. |
| Summary repair | `paper.extract.normalize_response_keys` | Uses loaded extraction schema. |
| Translate | `translator.config.TranslateConfig`, `translator.engine.MarkdownTranslator.translate`, `paper.config.resolve_api_keys`, `RoutePool` | Uploader-selected provider/model and runtime-resolved credentials are used. |
| Translation repair | `translator.fixers.fix_markdown` | Standard repair level. |

`PipelineWorker.from_production_config` and `run_production_worker` are the
Supervisor-facing constructors. They accept config paths, state, and artifact
stores; they do not require external Python provider callables.

## Public interfaces

- `FIXED_STEP_SEQUENCE` / `PROCESSING_STEPS`: immutable ten-step tuple.
- `PipelineAdapters`: synchronous or asynchronous fake/integration adapter bundle.
- `ProductionAdapters` / `build_production_adapters`: real construction path.
- `PipelineWorker.run_job`, `run_job_async`, `run_once`, `run_once_async`.
- `run_worker`, `run_processing_worker`, `run_production_worker`,
  `worker_entrypoint`.
- `WorkerResult`, `PreviewArtifacts`, and public-safe `WorkerFailure` metadata.

## Requirement mapping

- Fixed ordering and selected-model scope: fixed tuple; only `ocr`, `extract`,
  and `translate` read job-selected model keys. Supporting keys come from the
  immutable `PipelineConfig.supporting_models` fingerprint.
- Lease CAS/refresh: every state mutation carries current lease token;
  heartbeat task refreshes lease during adapter calls; stale lease exits as
  `lease_lost`.
- Resume/checksum: successful work artifacts are atomically promoted and
  recorded with digest/size. `resume_step` validates all checkpoints and
  clears the earliest invalid suffix.
- Failure safety: attempts expose duration, error type, retryability, and
  sanitized message; credentials, bodies, and absolute local paths are
  redacted.
- Validation retry: bounded by `validation_retry_limit`; retry attempts and
  terminal validation failure are observable.
- Cancellation: checked before and after remote output; cancelled output is
  not promoted or sent downstream and cancellation is recorded at boundary.
- Protected preview: PDF, source Markdown, summary JSON, and translated
  Markdown are atomically written below formal root and registered only under
  current lease; digest is derived from component digests.
- BibTeX: absent input yields `review_ready/not_provided`; unique match yields
  `review_ready/matched` and automatic binding; ambiguous/unmatched supplied
  input yields `needs_attention`.
- No publication, API, UI, or deployment work was added.

## TDD rounds

1. RED: black-box Worker contract tests were written first for tiny PDF,
   fixed order, failures, restart, corruption, validation retry, cancellation,
   heartbeat, model invalidation, preview, and BibTeX outcomes.
2. GREEN: orchestration/state/artifact implementation made the initial Worker
   suite pass; checkpoint and lease failures were then exercised through fake
   adapters.
3. RED: production-builder contract added with a real Paper TOML path and no
   extractor/translator injection; it initially exposed the injection-only
   construction gap.
4. GREEN: `adapters.py` now loads Paper config, resolves provider/model,
   stages `extract_documents`, constructs `MarkdownTranslator`, and resolves
   runtime credentials. Supporting validation/summary repair uses public
   Paper seams.
5. GREEN regression: focused Worker suite and all pipeline tests pass.

Tests assert only public inputs/outputs and observable state/artifact results;
temporary `pybtex` stubs are not used and no test mutates module state without
fixture restoration.

## Files

- `python/deepresearch_flow/pipeline/adapters.py`: real OCR/Paper/translator
  adapter construction.
- `python/deepresearch_flow/pipeline/steps.py`: adapter bundle, result/value
  objects, and invocation/serialization helpers.
- `python/deepresearch_flow/pipeline/worker.py`: lease/heartbeat,
  cancellation, fixed-step orchestration, retry/resume, and preview.
- `python/deepresearch_flow/pipeline/state.py`: expanded steps, CAS metadata,
  attempt observability, checkpoint validation, protected-artifact registration.
- `python/deepresearch_flow/pipeline/artifacts.py`: atomic protected formal
  artifacts and containment validation.
- `python/deepresearch_flow/pipeline/config.py`: validation retry and pinned
  supporting-model configuration.
- `python/deepresearch_flow/pipeline/__init__.py`: public exports.
- `python/deepresearch_flow/pipeline/tests/test_worker.py`: black-box Worker
  and production-construction tests.

## Verification

- `.venv/bin/pytest python/deepresearch_flow/pipeline/tests/test_worker.py -q`:
  **21 passed**.
- `.venv/bin/pytest python/deepresearch_flow/pipeline/tests -q`:
  **69 passed**.
- Touched old-module regression set (OCR, Paper config/routing/extraction,
  translator fixers): **158 passed**.
- Translator engine guardrail subset excluding the sandbox-hostile rumdl
  timeout case: **22 passed, 1 deselected**. The isolated rumdl timeout test
  exceeded the 30-second command window without producing a result; it is an
  existing external formatter test and unrelated to Task 3 changes.
- `.venv/bin/ruff check python/deepresearch_flow/pipeline`: passed.
- `.venv/bin/ty check` on production/orchestration source: passed.
- `git diff --check`: passed.

## Commit

Conventional Commit: `feat(pipeline): add resumable processing worker` (final commit is the latest `git log` entry).

## Risks / follow-up

- Worker orchestration remains intentionally concentrated in `worker.py`; real
  provider construction is separated into `adapters.py`, while public values
  and invocation helpers are in `steps.py`.
- Real extraction/translation still require configured provider credentials and
  declared `provider/model` values; no network call is made by tests.
- The existing rumdl timeout test can hang in this sandbox; production code
  already treats formatter timeout as a non-fatal repair fallback.
