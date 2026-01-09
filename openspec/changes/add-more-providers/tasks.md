## 1. Configuration
- [x] 1.1 Extend provider config model to include fields for azure/gemini/claude.
- [x] 1.2 Validate provider-specific required fields at config load time.

## 2. Providers
- [x] 2.1 Implement `gemini_ai_studio` provider via `google-genai`.
- [x] 2.2 Implement `gemini_vertex` provider via `google-genai` with Vertex config.
- [x] 2.3 Implement `azure_openai` provider via `httpx`.
- [x] 2.4 Implement `claude` provider via `anthropic` SDK (requires `anthropic_version`).

## 3. Documentation
- [x] 3.1 Update `config.example.toml` with provider examples.
- [x] 3.2 Update README provider list and usage notes.

## 4. Packaging
- [x] 4.1 Add new provider SDK dependencies to `pyproject.toml`.
