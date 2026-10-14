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
  playground, or by editing `tenants.yaml`. **MCP never writes configs.**
- A store write takes effect on the next `get()`, because the cached instance for that config is
  dropped at once.

## How the tenant is resolved

The highest source wins:

| Transport | 1st | 2nd | 3rd |
|---|---|---|---|
| REST / gRPC | `X-Tenant-ID` header or JWT `tenant` claim (per request) | — | — |
| MCP streamable-http | `X-Tenant-ID` header or JWT claim (per request) | `nw_select_tenant` selection | `NW_TENANT_ID` pin |
| MCP stdio | `nw_select_tenant` selection, unless `NW_MCP_TENANT_PIN_LOCKED=true` | `NW_TENANT_ID` pin | — |

- `NW_TENANT_ID_HEADER` renames the header. The tenant never comes from the URL, and it is never a
  connector-tool argument.
- With multi-tenancy on, a request with no tenant fails with `400 MISSING_TENANT`. On
  streamable-http, `initialize` falls back to `__default__` if that tenant exists in the store.
- On streamable-http the per-request header always wins, so one session's `nw_select_tenant` can
  never shadow another concurrent session's tenant.
- A JWT `tenant` claim that disagrees with the header or session tenant fails closed with
  `403 TENANT_IDENTITY_MISMATCH`. It is never silently overridden.

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

### Where tenant secrets are stored

Secrets sent through `/v1` (`PUT /v1/connectors/{cid}/secrets`, or `secrets` in a config body) are
never written to `tenants.yaml`. That file holds configs and secret **names** only, and is created
`0600`. Values go to a secret store, and **with no store configured those requests fail with
`400`** (nothing is saved).

For development, enable the encrypted local file:

| Name | Value |
|------|--------|
| `NW_ALLOW_LOCAL_SECRET_FILE` | `true` |
| `NW_LOCAL_SECRET_KEY` | a Fernet key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `NW_LOCAL_SECRET_FILE` | absolute path **outside the source tree** (and outside `/app` in the image), e.g. a mounted volume |

The file is Fernet-encrypted and created `0600`. Startup refuses a missing key, a relative path or a
path inside the source tree. A wrong key is an error, never an empty store. There is no key
rotation yet. In production, supply secrets through environment variables or a cloud secret backend
(`NW_SECRET_BACKEND`), which is read-only.

Inline secret fields in a config (`*_value`, `password`, ...) are not read by any connector and are
dropped, with a warning, when `tenants.yaml` is written.

#### Upgrading

| Change | Migration |
|---|---|
| `/v1` secret writes return `400` until a store is configured | Enable the local file (above), or use env vars / a cloud backend. |
| An existing `tenants.yaml` with plaintext `secrets:` | With a store configured it migrates automatically on the next start and the file is rewritten as names only. With no store it still loads (a warning is logged) and is kept `0600`. |
| Inline secret fields are no longer written | None needed. |

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
