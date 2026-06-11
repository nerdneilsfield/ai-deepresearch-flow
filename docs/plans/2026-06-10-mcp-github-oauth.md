# MCP GitHub OAuth Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add GitHub OAuth for ChatGPT/Claude remote MCP clients while preserving static Bearer token access for ordinary agent CLI clients.

**Architecture:** drflow/FastMCP is the MCP authorization server and resource server integration; GitHub is only the upstream identity provider. Use a verified FastMCP-compatible OAuth routing model. drflow remains single-library and config-authorized: stable GitHub user ids are checked against an allowlist, and all allowed users share the same read/search MCP surface. Static Bearer remains a separate local/automation path with constant-time verification and read/search scopes only.

**Tech Stack:** Python, Starlette, FastMCP >=3.2.3, GitHub OAuth, pytest/httpx ASGI tests.

---

## Requirements

- Preserve current static Bearer behavior for `/mcp`, `/mcp/`, `/mcp-sse`, and
  `/mcp-sse/`.
- Add GitHub OAuth as an opt-in MCP auth mode.
- Support combined static Bearer + OAuth mode for migration/automation.
- Authorize GitHub identities by stable GitHub user id, not username alone.
- Do not create drflow users, sessions, user tables, or per-user data stores.
- Do not expose admin/write APIs through OAuth.
- Fail fast on invalid OAuth configuration with non-secret actionable errors.
- Keep tests black-box per project policy.
- Freeze OAuth metadata/callback/resource routing before implementation proceeds.
- Never accept GitHub opaque access tokens directly as MCP resource Bearer tokens.
- Freeze downstream MCP client registration model: DCR, CIMD, or preconfigured clients.

## Proposed files

- Modify: `python/deepresearch_flow/paper/snapshot/auth.py`
- Modify: `python/deepresearch_flow/paper/snapshot/mcp_server.py`
- Modify: `python/deepresearch_flow/paper/snapshot/api.py`
- Modify: `python/deepresearch_flow/paper/db.py`
- Modify: `python/deepresearch_flow/paper/config.py` only if config-file MCP auth support is chosen
- Modify: `config.example.toml`
- Modify: Docker/nginx templates for every Task 1 OAuth operational route
- Modify: `README.md`
- Modify: `README_ZH.md`
- Test: `python/deepresearch_flow/paper/snapshot/tests/test_auth.py`
- Test: `python/deepresearch_flow/paper/snapshot/tests/test_mcp_transport.py`
- Add or modify: `python/deepresearch_flow/paper/snapshot/tests/test_mcp_oauth.py`
- Test as needed: `python/deepresearch_flow/paper/tests/test_db_api_serve_cli.py`
- Test: Docker/nginx tests for every Task 1 OAuth operational route

## Task 1: Blocking spike for FastMCP OAuth routing

**Files:**
- Inspect: installed `fastmcp` package docs/types locally
- Inspect: `python/deepresearch_flow/paper/snapshot/mcp_server.py`
- Inspect: `python/deepresearch_flow/paper/snapshot/api.py`

**Purpose:** Determine the exact externally visible OAuth routes before writing
implementation or README instructions.

**Step 1: Identify FastMCP auth construction requirements**

Determine whether auth must be supplied at `FastMCP(...)` construction time or
can be assigned safely before `http_app()`.

**Step 2: Identify exact FastMCP auth classes and callback behavior**

Find import paths and behavior for:

- GitHub OAuth provider or OAuthProxy;
- MultiAuth;
- token verifier base class;
- callback path default and configurability;
- protected resource metadata path;
- authorization server metadata path;
- how `base_url`, `path`, and `resource` are derived.

**Step 3: Choose and document routing model**

Freeze one of these models:

1. root-level OAuth operational routes with MCP resource `/mcp`; or
2. mounted OAuth operational routes such as `/mcp/.well-known/...` and
   `/mcp/auth/callback`, only if verified against expected clients.

