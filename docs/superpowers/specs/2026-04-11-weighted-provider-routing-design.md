# Weighted Provider Routing + Mode Probe Design Spec

**Date:** 2026-04-11
**Status:** Draft
**Scope:** Replace the legacy provider key/model config with weighted provider resources and add a real mode-probing CLI.

## Overview

This change replaces the current provider configuration shape:

- `api_keys`
- `model_list`
- `structured_mode`

with a breaking new structure that separates:

1. provider endpoint pools (`base`)
2. provider model capability declarations (`models`)
3. default weighted main model routing (`main_model`)

The change also adds a new CLI command:

- `deepresearch-flow utils test-mode`

This command probes real model support for `json_schema` and `json_object` and can optionally write the detected results back into the config file.

## Goals

- Support weighted load balancing across models, URLs, and keys.
- Keep provider-local model capability declarations in config.
- Keep a single `--model` entrypoint for both single-model selection and weighted main-model overrides.
- Add a real probe command to verify mode support without trusting config declarations.

## Non-Goals

- Backward compatibility with the old provider config format.
- Automatic migration from old config to new config.
- Exhaustive probing of every `base + key` combination for a model.
- Support for `stream` probing in the first version of `utils test-mode`.

## Configuration

### Breaking change

The old fields are removed:

- `api_keys`
- `model_list`
- `structured_mode`

Configs using the old layout fail validation.

### Provider shape

Each provider remains a `[[providers]]` entry.

Each provider contains:

- `base`: weighted endpoint pool
- `models`: model capability declarations

### Example

```toml
main_model = [
  { model = "providerA/modelA1", weight = 4 },
  { model = "providerA/modelA2", weight = 1 },
  { model = "providerB/modelB1", weight = 3 }
]

[[providers]]
name = "providerA"
type = "openai_compatible"

base = [
  {
    url = "https://endpoint-a.example.com/v1",
    weight = 3,
    key = [
      { value = "env:KEY_A1", weight = 5 },
      { value = "env:KEY_A2", weight = 1 }
    ]
  },
  {
    url = "https://endpoint-b.example.com/v1",
    weight = 1,
    key = [
      { value = "env:KEY_B1", weight = 1 }
    ]
  }
]

models = [
  {
    model_name = "modelA1",
    is_stream = true,
    is_support_json_schema = true,
    is_support_json_object = true
  },
  {
    model_name = "modelA2",
    is_stream = true,
    is_support_json_schema = false,
    is_support_json_object = true
  }
]
```

## Field semantics

### `main_model`

Top-level default weighted model pool.

Each item contains:

- `model`: `provider/model_name`
- `weight`: positive integer

`main_model` defines weighted selection between models. It does not define URL or key routing.

When `main_model` contains exactly one item, runtime behavior is equivalent to using that same `provider/model` as a fixed model selection. The item still uses normal validation and may keep `weight = 1` for consistency.

### `providers[].base`

Provider-local weighted endpoint pool.

Each item contains:

- `url`: request base URL
- `weight`: positive integer
- `key`: weighted key list

### `providers[].base[].key`

Provider endpoint-local weighted key pool.

Each key contains:

- `value`: API key value or `env:VAR`
- `weight`: positive integer

Existing per-key quota and cooldown metadata remain attached at key level.

### `providers[].models`

Provider-local model declarations.

Each item contains:

- `model_name`
- `is_stream`
- `is_support_json_schema`
- `is_support_json_object`

These items declare capability only. They do not carry routing weight.

## Runtime selection

Runtime model execution uses three layers of weighted load balancing:

1. Select a model from `main_model`
2. Select a URL from the chosen provider's `base`
3. Select a key from the chosen base's `key`

This is weighted selection, not round-robin rotation.

## `--model` behavior

`--model` remains the only model-selection CLI parameter.

It supports three forms:

1. Single model reference: `provider/model`
2. Inline JSON main-model pool
3. File reference: `@path/to/main_model.json`

### Single model form

```bash
--model providerA/modelA1
```

This fixes execution to one model and bypasses weighted `main_model` selection.

