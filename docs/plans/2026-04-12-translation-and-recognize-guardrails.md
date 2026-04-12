# Translation And Recognize Guardrails Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate confirmed content-corruption bugs in the translation and recognize pipelines by tightening protection boundaries, fixing math cleanup correctness, and adding regression tests for the full guardrail chain.

**Architecture:** Treat this as two independent repair tracks plus one shared verification track. First harden the `translator` pipeline so pre-fixers cannot mutate protected content, math and placeholders are fully frozen/restored, and no post-restore formatter can silently rewrite recovered content. In parallel, fix `recognize/math.py` so formula cleanup no longer invents LaTeX commands or corrupts repaired formulas. Finish by adding integration-style tests that exercise the real roundtrip instead of only unit-level helpers.

**Tech Stack:** Python 3.14, click, httpx, pytest, existing translator/recognize modules under `python/deepresearch_flow/`

---

## Scope

This plan covers:

- `python/deepresearch_flow/translator/fixers.py`
- `python/deepresearch_flow/translator/protector.py`
- `python/deepresearch_flow/translator/placeholder.py`
- `python/deepresearch_flow/translator/engine.py`
- `python/deepresearch_flow/translator/segment.py`
- `python/deepresearch_flow/translator/tests/`
- `python/deepresearch_flow/recognize/math.py`
- `tests/test_math.py`

This plan does not cover:

- `openspec/`
- new product features
- prompt redesign beyond minimal consistency fixes
- unrelated OCR / paper rendering refactors

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `python/deepresearch_flow/translator/fixers.py` | Modify | Move or guard unsafe pre-protect fixers; narrow HTML/table/math cleanup scope |
| `python/deepresearch_flow/translator/protector.py` | Modify | Complete freeze matrix, especially `\(...\)` and brittle block detection |
| `python/deepresearch_flow/translator/placeholder.py` | Modify | Add safer restore validation hooks and unknown-placeholder detection helpers |
| `python/deepresearch_flow/translator/engine.py` | Modify | Reorder restore/post-process flow and enforce placeholder validation |
| `python/deepresearch_flow/translator/segment.py` | Modify | Add only minimal defensive segmentation changes if required by tests |
| `python/deepresearch_flow/translator/config.py` | Modify | Wire `strict_placeholder_check` into the real execution path if it remains a config-driven behavior |
| `python/deepresearch_flow/translator/tests/test_fixers.py` | Modify | Add missing regression coverage for pre-protect corruption and HTML/code boundaries |
| `python/deepresearch_flow/translator/tests/test_protector.py` | Create | Add protector/placeholder roundtrip tests |
| `python/deepresearch_flow/translator/tests/test_engine_translate_guardrails.py` | Create | Add end-to-end guardrail tests for protect -> translate -> restore flow |
| `python/deepresearch_flow/recognize/math.py` | Modify | Fix confirmed cleanup and repair pipeline correctness bugs |
| `tests/test_math.py` | Modify | Expand unit coverage for uncovered critical/high branches |

---

## Task 1: Lock Down Translator Pre-Protect Fixers

**Files:**
- Modify: `python/deepresearch_flow/translator/fixers.py`
- Modify: `python/deepresearch_flow/translator/tests/test_fixers.py`

- [ ] **Step 1: Write failing tests for unsafe pre-protect mutations**

Add tests covering:

- reference markers inside math or code must not be rewritten
- URLs/emails/phones inside protected-looking content must not be rewritten
- `<td>...</td>` inside fenced code must remain unchanged
- `fix_markdown()` must not mutate `\(...\)` inline math before protection

- [ ] **Step 2: Run the fixer tests and confirm they fail**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/test_fixers.py -q
```

Expected: FAIL on the new regression cases.

- [ ] **Step 3: Reduce the pre-protect fixer surface**

In `fixers.py`:

- keep only demonstrably safe text-normalization rules before protection
- move `ReferenceProcessor.fix_references()` and `LinkProcessor.fix_links()` behind protection, or add robust protected-range handling before they run
- make `fix_html_table_math_spaces()` skip fenced code and non-table examples
- expand protected-range detection for helper fixers so inline code and `\(...\)` are not touched

- [ ] **Step 4: Re-run fixer tests and make them pass**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/test_fixers.py -q
```

Expected: PASS

- [ ] **Step 5: Commit the fixer hardening**

