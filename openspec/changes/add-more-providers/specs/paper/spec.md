## ADDED Requirements

### Requirement: Provider-specific configuration fields
The system SHALL validate provider-specific required fields during config load.
At minimum:
- `azure_openai` requires `endpoint`, `api_version`, and `deployment`.
- `gemini_ai_studio` requires at least one API key entry.
- `gemini_vertex` requires `project_id` and `location`.
- `claude` requires `anthropic_version` and at least one API key entry.

#### Scenario: Reject missing azure deployment
- **WHEN** a provider with type `azure_openai` is missing `deployment`
- **THEN** config validation fails with a provider-specific error

#### Scenario: Reject missing Vertex project
- **WHEN** a provider with type `gemini_vertex` is missing `project_id`
- **THEN** config validation fails with a provider-specific error

#### Scenario: Reject missing Claude version
- **WHEN** a provider with type `claude` is missing `anthropic_version`
- **THEN** config validation fails with a provider-specific error

## MODIFIED Requirements

### Requirement: Supported provider types
The system SHALL support at least the following provider types:
- `ollama` using the native Ollama API
- `openai_compatible` using an OpenAI-compatible HTTP API
- `dashscope` using the DashScope SDK API
- `gemini_ai_studio` using the Google AI Studio API
- `gemini_vertex` using Vertex AI Gemini
- `azure_openai` using Azure OpenAI deployments
- `claude` using the Anthropic SDK

#### Scenario: Select ollama provider explicitly
- **WHEN** the user passes `--model ollama/llama3.1`
- **THEN** the tool routes requests to the provider named `ollama` using model `llama3.1`

#### Scenario: Select dashscope provider explicitly
- **WHEN** the user passes `--model dashscope/qwen-max`
- **THEN** the tool routes requests to the provider named `dashscope` using model `qwen-max`

#### Scenario: Select Gemini AI Studio provider explicitly
- **WHEN** the user passes `--model gemini_ai_studio/gemini-2.0-flash`
- **THEN** the tool routes requests to the provider named `gemini_ai_studio` using model `gemini-2.0-flash`

#### Scenario: Select Gemini Vertex provider explicitly
- **WHEN** the user passes `--model gemini_vertex/gemini-2.0-flash`
- **THEN** the tool routes requests to the provider named `gemini_vertex` using model `gemini-2.0-flash`

#### Scenario: Select Azure OpenAI provider explicitly
- **WHEN** the user passes `--model azure_openai/gpt-4o-mini`
- **THEN** the tool routes requests to the provider named `azure_openai` using model `gpt-4o-mini`

#### Scenario: Select Claude provider explicitly
- **WHEN** the user passes `--model claude/claude-sonnet-4-5-20250929`
- **THEN** the tool routes requests to the provider named `claude` using model `claude-sonnet-4-5-20250929`
