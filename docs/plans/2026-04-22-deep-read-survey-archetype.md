# Deep Read Survey Archetype Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `deep_read` classify each paper as `survey`, `method`, `system`, or `other`, then use that archetype to drive survey-specific prompt behavior without creating a new template.

**Architecture:** Add a new structured field, `paper_archetype`, to the `deep_read` schema and make `module_a` produce it together with `module_a` text. Later `deep_read` stages consume that archetype from prior outputs and switch between survey-oriented and non-survey instructions inside the existing `deep_read_user.j2` template.
The existing staged extraction flow continues to use derived per-stage schemas for validation, while single-shot extraction keeps full-schema validation. Prompt branching should use an explicit archetype hint passed through the Jinja context rather than trying to parse the existing `previous_outputs` string.

**Tech Stack:** Python, JSON Schema, Jinja2 prompt templates, existing staged extraction flow, pytest.

---

### Task 1: Extend the deep_read schema and stage-A field contract

**Files:**
- Modify: `python/deepresearch_flow/paper/schemas/deep_read_schema.json`
- Modify: `python/deepresearch_flow/paper/template_registry.py`
- Modify: `python/deepresearch_flow/paper/extract.py`
- Test: `python/deepresearch_flow/paper/tests/test_extract_retry_planning.py`
- Create or modify: `python/deepresearch_flow/paper/tests/test_deep_read_prompt.py`

**Step 1: Write the failing tests**

Add black-box tests covering:
- `deep_read` schema includes required top-level field `paper_archetype`
- `paper_archetype` is constrained to `survey`, `method`, `system`, `other`
- `deep_read` stage A requests `paper_archetype` and `module_a` together
- a minimal stage-A payload containing `paper_archetype` and `module_a` passes stage-schema validation
- a full single-shot payload including `paper_archetype` passes full-schema validation

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
- `extract.py` tests/fixtures only as needed to reflect the stage-schema contract explicitly

**Step 4: Run tests to verify they pass**

Run the same test command again and confirm PASS.

**Step 5: Commit implementation and tests separately**

```bash
git add \
  python/deepresearch_flow/paper/schemas/deep_read_schema.json \
  python/deepresearch_flow/paper/template_registry.py \
  python/deepresearch_flow/paper/extract.py
git commit -m "feat(paper): add deep read archetype field"

git add \
  python/deepresearch_flow/paper/tests/test_extract_retry_planning.py \
  python/deepresearch_flow/paper/tests/test_deep_read_prompt.py
git commit -m "test(paper): cover deep read archetype schema"
```

### Task 2: Expose archetype hint to the prompt context

**Files:**
- Modify: `python/deepresearch_flow/paper/template_registry.py`
- Modify: `python/deepresearch_flow/paper/extract.py`
- Modify: `python/deepresearch_flow/paper/prompt_templates/deep_read_user.j2`
- Test: `python/deepresearch_flow/paper/tests/test_deep_read_prompt.py`

**Step 1: Write the failing tests**

Add black-box tests covering:
- later-stage deep-read prompts can receive an explicit archetype hint through prompt construction
- prompt branching does not depend on parsing `previous_outputs` as structured Jinja data
- single-shot prompt field enumeration includes `paper_archetype`

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest -q python/deepresearch_flow/paper/tests/test_deep_read_prompt.py
```

Expected: failures because the prompt does not yet mention `paper_archetype` classification rules.

**Step 3: Write minimal implementation**

Update prompt plumbing so:
- `load_prompt_templates(...)` and its callers accept an optional `paper_archetype_hint`
- later stages pass the resolved archetype into Jinja as that hint
- `previous_outputs` remains the existing JSON string for prose/reference compatibility
- single-shot mode mentions `paper_archetype` in the required JSON field list

**Step 4: Run tests to verify they pass**

Run the same pytest command and confirm PASS.

**Step 5: Commit implementation and tests separately**

```bash
git add \
  python/deepresearch_flow/paper/template_registry.py \
  python/deepresearch_flow/paper/extract.py \
  python/deepresearch_flow/paper/prompt_templates/deep_read_user.j2 \
git commit -m "feat(paper): pass archetype hints to deep read prompts"

git add \
  python/deepresearch_flow/paper/tests/test_deep_read_prompt.py
