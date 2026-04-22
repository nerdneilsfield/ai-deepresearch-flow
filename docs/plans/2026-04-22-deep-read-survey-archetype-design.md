# Deep Read Survey Archetype Design

## Context

`deep_read` currently assumes a method-paper reading pattern almost everywhere:

- module prompts emphasize pipelines, training settings, pseudocode, failure modes, and performance trade-offs
- only one small branch is survey-aware: the prompt asks for a taxonomy graph for survey/review papers
- there is no structured field that records how the model classified the paper

That means survey papers are often pushed into the wrong output shape. The model may still produce usable text, but it is being asked to explain training recipes and method flow when the actual paper is synthesizing prior work, benchmark practice, and open problems.

## Requirements

- Keep `deep_read` as a single built-in template.
- Let the model decide whether the paper is a `survey`, `method`, `system`, or `other`.
- Persist that judgment in structured JSON so it is visible, testable, and reusable.
- Make survey papers follow a survey-oriented reading frame across the relevant modules.
- Preserve the current behavior for non-survey papers as much as possible.
- Avoid changing the rendered markdown format unless there is a strong reason.
- Maintain compatibility with the existing staged extraction flow and current `deep_read` render path.

## Non-Goals

- Adding a separate `deep_read_survey` template.
- Building a classifier outside the extraction model.
- Reworking snapshot/search/render pipelines to special-case survey papers.
- Redesigning `deep_read_phi`.
- Introducing a different archetype field name for `deep_read_phi`; if phi adopts this later, it should reuse `paper_archetype`.

## Recommended Approach

Add a small structured field, `paper_archetype`, to the `deep_read` schema and make `module_a` responsible for producing it. Subsequent `deep_read` stages should read that archetype from prior outputs and switch instructions accordingly.

This keeps the model in control of the judgment while making the decision observable and stable across multi-stage extraction.

## Structured Output Change

Add a new required top-level string field:

```json
"paper_archetype": "survey" | "method" | "system" | "other"
```

Rules:

- `survey`: the main contribution is synthesis, taxonomy, comparison, benchmark aggregation, trend analysis, or gap identification across prior work
- `method`: the main contribution is a new algorithm, model, or learning procedure
- `system`: the main contribution is a platform, architecture, accelerator, system design, or engineering stack
- `other`: papers that do not fit the above cleanly

`paper_archetype` should be:

- present in `properties`
- required in the base `deep_read` schema
- constrained with `enum: ["survey", "method", "system", "other"]`

This avoids downstream normalization ambiguity and keeps the field stable for later prompt branching.

### Stage-schema compatibility

The base `deep_read` schema remains a full-document schema. Staged extraction does not validate stage outputs against that full schema directly; the extract pipeline builds a smaller stage schema from the current stage field list.

That means compatibility depends on two things landing together:

1. add `paper_archetype` to the base schema properties/required list
2. add `paper_archetype` to the stage-A field list so the derived stage schema accepts it

The change must preserve the current staged behavior where partial stage payloads validate successfully while single-shot payloads still validate against the full schema.

## Stage Wiring

### Module A

`module_a` becomes the archetype decision point.

Instead of returning only `module_a`, stage A should return:

- `paper_archetype`
- `module_a`

`module_a` must instruct the model to:

1. classify the paper into one of the four archetypes
2. give a compact justification inside the prose of module A
3. avoid hedging with multiple labels

To avoid bloating `module_a`, the justification should stay short, for example one brief subsection or 1-2 bullets. This change does not add a separate `paper_archetype_reason` field because the goal is to minimize schema churn and keep compatibility tight.

### Later modules

Subsequent stages should treat the resolved archetype as the active reading mode and adapt instructions accordingly.

The model may mention uncertainty in prose, but the structured field stays singular.

### Single-shot mode

Single-shot `deep_read` must also require `paper_archetype`.

Today the single-shot prompt explicitly enumerates the expected JSON fields. This change must update that list so single-shot output includes `paper_archetype` alongside the existing `module_*` fields; otherwise single-shot extraction will fail full-schema validation.

## Template Context Plumbing

`previous_outputs` is currently passed into the prompt as a JSON string for reference text, not as a structured Jinja object. That is fine for prose context, but it is not sufficient for template branching.