Also decide whether `/mcp-sse` shares the `/mcp` OAuth resource or remains
static-Bearer only in this phase. The spike output must include a final route
 table for `POST /mcp`, protected-resource metadata, authorization-server
metadata, `/authorize`, `/token`, registration if present, consent if present,
and the upstream GitHub callback. It must state whether routes are merged into
`api.py:create_app()` or provided by a FastMCP `http_app(path=...)`, and whether
`_McpTrailingSlashMiddleware` remains active. It must also freeze the downstream
MCP OAuth client model as `dcr`, `cimd`, or `preconfigured`, including whether
registration endpoint is exposed, supported token endpoint auth methods, and
whether ChatGPT/Claude require client credentials.

**Step 4: Add black-box spike tests or script**

Use ASGI requests to verify the selected model externally. Assertions should be
only request/response behavior:

- unauthenticated `POST /mcp` returns OAuth-discoverable challenge;
- the challenge points to a metadata URL reachable on the public app;
- protected resource metadata identifies `{MCP_PUBLIC_BASE_URL}/mcp`;
- authorization server metadata is reachable and includes `issuer`,
  `authorization_endpoint`, `token_endpoint`, `code_challenge_methods_supported`
  containing `S256`, client registration metadata if applicable,
  `token_endpoint_auth_methods_supported`, `scopes_supported`, and token endpoint
  auth metadata consistent with behavior;
- callback path is reachable and matches the path planned for the upstream
  GitHub OAuth App;
- `/mcp` and `/mcp/` do not redirect;
- duplicate/conflicting metadata routes are not exposed for two transports;
- malicious `Host`, `X-Forwarded-Host`, `X-Forwarded-Proto`, and `Forwarded`
  headers do not affect challenge URLs, metadata, authorization redirects,
  GitHub `redirect_uri`, issuer, resource, or callback URL;
- MCP client redirect URI and upstream GitHub callback URI are separately
  documented and validated;
- unregistered client, mismatched redirect, malicious registered redirect, and
  unexpected DCR registration are rejected;
- `offline_access` is not advertised unless refresh-token TTL, rotation,
  revocation, and allowlist-change behavior are explicitly designed.

**Step 5: Record spike result**

Update this plan/design if the actual FastMCP route model differs. Do not start
Task 4 or docs until this task freezes callback and metadata paths.

**Validation command:**

```bash
uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_mcp_oauth.py -q
```

**Commit:**

If tests/helpers are added:

```bash
git add python/deepresearch_flow/paper/snapshot/tests/test_mcp_oauth.py
git commit -m "test(mcp): characterize OAuth route discovery"
```

## Task 2: Add MCP auth configuration model

**Files:**
- Modify: `python/deepresearch_flow/paper/snapshot/auth.py`
- Modify: `python/deepresearch_flow/paper/snapshot/mcp_server.py`
- Modify: `python/deepresearch_flow/paper/db.py`

**Behavior contract:**

Module path: `deepresearch_flow.paper.snapshot.auth`

Public config object behavior:

- Function or class accepts mode, static token, GitHub client id, GitHub client
  secret, public base URL, allowed stable user ids, optional display logins, and
  provider/token settings exposed by the spike.
- Return type is a typed immutable config value or equivalent typed structure.
- Expected behavior:
  - mode `none` accepts missing token/provider settings only when explicit and
    local/dev/public override rules allow it;
  - mode `bearer` requires a static access token;
  - mode `oauth` requires complete GitHub upstream settings and at least one
    stable allowed user id;
  - mode `bearer_or_oauth` requires both a valid static bearer token and complete
    OAuth settings; use `bearer` for static-only deployments;
  - non-loopback `public_base_url` must be absolute HTTPS, no query, no fragment,
    and no `/mcp` path suffix;
  - startup errors are actionable and never include secrets.

**Steps:**

1. Write black-box tests for config validation behavior.
2. Run the targeted tests and confirm they fail because the config object does
   not exist or does not validate these cases.
