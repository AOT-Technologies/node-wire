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
| `NW_ALLOWED_CONNECTORS` | **Required.** A comma-separated list of connector names to load (e.g., `fhir_epic,http_generic`). Node Wire defaults to a fail-closed policy. |

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
| **LLM / Agent** | `LLM_PROVIDER`, `GROQ_API_KEY` / `GROQ_MODEL`, `NVIDIA_API_KEY` / `NVIDIA_BASE_URL` / `NVIDIA_MODEL` (or other provider keys) | AI agent / ToolHive / playground LLM switcher |

### Transport & Binding Config

| Variable | Description | Default |
|----------|-------------|---------|
| `MODE` | Execution mode (`API`, `GRPC`, `MCP`) | `API` |
| `PORT` | Port for the REST API | `8000` |
| `NW_REST_HOST` | REST API bind address | `127.0.0.1` |
| `NW_REST_PLAYGROUND_ENABLED` | Mount the interactive playground at `/playground/` when `true`; when unset, enabled only if a `playground/` directory exists at the repo root | _(auto)_ |
| `NW_MCP_TRANSPORT` | MCP transport mode (`stdio` or `streamable-http`) | `stdio` |
| `NW_MCP_HOST` | MCP streamable-http bind address | `127.0.0.1` |
| `NW_MCP_PORT` | Port for streamable-http MCP | `8081` |
| `NW_REST_AUTH_DISABLED` | Disable REST API authentication (local dev only) | `false` |
| `NW_MCP_AUTH_DISABLED` | Disable MCP authentication (local dev only); default (unset) enforces auth. The legacy `NW_MCP_AUTH_ENABLED` flag is deprecated. | `false` |
| `NW_MCP_API_KEY` | Shared secret for MCP API-key auth (set in production) | _(unset)_ |
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

### Multi-tenancy

| Variable | Description | Default |
|----------|-------------|---------|
| `NW_MULTITENANCY_ENABLED` | When `true`, resolve tenant from header / `NW_TENANT_ID` / JWT and require a tenant (missing → error). When `false`, always `__default__`. | `false` |
| `NW_TENANT_ID` | **MCP stdio only** — default process pin. Chat can override via `nw_select_tenant` unless `NW_MCP_TENANT_PIN_LOCKED=true`. Do not set on multi-tenant streamable-http (use `X-Tenant-ID` instead). | _(unset)_ |
| `NW_TENANT_ID_HEADER` | HTTP/gRPC header name for tenant id (case-insensitive) | `X-Tenant-ID` |
| `NW_TENANTS_PATH` | Path to the YAML file that persists runtime named configs + tenant secret overlays (`config/tenants.yaml` by default; gitignored). Loaded by REST and by standalone MCP (`McpServer` / `agents.mcp_entrypoint`) at startup. | `config/tenants.yaml` |
| `NW_MCP_ALLOWED_TENANTS` | Comma-separated tenant ids the MCP server may list or select. Empty = all tenants that have configs. | _(unset)_ |
| `NW_MCP_TENANT_PIN_LOCKED` | When `true`, reject `nw_select_tenant` (pin always wins). | `false` |

Named-tenant secrets use `NW_{TENANT}_{CONNECTOR}_{CONFIG}_{KEY}` (one credential vault per named config). MCP transport details: [mcp-servers.md](mcp-servers.md#multi-tenancy-mcp).

When multitenancy is enabled, MCP exposes `nw_list_tenants`, `nw_select_tenant` (returns configs), `nw_list_configs`, and `nw_select_config`. One select applies to **every connector** on that MCP process (stdio and streamable-http). Env/header is the default pin; chat can switch unless `NW_MCP_TENANT_PIN_LOCKED=true`. Provision configs via playground REST / YAML — not via MCP.

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

The factory uses an `EnvSecretProvider` by default. It looks up keys exactly as provided, and then in uppercase (e.g., `my_key` then `MY_KEY`).

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
- **Per-identity rate limiting (REST, MCP, gRPC):** Off by default; a single global token bucket (`NW_RATE_LIMIT_BURST` / `NW_RATE_LIMIT_REFILL_RATE`, disable via `NW_RATE_LIMIT_DISABLED=true`) always applies on top of it for coarse DoS protection, but that bucket is shared by every caller — one noisy/malicious identity can still exhaust it for everyone else. Enable the opt-in per-identity sliding-window limiter with `NW_RATE_LIMIT_PER_IDENTITY_ENABLED=true` to isolate callers from each other; it's a `node_wire_runtime` facility shared by all three transports; tune it with `NW_RATE_LIMIT_PER_IDENTITY_MAX_REQUESTS`, `NW_RATE_LIMIT_PER_IDENTITY_WINDOW_SECONDS`, `NW_RATE_LIMIT_PER_IDENTITY_MAX_TRACKED_KEYS`, and `NW_RATE_LIMIT_PER_IDENTITY_KEY_TTL_SECONDS` (the last two bound memory). REST keys buckets by API key/JWT fingerprint when auth is enabled, falling back to client IP for unauthenticated traffic; MCP and gRPC key by the authenticated principal, falling back to a shared per-transport bucket when no identity is available. Set `NW_REST_TRUSTED_PROXY_HOPS` to the number of reverse proxies in front of the app (e.g. `1` behind nginx/ALB) so REST's IP fallback isn't spoofable via `X-Forwarded-For`; leave at `0` to ignore it. The legacy `NW_REST_RATE_LIMIT_ENABLED` (and its `_MAX_REQUESTS`/`_WINDOW_SECONDS`/`_MAX_TRACKED_KEYS`/`_KEY_TTL_SECONDS` siblings) still work as a deprecated, REST-only alias for backward compatibility; the canonical `NW_RATE_LIMIT_PER_IDENTITY_*` names take precedence when set.
- **REST body size:** Set `NW_REST_MAX_BODY_BYTES` (default 10 MiB) to cap JSON bodies on `/connectors/*` and `/scenarios/*` before handlers parse them. Also set `client_max_body_size` (or equivalent) on your reverse proxy for defense in depth.
- **Network bindings:** MCP streamable-http defaults to `NW_MCP_HOST=127.0.0.1`; set `0.0.0.0` only when intentionally exposing beyond localhost. For gRPC, set `NW_GRPC_TLS_CERT_PATH` and `NW_GRPC_TLS_KEY_PATH`, or enable `NW_GRPC_REQUIRE_TLS=true` in production to refuse plaintext startup. Terminate TLS at a reverse proxy if not terminating in-process.
