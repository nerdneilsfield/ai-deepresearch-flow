# MCP GitHub OAuth Design

**Date:** 2026-06-10
**Status:** Draft, revised after adversarial review round 0
**Scope:** Snapshot API MCP endpoints and OAuth support for remote MCP clients.

## Decision

Add GitHub OAuth for interactive remote MCP clients while keeping the existing
static Bearer token path for CLI and automation clients.

This is **multi-principal access control**, not a drflow multi-user product:

- Multiple GitHub principals may be allowed.
- All allowed principals access the same snapshot database and the same read-only
  MCP tool/resource surface.
- drflow does not store passwords, sessions, per-user databases, per-user
  preferences, or a user-management UI.
- Admin/write APIs remain on the existing admin-token surface and are not
  authorized by MCP OAuth tokens.

## Source constraints

- OpenAI Apps SDK MCP auth expects protected resource metadata, OAuth metadata,
  authorization-code flow with PKCE, and propagation of the `resource`
  parameter so access tokens are bound to the MCP resource.
  Source: <https://developers.openai.com/apps-sdk/build/auth>
- OpenAI Apps SDK MCP concepts describe MCP auth as OAuth 2.1 based and
  extensible through protected resource metadata, CIMD/DCR, and PKCE.
  Source: <https://developers.openai.com/apps-sdk/concepts/mcp-server>
- Claude Code remote MCP can discover OAuth from `401`/`403` plus a
  `WWW-Authenticate` challenge. If a configured static Authorization header is
  rejected, Claude treats that as a failed static-token connection instead of
  falling back to OAuth.
  Source: <https://docs.anthropic.com/en/docs/claude-code/mcp>
- Claude API MCP connector can receive an already-obtained
  `authorization_token`; API consumers handle OAuth/token refresh outside the
  API call.
  Source: <https://docs.anthropic.com/en/docs/agents-and-tools/mcp-connector>
- FastMCP >=3.2.3 supports server auth providers including OAuth proxies,
  token verifiers, and `MultiAuth`. The implementation spike must use the
  locally installed FastMCP version and record that exact version before relying
  on route, callback, metadata, or token behavior.
  Source: FastMCP docs via Context7.

## Goals

1. Let ChatGPT and Claude remote MCP clients authenticate interactively through
   GitHub OAuth.
2. Let local/agent CLI clients continue using `MCP_ACCESS_TOKEN` as a static
   Bearer token.
3. Avoid implementing drflow-native users or multi-tenant data semantics.
4. Keep MCP read/search access separate from admin/write APIs.
5. Keep existing `/mcp`, `/mcp/`, `/mcp-sse`, and `/mcp-sse/` path compatibility.
6. Make OAuth metadata, callback, and resource/audience binding explicit and
   testable before implementation proceeds.

## Non-goals

- No drflow user table.
- No password login.
- No session dashboard.
- No per-user paper libraries.
- No per-user permissions beyond allow/deny plus read/search scopes.
- No OAuth exposure for admin/write APIs.
- GitHub OAuth access tokens are never accepted as MCP resource Bearer tokens.
- No trust in request `Host` or forwarded headers for issuer/resource/callback
  generation unless a future design explicitly defines trusted proxies.

## Authentication modes

Introduce explicit MCP auth mode:

```toml
[mcp.auth]
mode = "bearer_or_oauth" # none | bearer | oauth | bearer_or_oauth
```

Mode semantics:

| Mode | Behavior |
|---|---|
| `none` | MCP endpoints are public. Allowed only for local/dev or explicit public override. |
| `bearer` | Existing static Bearer token only. |
| `oauth` | GitHub OAuth only. Recommended for public interactive MCP deployments after migration. |
| `bearer_or_oauth` | Both static Bearer and complete GitHub OAuth are configured; either valid credential type can authenticate. Use for migration and automation. |

Default behavior:

- If `MCP_ACCESS_TOKEN` or `--mcp-access-token` is set and no auth mode is set,
  default to `bearer` to preserve compatibility.
- `none` must be explicit. Binding to a non-localhost host in `none` mode should
  fail fast unless an explicit `--allow-public-mcp` style override is provided.
- `bearer_or_oauth` requires both valid static Bearer config and complete OAuth
  config. It is not a stronger security mode than OAuth; it intentionally keeps
  a static-token bypass for CLI/automation, so deployments must treat that static
  token as a high-value secret. Use `bearer` for static-only deployments.

Recommended migration configuration:

