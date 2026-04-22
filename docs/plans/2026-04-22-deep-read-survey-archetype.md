# Deep Read Survey Archetype Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `deep_read` classify each paper as `survey`, `method`, `system`, or `other`, then use that archetype to drive survey-specific prompt behavior without creating a new template.

**Architecture:** Add a new structured field, `paper_archetype`, to the `deep_read` schema and make `module_a` produce it together with `module_a` text. Later `deep_read` stages consume that archetype from prior outputs and switch between survey-oriented and non-survey instructions inside the existing `deep_read_user.j2` template.

**Tech Stack:** Python, JSON Schema, Jinja2 prompt templates, existing staged extraction flow, pytest.

---

### Task 1: Extend the deep_read schema and stage-A field contract

**Files:**
- Modify: `python/deepresearch_flow/paper/schemas/deep_read_schema.json`
- Modify: `python/deepresearch_flow/paper/template_registry.py`
- Test: `python/deepresearch_flow/paper/tests/test_extract_retry_planning.py`
- Create or modify: `python/deepresearch_flow/paper/tests/test_deep_read_prompt.py`

**Step 1: Write the failing tests**

Add black-box tests covering:
- `deep_read` schema includes required top-level field `paper_archetype`
- `deep_read` stage A requests `paper_archetype` and `module_a` together
- the archetype value is expected to be one of `survey`, `method`, `system`, `other`

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest -q \
  python/deepresearch_flow/paper/tests/test_extract_retry_planning.py \
  python/deepresearch_flow/paper/tests/test_deep_read_prompt.py
```

Expected: failures showing `paper_archetype` is absent from the schema and stage-A field list.

**Step 3: Write minimal implementation**

Update:
- `deep_read_schema.json` to require `paper_archetype`
- `template_registry.py` so `StageDefinition("module_a", ...)` becomes `["paper_archetype", "module_a"]`

**Step 4: Run tests to verify they pass**

Run the same test command again and confirm PASS.

**Step 5: Commit**

```bash
git add \
  python/deepresearch_flow/paper/schemas/deep_read_schema.json \
  python/deepresearch_flow/paper/template_registry.py \
  python/deepresearch_flow/paper/tests/test_extract_retry_planning.py \
  python/deepresearch_flow/paper/tests/test_deep_read_prompt.py
git commit -m "feat(paper): add deep read archetype field"
```

### Task 2: Make deep_read prompt classify the paper archetype

**Files:**
- Modify: `python/deepresearch_flow/paper/prompt_templates/deep_read_user.j2`
- Test: `python/deepresearch_flow/paper/tests/test_deep_read_prompt.py`

**Step 1: Write the failing tests**

Add black-box tests covering:
- stage-A prompt tells the model to output `paper_archetype`
- stage-A prompt defines the four allowed archetypes
- stage-A prompt asks for a single best-fit label rather than multiple labels

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest -q python/deepresearch_flow/paper/tests/test_deep_read_prompt.py
```

Expected: failures because the prompt does not yet mention `paper_archetype` classification rules.

**Step 3: Write minimal implementation**

Update `deep_read_user.j2` so:
- single-shot mode mentions `paper_archetype` in the required JSON fields
- stage-A mode explicitly instructs the model to classify the paper into one archetype
- prompt text defines `survey`, `method`, `system`, `other` in concise operational terms

**Step 4: Run tests to verify they pass**

Run the same pytest command and confirm PASS.

**Step 5: Commit**

```bash
git add \
  python/deepresearch_flow/paper/prompt_templates/deep_read_user.j2 \
  python/deepresearch_flow/paper/tests/test_deep_read_prompt.py
git commit -m "feat(paper): classify deep read paper archetypes"
```

### Task 3: Add survey-specific instructions to later deep_read modules

**Files:**
- Modify: `python/deepresearch_flow/paper/prompt_templates/deep_read_user.j2`
- Test: `python/deepresearch_flow/paper/tests/test_deep_read_prompt.py`

**Step 1: Write the failing tests**

Add black-box tests covering:
- when prior outputs indicate `paper_archetype = survey`, the prompt instructs modules C3/C4/C5/D/E/H to focus on taxonomy, comparison, benchmark coverage, and gap analysis
- non-survey prompt branches still keep method/system-oriented instructions
- survey branch no longer requires survey papers to explain training recipes or pseudocode as if they were method papers

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest -q python/deepresearch_flow/paper/tests/test_deep_read_prompt.py
```

Expected: failures because later-module instructions are still method-paper-centric.

**Step 3: Write minimal implementation**

Refine `deep_read_user.j2` to:
- read `paper_archetype` from `previous_outputs`
- branch module guidance for `survey`
- keep existing `method` / `system` guidance largely intact
- use `other` as a conservative fallback that avoids overclaiming method-specific details

**Step 4: Run tests to verify they pass**

Run the same pytest command and confirm PASS.

**Step 5: Commit**

```bash
git add \
  python/deepresearch_flow/paper/prompt_templates/deep_read_user.j2 \
  python/deepresearch_flow/paper/tests/test_deep_read_prompt.py
git commit -m "feat(paper): tailor deep read prompts for surveys"
```

### Task 4: Verify normalization and downstream compatibility

**Files:**
- Modify if needed: `python/deepresearch_flow/paper/extract.py`
- Test: `python/deepresearch_flow/paper/tests/test_deep_read_prompt.py`
- Test: `python/deepresearch_flow/paper/tests/test_web_markdown.py`

**Step 1: Write the failing tests**

Add black-box tests covering:
- extracted JSON containing `paper_archetype` survives response-key normalization
- existing render paths still work when a `deep_read` record includes `paper_archetype`
- render output does not need to display the field explicitly

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest -q \
  python/deepresearch_flow/paper/tests/test_deep_read_prompt.py \
  python/deepresearch_flow/paper/tests/test_web_markdown.py
```

Expected: failures only if some downstream path rejects the new field or stage output shape.

**Step 3: Write minimal implementation**

Only if required by tests:
- adjust normalization or compatibility code so `paper_archetype` is preserved
- do not change markdown rendering unless tests prove it is necessary

**Step 4: Run tests to verify they pass**

Run the same pytest command and confirm PASS.

**Step 5: Commit**

```bash
git add \
  python/deepresearch_flow/paper/extract.py \
  python/deepresearch_flow/paper/tests/test_deep_read_prompt.py \
  python/deepresearch_flow/paper/tests/test_web_markdown.py
git commit -m "fix(paper): preserve deep read archetype metadata"
```

### Task 5: Run focused verification

**Files:**
- Verify only

**Step 1: Run focused test suite**

Run:

```bash
uv run pytest -q \
  python/deepresearch_flow/paper/tests/test_extract_retry_planning.py \
  python/deepresearch_flow/paper/tests/test_deep_read_prompt.py \
  python/deepresearch_flow/paper/tests/test_web_markdown.py
```

Expected: PASS

**Step 2: Run one prompt-shape smoke check**

Run a small local check that builds `deep_read` messages for:
- stage A
- one later stage with `paper_archetype = survey`
- one later stage with `paper_archetype = method`

Expected: prompt text clearly differs between survey and non-survey branches.

**Step 3: Commit any remaining cleanup**

If there are residual non-functional prompt/test cleanups after the focused suite is green, commit them separately with a narrow message.
