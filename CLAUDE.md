## Testing Policy

### Black-Box Testing Only

When writing tests (whether via subagent or directly), treat every function as a black box:

- **Public APIs and internal helpers alike** — test both, but only through their signature and documented behavior, never by reading the implementation.
- **Subagent test writers** receive only: function name, module path, parameter types, return types, and a description of what the function does. They do NOT receive the source code.
- **No implementation leakage** — tests must not depend on internal variable names, branch structure, regex patterns, or private state. Assert on inputs and outputs only.
- **Coverage of internals** — internal/private helpers are tested just as thoroughly as public APIs, but still as black boxes. Knowing a helper exists and what it should do is fine; knowing how it does it is not.