```toml
[mcp.auth]
mode = "bearer_or_oauth"

[mcp.auth.bearer]
access_token = "env:MCP_ACCESS_TOKEN"

[mcp.auth.github]
enabled = true
public_base_url = "https://drflow.example.com"
client_id = "env:GITHUB_OAUTH_CLIENT_ID"
client_secret = "env:GITHUB_OAUTH_CLIENT_SECRET"
allowed_user_ids = ["12345678"]
# Optional readability check only. Authorization uses stable ids.
allowed_users = [{ id = "12345678", login = "dengqi" }]
```

Recommended steady-state public configuration after static clients migrate:

```toml
[mcp.auth]
mode = "oauth"
```

## Identity model

GitHub `login` is not a stable authorization root because users can rename their
accounts. Authorization must use a stable GitHub user identifier:

```text
provider = github
subject = GitHub stable numeric GitHub REST `/user.id`
login = GitHub login, display/diagnostic only
```

Authorization decision:

```text
allowed = github.stable_user_id in allowed_user_ids
```

`allowed_users = [{ id, login }]` may be supported as a readability layer. At
startup or first authorization, drflow should verify that the configured login
matches the stable id when feasible, but mismatch must not authorize a different
identity. If a username-only compatibility option is ever added, it must be
marked insecure/deprecated and either resolved to a stable id before use or
rejected in production.

## Authorization-server boundary

For MCP clients, drflow/FastMCP is the MCP authorization server and resource
server integration. GitHub is only the upstream identity provider.

Required boundary:

- ChatGPT, Claude, MCP Inspector, and other MCP clients interact with
  drflow/FastMCP OAuth metadata, authorization, token, and resource endpoints.
- GitHub OAuth is used only between drflow/FastMCP and GitHub to authenticate the
  human GitHub account.
- GitHub opaque access tokens are never accepted directly as MCP Bearer tokens at
  `/mcp` or `/mcp-sse`.
- After upstream GitHub login, drflow/FastMCP must issue or verify a
  resource-bound MCP access token for `{MCP_PUBLIC_BASE_URL}/mcp`.
- If the chosen FastMCP GitHub provider cannot prove this MCP-token boundary, the
  OAuth implementation must stop and return to design instead of degrading to
  direct GitHub-token acceptance.

## Upstream GitHub token/session storage

GitHub tokens are upstream credentials. They are used only to fetch/verify the
GitHub numeric `/user.id` during login. They must not be exposed to MCP clients
and must not be accepted at resource endpoints. Do not persist GitHub access or
refresh tokens unless the chosen FastMCP/OAuthProxy implementation requires it;
if persistence is unavoidable, define TTL, encryption/secret storage, redaction,
rotation, revocation, and backup handling before implementation. No upstream
GitHub refresh token is accepted in this phase unless explicitly added by a
separate design.

## Token model

Preferred behavior:

1. GitHub authenticates the browser/user.
2. FastMCP or drflow verifies the OAuth result and derives a GitHub stable user
   id.
3. drflow authorizes the principal against `allowed_user_ids`.
4. The MCP request is accepted only if the token is valid for the canonical MCP
   resource and has the required scopes.

MCP access tokens accepted by drflow must be FastMCP/drflow-issued or otherwise
cryptographically/provider-verified tokens for this MCP resource. They must
satisfy these checks, whether verified by FastMCP or custom code:

- valid signature or provider verification result;
- expected issuer;
- expected audience/resource;
- unexpired `exp`;
- valid `nbf`/`iat` if present;
- stable `sub`/GitHub numeric database id claim;
- required scope, initially `mcp:read` and/or `mcp:search`;
- signing algorithm and key source restricted to the configured provider.

Rejected tokens include:

- expired tokens;
- missing or wrong audience/resource;
- wrong issuer;
- missing required scope;
- tokens with unknown/unsafe signing algorithm;
- GitHub OAuth access tokens directly presented to MCP. GitHub tokens may be
  used only in the upstream login/token-exchange step, never as resource-server
  bearer credentials.

Static `MCP_ACCESS_TOKEN` must map to a fixed local principal such as
`mcp-static` with the same read/search scopes. Static-token verification must
preserve constant-time comparison semantics. Do not replace the existing static
path with a development-only verifier unless the verifier is audited and the
security behavior is explicitly accepted.

## OAuth flow requirements

OAuth-capable modes must support:

- a frozen downstream MCP OAuth client model: DCR, CIMD, or preconfigured
  clients. Open DCR is disabled unless Task 1 proves it is required and safely
  constrained. If DCR is enabled, registration policy must restrict redirect
  schemes/hosts/classes for ChatGPT, Claude, and MCP Inspector and reject
  malicious registered redirects;