Run:

```bash
git add python/deepresearch_flow/translator/fixers.py python/deepresearch_flow/translator/tests/test_fixers.py
git commit -m "fix(translator): guard pre-protect fixers"
```

---

## Task 2: Complete Freeze/Restore Coverage

**Files:**
- Modify: `python/deepresearch_flow/translator/protector.py`
- Modify: `python/deepresearch_flow/translator/placeholder.py`
- Create: `python/deepresearch_flow/translator/tests/test_protector.py`

- [ ] **Step 1: Write failing roundtrip tests for protector and placeholders**

Add tests covering:

- `\(...\)` inline math is frozen and restored unchanged
- fenced code containing ````` ```json ````` or `<td>` examples is preserved
- unknown `__PH_...__` residuals are detected
- restore does not silently leave unexpanded placeholders behind

- [ ] **Step 2: Run the new protector tests and separate confirmed failures from exploratory checks**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/test_protector.py -q
```

Expected:

- confirmed bug regressions should FAIL
- exploratory coverage checks may already PASS if the current implementation already handles that edge

Record which cases are true regressions versus already-supported behavior before changing code.

- [ ] **Step 3: Complete the freeze matrix**

In `protector.py`:

- add explicit support for `\(...\)` inline math
- tighten code-fence closing logic so embedded fence-like lines do not terminate early
- review HTML freezing rules for void tags and line-position assumptions
- keep changes minimal and test-driven; do not redesign placeholder format unless tests require it

- [ ] **Step 4: Add restore-time validation helpers**

In `placeholder.py`:

- add helper(s) to detect unresolved placeholder-like tokens
- expose a way for the engine to fail fast on missing or unknown placeholders after restore

- [ ] **Step 5: Re-run protector tests and make them pass**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/test_protector.py -q
```

Expected: PASS

- [ ] **Step 6: Commit the freeze/restore fixes**

Run:

```bash
git add python/deepresearch_flow/translator/protector.py python/deepresearch_flow/translator/placeholder.py python/deepresearch_flow/translator/tests/test_protector.py
git commit -m "fix(translator): complete freeze and restore guardrails"
```

---

## Task 3: Repair Translator Execution Order

**Files:**
- Modify: `python/deepresearch_flow/translator/engine.py`
- Optionally modify: `python/deepresearch_flow/translator/segment.py`
- Create: `python/deepresearch_flow/translator/tests/test_engine_translate_guardrails.py`

- [ ] **Step 1: Write failing engine-level guardrail tests**

Add tests for:

- `protect -> translate -> unprotect` keeps math/code/link placeholders stable through retries/fallback
- post-format does not rewrite restored protected content
- unknown placeholder residuals fail validation
- failed nodes falling back to origin still preserve placeholder and protection invariants

- [ ] **Step 2: Run the engine guardrail tests and confirm they fail**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/test_engine_translate_guardrails.py -q
```

Expected: FAIL because restore currently happens before post-format/normalize and strict placeholder checks are not enforced.

- [ ] **Step 3: Reorder the translator pipeline**

In `engine.py` and `config.py`:

- read the concrete call chain around `fix_markdown()`, `_format_markdown("pre")`, `protect()`, `unprotect()`, `_format_markdown("post")`, and `_normalize_markdown_blocks()` before editing
- make the restore/post-process order safe for recovered protected content
- wire `strict_placeholder_check` into the actual execution path if it remains config-driven
- reject unknown `__PH_...__` leftovers after restore
- keep fallback/retry behavior unchanged except where validation must now fail fast

- [ ] **Step 4: Touch `segment.py` only if a test proves leakage through segmentation**

If current tests still fail because unprotected block types are segmented as prose, add the smallest defensive change necessary. Otherwise leave `segment.py` alone.