3. Implement the minimal config object and validation.
4. Thread the config through API server creation without changing existing
   runtime behavior.
5. Run targeted tests.

**Commands:**

```bash
uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_auth.py -q
```

**Commit:**

```bash
git add python/deepresearch_flow/paper/snapshot/auth.py \
        python/deepresearch_flow/paper/snapshot/mcp_server.py \
        python/deepresearch_flow/paper/db.py \
        python/deepresearch_flow/paper/snapshot/tests/test_auth.py
git commit -m "feat(mcp): add auth configuration model"
```

## Task 3: Preserve static Bearer through the new auth model

**Files:**
- Modify: `python/deepresearch_flow/paper/snapshot/auth.py`
- Modify: `python/deepresearch_flow/paper/snapshot/mcp_server.py`
- Modify: `python/deepresearch_flow/paper/snapshot/api.py`
- Test: `python/deepresearch_flow/paper/snapshot/tests/test_mcp_transport.py`

**Behavior contract:**

Endpoint behavior:

- `POST /mcp` with valid static Bearer token returns a valid MCP response.
- `POST /mcp/` with valid static Bearer token returns a valid MCP response.
- `GET /mcp-sse` with valid static Bearer token returns an SSE response.
- `GET /mcp-sse/` with valid static Bearer token returns an SSE response.
- Missing static token in bearer mode returns unauthorized.
- Wrong static token in bearer mode returns unauthorized.
- Static-token verification preserves the security requirement of constant-time
  comparison; tests assert authorization input/output only, while code review
  verifies use of a constant-time compare primitive.
- Static token maps to a fixed read/search MCP principal, not admin powers.

**Steps:**

1. Add/adjust black-box tests that exercise the endpoint behavior above through
   the ASGI app.
2. Run the tests and confirm any newly introduced behavior fails before the
   implementation change.
3. Implement static-token verification through the new auth config while keeping
   existing behavior.
4. If integrating with FastMCP auth, use a drflow custom verifier instead of a
   development-only static verifier unless the latter is audited and accepted.
5. Keep the no-redirect MCP path middleware behavior intact.
6. Run targeted tests.

**Commands:**

```bash
uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_mcp_transport.py \
              python/deepresearch_flow/paper/snapshot/tests/test_auth.py -q
```

**Commit:**

```bash
git add python/deepresearch_flow/paper/snapshot/auth.py \
        python/deepresearch_flow/paper/snapshot/mcp_server.py \
        python/deepresearch_flow/paper/snapshot/api.py \
        python/deepresearch_flow/paper/snapshot/tests/test_mcp_transport.py \
        python/deepresearch_flow/paper/snapshot/tests/test_auth.py
git commit -m "refactor(mcp): route bearer auth through config"
```

## Task 4: Refactor MCP server/app creation only as needed

**Files:**
- Modify: `python/deepresearch_flow/paper/snapshot/mcp_server.py`
- Modify: `python/deepresearch_flow/paper/snapshot/api.py`
- Test: `python/deepresearch_flow/paper/snapshot/tests/test_mcp_server_schema_compat.py`
- Test: `python/deepresearch_flow/paper/snapshot/tests/test_helpers.py`
- Test: `python/deepresearch_flow/paper/snapshot/tests/test_mcp_legacy_fallback.py`
- Test: `python/deepresearch_flow/paper/snapshot/tests/test_mcp_transport.py`

**Behavior contract:**

- Existing tool names remain unchanged.
- Existing resource URI templates remain unchanged.
- Existing top-level helper/tool functions remain importable.
- Repeated app construction does not leak auth config between apps.
- OAuth operational routes do not shadow `/api/v1/admin` or ordinary API routes.

**Steps:**

1. Based on Task 1, choose the smallest safe refactor:
   - safe global-auth assignment before `http_app()`, or
   - factory with `register_snapshot_tools(server)` while preserving top-level
     callables.
