## Testing Policy

### Black-Box Testing at Every Layer

The distinction is NOT "external = test, internal = skip." It is:

- **External APIs: black-box test.**
- **Internal helpers: also black-box test.**

Every function, regardless of visibility, is tested only through its interface contract:

- **What the test writer receives:** module path, function name, parameter types, return types, and a plain-language description of what the function does.
- **What the test writer does NOT receive:** source code, implementation logic, regex patterns, branch structure, prompts, copy text, or private state.
- **Assertions are input/output only.** No dependency on internal steps, intermediate variables, execution order within the function, or how many branches exist.
- **Subagents writing tests** get the interface spec, never the implementation. This is mandatory — do not pass source code to a test-writing subagent.
- **Internal/private helpers are tested just as thoroughly as public APIs.** "Internal" means "not user-facing," not "exempt from testing" or "allowed to be white-box."
