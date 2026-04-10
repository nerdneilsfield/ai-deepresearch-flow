# Weighted Provider Routing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the legacy provider config with weighted model/base/key routing, keep a single `--model` entrypoint, and add `deepresearch-flow utils test-mode` for real mode probing.

**Architecture:** Convert shared provider config loading from the old `api_keys + model_list + structured_mode` shape into a new `providers.base + providers.models + main_model` model. Centralize weighted routing and `--model` parsing in reusable helpers so `paper`, `recognize`, `translator`, and the new `utils` command share the same model lookup and selection rules. Implement mode probing as a real request path that reuses provider call plumbing and optionally writes probe results back to the config file.

**Tech Stack:** Python 3.14, click, tomllib, httpx, existing provider clients under `python/deepresearch_flow/paper/providers/`, pytest via `uv run pytest`

**Spec:** `docs/superpowers/specs/2026-04-11-weighted-provider-routing-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `python/deepresearch_flow/paper/config.py` | Modify | Replace legacy config dataclasses/parsing with weighted provider resources and strict validation |
| `python/deepresearch_flow/paper/extract.py` | Modify | Replace legacy model parsing and key rotation entrypoints with weighted routing helpers |
| `python/deepresearch_flow/paper/llm.py` | Modify | Accept routed endpoint/key/model capability data instead of provider-level structured mode |
| `python/deepresearch_flow/paper/providers/openai_compatible.py` | Modify | Reuse for real `json_schema` / `json_object` probe requests |
| `python/deepresearch_flow/paper/providers/azure_openai.py` | Modify | Reuse for real `json_schema` / `json_object` probe requests |
| `python/deepresearch_flow/paper/providers/ollama.py` | Modify | Keep routing-compatible structured mode behavior if still supported |
| `python/deepresearch_flow/paper/routing.py` | Create | Weighted selectors, `--model` parsing, route resolution, capability checks |
| `python/deepresearch_flow/paper/tests/test_weighted_config.py` | Create | Config parsing and validation tests for new layout |
| `python/deepresearch_flow/paper/tests/test_weighted_routing.py` | Create | Weighted selection and `--model` parsing tests |
| `python/deepresearch_flow/paper/tests/test_utils_test_mode_cli.py` | Create | CLI tests for `deepresearch-flow utils test-mode` |
| `python/deepresearch_flow/paper/tests/test_extract_errors.py` | Modify | Update fixtures/types for new config model |
| `python/deepresearch_flow/paper/tests/test_extract_retry_planning.py` | Modify | Update fixtures/types for new routing helpers |
| `python/deepresearch_flow/paper/cli.py` | Modify | Keep `paper extract --model` as single entrypoint with single-model / JSON / `@file` forms |
| `python/deepresearch_flow/paper/db.py` | Modify | Update any paper DB commands that still parse `--model provider/model` via shared helpers |
| `python/deepresearch_flow/recognize/cli.py` | Modify | Update config/model resolution to use new shared routing/config objects |
| `python/deepresearch_flow/recognize/math.py` | Modify | Replace provider-level `structured_mode` reads with model capability checks |
| `python/deepresearch_flow/recognize/mermaid.py` | Modify | Replace provider-level `structured_mode` reads with model capability checks |
| `python/deepresearch_flow/translator/cli.py` | Modify | Update config/model resolution to use new shared routing/config objects |
| `python/deepresearch_flow/translator/engine.py` | Modify | Replace key rotation inputs with weighted endpoint/key selection |
| `python/deepresearch_flow/utils/__init__.py` | Create | Package init for utils command group |
| `python/deepresearch_flow/utils/cli.py` | Create | `utils` click group and `test-mode` command |
| `python/deepresearch_flow/cli.py` | Modify | Register the new `utils` command group |
| `config.example.toml` | Modify | Replace old provider example with weighted provider/resources example |
| `README.md` | Modify | Document new config shape, `--model` forms, and `utils test-mode` |
| `README_ZH.md` | Modify | Document new config shape, `--model` forms, and `utils test-mode` |

---

### Task 1: Replace Config Dataclasses and Parser

**Files:**
- Modify: `python/deepresearch_flow/paper/config.py`
- Create: `python/deepresearch_flow/paper/tests/test_weighted_config.py`

- [ ] **Step 1: Write failing config tests for the new shape**

Create `python/deepresearch_flow/paper/tests/test_weighted_config.py` covering:

```python
def test_loads_weighted_provider_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        main_model = [{ model = "openai/gpt-4.1", weight = 2 }]

        [[providers]]
        name = "openai"
        type = "openai_compatible"
        base = [{ url = "https://api.example.com/v1", weight = 1, key = [{ value = "env:OPENAI_API_KEY", weight = 1 }] }]
        models = [{ model_name = "gpt-4.1", is_stream = true, is_support_json_schema = true, is_support_json_object = true }]
        """,
        encoding="utf-8",
    )
    loaded = load_config(str(config_path))
    assert loaded.main_model[0].model == "openai/gpt-4.1"
    assert loaded.providers[0].base[0].key[0].value == "env:OPENAI_API_KEY"


def test_rejects_legacy_api_keys_shape(tmp_path: Path) -> None:
    ...


def test_rejects_missing_main_model(tmp_path: Path) -> None:
    ...


def test_rejects_main_model_reference_not_declared(tmp_path: Path) -> None:
    ...


def test_rejects_non_positive_weight(tmp_path: Path) -> None:
    ...


def test_env_resolution_failure_is_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ...
```

- [ ] **Step 2: Run the config tests and confirm they fail**

Run: `cd /home/dengqi/Source/langs/python/ai-deepresearch-flow && uv run pytest python/deepresearch_flow/paper/tests/test_weighted_config.py -v`

Expected: FAIL because the new dataclasses and parser do not exist yet.

- [ ] **Step 3: Replace the legacy dataclasses**

In `python/deepresearch_flow/paper/config.py`, remove or replace:

- `ApiKeyConfig`
- legacy `ProviderConfig.api_keys`
- legacy `ProviderConfig.structured_mode`
- legacy `ProviderConfig.model_list`

Introduce new frozen dataclasses:

```python
@dataclass(frozen=True)
class KeyConfig:
    value: str
    weight: int
    quota_duration: int | None = None
    reset_time: str | None = None
    quota_error_tokens: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BaseConfig:
    url: str
    weight: int
    key: list[KeyConfig]


@dataclass(frozen=True)
class ModelCapability:
    model_name: str
    is_stream: bool
    is_support_json_schema: bool
    is_support_json_object: bool


@dataclass(frozen=True)
class MainModelConfig:
    model: str
    weight: int
```

Extend `ProviderConfig` with:

- `base: list[BaseConfig]`
- `models: list[ModelCapability]`

Extend `PaperConfig` with:

- `main_model: list[MainModelConfig]`

- [ ] **Step 4: Implement parser helpers and validation**

In `python/deepresearch_flow/paper/config.py`:

- add helper parsers for weighted object arrays
- reject the old `api_keys`, `model_list`, and `structured_mode` fields with explicit errors
- validate that every provider has non-empty `base` and `models`
- validate every `base` has non-empty `key`
- validate every `weight` is a positive integer
- validate every `main_model[].model` resolves to a declared `providers[].models[]`

- [ ] **Step 5: Implement strict `env:` resolution**

Add resolution helpers for keys:

```python
def resolve_key_value(raw_value: str) -> str:
    if raw_value.startswith("env:"):
        env_name = raw_value.split(":", 1)[1]
        resolved = os.environ.get(env_name)
        if not resolved:
            raise ValueError(f"Environment variable not set: {env_name}")
        return resolved
    return raw_value
```

Do not silently skip unresolved env entries.

- [ ] **Step 6: Run config tests and make them pass**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_weighted_config.py -v`

Expected: PASS

- [ ] **Step 7: Commit the config refactor**

Run:

```bash
git add python/deepresearch_flow/paper/config.py python/deepresearch_flow/paper/tests/test_weighted_config.py
git commit -m "refactor: replace legacy provider config with weighted resources"
```

---

### Task 2: Add Shared Weighted Routing Helpers

**Files:**
- Create: `python/deepresearch_flow/paper/routing.py`
- Create: `python/deepresearch_flow/paper/tests/test_weighted_routing.py`

- [ ] **Step 1: Write failing routing tests**

Create `python/deepresearch_flow/paper/tests/test_weighted_routing.py` with cases for:

```python
def test_parse_single_model_ref_resolves_declared_model() -> None:
    route = parse_model_selector("openai/gpt-4.1", config.providers)
    assert route.kind == "single"
    assert route.models == ["openai/gpt-4.1"]


def test_parse_inline_json_model_pool() -> None:
    route = parse_model_selector('[{"model":"openai/gpt-4.1","weight":2}]', config.providers)
    assert route.kind == "pool"
    assert route.pool[0].weight == 2


def test_parse_at_file_model_pool(tmp_path: Path) -> None:
    ...


def test_rejects_unknown_single_model() -> None:
    ...


def test_rejects_unknown_model_in_json_pool() -> None:
    ...


def test_single_item_main_model_is_equivalent_to_fixed_route() -> None:
    ...


def test_weighted_base_selection_uses_provider_scope() -> None:
    ...


def test_weighted_key_selection_uses_base_scope() -> None:
    ...
```

- [ ] **Step 2: Run routing tests and confirm they fail**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_weighted_routing.py -v`

Expected: FAIL because `paper.routing` does not exist yet.

- [ ] **Step 3: Implement shared selector helpers**

In `python/deepresearch_flow/paper/routing.py`, add:

- `parse_model_selector(model_ref: str, providers: list[ProviderConfig])`
- `load_main_model_override(model_ref: str, providers: list[ProviderConfig])`
- `resolve_model_capability(provider_name: str, model_name: str, providers: list[ProviderConfig])`
- `choose_weighted(items, *, rng: random.Random | None = None)`
- `select_runtime_route(config: PaperConfig, model_selector: ParsedModelSelector, *, rng: random.Random | None = None)`

Keep the returned runtime route explicit:

```python
@dataclass(frozen=True)
class RuntimeRoute:
    provider: ProviderConfig
    base: BaseConfig
    key: KeyConfig
    model: ModelCapability
```

- [ ] **Step 4: Implement `--model` parsing**

Rules to encode:

- `provider/model` means fixed model
- valid JSON array means runtime main-model pool override
- `@file` means load JSON array from file
- all referenced models must resolve to declared `providers[].models[]`

- [ ] **Step 5: Run routing tests and make them pass**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_weighted_routing.py -v`

Expected: PASS

- [ ] **Step 6: Commit the routing helpers**

Run:

```bash
git add python/deepresearch_flow/paper/routing.py python/deepresearch_flow/paper/tests/test_weighted_routing.py
git commit -m "feat: add weighted model base and key routing helpers"
```

---

### Task 3: Migrate `paper extract` to Shared Routing

**Files:**
- Modify: `python/deepresearch_flow/paper/cli.py`
- Modify: `python/deepresearch_flow/paper/extract.py`
- Modify: `python/deepresearch_flow/paper/llm.py`
- Modify: `python/deepresearch_flow/paper/providers/openai_compatible.py`
- Modify: `python/deepresearch_flow/paper/providers/azure_openai.py`
- Modify: `python/deepresearch_flow/paper/providers/ollama.py`
- Modify: `python/deepresearch_flow/paper/tests/test_extract_errors.py`
- Modify: `python/deepresearch_flow/paper/tests/test_extract_retry_planning.py`

- [ ] **Step 1: Update extract-facing tests and fixtures**

Adjust existing paper tests so they build new config objects and routes instead of legacy `api_keys/model_list/structured_mode`.

Focus on:

- `python/deepresearch_flow/paper/tests/test_extract_errors.py`
- `python/deepresearch_flow/paper/tests/test_extract_retry_planning.py`

- [ ] **Step 2: Run the extract-related tests and confirm they fail**

Run:

```bash
uv run pytest \
  python/deepresearch_flow/paper/tests/test_extract_errors.py \
  python/deepresearch_flow/paper/tests/test_extract_retry_planning.py -v
```

Expected: FAIL because the extract path still assumes legacy provider fields.

- [ ] **Step 3: Replace `parse_model_ref()` usage in `paper/cli.py`**

In `python/deepresearch_flow/paper/cli.py`:

- keep `@click.option("-m", "--model", ...)`
- change help text from just `provider/model` to note single-model, JSON, or `@file`
- replace `parse_model_ref(model_ref, config.providers)` with the shared parser from `paper.routing`
- stop validating `provider.structured_mode`

- [ ] **Step 4: Replace legacy key rotator entrypoints in `paper/extract.py`**

In `python/deepresearch_flow/paper/extract.py`:

- remove assumptions that each provider owns one `base_url` plus one `api_keys` list
- adapt `KeyRotator` to work with resolved `KeyConfig` objects
- build runtime routes via `select_runtime_route(...)`
- pass routed URL, key, and capability data into provider calls

- [ ] **Step 5: Move structured-output choice from provider level to model capability**

In `python/deepresearch_flow/paper/llm.py` and call sites:

- stop accepting provider-level `structured_mode`
- derive requested mode from the extraction operation
- check `RuntimeRoute.model.is_support_json_schema` / `is_support_json_object`
- fail explicitly if the selected model cannot satisfy the requested mode

- [ ] **Step 6: Update provider client call signatures**

In:

- `python/deepresearch_flow/paper/providers/openai_compatible.py`
- `python/deepresearch_flow/paper/providers/azure_openai.py`
- `python/deepresearch_flow/paper/providers/ollama.py`
- related call sites in `python/deepresearch_flow/paper/llm.py`

make the provider client boundary routing-compatible.

At minimum:

- stop requiring the old provider-level `structured_mode` contract at the public call boundary
- accept the routed request data needed for execution and probing
- keep the structured-output payload generation driven by the selected model capability and requested mode
- keep the provider helper signatures consistent enough that `utils test-mode` can reuse them without a second incompatible path

- [ ] **Step 7: Run the extract-related tests and make them pass**

Run:

```bash
uv run pytest \
  python/deepresearch_flow/paper/tests/test_extract_errors.py \
  python/deepresearch_flow/paper/tests/test_extract_retry_planning.py \
  python/deepresearch_flow/paper/tests/test_weighted_config.py \
  python/deepresearch_flow/paper/tests/test_weighted_routing.py -v
```

Expected: PASS

- [ ] **Step 8: Commit the paper extract migration**

Run:

```bash
git add \
  python/deepresearch_flow/paper/cli.py \
  python/deepresearch_flow/paper/extract.py \
  python/deepresearch_flow/paper/llm.py \
  python/deepresearch_flow/paper/providers/openai_compatible.py \
  python/deepresearch_flow/paper/providers/azure_openai.py \
  python/deepresearch_flow/paper/providers/ollama.py \
  python/deepresearch_flow/paper/tests/test_extract_errors.py \
  python/deepresearch_flow/paper/tests/test_extract_retry_planning.py
git commit -m "refactor: migrate paper extract to weighted routing"
```

---

### Task 4: Migrate Other Config Consumers

**Files:**
- Modify: `python/deepresearch_flow/recognize/cli.py`
- Modify: `python/deepresearch_flow/recognize/math.py`
- Modify: `python/deepresearch_flow/recognize/mermaid.py`
- Modify: `python/deepresearch_flow/translator/cli.py`
- Modify: `python/deepresearch_flow/translator/engine.py`
- Modify: `python/deepresearch_flow/paper/db.py`

- [ ] **Step 1: Audit remaining legacy-field reads**

Run:

```bash
cd /home/dengqi/Source/langs/python/ai-deepresearch-flow
rg -n "api_keys|model_list|structured_mode|base_url|resolve_api_keys\\(" python/deepresearch_flow -S
find python/deepresearch_flow/recognize/tests -maxdepth 2 -type f | sort
find python/deepresearch_flow/translator/tests -maxdepth 2 -type f | sort
```

Expected:

- only relevant remaining legacy-field usages should be in files being migrated now
- `recognize` may have no dedicated test directory
- if `translator` has direct tests for changed paths, include them in Step 5

- [ ] **Step 2: Migrate `recognize` to shared model resolution**

In `python/deepresearch_flow/recognize/cli.py`:

- keep the public `--model` interface
- replace direct `parse_model_ref(...)` dependency with shared `paper.routing` helpers

In `python/deepresearch_flow/recognize/math.py` and `mermaid.py`:

- remove direct reads of `provider.structured_mode`
- read capability from the routed model declaration instead

- [ ] **Step 3: Migrate `translator` to shared model resolution**

In `python/deepresearch_flow/translator/cli.py` and `translator/engine.py`:

- replace legacy key-pool setup with routed `base + key` selection
- keep translation behavior unstructured, but stop depending on removed legacy fields

- [ ] **Step 4: Migrate `paper db` model selection**

In `python/deepresearch_flow/paper/db.py`:

- replace any direct `provider/model` parser or `resolve_api_keys(...)` usage with shared routing/config helpers

- [ ] **Step 5: Run affected targeted tests or smoke tests**

Run:

```bash
uv run pytest python/deepresearch_flow/paper/tests/test_db_api_push_cli.py -v
uv run pytest python/deepresearch_flow/paper/tests/test_extract_errors.py -v
uv run pytest python/deepresearch_flow/translator/tests/test_fixers.py -v
```

If `recognize` lacks direct tests for the changed paths, run CLI smoke commands with `--help`:

```bash
uv run python -m deepresearch_flow recognize --help
uv run python -m deepresearch_flow translator --help
uv run python -m deepresearch_flow paper --help
```

- [ ] **Step 6: Commit the shared-consumer migration**

Run:

```bash
git add \
  python/deepresearch_flow/recognize/cli.py \
  python/deepresearch_flow/recognize/math.py \
  python/deepresearch_flow/recognize/mermaid.py \
  python/deepresearch_flow/translator/cli.py \
  python/deepresearch_flow/translator/engine.py \
  python/deepresearch_flow/paper/db.py
git commit -m "refactor: migrate shared model consumers to weighted config"
```

---

### Task 5: Add `deepresearch-flow utils test-mode`

**Files:**
- Create: `python/deepresearch_flow/utils/__init__.py`
- Create: `python/deepresearch_flow/utils/cli.py`
- Modify: `python/deepresearch_flow/cli.py`
- Create: `python/deepresearch_flow/paper/tests/test_utils_test_mode_cli.py`

- [ ] **Step 1: Write failing CLI tests for `utils test-mode`**

Create `python/deepresearch_flow/paper/tests/test_utils_test_mode_cli.py` with cases like:

```python
def test_rejects_bare_model_name(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(cli, ["utils", "test-mode", "--config", str(config_path), "--model", "gpt-4.1"])
    assert result.exit_code != 0
    assert "provider/model" in result.output


def test_rejects_unknown_declared_model(...) -> None:
    ...


def test_reports_probe_results_without_write_back(...) -> None:
    ...


def test_write_back_updates_only_probed_modes(...) -> None:
    ...


def test_probe_failure_exits_non_zero_and_does_not_write_back(...) -> None:
    ...
```

Use mocks/monkeypatching around the actual provider probe helper so tests do not hit the network.

- [ ] **Step 2: Run the new CLI tests and confirm they fail**

Run: `uv run pytest python/deepresearch_flow/paper/tests/test_utils_test_mode_cli.py -v`

Expected: FAIL because the `utils` command group does not exist yet.

- [ ] **Step 3: Add the `utils` click group**

Create `python/deepresearch_flow/utils/cli.py`:

```python
@click.group()
def utils() -> None:
    """Utility commands."""
```

Register it in `python/deepresearch_flow/cli.py`.

- [ ] **Step 4: Implement `test-mode`**

In `python/deepresearch_flow/utils/cli.py`, add:

- command name: `test-mode`
- repeatable `--model provider/model`
- `--config`
- `--write-back`

Implementation rules:

- every requested model must already exist in `providers[].models[]`
- select one weighted `base` and one weighted `key`
- perform real probe requests for `json_schema` and `json_object`
- if any probe fails, report the reason and exit non-zero
- probe failures do not get converted to `false`
- without `--write-back`, report only
- with `--write-back`, update only the probed capability fields

- [ ] **Step 5: Add a provider-agnostic probe helper**

Implement a small helper that sends minimal structured requests through the same provider clients used by runtime execution.

Keep the helper isolated enough that CLI tests can monkeypatch it cleanly.

- [ ] **Step 6: Run the CLI tests and make them pass**

Run:

```bash
uv run pytest python/deepresearch_flow/paper/tests/test_utils_test_mode_cli.py -v
uv run python -m deepresearch_flow utils test-mode --help
```

Expected: PASS and help output includes `--write-back`.

- [ ] **Step 7: Commit the new utility command**

Run:

```bash
git add \
  python/deepresearch_flow/utils/__init__.py \
  python/deepresearch_flow/utils/cli.py \
  python/deepresearch_flow/cli.py \
  python/deepresearch_flow/paper/tests/test_utils_test_mode_cli.py
git commit -m "feat: add utils test-mode for real capability probing"
```

---

### Task 6: Update Example Config and Docs

**Files:**
- Modify: `config.example.toml`
- Modify: `README.md`
- Modify: `README_ZH.md`

- [ ] **Step 1: Rewrite `config.example.toml` to the new shape**

Replace old examples using:

- `api_keys`
- `model_list`
- `structured_mode`

with examples that show:

- weighted `main_model`
- provider `base`
- base `key`
- provider `models`

- [ ] **Step 2: Update README command examples**

Document:

- `--model provider/model`
- `--model '[...]'`
- `--model @main_model.json`
- `deepresearch-flow utils test-mode`
- `--write-back`

- [ ] **Step 3: Add migration notes**

In both READMEs, add a short breaking-change note:

- old provider config is no longer accepted
- env resolution now fails explicitly when missing
- `test-mode` only probes one weighted `base + key` path per model

- [ ] **Step 4: Verify documentation references**

Run:

```bash
rg -n "api_keys|model_list|structured_mode" README.md README_ZH.md config.example.toml
```

Expected: Only historical or migration-note mentions remain.

- [ ] **Step 5: Commit the docs update**

Run:

```bash
git add config.example.toml README.md README_ZH.md
git commit -m "docs: document weighted provider routing and test-mode"
```

---

### Task 7: Full Verification

**Files:**
- No code changes in this task

- [ ] **Step 1: Run focused pytest suite**

Run:

```bash
uv run pytest \
  python/deepresearch_flow/paper/tests/test_weighted_config.py \
  python/deepresearch_flow/paper/tests/test_weighted_routing.py \
  python/deepresearch_flow/paper/tests/test_utils_test_mode_cli.py \
  python/deepresearch_flow/paper/tests/test_extract_errors.py \
  python/deepresearch_flow/paper/tests/test_extract_retry_planning.py \
  python/deepresearch_flow/paper/tests/test_db_api_push_cli.py -v
```

Expected: PASS

- [ ] **Step 2: Run CLI help smoke tests**

Run:

```bash
uv run python -m deepresearch_flow --help
uv run python -m deepresearch_flow paper --help
uv run python -m deepresearch_flow utils --help
uv run python -m deepresearch_flow utils test-mode --help
```

Expected: PASS

- [ ] **Step 3: Run one config smoke check**

Create a temporary config using the new weighted shape with literal test key values rather than `env:` references, then run:

```bash
uv run python -m deepresearch_flow paper extract \
  --config /tmp/test-config.toml \
  --input README.md \
  --model openai/gpt-4.1 \
  --dry-run
```

Expected: config loads successfully and the command reaches dry-run discovery without legacy-config validation errors or missing-environment-variable failures.

- [ ] **Step 4: Commit verification-only updates if needed**

If code changed during verification, commit with a focused message such as:

```bash
git add <files>
git commit -m "test: finish weighted routing verification fixes"
```