2. Write black-box/schema compatibility tests before modifying behavior.
3. Run tests and confirm failures if current structure cannot satisfy selected
   route/auth behavior.
4. Implement minimal refactor.
5. Run compatibility tests.

**Commands:**

```bash
uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_helpers.py \
              python/deepresearch_flow/paper/snapshot/tests/test_mcp_server_schema_compat.py \
              python/deepresearch_flow/paper/snapshot/tests/test_mcp_legacy_fallback.py \
              python/deepresearch_flow/paper/snapshot/tests/test_mcp_transport.py -q
```

**Commit:**

```bash
git add python/deepresearch_flow/paper/snapshot/mcp_server.py \
        python/deepresearch_flow/paper/snapshot/api.py \
        python/deepresearch_flow/paper/snapshot/tests/test_helpers.py \
        python/deepresearch_flow/paper/snapshot/tests/test_mcp_server_schema_compat.py \
        python/deepresearch_flow/paper/snapshot/tests/test_mcp_legacy_fallback.py \
        python/deepresearch_flow/paper/snapshot/tests/test_mcp_transport.py
git commit -m "refactor(mcp): prepare auth-aware app creation"
```

## Task 5: Add GitHub OAuth provider wiring and stable-id allowlist

**Files:**
- Modify: `python/deepresearch_flow/paper/snapshot/auth.py`
- Modify: `python/deepresearch_flow/paper/snapshot/mcp_server.py`
- Add or modify: `python/deepresearch_flow/paper/snapshot/tests/test_mcp_oauth.py`

**Behavior contract:**

Endpoint/auth behavior:

- In OAuth mode, unauthenticated MCP requests return an OAuth-discoverable
  challenge.
- OAuth metadata endpoints are reachable through the selected routing model.
- Metadata uses configured `MCP_PUBLIC_BASE_URL`, not request `Host`.
- A valid MCP access token issued/verified by drflow/FastMCP for
  `{MCP_PUBLIC_BASE_URL}/mcp`, derived from an upstream GitHub login with stable
  user id in the allowlist, can access MCP.
- A valid OAuth identity with GitHub stable user id outside the allowlist is
  rejected with `403`.
- Missing stable id claim is rejected.
- Expired token, wrong issuer, wrong audience/resource, missing required scope,
  direct GitHub opaque token, invalid redirect URI, weak/non-S256 PKCE, and
  replayed state are rejected.
- Missing/invalid/replayed OAuth state and missing/invalid PKCE verifier are
  rejected through black-box tests against the final public authorize/token/
  callback behavior. If FastMCP hides part of the flow, the spike must document
  exact version and upstream compliance evidence before any test is exempted.
- Bearer processing order is fixed: exact static token match succeeds as static
  principal; otherwise try OAuth MCP-token verifier; if neither accepts, return
  `401` and do not start browser OAuth fallback. Interactive OAuth challenge is
  only for missing credentials.

**Steps:**

1. Write black-box tests around ASGI/MCP request-response behavior and public
   auth config/verifier contracts.
2. Use fakes/stubs only at the external GitHub OAuth/token/userinfo/JWKS HTTP
   boundary; do not assert internal provider branch behavior.
3. Run tests and confirm failure before implementation.
4. Wire FastMCP GitHub OAuth provider or OAuthProxy.
5. Add stable GitHub user id allowlist enforcement at the provider/verifier
   boundary.
6. Map allowed OAuth principals to read/search scopes.
7. Run targeted OAuth tests.

**Commands:**

```bash
uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_mcp_oauth.py -q
```

**Commit:**

```bash
git add python/deepresearch_flow/paper/snapshot/auth.py \
        python/deepresearch_flow/paper/snapshot/mcp_server.py \
        python/deepresearch_flow/paper/snapshot/tests/test_mcp_oauth.py
git commit -m "feat(mcp): add GitHub OAuth authentication"
```

## Task 6: Add CLI and config file options