- OAuth authorization-code flow with PKCE S256;
- rejection of `plain` PKCE, missing `code_challenge_method`, weak verifier,
  invalid verifier length/charset, and replayed verifier/state;
- high-entropy one-time `state` with short TTL;
- state bound to the original client/resource/redirect context;
- replay rejection for state/code exchange;
- protected resource metadata;
- authorization server metadata;
- `WWW-Authenticate` challenge that points clients to reachable OAuth discovery
  metadata;
- preservation/propagation of the `resource` parameter;
- exact validation of OAuth client `redirect_uri`; no wildcard host/scheme, no
  reflection of arbitrary redirect URI, and no acceptance of unregistered clients
  unless the chosen DCR policy explicitly permits and constrains them;
- server-side binding of `state` to `client_id`, `redirect_uri`, `resource`, and
  `code_challenge`;
- callback and token exchange behavior compatible with ChatGPT, Claude remote
  MCP, FastMCP clients, and MCP Inspector.

## Redirect URI classes

The design has two distinct redirect concepts:

| Redirect | Owned by | Example | Purpose |
|---|---|---|---|
| MCP client redirect URI | ChatGPT/Claude/MCP client | ChatGPT connector callback or Claude local callback | drflow/FastMCP authorization server redirects the MCP client after authorization |
| Upstream GitHub callback URI | drflow/FastMCP OAuthProxy | `{MCP_PUBLIC_BASE_URL}/auth/github/callback` or spike-frozen equivalent | GitHub redirects the human browser back to drflow/FastMCP after GitHub login |

The GitHub OAuth App callback URL is the upstream GitHub callback URI, not the
ChatGPT/Claude MCP client redirect URI. Task 1 must freeze both classes and the
downstream MCP client registration model before README instructions are written.
`GITHUB_OAUTH_CLIENT_ID` and `GITHUB_OAUTH_CLIENT_SECRET` are upstream GitHub
OAuth App credentials, not MCP client credentials.

## Canonical resource and routing

Canonical MCP resource for OAuth is:

```text
{MCP_PUBLIC_BASE_URL}/mcp
```

where `MCP_PUBLIC_BASE_URL` is a configured HTTPS origin such as
`https://drflow.example.com`.

Rules:

- `MCP_PUBLIC_BASE_URL` is configuration, not derived from request headers.
- Non-loopback `MCP_PUBLIC_BASE_URL` must be HTTPS. Non-HTTPS is allowed only for loopback hosts and only with an explicit development flag.
- It must not include `/mcp`, query, or fragment.
- Trailing slash is normalized away.
- Request `Host`, `X-Forwarded-*`, and `Forwarded` are not trusted for issuer,
  resource, metadata, authorization redirect, GitHub `redirect_uri`, issuer, resource, or callback URL generation in this design.
- `/mcp` and `/mcp/` both serve the same streamable HTTP MCP resource.
- `/mcp-sse` and `/mcp-sse/` remain supported. The implementation spike must
  decide whether SSE shares the `/mcp` OAuth resource or uses static Bearer only;
  this decision must be frozen before coding OAuth.

### Required routing spike before implementation

The current code mounts FastMCP as `Mount("/mcp", app=mcp.http_app(path="/"))`.
That can produce incorrect OAuth metadata, callback paths, and resource values.
Before implementing OAuth, perform a spike that proves the final routing model by
black-box ASGI requests:

- unauthenticated `POST /mcp` returns a discoverable challenge;
- the challenge's metadata URL is reachable from the public app;
- protected resource metadata identifies `{MCP_PUBLIC_BASE_URL}/mcp`;
- authorization server metadata is reachable at the documented path and contains
  at least `issuer`, `authorization_endpoint`, `token_endpoint`,
  `code_challenge_methods_supported` containing `S256`, and token endpoint
  authentication metadata consistent with implementation;
- GitHub callback URL is exact and documented;
- no duplicate or conflicting OAuth metadata/callback routes are exposed for
  `/mcp` and `/mcp-sse`;
- a final route table is recorded for MCP, metadata, authorization, token,
  registration if any, consent if any, and upstream GitHub callback routes;
- the downstream client-registration model is recorded as `dcr`, `cimd`, or
  `preconfigured`, including `token_endpoint_auth_methods_supported` and whether
  ChatGPT/Claude need client credentials;
