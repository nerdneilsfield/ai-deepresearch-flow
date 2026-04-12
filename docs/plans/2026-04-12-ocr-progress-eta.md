# OCR Progress ETA Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add interactive CLI-only `tqdm` progress output with ETA for `deepresearch_flow` OCR runs.

**Architecture:** Keep progress rendering in the CLI layer so the OCR library remains usable from non-terminal callers without terminal UI side effects. Pass an optional progress object into `run_ocr()`, update it once per discovered file regardless of processed, skipped, or failed outcome, and let `tqdm` compute ETA from completed work.

**Tech Stack:** Python 3.12, Click, `tqdm`, pytest, unittest.mock

---

### Task 1: Cover runner progress updates

**Files:**
- Modify: `python/deepresearch_flow/ocr/tests/test_runner.py`
- Modify: `python/deepresearch_flow/ocr/runner.py`

**Step 1: Write the failing test**

Add tests that pass a fake progress object into `run_ocr()` and assert it receives one update per input file for processed, skipped, and failed outcomes.

**Step 2: Run test to verify it fails**

Run: `pytest python/deepresearch_flow/ocr/tests/test_runner.py -v`
Expected: FAIL because `run_ocr()` does not accept or update progress yet.

**Step 3: Write minimal implementation**

Add an optional progress parameter to `run_ocr()` and update it in a `finally` block or equivalent single-exit path so every file advances the bar exactly once.

**Step 4: Run test to verify it passes**

Run: `pytest python/deepresearch_flow/ocr/tests/test_runner.py -v`
Expected: PASS

### Task 2: Wire interactive CLI progress creation

**Files:**
- Modify: `python/deepresearch_flow/recognize/cli.py`
- Modify: `python/deepresearch_flow/ocr/tests/test_cli.py`

**Step 1: Write the failing test**

Add CLI tests that assert the OCR subcommand creates a `tqdm` bar only when running in an interactive terminal and passes it to `run_ocr()`.

**Step 2: Run test to verify it fails**

Run: `pytest python/deepresearch_flow/ocr/tests/test_cli.py -v`
Expected: FAIL because the OCR CLI does not create or pass a progress bar yet.

**Step 3: Write minimal implementation**

Detect interactive terminal output in the OCR CLI command, create `tqdm(total=len(discover_files(...)), desc="ocr", unit="file")`, pass it to `run_ocr()`, and always close it.

**Step 4: Run test to verify it passes**

Run: `pytest python/deepresearch_flow/ocr/tests/test_cli.py -v`
Expected: PASS

### Task 3: Run focused verification

**Files:**
- Verify only

**Step 1: Run focused OCR tests**

Run: `pytest python/deepresearch_flow/ocr/tests/test_runner.py python/deepresearch_flow/ocr/tests/test_cli.py -v`
Expected: PASS

**Step 2: Run lint-style smoke if needed**

Run: `python -m pytest python/deepresearch_flow/ocr/tests -v`
Expected: PASS or clearly reported unrelated pre-existing failures.
