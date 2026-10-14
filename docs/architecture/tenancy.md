<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Tenancy

How Node Wire isolates callers by tenant: where the tenant comes from, how named configs entitle
it, where its secrets live, and how MCP sessions select them. This page owns that behaviour.
Environment variables are listed in [Configuration — Multi-tenancy](../configuration.md#multi-tenancy).
A runnable walkthrough is in [Use a connector](../connectors.md).

Read [Domain](domain.md#tenancy) and [Seams](seams.md) first.

## Single-tenant by default

With `NW_MULTITENANCY_ENABLED` unset or `false`, every call resolves to `__default__`, and the
connectors enabled in `config/connectors.yaml` are that tenant's configs. Set it to `true` to
resolve a tenant per request. `NW_TENANTS_PATH` (default `config/tenants.yaml`, gitignored) holds
the named configs and per-tenant secret overlays. REST, gRPC and standalone MCP
(`agents.mcp_entrypoint`, generated hosts, ToolHive images) all load it at startup.
`config/tenants.example.yaml` shows the format.

## Entitlement and named configs

- **A config existing is what entitles a tenant** to a connector. A tenant with no config gets
  `ConfigNotFoundError` from the factory, which is `403` on REST.
- The first config created for a `(tenant, connector)` pair becomes its default. Pick another with
  `config_name` (the `factory.get` argument, the REST body field or the MCP tool argument).
- Configs are created, changed and deleted through the store: the REST API
  (`POST`/`GET`/`PUT`/`DELETE /v1/connectors/{cid}/configs[/{name}]`, `PUT …/{name}/default`), the
  playground, or by editing `tenants.yaml`. **MCP never writes configs.** `__default__` is the
  exception: it comes from `connectors.yaml` only, and `/v1` writes to it return `400`.
- A store write takes effect on the next `get()`, because the cached instance for that config is
  dropped at once.

## How the tenant is resolved

The highest source wins:

| Transport | 1st | 2nd | 3rd |
|---|---|---|---|
| REST / gRPC | The caller's tenant (tenant-bound key or JWT `tenant` claim), else `X-Tenant-ID` header (see below) | — | — |
| MCP streamable-http | The caller's tenant, else `X-Tenant-ID` header (per request) | `nw_select_tenant` selection | `NW_TENANT_ID` pin |
| MCP stdio | `nw_select_tenant` selection, unless `NW_MCP_TENANT_PIN_LOCKED=true` | `NW_TENANT_ID` pin | — |

- `NW_TENANT_ID_HEADER` renames the header. The tenant never comes from the URL, and it is never a
  connector-tool argument.
- With multi-tenancy on, a request with no tenant fails with `400 MISSING_TENANT`. There is no
  fallback to `__default__`.
- On streamable-http the per-request header always wins, so one session's `nw_select_tenant` can
  never shadow another concurrent session's tenant. A tool's `tenant_id` argument cannot leave the
  request tenant either.
- A tenant claim (JWT or key) that disagrees with the header fails closed with
  `403 TENANT_IDENTITY_MISMATCH`. It is never silently overridden.

## Who may pick a tenant

The `X-Tenant-ID` header is supplied by the caller, so node-wire does not trust it on its own.

- **`__default__` is operator-only.** A caller can never select it, by header or by token claim
  (`403 TENANT_IDENTITY_MISMATCH`). It is reached only through the `NW_TENANT_ID` stdio pin and
  operator/YAML configuration. It holds the operator's env-backed secrets.
- **Tenant-bound credentials** carry their own tenant, so the header can only agree with it.
  Use a JWT with a `tenant_id` claim, or an API-key registry file.
- **Tenant-less credentials** (the shared `NW_*_API_KEY`, or a JWT with no tenant claim) may supply a
  header only when `NW_TRUSTED_TENANT_HEADER=true`, meaning an authenticating gateway in front sets
  it. Otherwise the request is rejected with `403 TENANT_IDENTITY_MISMATCH`.
- With auth disabled (local development) or an embedding host that authenticates itself, there is
  no identity and the header is taken as given.

At startup, with multi-tenancy on and a shared API key configured, node-wire refuses to start unless
`NW_API_KEYS_FILE` or `NW_TRUSTED_TENANT_HEADER=true` is set. It logs the chosen tenancy mode.

### Tenant-bound API keys

Point `NW_API_KEYS_FILE` at a JSON array. It is used by REST, gRPC and MCP, and is read once at
startup (restart to change it):

```json
[{"key_id": "acme-prod", "hash": "<sha256 hex of the key>", "tenant": "acme", "scopes": ["mcp:acme.read"]}]
```

Only the hash is stored. Generate the key with `openssl rand -hex 32` (keys must be random, not
chosen: the hash is plain SHA-256, which is only safe for high-entropy secrets) and hash it with
`printf %s "$KEY" | sha256sum`. A key is presented like any API
key (`Authorization: Bearer` or `X-API-Key`) and its `scopes` apply to it alone;
`NW_*_API_KEY_SCOPES` apply only to the shared key. A `tenant` of `__default__` is refused at load.
To rotate, add a second entry for the same tenant, then remove the old one.

### Upgrading

| Change | Migration |
|---|---|
| Callers can no longer select `__default__` | Use a named tenant. |
| MCP streamable-http with no tenant is `400`, not `__default__` | Send a tenant header, or use a tenant-bound key. |
| Multi-tenant + shared API key refuses to start | Set `NW_API_KEYS_FILE`, or `NW_TRUSTED_TENANT_HEADER=true` behind a gateway. |
| A JWT with no tenant claim can no longer pick a tenant by header | Add a `tenant_id` claim, or set `NW_TRUSTED_TENANT_HEADER=true`. |

## The pin contract (hosts embedding the factory)

Resolve the request tenant once, with `resolve_tenant_id` in bindings or your own auth, then call
`ConnectorFactory.get(tenant_id=...)`. **Omitting `tenant_id` on `get` always resolves
`__default__`, never "the current request".** The returned instance is pinned. `run()` may omit
`tenant_id`, and a conflicting `run(tenant_id=...)` returns `TENANT_MISMATCH` (`ErrorCategory.AUTH`)
without running the action. Nested `call_action` calls inherit the pin.

## Tenant secrets

A named tenant's secret is looked up under one scoped key:

- `NW_{TENANT}_{CONNECTOR}_{KEY}` for the default config;
- `NW_{TENANT}_{CONNECTOR}_{CONFIG}_{KEY}` for a named config (one credential vault per named config).

The value comes from the config's `secrets:` block in `tenants.yaml` (loaded into an in-memory
overlay that is checked first), then from the environment. Non-alphanumeric characters become `_` and the name is upper-cased: config `test-drive` for tenant
`acme` on Google Drive reads `NW_ACME_GOOGLE_DRIVE_TEST_DRIVE_SA_JSON`. A missing secret fails
with `tenant secret not found: <tenant>/<connector>/...`.

## MCP tenant and config tools

With multi-tenancy on, every Node Wire MCP server (`agents.mcp_entrypoint`, per-connector images
and `nw-mcp-builder` hosts) adds these tools:

| Tool | Does |
|---|---|
| `nw_list_tenants` `{connector_id?}` | Lists tenants with configs, plus `current_tenant_id` / `pinned_tenant_id`. `NW_MCP_ALLOWED_TENANTS` limits the list. |
| `nw_select_tenant` `{tenant_id}` | Sets the session tenant for **every connector** on this process and returns its configs. Rejected when `NW_MCP_TENANT_PIN_LOCKED=true`. Unknown tenants fail closed. |
| `nw_list_configs` `{connector_id?, tenant_id?}` | Lists named configs for the selected or pinned tenant. |
| `nw_select_config` `{config_name}` | Makes that name the default for **every connector** on this process. It returns `connectors_with_config` / `connectors_missing_config`. |

The dotted aliases (`nw.list_tenants`, …) still work. Every connector tool also accepts an optional
per-call `config_name`, which overrides `nw_select_config` for that call only.

Things that surprise people:

- **One config name for all connectors.** If tenant `acme` has Drive config `test-drive` but Epic
  only `test`, Epic calls fail until you select a name that exists on every connector you call, or
  add the missing config.
- **Selections are per process.** Two images (Drive, Epic) are two processes, and selecting on one
  does not change the other. Run one MCP server with both connectors (`agents.mcp_entrypoint`) when
  one selection should cover both.
- `nw gen-stacklok` servers fix the tenant at the ToolHive proxy and expose only the config tools
  ([stacklok MCP servers](../stacklok-mcp-servers.md#what-the-generated-server-does)).

A typical session:

```text
1. tools/call nw_list_tenants  { "connector_id": "google_drive" }   # optional filter
2. tools/call nw_select_tenant { "tenant_id": "<id from step 1>" }  # returns configs
3. tools/call nw_select_config { "config_name": "<name from step 2>" }
4. tools/call google_drive_files_list  { ... }
```

### Running one multi-tenant MCP container under ToolHive

For the unified image over stdio, mount host `config/tenants.yaml` read-only at
`/app/config/tenants.yaml` and set:

| Name | Value |
|------|--------|
| `NW_MCP_TRANSPORT` | `stdio` |
| `NW_ALLOWED_CONNECTORS` | e.g. `google_drive,fhir_epic` |
| `NW_MULTITENANCY_ENABLED` | `true` |
| `NW_TENANTS_PATH` | `/app/config/tenants.yaml` |
| `NW_MCP_AUTH_DISABLED` | `true` (local only) |
| `NW_MCP_SCOPE_POLICY_DEFAULT` | `allow` (local only) |
| `NW_MCP_TENANT_PIN_LOCKED` | `false` |

Connector credentials can live in the file's `secrets:` blocks or in per-tenant env vars, so flat
ToolHive secrets are optional.