- whether `_McpTrailingSlashMiddleware` remains active, and the exact FastMCP
  `http_app(path=...)` value, are recorded;
- `scopes_supported` is recorded; if `offline_access` is present, refresh-token
  TTL, rotation, revocation, and allowlist-change delay must be frozen. If
  refresh tokens are not supported, `offline_access` must not be advertised.

Preferred implementation direction is to expose OAuth operational routes at the
main Starlette app root when required, and ensure FastMCP sees the external MCP
path as `/mcp` rather than `/`. If that is not feasible, the mounted-path design
must be explicitly documented and verified against real clients.

## Endpoint behavior

| Request | Expected behavior |
|---|---|
| `POST /mcp` with valid static Bearer | 200 MCP response, no redirect |
| `POST /mcp/` with valid static Bearer | 200 MCP response |
| `GET /mcp-sse` with valid static Bearer | SSE response, no redirect |
| `GET /mcp-sse/` with valid static Bearer | SSE response |
| no auth in `bearer` mode | 401 with Bearer challenge |
| no auth in OAuth-capable mode | 401 with OAuth-discoverable `WWW-Authenticate` challenge |
| valid OAuth token, allowed GitHub stable user id | 200 MCP response |
| valid OAuth token, disallowed GitHub stable user id | 403 |
| invalid/unknown Bearer token | try exact static-token match first, then OAuth MCP-token verifier; if neither accepts, return 401 without starting interactive OAuth fallback |
| OAuth token on admin API | rejected; admin API keeps separate auth |
| `MCP_ACCESS_TOKEN == ADMIN_TOKEN` in public/OAuth mode | fail fast unless a future explicit unsafe override is designed |
| admin token on MCP | not treated as OAuth or MCP static token unless explicitly equal to MCP token |

Claude Code behavior makes invalid static headers important: if users configure a
bad static header, they should fix/remove it rather than unexpectedly entering
OAuth.

## Architecture

### Current state

Current MCP auth is an outer ASGI wrapper:

```text
Starlette Mount(/mcp) -> bearer_auth_app -> FastMCP http_app(path="/")
```

This works for static Bearer and avoids trailing-slash redirects, but it cannot
provide complete OAuth metadata/flows without routing changes.

### Target state

Use FastMCP-managed OAuth where possible, plus drflow-controlled static token
verification:

```text
MCP auth layer
  interactive provider: GitHub OAuth / OAuthProxy
  token verifiers:
    - GitHub/OAuth verifier with stable id allowlist
    - drflow static bearer verifier using constant-time compare
```

Auth must happen before MCP tool/resource dispatch. Do not add allowlist checks
inside every tool.

### Global FastMCP object constraint

The current code defines `mcp = FastMCP("Paper DB MCP")` as a module-level global
and registers tools/resources against it. If auth must be supplied at construction
time, a factory refactor is required.

Acceptable refactor constraints:

- preserve all current tool names;
- preserve all current resource URI templates;
- keep top-level helper/tool functions importable by existing tests;
- verify `test_mcp_server_schema_compat.py`, `test_helpers.py`, and
  `test_mcp_legacy_fallback.py` after changes;
- avoid duplicate registration across repeated app construction.

Smallest acceptable spike path:

1. Determine whether `mcp.auth` can be assigned safely before `http_app()`.
2. If safe, document the single-config assumption and test repeated app creation
   does not leak auth across configurations.
3. If unsafe, extract `register_snapshot_tools(server)` while keeping tool
   callables top-level, then create per-config FastMCP servers.

## Scope model

Initial downstream MCP scopes:

```text
mcp:read   # list/read paper metadata, resources, summaries, source text
mcp:search # keyword/semantic search tools
```

GitHub upstream scopes are separate from MCP scopes. GitHub scopes are only for
identifying the upstream user id and must remain minimal; they must never map
directly to MCP permissions. The spike/docs must verify whether no GitHub scope
or a minimal public-profile scope is sufficient to read GitHub REST `/user.id`;
`repo` and broad GitHub scopes are out of scope.

Initial public scope mapping:

| MCP surface | Required scope |
|---|---|
| metadata/list/filter/detail resources and tools | `mcp:read` |
| keyword, semantic, and advanced search tools | `mcp:search` plus `mcp:read` if result payload includes paper metadata |
| future write/admin tools | not exposed through OAuth; fail closed |

Rules:

