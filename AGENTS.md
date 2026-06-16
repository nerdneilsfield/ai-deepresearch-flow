## Local Workflow Rules

- Do not change code unless the user explicitly asks for code changes.
- Repo-wide formal/fuzz/inventory/deep verification is local-only by default. CI/CD, release,
  publish, Docker image, PyPI, and GitHub Actions workflows must not run `tests/verification`,
  `tools/verification`, `verify-formal*`, `verify-fuzz*`, `verify-state-gaps`,
  `verify-inventory`, or equivalent deep-verification gates unless the user explicitly overrides
  this rule in the same turn.
- Before editing `.github/workflows/**`, release scripts, publish scripts, Docker release config,
  or CI-related Makefile targets, verify that the local-only deep-verification rule remains true.

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