**Files:**
- Modify: `python/deepresearch_flow/paper/db.py`
- Modify: `python/deepresearch_flow/paper/config.py` if config-file parsing is
  supported for MCP auth
- Modify: `config.example.toml`
- Test: relevant CLI/config tests under `python/deepresearch_flow/paper/tests/`

**Behavior contract:**

CLI/config behavior:

- `--mcp-access-token` and `MCP_ACCESS_TOKEN` keep working.
- New OAuth settings can be supplied through explicit environment variables and,
  if chosen, config file.
- Invalid OAuth mode without required fields fails at startup with a clear
  non-secret error.
- `bearer_or_oauth` requires both valid static Bearer config and complete OAuth
  config. Static-only deployments must use `bearer`; OAuth-only deployments must
  use `oauth`.
- Source precedence is explicit. In this phase, CLI precedence applies to the
  existing `--mcp-access-token`; OAuth configuration is environment-first unless
  Task 6 explicitly adds named OAuth CLI flags. If TOML support is added, env
  overrides TOML.

**Proposed environment names:**

```text
MCP_AUTH_MODE
MCP_ACCESS_TOKEN
MCP_PUBLIC_BASE_URL
GITHUB_OAUTH_CLIENT_ID
GITHUB_OAUTH_CLIENT_SECRET
MCP_GITHUB_ALLOWED_USER_IDS
```

**Env parsing contract:**

`MCP_GITHUB_ALLOWED_USER_IDS` is a comma-separated list of canonical decimal
GitHub REST `/user.id` values. Trim whitespace; reject empty entries, non-digits,
leading `+`, negative values, and leading-zero forms; compare canonical decimal
strings only; do not log the full raw config if validation fails. In `bearer_or_oauth`, any OAuth
mode requires all OAuth env/config fields; partial OAuth config fails fast.

**Steps:**

1. Add black-box tests for CLI/config behavior.
2. Run tests and confirm failure before implementation.
3. Add options/env handling.
4. Add config-file support only if it fits existing `PaperConfig` without forcing
   unrelated paper model/provider config onto API serving.
5. Add config examples.
6. Run targeted CLI/config tests.

**Commands:**

```bash
uv run pytest python/deepresearch_flow/paper/tests/test_db_api_serve_cli.py -q
```

**Commit:**

```bash
git add python/deepresearch_flow/paper/db.py \
        python/deepresearch_flow/paper/config.py \
        config.example.toml \
        python/deepresearch_flow/paper/tests/test_db_api_serve_cli.py
git commit -m "feat(mcp): expose GitHub OAuth configuration"
```

## Task 7: Verify admin/API route isolation

**Files:**
- Modify tests under `python/deepresearch_flow/paper/snapshot/tests/`
- Modify implementation only if route isolation tests fail.

**Behavior contract:**

- OAuth MCP token does not authorize `/api/v1/admin` endpoints.
- Static MCP token does not authorize admin endpoints. In public/OAuth modes,
  startup fails if `MCP_ACCESS_TOKEN == ADMIN_TOKEN`; no shared-token override is
  part of this design.
- Admin token still authorizes admin endpoints.
- Missing admin token behavior remains unchanged.
- OAuth operational routes do not shadow `/api/v1/*` routes.

**Steps:**

1. Add black-box route isolation tests.
2. Run tests and confirm failures if current routing violates the contract.
3. Fix route/middleware mounting only if needed.
4. Run admin and MCP route tests.

**Commands:**

```bash
uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_admin.py \
              python/deepresearch_flow/paper/snapshot/tests/test_mcp_oauth.py -q
```

**Commit:**

```bash
git add python/deepresearch_flow/paper/snapshot/tests/test_admin.py \
        python/deepresearch_flow/paper/snapshot/tests/test_mcp_oauth.py \
        python/deepresearch_flow/paper/snapshot/api.py \
        python/deepresearch_flow/paper/snapshot/mcp_server.py
git commit -m "test(mcp): verify OAuth route isolation"
```