The recommended compatibility-preserving solution is:

- keep `previous_outputs` as the existing JSON string for display/reference
- add one separate optional Jinja context variable, for example `paper_archetype_hint`
- default that hint to an empty string so existing templates remain unaffected

For `deep_read`:

- stage A runs without a hint and produces `paper_archetype`
- later stages receive `paper_archetype_hint` resolved from prior stage state
- single-shot mode does not need the hint for branching because it produces the classification in one pass

This is less invasive than converting `previous_outputs` into a structured object and keeps compatibility with the current prompt plumbing.

## Prompt Behavior by Archetype

### Survey

Survey papers should no longer be forced into a method-paper frame.

The prompt should shift emphasis as follows:

- `module_b`: scope, inclusion boundary, taxonomy axes, representative work coverage
- `module_c3`: classification framework and comparison dimensions, not implementation pipeline
- `module_c4`: datasets/benchmarks/evaluation protocols covered by the survey, not training recipe details
- `module_c5`: cross-paper comparison results, consensus/disagreement, benchmark interpretation
- `module_d`: taxonomy deep-dive, representative clusters, table/figure interpretation, coverage structure
- `module_e`: coverage bias, taxonomy assumptions, blind spots, future survey update directions
- `module_h`: prioritize taxonomy figures, comparison tables, benchmark summary tables, timeline/grouping charts

Survey-specific graph requirements should explicitly prefer:

- taxonomy graph
- representative-work grouping graph
- benchmark/protocol relationship graph when relevant

### Method / System / Other

Keep the current `deep_read` structure mostly intact.

- `method`: retain pipeline/flow/data/training emphasis
- `system`: retain architecture/dataflow/deployment emphasis
- `other`: keep the general deep-read structure, but avoid overclaiming missing method-specific details

## Rendering and Compatibility

The markdown render template does not need to surface `paper_archetype` initially.

Reasons:

- avoids unnecessary output churn
- keeps the change focused on extraction quality and structured metadata
- allows downstream surfaces to adopt the field later if needed

Because the schema already permits extra top-level metadata such as `output_language`, adding `paper_archetype` is low-risk for existing consumers that read JSON records directly.

Compatibility expectations:

- rendered markdown should remain unchanged for existing non-survey records except for any internal JSON metadata additions
- adding `paper_archetype` to a `deep_read` JSON record must not break current markdown rendering
- this change should not require snapshot/search/web template updates in the first iteration

## Archetype Stability and Correction Path

The first implementation should keep correction behavior simple and compatible with existing extraction controls:

- if stage A misclassifies a paper, rerun `module_a` through the existing forced-stage path, then rerun dependent stages
- manual JSON override of `paper_archetype` is not part of this change

This keeps the change small while still giving a practical recovery path. A future follow-up may add explicit manual archetype override if real usage shows that stage-A misclassification needs a stronger correction mechanism.

## Files Affected

- `python/deepresearch_flow/paper/schemas/deep_read_schema.json`
- `python/deepresearch_flow/paper/template_registry.py`
- `python/deepresearch_flow/paper/prompt_templates/deep_read_user.j2`
- `python/deepresearch_flow/paper/extract.py`
- `python/deepresearch_flow/paper/tests/test_extract_retry_planning.py`
- new or updated prompt/schema black-box tests under `python/deepresearch_flow/paper/tests/`

## Testing Strategy

Tests should stay black-box:

- schema accepts `paper_archetype` with the required enum
- stage A requests `paper_archetype` together with `module_a`
- stage-A partial payloads validate successfully against the derived stage schema
- full single-shot payloads validate successfully against the full schema
- `deep_read` prompt text includes survey-specific instructions when the archetype is survey
- non-survey instructions remain available for `method`/`system`
- later-stage prompt branching uses the dedicated archetype hint variable rather than parsing `previous_outputs`
- response normalization preserves `paper_archetype`
- existing non-survey render output remains unchanged when `paper_archetype` is present

## Success Criteria

- `deep_read` outputs a stable `paper_archetype`
- survey papers are guided toward taxonomy/comparison/gap-analysis outputs rather than forced method details
- existing non-survey `deep_read` behavior is preserved except for the new classification field
- current staged extraction and single-shot extraction both continue to validate successfully
- no new template is introduced
