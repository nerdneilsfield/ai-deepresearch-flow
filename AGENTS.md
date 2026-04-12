## Local Workflow Rules

- Do not change code unless the user explicitly asks for code changes.

## Testing Policy

### Black-Box Testing at Every Layer

The distinction is NOT "external = test, internal = skip." It is:

- **External APIs: black-box test.**
- **Internal helpers: also black-box test.**

Rules for all test writing (including subagent-dispatched tests):

- Provide only: module path, function name, parameter types, return types, and a plain-language description of expected behavior.
- Do NOT provide: source code, implementation logic, regex patterns, branch structure, prompts, copy text, or private state.
- Assertions must be input/output only. No dependency on internal steps, intermediate variables, execution order within the function, or how many branches exist.
- Internal/private helpers are tested just as thoroughly as public APIs. "Internal" does not mean "white-box allowed" or "less testing."