URL and key selection still use weighted provider-local selection.

### Inline JSON form

```bash
--model '[{"model":"providerA/modelA1","weight":4},{"model":"providerB/modelB1","weight":3}]'
```

### File reference form

```bash
--model @main_model.json
```

The referenced file must contain the same JSON array shape as inline JSON.

## `--model` precedence

`--model` always has higher priority than config-file `main_model`.

Rules:

- `--model provider/model` overrides config and uses one fixed model
- `--model <json>` overrides config and uses the runtime JSON model pool
- `--model @file` overrides config and uses the JSON model pool loaded from file

## Validation

### Config validation

Config loading must fail when any of the following is true:

- `main_model` is missing or empty
- a `main_model[].model` does not resolve to a declared `providers[].models[]` entry
- a provider has empty or missing `base`
- a base has empty or missing `key`
- a provider has empty or missing `models`
- any `weight` is missing, non-integer, or not positive

### CLI validation

`--model` parsing must fail when:

- a single model is not in `provider/model` format
- a single model does not resolve to a declared `providers[].models[]` entry
- inline JSON is invalid JSON
- inline JSON is not an array
- an array item is missing `model` or `weight`
- an array item has a non-positive `weight`
- an array item `model` does not resolve to a declared `providers[].models[]` entry
- `@file` does not exist
- `@file` content is not valid JSON array

## Capability semantics

Model capability is defined per declared model:

- `is_support_json_schema`
- `is_support_json_object`
- `is_stream`

These flags belong to the provider-local model declaration and not to `main_model`, `base`, or `key`.

If runtime requests a mode that the selected model does not support, the tool should fail explicitly instead of silently downgrading to another mode.

## CLI: `deepresearch-flow utils test-mode`

### Purpose

`deepresearch-flow utils test-mode` verifies actual support for structured output modes by sending real requests.

It does not trust config declarations as the source of truth for the probe result.

### Command shape

```bash
deepresearch-flow utils test-mode \
  --config config.toml \
  --model providerA/modelA1 \
  --model providerB/modelB1
```

Initial supported modes:

- `json_schema`
- `json_object`

### Input model rules

- Every input model must be passed as `provider/model`
- Bare model names are rejected
- Every input model must already exist in `providers[].models[]`
- Unknown models fail immediately
- The command does not auto-create missing model declarations

### Probe behavior

For each requested `provider/model`, the command:

1. finds the declared provider
2. finds the declared model capability record
3. selects one `base` using the same weighted logic as runtime execution
4. selects one `key` under that base using the same weighted logic as runtime execution
5. performs real probe requests for `json_schema` and `json_object`

The first version probes one weighted `base + key` combination only.

It does not enumerate all URL and key combinations in the provider.

### Write-back behavior

The command exposes `--write-back` to persist probe results into the config file.

Without `--write-back`, the command only reports probe results and does not modify config.

When write-back is enabled:

- `json_schema` probe result updates `is_support_json_schema`
- `json_object` probe result updates `is_support_json_object`
- only the modes actually probed by the command are updated
- unrelated capability fields remain unchanged
- probe failures do not overwrite existing capability fields

### Probe failure behavior

If a probe request fails because of transport errors, authentication errors, provider-side request errors, or remote model rejection, the command treats that model probe as failed instead of inferring capability support.

Rules:

- a failed probe does not get converted into `false`
- a failed probe does not update capability fields during `--write-back`
- the command reports the failure reason for the affected `provider/model`
- the command exits non-zero if any requested model probe fails

### Risk note

Because `test-mode` probes one weighted `base + key` path only, the result represents the selected route's actual behavior at probe time.

It is not a guarantee that every `base + key` under the provider behaves the same way.

### Implementation notes

- Reuse the same weighted chooser logic for runtime execution and `utils test-mode`.
- Keep key-level quota/cooldown metadata attached to key objects.
- Continue resolving `env:` values at runtime.
- `env:VAR` resolution failure must raise an explicit error instead of silently skipping the key or provider entry.
- Keep `provider/model` as the canonical external identifier for models.
