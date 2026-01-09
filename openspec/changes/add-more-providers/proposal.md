# Change: Add additional model providers (Gemini, Azure OpenAI, Claude)

## Why
Users need to run extraction against more hosted model backends, including Gemini (AI Studio + Vertex), Azure OpenAI, and Claude via the official SDKs.

## What Changes
- Add provider types for Gemini AI Studio, Gemini Vertex AI, Azure OpenAI, and Claude.
- Extend provider configuration validation with provider-specific required fields.
- Add SDK dependencies for Gemini and Claude.
- Update config examples and documentation to cover the new providers.

## Impact
- Affected specs: `paper` capability
- Affected code: provider config loading, provider client implementations, README/config examples, `pyproject.toml`