- Existing read/search tools get one or both scopes.
- New MCP tools must declare required scopes before exposure.
- Future write/admin tools default to not exposed through OAuth.
- Tool/resource schema or metadata must expose required auth schemes/scopes where
  FastMCP/OpenAI Apps SDK supports it. Protected tools should advertise OAuth
  `securitySchemes`, and auth-required/insufficient-scope MCP errors should
  include `_meta["mcp/www_authenticate"]` when the framework supports tool-level
  challenges.
- Static bearer principal gets the same read/search scopes, not admin powers.

## Error semantics

- Missing credentials in static-only mode: `401`, `WWW-Authenticate: Bearer`.
- Missing credentials in OAuth-capable mode: `401` with OAuth-discoverable
  `WWW-Authenticate` challenge.
- Unknown, invalid, expired, wrong-issuer, or wrong-audience Bearer token:
  `401` with challenge; no browser OAuth fallback for that same request.
- Authenticated GitHub principal not in allowlist: `403`.
- OAuth token missing required scope: prefer a challenge-capable `401` or
  tool-level `_meta["mcp/www_authenticate"]`; use `403` only when reauth cannot
  fix the denial.
- Expired/wrong issuer/wrong audience token: `401`.
- Misconfigured OAuth provider at startup: fail fast with a clear non-secret
  message.

Example startup errors:

```text
MCP OAuth config error: GITHUB_OAUTH_CLIENT_ID is required when mode=oauth
MCP OAuth config error: GITHUB_OAUTH_CLIENT_SECRET is required when mode=oauth
MCP OAuth config error: MCP_PUBLIC_BASE_URL must be an absolute https URL in production
MCP OAuth config error: allowed_user_ids must contain at least one GitHub user id
```

## Logging

Log security-relevant events without secrets:

- auth mode selected;
- OAuth provider enabled;
- allowlist count, not identities at info level;
- request id / correlation id;
- auth event type, provider, result, reason code;
- missing token;
- invalid token;
- disallowed GitHub id as a hash or redacted id at info level;
- OAuth config missing required fields.

Never log access tokens, authorization codes, client secrets, full
`Authorization` headers, or callback URLs containing codes/state. GitHub login may
appear only in debug logs and only when debug logging is explicitly enabled.

## Testing strategy

Follow the project black-box testing policy. Tests should assert only externally
observable request/response behavior and public function contracts.

Allowed fake boundary:

- fake external GitHub OAuth/token/userinfo/JWKS HTTP service;
- fake public config values.

Avoid:

- asserting FastMCP provider class trees;
- asserting internal branch structure;
- asserting private state;
- asserting provider call counts/order unless exposed as observable behavior.

Test contracts must cover:

1. Static Bearer authenticates `/mcp`, `/mcp/`, `/mcp-sse`, and `/mcp-sse/`.
2. OAuth-capable mode exposes a reachable OAuth challenge and metadata.
3. Metadata, `WWW-Authenticate`, authorization redirects, GitHub `redirect_uri`, issuer, resource, and callback URLs use configured `MCP_PUBLIC_BASE_URL`, not request `Host`, `X-Forwarded-*`, or `Forwarded`.
4. Authorized stable GitHub user id authenticates.
5. Disallowed stable GitHub user id is rejected with `403`.
6. Expired token, wrong issuer, wrong audience, missing scope, direct GitHub opaque token, weak PKCE, invalid redirect URI, and replayed state are rejected.
7. Missing/invalid/replayed OAuth state and missing/invalid PKCE verifier are
   rejected, if the chosen implementation owns those steps.
8. OAuth tokens cannot access admin/write endpoints.
9. Admin tokens still authorize admin endpoints and do not implicitly authorize
   MCP.
10. Misconfigured OAuth mode fails at startup with actionable non-secret errors.
11. Existing MCP schema/resource compatibility tests continue passing.

## Operations checklist

### Preflight

- [ ] Server public URL is HTTPS and matches `MCP_PUBLIC_BASE_URL`.
- [ ] `MCP_PUBLIC_BASE_URL` has no `/mcp`, query, or fragment.
- [ ] GitHub OAuth App Homepage URL is set to the public base URL.
- [ ] GitHub OAuth App callback URL exactly matches the documented callback path.
- [ ] `GITHUB_OAUTH_CLIENT_ID` is set.
- [ ] `GITHUB_OAUTH_CLIENT_SECRET` is set through env/secret manager, not committed.
- [ ] `allowed_user_ids` is configured as a TOML array of canonical decimal GitHub REST `/user.id` values and reviewed. The environment variable form is comma-separated.
- [ ] Static `MCP_ACCESS_TOKEN` remains configured during migration if using
      `bearer_or_oauth`.
