# Local Formal Models

This directory contains finite TLA+ models for critical state machines.

## Tooling

- Primary local state-space tool: TLC (`tla2tools.jar`).
- Secondary local SMT sanity tool: Z3 via `tools/formal/smt/check_all_smt_models.py`.

These checks are local-only and intentionally excluded from CI/CD and default
`make check` / `make verify-repo-strict`.

## Commands

```bash
# install TLC jar locally if missing
mkdir -p .cache/formal
curl -L https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar \
  -o .cache/formal/tla2tools.jar

make verify-formal-tlc
make verify-formal-smt
make verify-formal-local
```

## Modeling rules

OAuth/MCP models must be derived from the official MCP authorization spec and
its referenced OAuth RFCs. Project-specific safety policy may be modeled only
when explicitly documented as project policy.

`lastReturn`-style fields represent the current response event, not an audit log
of historical responses, unless a model explicitly states otherwise.

## State/fault discovery before trusting models

Passing TLC/Z3 means the checked model is internally consistent for its declared
finite universe. It does not mean the model remembered every relevant external
state. Run:

```bash
make discover-state-gaps
```

to enumerate independent protocol/fault states from
`docs/verification/state-space-obligations.yml`. Run:

```bash
make verify-state-gaps
```

only when you want local failure while unresolved known/uncovered gaps remain.
