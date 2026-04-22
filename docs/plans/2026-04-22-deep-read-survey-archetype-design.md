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

## Non-Goals

- Adding a separate `deep_read_survey` template.
- Building a classifier outside the extraction model.
- Reworking snapshot/search/render pipelines to special-case survey papers.
- Redesigning `deep_read_phi`.

## Recommended Approach

Add a small structured field, `paper_archetype`, to the `deep_read` schema and make `module_a` responsible for producing it. Subsequent `deep_read` stages should read that archetype from prior outputs and switch instructions accordingly.

This keeps the model in control of the judgment while making the decision observable and stable across multi-stage extraction.

## Structured Output Change

Add a new optional-but-present top-level string field:

```json
"paper_archetype": "survey" | "method" | "system" | "other"
```

Rules:

- `survey`: the main contribution is synthesis, taxonomy, comparison, benchmark aggregation, trend analysis, or gap identification across prior work
- `method`: the main contribution is a new algorithm, model, or learning procedure
- `system`: the main contribution is a platform, architecture, accelerator, system design, or engineering stack
- `other`: papers that do not fit the above cleanly

`paper_archetype` should be required in the `deep_read` schema so it is always available once extraction succeeds.

## Stage Wiring

### Module A

`module_a` becomes the archetype decision point.

Instead of returning only `module_a`, stage A should return:

- `paper_archetype`
- `module_a`

`module_a` must instruct the model to:

1. classify the paper into one of the four archetypes
2. give short evidence inside the prose of module A
3. avoid hedging with multiple labels

### Later modules

Subsequent stages should treat `paper_archetype` from `previous_outputs` as the active reading mode and adapt instructions accordingly.

The model may mention uncertainty in prose, but the structured field stays singular.

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

## Files Affected

- `python/deepresearch_flow/paper/schemas/deep_read_schema.json`
- `python/deepresearch_flow/paper/template_registry.py`
- `python/deepresearch_flow/paper/prompt_templates/deep_read_user.j2`
- possibly `python/deepresearch_flow/paper/tests/test_extract_retry_planning.py` if stage-A field expectations need updates
- new or updated prompt/schema black-box tests under `python/deepresearch_flow/paper/tests/`

## Testing Strategy

Tests should stay black-box:

- schema accepts `paper_archetype`
- stage A requests `paper_archetype` together with `module_a`
- `deep_read` prompt text includes survey-specific instructions when the archetype is survey
- non-survey instructions remain available for `method`/`system`
- response normalization preserves `paper_archetype`

## Success Criteria

- `deep_read` outputs a stable `paper_archetype`
- survey papers are guided toward taxonomy/comparison/gap-analysis outputs rather than forced method details
- existing non-survey `deep_read` behavior is preserved except for the new classification field
- no new template is introduced
