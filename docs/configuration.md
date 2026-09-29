<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Configuration Guide

Node Wire is configured primarily through environment variables and a YAML configuration file.

## Environment Variables

All secrets and settings are loaded from environment variables. A template is provided at `sample.env`.

```bash
# Linux/macOS/PowerShell
cp sample.env .env

# Windows (CMD)
copy sample.env .env
```

### Required Variables

| Variable | Description |
|----------|-------------|
| `NW_ALLOWED_CONNECTORS` | **Required.** A comma-separated list of connector names to load (e.g., `fhir_epic,http_generic`). Node Wire defaults to a fail-closed policy: unset or empty loads **no** connectors, even when they are `enabled` in `config/connectors.yaml`. |
| `NW_CONNECTOR_MODULE_PREFIX` | Optional. Connectors whose entry-point target module does not start with this prefix are skipped with a warning. Default `node_wire_`; set to `""` to disable the check. |

### Connector Secrets

| Section | Key Variables | When Needed |
|---------|---------------|-------------|
| **FHIR Epic** | `EPIC_FHIR_BASE_URL`, `EPIC_TOKEN_URL`, `EPIC_CLIENT_ID`, `EPIC_KID`, `EPIC_PRIVATE_KEY` | Epic EHR integration |
| **FHIR Cerner** | `CERNER_FHIR_BASE_URL`, `CERNER_TOKEN_URL`, `CERNER_CLIENT_ID`, `CERNER_KID`, `CERNER_PRIVATE_KEY`, `CERNER_SCOPES` | Cerner EHR integration |
| **Google Drive** | `GOOGLE_DRIVE_SA_JSON`, `GOOGLE_DRIVE_FOLDER_ID` | Google Drive connector |
| **SMTP** | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USE_TLS`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `FROM_EMAIL` | Sending emails; relay pinned to env (not request payload) |
| **Slack** | `SLACK_BOT_TOKEN` | Sending Slack messages |
| **Stripe** | `STRIPE_API_KEY` | Stripe payments |
| **Salesforce** | `SALESFORCE_INSTANCE_URL`, `SALESFORCE_TOKEN_URL`, `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET`, `SALESFORCE_REFRESH_TOKEN` | Salesforce CRM integration |
| **LLM / Agent** | `LLM_PROVIDER`, `GROQ_API_KEY` / `GROQ_MODEL`, `NVIDIA_API_KEY` / `NVIDIA_BASE_URL` / `NVIDIA_MODEL`, `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL` / `OPENROUTER_MODEL` / `OPENROUTER_MODELS`, `OLLAMA_BASE_URL` / `OLLAMA_MODEL` / `OLLAMA_API_KEY` (or other provider keys) | AI agent / ToolHive / playground LLM switcher (Groq, NVIDIA, OpenRouter, local Ollama). OpenRouter free models log prompts — do not send PHI/confidential data. |

### Transport & Binding Config

| Variable | Description | Default |
|----------|-------------|---------|
| `MODE` | Execution mode (`API` or `GRPC`). There is no working `MODE=MCP` — that value starts a stub process (`McpServer()` then an infinite sleep loop, no JSON-RPC handling) left over from an early proof of concept. Run MCP via `python -m agents.mcp_entrypoint` instead (see [mcp.md](mcp.md)). | `API` |
| `PORT` | Port for the REST API | `8000` |
| `NW_REST_HOST` | REST API bind address | `127.0.0.1` |
| `NW_REST_PLAYGROUND_ENABLED` | Mount the interactive playground at `/playground/` when `true`; when unset, enabled only if a `playground/` directory exists at the repo root | _(auto)_ |
| `NW_MCP_TRANSPORT` | MCP transport mode (`stdio` or `streamable-http`) | `stdio` |
| `NW_MCP_HOST` | MCP streamable-http bind address | `127.0.0.1` |
| `NW_MCP_PORT` | Port for streamable-http MCP | `8081` |
| `NW_REST_AUTH_DISABLED` | Disable REST API authentication (local dev only) | `false` |
| `NW_MCP_AUTH_DISABLED` | Disable MCP authentication (local dev only); default (unset) enforces auth. The legacy `NW_MCP_AUTH_ENABLED` flag is deprecated. | `false` |
| `NW_MCP_API_KEY` | Shared secret for MCP API-key auth (set in production) | _(unset)_ |
| `NW_MCP_TOOL_MODE` | `list` — every tool in `tools/list`; `search` — list only `nw_search_tools` + `nw_call_tool` and hand out tool schemas on demand (for large connectors). Unknown values fail at startup | `list` |
| `NW_MCP_SCOPE_POLICY_DEFAULT` | Scope policy when action map has no entry: `deny` (conventional `mcp:<connector>.<action>`) or `allow` (map-only) | `deny` |
| `NW_MCP_SCOPE_POLICY_STRICT` | Fail startup if scope policy would be disabled (`allow` + empty map) | `false` |
| `NW_GRPC_API_KEY` | Shared secret for gRPC metadata (`authorization` or `x-api-key`) | _(unset)_ |
| `NW_GRPC_API_KEY_SCOPES` | Scopes for gRPC API key (same format as `NW_MCP_API_KEY_SCOPES`) | _(empty)_ |
| `NW_GRPC_AUTH_DISABLED` | Disable gRPC authentication (local dev only; pair with `NW_MCP_SCOPE_POLICY_DEFAULT=allow` or scoped dev keys) | `false` |
| `NW_GRPC_TLS_CERT_PATH` | gRPC server TLS certificate | _(unset)_ |
| `NW_GRPC_TLS_KEY_PATH` | gRPC server TLS private key | _(unset)_ |
| `NW_GRPC_REQUIRE_TLS` | Fail startup if TLS credentials are missing | `false` |
| `NW_JWT_AUDIENCE` | Expected JWT `aud` claim when any `*_JWT_SECRET` is set (MCP / REST / gRPC) | _(required with JWT secret)_ |
| `NW_JWT_ISSUER` | Expected JWT `iss` claim when any `*_JWT_SECRET` is set | _(required with JWT secret)_ |
| `NW_SMTP_ALLOWED_HOSTS` | Optional comma-separated SMTP relay hostnames permitted for `smtp.send_email` (recommended for production) | _(unset = env relay only)_ |
| `NW_HTTP_GENERIC_ALLOWED_HOSTS` | Optional egress allowlist for the `http_generic` connector only | _(unset)_ |
| `NW_REST_ALLOWED_HOSTS` | Optional egress allowlist for `RestConnector` / OpenAPI-generated connectors (comma-separated hostnames). Distinct from `NW_HTTP_GENERIC_ALLOWED_HOSTS`. | _(unset)_ |
| `NW_REST_TRUST_ENV` | When `true`, generated REST connectors construct httpx with `trust_env=True` so `HTTPS_PROXY` / `HTTP_PROXY` apply. Default remains SSRF-safe (`false`); enabling this re-introduces proxy-based egress — the operator is responsible for proxy trust. Redirects stay disabled. | `false` |

### Authentication & Request Limits

Defaults below are the values in code, not recommendations — see
[Security Best Practices](#security-best-practices) for what to set in production.

| Variable | Description | Default |
|----------|-------------|---------|
| `NW_REST_API_KEY` | Shared secret for REST API-key auth. Send as `Authorization: Bearer <key>` or `X-API-Key: <key>` | _(unset)_ |
| `NW_REST_API_KEY_SCOPES` | Scopes granted to `NW_REST_API_KEY` (JSON array, or comma/space-separated) | _(empty)_ |
| `NW_MCP_API_KEY_SCOPES` | Scopes granted to `NW_MCP_API_KEY` (same format) | _(empty)_ |
| `NW_REST_JWT_SECRET` | HS256 secret for REST JWT ingress auth. Requires `NW_JWT_AUDIENCE` + `NW_JWT_ISSUER` | _(unset)_ |
| `NW_MCP_JWT_SECRET` | HS256 secret for MCP JWT ingress auth | _(unset)_ |
| `NW_GRPC_JWT_SECRET` | HS256 secret for gRPC JWT ingress auth | _(unset)_ |
| `NW_REST_LOAD_DOTENV` | Load `.env` from disk at startup. Set `false` in production | `true` |
| `NW_REST_MAX_BODY_BYTES` | Max JSON body on `/connectors/*` and `/scenarios/*`, rejected before parsing | `10485760` (10 MiB) |
| `NW_REST_TRUSTED_PROXY_HOPS` | Number of reverse proxies in front of the app, so REST's client-IP fallback cannot be spoofed via `X-Forwarded-For`. `0` ignores the header | `0` |

### Rate Limiting

Two independent limiters. The **global token bucket** is always on (unless disabled) and is
shared by every caller, so one noisy identity can still exhaust it for everyone. The
**per-identity sliding window** is opt-in and isolates callers from each other. Both are
`node_wire_runtime` facilities shared by REST, MCP, and gRPC.

| Variable | Description | Default |
|----------|-------------|---------|
| `NW_RATE_LIMIT_DISABLED` | Disable the global token bucket entirely | `false` |
| `NW_RATE_LIMIT_BURST` | Global bucket capacity | `50` |
| `NW_RATE_LIMIT_REFILL_RATE` | Global bucket refill, tokens per second | `10.0` |
| `NW_RATE_LIMIT_PER_IDENTITY_ENABLED` | Enable the per-identity sliding-window limiter | `false` |
| `NW_RATE_LIMIT_PER_IDENTITY_MAX_REQUESTS` | Requests allowed per window, per identity | `120` |
| `NW_RATE_LIMIT_PER_IDENTITY_WINDOW_SECONDS` | Window size in seconds | `60` |
| `NW_RATE_LIMIT_PER_IDENTITY_MAX_TRACKED_KEYS` | LRU cap on tracked identities (bounds memory) | `10000` |
| `NW_RATE_LIMIT_PER_IDENTITY_KEY_TTL_SECONDS` | Idle eviction for a tracked identity (bounds memory) | `3600` |

How callers are keyed:

| Transport | Bucket key | Fallback |
|---|---|---|
| REST | API-key / JWT fingerprint when auth is enabled | client IP (honours `NW_REST_TRUSTED_PROXY_HOPS`) |
| MCP | authenticated principal | one shared per-transport bucket |
| gRPC | authenticated principal | one shared per-transport bucket |

The REST-only `NW_REST_RATE_LIMIT_ENABLED` family (`_MAX_REQUESTS`, `_WINDOW_SECONDS`,
`_MAX_TRACKED_KEYS`, `_KEY_TTL_SECONDS`) still works as a **deprecated alias**; the canonical
`NW_RATE_LIMIT_PER_IDENTITY_*` names take precedence when both are set.

### Multi-tenancy

| Variable | Description | Default |
|----------|-------------|---------|
| `NW_MULTITENANCY_ENABLED` | When `true`, resolve tenant from header / `NW_TENANT_ID` / JWT and require a tenant (missing → error). When `false`, always `__default__`. | `false` |
| `NW_TENANT_ID` | **MCP stdio only** — default process pin. Chat can override via `nw_select_tenant` unless `NW_MCP_TENANT_PIN_LOCKED=true`. Do not set on multi-tenant streamable-http (use `X-Tenant-ID` instead). | _(unset)_ |
| `NW_TENANT_ID_HEADER` | HTTP/gRPC header name for tenant id (case-insensitive) | `X-Tenant-ID` |
| `NW_TENANTS_PATH` | Path to the YAML file that persists runtime named configs + tenant secret overlays (`config/tenants.yaml` by default; gitignored). Loaded by REST, gRPC, and standalone MCP (`McpServer` / `agents.mcp_entrypoint`) at startup. | `config/tenants.yaml` |
| `NW_MCP_ALLOWED_TENANTS` | Comma-separated tenant ids the MCP server may list or select. Empty = all tenants that have configs. | _(unset)_ |
| `NW_MCP_TENANT_PIN_LOCKED` | When `true`, reject `nw_select_tenant` (pin always wins). | `false` |

Named-tenant secrets use `NW_{TENANT}_{CONNECTOR}_{KEY}` for the default config, or `NW_{TENANT}_{CONNECTOR}_{CONFIG}_{KEY}` for a named config (one credential vault per named config). MCP transport details: [nw-mcp-builder](cli/nw-mcp-builder.md#multi-tenancy-mcp).

When multitenancy is enabled, MCP exposes `nw_list_tenants`, `nw_select_tenant` (returns
configs), `nw_list_configs`, and `nw_select_config`. Provision configs via playground REST or
YAML — not via MCP.

**Tenant precedence** differs by transport. Highest wins:

| Transport | 1st | 2nd | 3rd |
|---|---|---|---|
| REST / gRPC | `X-Tenant-ID` header or JWT `tenant` claim (per request) | — | — |
| MCP streamable-http | `X-Tenant-ID` header or JWT claim (per request) | `nw_select_tenant` selection | `NW_TENANT_ID` pin |
| MCP stdio | `nw_select_tenant` selection, unless `NW_MCP_TENANT_PIN_LOCKED=true` | `NW_TENANT_ID` pin | — |

On streamable-http the live per-request header always wins, so a prior `nw_select_tenant`
call can never shadow another concurrent session's request-level tenant. A JWT `tenant` claim
that disagrees with a caller-supplied header or session tenant is rejected with
`TENANT_IDENTITY_MISMATCH` — never silently overridden.

**Config selection:** `nw_select_config` applies to **every connector** on that MCP process
(stdio and streamable-http). A per-call `config_name` tool argument overrides it for a single
call.

**Host / factory contract:** Resolve the request tenant once (`resolve_tenant_id` in bindings, or your own auth in an embedded app), then pass that id to `ConnectorFactory.get(tenant_id=...)`. Omitting `tenant_id` on `get` always resolves `__default__` — never the current HTTP/MCP tenant. After `get`, the connector instance is pinned: `run()` may omit `tenant_id` (uses the pin); a conflicting `run(tenant_id=...)` returns `TENANT_MISMATCH` (`ErrorCategory.AUTH`) without executing the action.

---

## Configuration File (`config/connectors.yaml`)

This file determines which connectors are enabled and which protocols they are exposed through.

```yaml
connectors:
  google_drive:
    enabled: true
    exposed_via:
      - rest
      - grpc
      - mcp
```

- **enabled**: Whether to load the connector at startup.
- **exposed_via**: List of protocols (`rest`, `grpc`, `mcp`).

---

## Secrets Management

The factory selects a secret provider from `NW_SECRET_BACKEND`. Env lookups use the key as
given, then `key.upper()` (e.g. `my_key` then `MY_KEY`).

| Variable | Description | Default |
|----------|-------------|---------|
| `NW_SECRET_BACKEND` | Secret provider to use: `env` or `aws_env` (see below) | `env` |
| `NW_ENV_SECRET_LEGACY_EMPTY` | Return `""` instead of raising for a missing key. Legacy behaviour — do not use in production | `false` |
| `NW_AWS_SECRETS_MANAGER_SECRET_ID` | Secret name or ARN. **Required** when `NW_SECRET_BACKEND=aws_env` | _(unset)_ |
| `AWS_REGION` | AWS region for `aws_env` | `us-east-1` |

Missing keys raise `SecretNotFoundError` (fail-closed) unless `NW_ENV_SECRET_LEGACY_EMPTY=true`.

### Secret backend (`NW_SECRET_BACKEND`)

| Value | Behavior |
|---|---|
| `env` _(default)_ | Reads from process environment. Raises `SecretNotFoundError` for absent keys (fail-closed). |
| `aws_env` | Tries AWS Secrets Manager JSON bundle first; falls back to env on `SecretNotFoundError`. Propagates `SecretProviderError` immediately (broken provider is never silently swallowed). |

Additional cloud backends (`vault`, `azure`, `gcp`) ship as optional extras in
`node-wire-runtime` but are **not** currently wired into the factory — using them
requires custom composition:

```bash
pip install "node-wire-runtime[aws]"    # boto3
pip install "node-wire-runtime[vault]"  # hvac
pip install "node-wire-runtime[azure]"  # azure-keyvault-secrets
pip install "node-wire-runtime[gcp]"    # google-cloud-secret-manager
```

### Google Drive Service Account (Local Example)

For local development, you can set `GOOGLE_DRIVE_SA_JSON` to the absolute path of your service account JSON file.

**PowerShell (Windows):**
```powershell
$saPath = "C:\path\to\service_account.json"
$env:GOOGLE_DRIVE_SA_JSON = Get-Content -Path $saPath -Raw
```

**Bash (Linux/macOS):**
```bash
export GOOGLE_DRIVE_SA_JSON=$(cat /path/to/service_account.json)
```

---

## Security Best Practices

- **Production REST:** Set `NW_REST_API_KEY` and send `Authorization: Bearer <key>` or `X-API-Key: <key>`.
- **Disable Dotenv:** Set `NW_REST_LOAD_DOTENV=false` in production to prevent loading from a `.env` file on disk.
- **Fail-Closed:** Always explicitly list allowed connectors in `NW_ALLOWED_CONNECTORS`.
- **Scope policy:** Unset `NW_MCP_SCOPE_POLICY_DEFAULT` defaults to **deny** in code. Configure `NW_MCP_API_KEY_SCOPES`, `NW_REST_API_KEY_SCOPES`, and `NW_GRPC_API_KEY_SCOPES` (or JWT claims) for each transport. Use `NW_MCP_SCOPE_POLICY_DEFAULT=allow` only for intentional local fail-open.
- **JWT ingress auth:** When using `NW_MCP_JWT_SECRET`, `NW_REST_JWT_SECRET`, or `NW_GRPC_JWT_SECRET`, set `NW_JWT_AUDIENCE` and `NW_JWT_ISSUER`. Minted tokens must include `exp`, `iat`, `aud`, and `iss` (HS256; asymmetric RS256 is not yet supported for bindings).
- **Log redaction:** A platform-wide logging filter redacts PHI-like field names and values (for example `search_params`, `body`, patient identifiers). FHIR connectors log operation mode, HTTP status, and counts only—not request parameters or raw FHIR response bodies.
- **Rate limiting:** The always-on global bucket is shared by every caller, so it only gives coarse DoS protection. Set `NW_RATE_LIMIT_PER_IDENTITY_ENABLED=true` to isolate callers from each other, and set `NW_REST_TRUSTED_PROXY_HOPS` to your reverse-proxy depth so the REST IP fallback is not spoofable. Full reference: [Rate Limiting](#rate-limiting).
- **REST body size:** Set `NW_REST_MAX_BODY_BYTES` (default 10 MiB) to cap JSON bodies on `/connectors/*` and `/scenarios/*` before handlers parse them. Also set `client_max_body_size` (or equivalent) on your reverse proxy for defense in depth.
- **Network bindings:** MCP streamable-http defaults to `NW_MCP_HOST=127.0.0.1`; set `0.0.0.0` only when intentionally exposing beyond localhost. For gRPC, set `NW_GRPC_TLS_CERT_PATH` and `NW_GRPC_TLS_KEY_PATH`, or enable `NW_GRPC_REQUIRE_TLS=true` in production to refuse plaintext startup. Terminate TLS at a reverse proxy if not terminating in-process.
