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
| **LLM / Agent** | `LLM_PROVIDER`, `GROQ_API_KEY` (or other provider key) | AI agent / ToolHive |

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
- **REST rate limiting:** Enable with `NW_REST_RATE_LIMIT_ENABLED=true`. Per-identity buckets are keyed by API key/JWT fingerprint when auth is enabled; unauthenticated traffic falls back to client IP. Bound memory with `NW_REST_RATE_LIMIT_MAX_TRACKED_KEYS` and `NW_REST_RATE_LIMIT_KEY_TTL_SECONDS`. Set `NW_REST_TRUSTED_PROXY_HOPS` to the number of reverse proxies in front of the app (e.g. `1` behind nginx/ALB); leave at `0` to ignore client-supplied `X-Forwarded-For`.
- **REST body size:** Set `NW_REST_MAX_BODY_BYTES` (default 10 MiB) to cap JSON bodies on `/connectors/*` and `/scenarios/*` before handlers parse them. Also set `client_max_body_size` (or equivalent) on your reverse proxy for defense in depth.
- **Network bindings:** MCP streamable-http defaults to `NW_MCP_HOST=127.0.0.1`; set `0.0.0.0` only when intentionally exposing beyond localhost. For gRPC, set `NW_GRPC_TLS_CERT_PATH` and `NW_GRPC_TLS_KEY_PATH`, or enable `NW_GRPC_REQUIRE_TLS=true` in production to refuse plaintext startup. Terminate TLS at a reverse proxy if not terminating in-process.
