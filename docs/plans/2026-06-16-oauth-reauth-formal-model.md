# OAuth Reauth Formal Model Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add the missing OAuth reauth/recovered-client behavior to the local state-space, SMT, TLA, and manifest verification surfaces.

**Architecture:** Treat a missing but syntactically recoverable dynamic client as a distinct reauth path, not as generic unknown-client rejection. The model must prove recovery never issues a token directly, malformed clients still fail closed, and a token can be issued only after the normal GitHub/PKCE/resource/redirect chain is valid.

**Tech Stack:** YAML state catalog, Python Z3 finite-universe checker, TLA+ TLC model, pytest verification tests.

---

### Task 1: Add failing catalog/SMT tests

**Files:**
- Modify: `tests/verification/test_state_gap_discovery.py`
- Modify: `tests/verification/test_smt_formal_gate.py`

**Steps:**
1. Add a black-box test that reads the repo OAuth state catalog and requires explicit recoverable-missing-client / reauth obligations.
2. Add a black-box test that runs the local SMT JSON gate and requires OAuth variables/actions to expose the reauth/recovery path.
3. Run those tests and confirm they fail on the current model.

### Task 2: Update catalog and manifest language

**Files:**
- Modify: `docs/verification/state-space-obligations.yml`
- Modify: `tools/verification/generate_bootstrap_manifest.py`
- Regenerate: `docs/verification/repo-verification-inventory.json`
- Regenerate: `docs/verification/repo-verification-manifest.yml`

**Steps:**
1. Split unknown client into malformed fail-closed vs recoverable dynamic-client reauth.
2. Add reauth/recovered states and explicit no-token-before-auth expected behavior.
3. Update manifest observable contract language to match recovery semantics.

### Task 3: Update SMT/TLA OAuth models

**Files:**
- Modify: `tools/formal/smt/check_all_smt_models.py`
- Modify: `formal/oauth_client_cache/OAuthClientCache.tla`
- Modify: `formal/oauth_client_cache/OAuthClientCache.cfg`

**Steps:**
1. Extend SMT variables with client validity, reauth pending, upstream auth completion, and redirect/resource guards.
2. Add recovery action and invariant that recovery alone cannot issue tokens.
3. Extend TLA model with recoverable-client authorize and token-after-auth actions.
4. Keep malformed clients fail-closed.

### Task 4: Verify locally

**Commands:**
- `uv run pytest tests/verification/test_state_gap_discovery.py tests/verification/test_smt_formal_gate.py -q`
- `DRFLOW_RUN_LOCAL_FORMAL=1 uv run pytest tests/verification/test_smt_formal_gate.py -q`
- `make verify-state-gaps`
- `make verify-formal-smt`
- `make verify-inventory`
- `git diff --check`