git commit -m "test(paper): cover deep read prompt hints"
```

### Task 3: Make deep_read prompt classify the paper archetype

**Files:**
- Modify: `python/deepresearch_flow/paper/prompt_templates/deep_read_user.j2`
- Test: `python/deepresearch_flow/paper/tests/test_deep_read_prompt.py`

**Step 1: Write the failing tests**

Add black-box tests covering:
- stage-A prompt tells the model to output `paper_archetype`
- the four allowed archetype literals appear in prompt text
- stage-A prompt asks for a single best-fit label
- the compact justification requirement for `module_a` is visible in the prompt

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest -q python/deepresearch_flow/paper/tests/test_deep_read_prompt.py
```

Expected: failures because the prompt does not yet define the classification contract clearly enough.

**Step 3: Write minimal implementation**

Update `deep_read_user.j2` so:
- stage-A mode explicitly instructs the model to classify the paper into one archetype
- prompt text defines `survey`, `method`, `system`, `other` in concise operational terms
- `module_a` is told to include only a brief justification rather than a long extra essay

**Step 4: Run tests to verify they pass**

Run the same pytest command and confirm PASS.

**Step 5: Commit implementation and tests separately**

```bash
git add python/deepresearch_flow/paper/prompt_templates/deep_read_user.j2
git commit -m "feat(paper): classify deep read paper archetypes"

git add python/deepresearch_flow/paper/tests/test_deep_read_prompt.py
git commit -m "test(paper): cover deep read archetype prompts"
```

### Task 4: Add survey-specific instructions to later deep_read modules

**Files:**
- Modify: `python/deepresearch_flow/paper/prompt_templates/deep_read_user.j2`
- Test: `python/deepresearch_flow/paper/tests/test_deep_read_prompt.py`

**Step 1: Write the failing tests**

Add black-box tests covering:
- when the archetype hint is `survey`, the prompt instructs modules C3/C4/C5/D/E/H to focus on taxonomy, comparison, benchmark coverage, and gap analysis
- the survey branch positively includes survey-oriented keywords such as taxonomy, classification, benchmark, protocol, or coverage in the relevant modules
- non-survey prompt branches still keep method/system-oriented instructions
- the method branch still includes the original method-paper anchors, so compatibility is preserved

**Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest -q python/deepresearch_flow/paper/tests/test_deep_read_prompt.py
```

Expected: failures because later-module instructions are still method-paper-centric.

**Step 3: Write minimal implementation**

Refine `deep_read_user.j2` to:
- read the archetype from the dedicated Jinja hint variable
- branch module guidance for `survey`
- keep existing `method` / `system` guidance largely intact
- use `other` as a conservative fallback that avoids overclaiming method-specific details

**Step 4: Run tests to verify they pass**

Run the same pytest command and confirm PASS.

**Step 5: Commit implementation and tests separately**

```bash
git add python/deepresearch_flow/paper/prompt_templates/deep_read_user.j2
git commit -m "feat(paper): tailor deep read prompts for surveys"

git add python/deepresearch_flow/paper/tests/test_deep_read_prompt.py
git commit -m "test(paper): cover survey-specific deep read prompts"
```

### Task 5: Verify normalization and downstream compatibility

**Files:**
- Modify if needed: `python/deepresearch_flow/paper/extract.py`
- Test: `python/deepresearch_flow/paper/tests/test_deep_read_prompt.py`
- Test: `python/deepresearch_flow/paper/tests/test_web_markdown.py`

**Step 1: Write the failing tests**

Add black-box tests covering:
- extracted JSON containing `paper_archetype` survives response-key normalization
- existing render paths still work when a `deep_read` record includes `paper_archetype`
- render output does not need to display the field explicitly
- adding `paper_archetype` to an existing method-paper record does not change the rendered markdown bytes

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

**Step 5: Commit implementation and tests separately**

```bash
git add python/deepresearch_flow/paper/extract.py
git commit -m "fix(paper): preserve deep read archetype metadata"

git add \
  python/deepresearch_flow/paper/tests/test_deep_read_prompt.py \
  python/deepresearch_flow/paper/tests/test_web_markdown.py
git commit -m "test(paper): verify deep read archetype compatibility"
```

### Task 6: Run focused verification

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

Expected: prompt text clearly differs between survey and non-survey branches, and method-path text remains stable.

**Step 3: Commit any remaining cleanup**

If there are residual non-functional prompt/test cleanups after the focused suite is green, commit them separately with a narrow message.