## Task 8: Docker/nginx and deployment config, if routing requires it

**Files:**
- Modify: Docker compose and nginx templates if OAuth routes live outside already
  proxied `/mcp` and `/mcp-sse` paths
- Test: Docker/nginx tests under `python/deepresearch_flow/paper/tests/`

**Behavior contract:**

- Reverse proxy routes MCP, SSE, and every Task 1 route-table OAuth endpoint.
- Proxy forwards `Authorization` to the API.
- Root-level `/.well-known/*`, `/authorize`, `/token`, `/register`, `/consent`,
  and `/auth/*` routes, if present, are not swallowed by frontend/static routes.
- Docker/image verification records FastMCP version and confirms it matches the
  Task 1 spike version or reruns the spike for the image version.
- SSE buffering is disabled where required.
- Proxy timeouts support long-lived MCP/SSE sessions.
- Public base URL remains config-driven and is not derived from forwarded
  headers.

**Steps:**

1. Based on Task 1 routing result, decide whether Docker/nginx files need edits.
2. If yes, write/update black-box/static config tests.
3. Apply minimal config changes.
4. Run Docker/nginx tests.

**Commands:**

```bash
uv run pytest python/deepresearch_flow/paper/tests/test_docker_nginx_config.py \
              python/deepresearch_flow/paper/tests/test_docker_compose_example.py -q
```

**Commit:**

```bash
git add scripts/docker python/deepresearch_flow/paper/tests/test_docker_nginx_config.py \
        python/deepresearch_flow/paper/tests/test_docker_compose_example.py
git commit -m "chore(mcp): proxy OAuth routes in docker config"
```

## Task 9: Documentation

**Files:**
- Modify: `README.md`
- Modify: `README_ZH.md`

**Content requirements:**

Document:

- static Bearer token usage for CLI/agent clients;
- GitHub OAuth usage for ChatGPT/Claude remote MCP clients;
- GitHub OAuth App, not GitHub App, setup;
- exact GitHub OAuth App Homepage URL;
- exact upstream GitHub OAuth App callback URL from Task 1;
- distinction between MCP client redirect URIs and upstream GitHub callback URI;
- downstream MCP client registration model: DCR, CIMD, or preconfigured clients;
- whether callback path accepts trailing slash;
- local and production examples;
- `MCP_PUBLIC_BASE_URL` and reverse proxy implications;
- stable GitHub user id allowlist semantics;
- username rename risk and why username is not the authorization root;
- required/minimal upstream GitHub OAuth scopes, separate from downstream MCP scopes;
- command/API example for finding numeric GitHub REST `/user.id`;
- migration from `bearer` to `bearer_or_oauth` to optional `oauth`;
- rollback to static Bearer, including OAuth connector cleanup, GitHub OAuth
  secret revocation/rotation when needed, `MCP_ACCESS_TOKEN` rotation after
  leakage, and validation that OAuth challenge is no longer exposed;
- stale static Authorization header preventing OAuth fallback;
- admin API remains separately protected;
- troubleshooting for redirect URI mismatch, wrong public URL, missing secrets,
  disallowed user id, and SSE/proxy buffering.

**Steps:**

1. Update English README after Task 1 freezes callback and metadata paths.
2. Update Chinese README with equivalent content.
3. Ensure README and README_ZH list the same config keys.
4. Run diff check.

**Commands:**

```bash
git diff --check
```

**Commit:**

```bash
git add README.md README_ZH.md
git commit -m "docs(mcp): document GitHub OAuth auth"
```

## Task 10: Full verification

**Files:**
- No new files unless failures require fixes.

**Steps:**

1. Run snapshot test suite.
2. Run broader paper API/CLI tests if CLI config changed.
3. Run Docker/nginx tests if deployment config changed.
4. Run `git diff --check`.
5. Manually validate with MCP Inspector using a table of Client, Mode, Headers,
   Expected status/discovery, and Pass evidence.