- [ ] **Step 5: Re-run the engine guardrail tests and make them pass**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/test_engine_translate_guardrails.py -q
```

Expected: PASS

- [ ] **Step 6: Commit the execution-order fix**

Run:

```bash
git add python/deepresearch_flow/translator/engine.py python/deepresearch_flow/translator/segment.py python/deepresearch_flow/translator/tests/test_engine_translate_guardrails.py
git commit -m "fix(translator): protect restored content from post-processing"
```

---

## Task 4: Fix Recognize Math Cleanup Correctness

**Files:**
- Modify: `python/deepresearch_flow/recognize/math.py`
- Modify: `tests/test_math.py`

- [ ] **Step 1: Write failing tests for the confirmed critical/high issues**

Extend `tests/test_math.py` to cover:

- `_restore_json_escaped_commands()` must not reinterpret ordinary `\n` / `\r` line breaks as LaTeX commands
- `_normalize_unknown_commands()` must preserve standard uppercase commands such as `\Rightarrow`, `\Big`, `\Re`, `\Im`
- nested braces inside `\text{...}` are handled safely
- `strip_wrapping_delimiters()` rejects empty wrapped formulas
- `_finalize_repaired_formula()` keeps the returned formula aligned with the reported errors
- `asyncio.gather(..., return_exceptions=True)` result handling is resilient to non-`Exception` failures

- [ ] **Step 2: Run the math tests and confirm they fail**

Run:

```bash
pytest tests/test_math.py -q
```

Expected: FAIL on the new regression cases.

- [ ] **Step 3: Fix the confirmed correctness bugs in `math.py`**

Implement only the reviewed fixes:

- narrow `_CONTROL_CHAR_COMMAND_PREFIXES`
- expand `_KNOWN_LATEX_COMMANDS` and/or replace the uppercase-command heuristic
- make `\text{...}` handling brace-safe
- remove or rewrite dead/redundant cleanup rules shadowed by the new control-character restoration path
- make `strip_wrapping_delimiters()` reject empty formula payloads
- make `_finalize_repaired_formula()` report errors for the same returned formula text
- harden gather-result handling and structured-mode fallback accounting

- [ ] **Step 4: Re-run math tests and make them pass**

Run:

```bash
pytest tests/test_math.py -q
```

Expected: PASS

- [ ] **Step 5: Commit the math fixes**

Run:

```bash
git add python/deepresearch_flow/recognize/math.py tests/test_math.py
git commit -m "fix(recognize): harden math cleanup and repair"
```

---

## Task 5: Run Cross-Track Regression Checks

**Files:**
- No new code unless a regression forces a minimal follow-up change

- [ ] **Step 1: Run the focused regression suite**

Run:

```bash
pytest \
  python/deepresearch_flow/translator/tests/test_fixers.py \
  python/deepresearch_flow/translator/tests/test_protector.py \
  python/deepresearch_flow/translator/tests/test_engine_translate_guardrails.py \
  tests/test_math.py \
  tests/test_mermaid.py -q
```

Expected: PASS

- [ ] **Step 2: Run any existing translator CLI tests that still apply**

Run:

```bash
pytest python/deepresearch_flow/translator/tests/test_cli_translate.py -q
```

Expected: PASS

- [ ] **Step 3: Record residual risk explicitly**

If any of these remain unaddressed after all green tests, note them in the implementation summary instead of silently accepting them:

- HTML protection for uncommon constructs
- segmenter behavior for future unsupported block types
- exact policy for unknown placeholder-like literals in user-authored prose

- [ ] **Step 4: Commit any final test-only adjustments**

Run:

```bash
git add python/deepresearch_flow/translator/tests tests
git commit -m "test: expand translation and recognize guardrails coverage"
```

Only do this step if there are remaining staged test-only changes after prior commits.

---

## Acceptance Criteria

- No pre-protect fixer mutates content that will later be frozen as code, math, URLs, links, or placeholders.
- `MarkdownProtector` freezes and restores `$...$`, `$$...$$`, `\[...\]`, and `\(...\)` consistently.
- Unknown or unresolved placeholder tokens are treated as failures, not silently emitted.
- Restored protected content is not modified by post-formatting or block normalization.
- `recognize/math.py` no longer invents LaTeX commands from normal line breaks or strip valid uppercase commands.
- The focused regression suite passes cleanly.
- All modified files reach at least 80% line coverage in the focused test suite, or the implementation summary explicitly lists the remaining uncovered lines and why they were deferred.

---

## Execution Notes

- Keep translator and recognize changes in separate commits even if implemented in one session.
- Task 1 -> Task 2 -> Task 3 must run in order; Task 4 may run in parallel in an isolated worktree if desired.
- Do not refactor unrelated helpers while touching the reviewed files.
- When a reviewed risk cannot be reproduced by a failing test, document it as deferred instead of “fixing” it speculatively.
