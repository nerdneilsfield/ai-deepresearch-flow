## Context
The current provider layer supports `ollama`, `openai_compatible`, and `dashscope` with explicit `provider/model` routing and config validation.

## Goals / Non-Goals
- Goals:
  - Add Gemini (AI Studio + Vertex), Azure OpenAI, and Claude providers.
  - Keep explicit provider routing and `model_list` enforcement.
  - Validate provider-specific required fields at config load time.
- Non-Goals:
  - Changing extraction schema requirements.
  - Introducing streaming output (not requested).

## Decisions
- Provider type names:
  - `gemini_ai_studio` for Google AI Studio (API key auth).
  - `gemini_vertex` for Vertex AI (ADC or service account).
  - `azure_openai` for Azure OpenAI (deployment-based endpoint).
  - `claude` for Anthropic Claude (official SDK).
- Gemini SDK:
  - Use `google-genai` so one SDK covers both AI Studio and Vertex.
  - `gemini_ai_studio` requires `api_keys` and uses standard Gemini model names.
  - `gemini_vertex` requires `project_id` and `location`; optional `credentials_path` overrides ADC.
- Azure OpenAI:
  - Use direct HTTP calls via `httpx` (no new SDK).
  - Require `endpoint`, `api_version`, and `deployment` in config.
- Claude:
  - Use `anthropic` SDK.
  - Require `anthropic_version` and `api_keys` in config.

## Risks / Trade-offs
- More dependencies increase install size; mitigate by pinning minimum versions and documenting usage.
- Vertex credential handling can be confusing; document ADC vs `credentials_path`.

## Migration Plan
- Add new provider types and config validation.
- Update `config.example.toml` and README.
- No migration required for existing provider configs.

## Open Questions
- Confirm provider type names and required field names (if you prefer different identifiers).