6. Manually validate Claude Code remote MCP OAuth.
7. Manually validate ChatGPT/OpenAI connector flow if available.
8. Manually validate one static Bearer client.
9. Manually validate rollback: switch to `bearer`, verify OAuth challenge is not
   exposed, verify Bearer still works, and record whether OAuth connector/secret
   cleanup is needed.
9. Manually validate disallowed GitHub user rejection.

**Commands:**

```bash
uv run pytest python/deepresearch_flow/paper/snapshot/tests -q
uv run pytest python/deepresearch_flow/paper/tests/test_db_api_serve_cli.py -q
git diff --check
```

Additional compatibility commands:

```bash
uv run pytest python/deepresearch_flow/paper/snapshot/tests/test_helpers.py \
              python/deepresearch_flow/paper/snapshot/tests/test_mcp_server_schema_compat.py \
              python/deepresearch_flow/paper/snapshot/tests/test_mcp_legacy_fallback.py \
              python/deepresearch_flow/paper/snapshot/tests/test_mcp_transport.py -q
```

Manual checks:

```bash
npx @modelcontextprotocol/inspector
```

**Commit:**

Only commit fixes found during verification. Otherwise no commit.

## Risks

- FastMCP auth may require construction-time auth, forcing a factory refactor.
- FastMCP/GitHub provider may not expose stable GitHub id in the needed form.
- ChatGPT and Claude may differ in OAuth discovery edge cases.
- A user-configured bad static Authorization header may prevent OAuth fallback in
  Claude Code; docs must explicitly tell users to remove bad static headers when
  testing OAuth.
- Root-level OAuth metadata/callback routes may require Docker/nginx template
  changes.
- Static Bearer in `bearer_or_oauth` remains a powerful bypass if leaked.
- OAuth access-log redaction may need explicit app/proxy configuration to avoid
  logging callback query strings.
- FastMCP version drift between local, CI, and Docker can invalidate route and
  metadata behavior; pin or verify version before release.

## Done criteria

- Static Bearer continues to work for `/mcp`, `/mcp/`, `/mcp-sse`, and
  `/mcp-sse/`.
- OAuth discovery works for remote MCP clients and advertises required tool-level auth metadata where supported.
- Metadata and token audience use the configured canonical `{MCP_PUBLIC_BASE_URL}/mcp`.
- Allowed GitHub stable user ids can authenticate.
- Disallowed GitHub stable user ids cannot authenticate.
- drflow stores no users and has no per-user data model.
- Admin API auth remains independent.
- Relevant automated tests pass.
- MCP compatibility tests pass.
- README and README_ZH explain setup, migration, rollback, and troubleshooting.


## Black-box contract summary

| Surface | Module path / endpoint | Inputs | Return / observable behavior |
|---|---|---|---|
| Auth config validation | `deepresearch_flow.paper.snapshot.auth` public config builder | mode, tokens, OAuth env/config strings, public base URL, allowed id list | typed config or non-secret validation error; no private state assertions |
| Static bearer verification | public static verifier or ASGI `/mcp` endpoints | Authorization header and expected config | authorized/unauthorized result only; no timing assertions |
| OAuth discovery | ASGI `/mcp` and metadata endpoints | missing auth, malicious Host/Forwarded headers | `401` challenge and metadata using configured public base URL |
| OAuth authorization | public OAuth endpoints chosen in Task 1 | client id, redirect URI, resource, PKCE, state | accepts only registered/valid redirect + S256 PKCE; rejects invalid/replayed inputs |
| MCP resource access | ASGI `/mcp`, `/mcp/`, `/mcp-sse`, `/mcp-sse/` | valid/invalid static token; OAuth token only for transports enabled by Task 1 | MCP response or 401/403 according to error semantics |
| Admin isolation | `/api/v1/admin/*` | MCP OAuth/static token vs admin token | MCP tokens rejected; admin token behavior unchanged |
