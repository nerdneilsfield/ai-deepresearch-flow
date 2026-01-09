## 1. Configuration
- [ ] 1.1 Extend provider config model to include fields for azure/gemini/claude.
- [ ] 1.2 Validate provider-specific required fields at config load time.

## 2. Providers
- [ ] 2.1 Implement `gemini_ai_studio` provider via `google-genai`.
- [ ] 2.2 Implement `gemini_vertex` provider via `google-genai` with Vertex config.
- [ ] 2.3 Implement `azure_openai` provider via `httpx`.
- [ ] 2.4 Implement `claude` provider via `anthropic` SDK (requires `anthropic_version`).

## 3. Documentation
- [ ] 3.1 Update `config.example.toml` with provider examples.
- [ ] 3.2 Update README provider list and usage notes.

## 4. Packaging
- [ ] 4.1 Add new provider SDK dependencies to `pyproject.toml`.