- [ ] Admin/write API token remains separate from MCP auth.

### Reverse proxy / Docker

- [ ] TLS terminates correctly before the app or at proxy.
- [ ] Proxy routes `/mcp`, `/mcp-sse`, every spike-recorded OAuth operational route, and callback paths to the API app.
- [ ] Proxy does not buffer SSE.
- [ ] Proxy read timeout supports long MCP/SSE sessions.
- [ ] Container env contains OAuth and Bearer variables expected by config.
- [ ] Logs and access logs do not print tokens, auth codes, client secrets, full
      Authorization headers, or callback query strings.

### Automated verification

- [ ] `/mcp` valid Bearer succeeds.
- [ ] `/mcp/` valid Bearer succeeds.
- [ ] `/mcp-sse` valid Bearer succeeds.
- [ ] `/mcp-sse/` valid Bearer succeeds.
- [ ] Missing auth in bearer mode returns 401 Bearer challenge.
- [ ] Missing auth in OAuth mode returns OAuth-discoverable challenge.
- [ ] OAuth metadata endpoints are reachable.
- [ ] OAuth allowed GitHub stable user id can call MCP.
- [ ] OAuth disallowed GitHub stable user id is rejected.
- [ ] OAuth token cannot access admin/write API.
- [ ] Invalid OAuth config fails startup with actionable non-secret error.

### Manual client verification

- [ ] MCP Inspector can connect.
- [ ] Claude Code remote MCP can complete OAuth.
- [ ] ChatGPT/OpenAI connector flow is tested when available.
- [ ] A stale/wrong static Bearer header fails clearly.
- [ ] Removing static Authorization header allows OAuth flow.
- [ ] Disallowed GitHub account cannot access MCP.

### Migration and rollback

- [ ] Current Bearer clients documented.
- [ ] Deploy `bearer_or_oauth` first.
- [ ] Validate Bearer still works.
- [ ] Validate OAuth with allowed user id.
- [ ] Instruct OAuth users to remove static Authorization headers.
- [ ] Optionally switch to `oauth` only after clients migrate.
- [ ] Rollback path documented: set mode back to `bearer`, keep or rotate
      `MCP_ACCESS_TOKEN`, restart/redeploy, disable/remove OAuth connectors,
      revoke or rotate GitHub OAuth client secret when needed, and verify OAuth
      challenge is no longer exposed.

### Troubleshooting docs

- [ ] `redirect_uri_mismatch`.
- [ ] MCP token audience/resource mismatch.
- [ ] server clock skew, expired token, and `nbf` failures.
- [ ] wrong public base URL / proxy scheme.
- [ ] missing client id/secret.
- [ ] disallowed GitHub user id.
- [ ] stale Bearer header prevents OAuth fallback.
- [ ] SSE/proxy buffering.

## Rollout

1. Preserve default static Bearer behavior.
2. Complete the FastMCP auth routing spike and freeze callback/metadata paths.
3. Add config and docs before encouraging OAuth use.
4. Ship GitHub OAuth as opt-in.
5. Validate with FastMCP client, MCP Inspector, Claude Code remote MCP, and
   ChatGPT/OpenAI connector when available.
6. Migrate from `bearer_or_oauth` to `oauth` where public clients no longer need
   the static-token fallback.

## Open questions to resolve in the spike

These are blocking before OAuth implementation and README callback instructions:

1. Does FastMCP expose GitHub stable user id in claims directly, or do we need a
   custom GitHub userinfo verifier/wrapper?
2. Can a drflow custom static token verifier integrate with FastMCP `MultiAuth`
   while preserving constant-time compare?
3. What exact callback path does the chosen FastMCP GitHub provider require in
   the current Starlette routing model?
4. Are OAuth operational routes root-level (`/.well-known`, `/auth/callback`) or
   mounted under `/mcp`? Which form works with ChatGPT/Claude/MCP Inspector?
5. Should `/mcp-sse` be one of: A) static-Bearer only; B) OAuth-enabled sharing
   `{base}/mcp` audience; or C) OAuth-enabled with its own `{base}/mcp-sse`
   resource and metadata? This choice controls tests and README.
6. Does token TTL come from FastMCP/provider defaults, or can drflow configure it
   safely? Target MCP access token TTL is short-lived, preferably 5-15 minutes.
7. Are refresh tokens issued? If yes, what rotation/revocation behavior is used?
8. How quickly do allowlist changes and GitHub revocation take effect?
