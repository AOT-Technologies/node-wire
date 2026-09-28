<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# stacklok MCP servers on node-wire (`nw gen-stacklok`)

`nw gen-stacklok` builds an MCP server with [stacklok's mcp-builder](https://github.com/stacklok/mcp-builder)
— a curated, ToolHive-ready server generated from an `mcp-scope.yaml` — where every tool runs
through a **node-wire connector** on the **node-wire runtime** instead of stacklok's generated
HTTP client. stacklok's generator is vendored in `nw-stacklok-builder/` (see its `UPSTREAM.md`)
and modified only through small hooks.

| | stacklok mcp-builder | `nw gen-stacklok` |
|---|---|---|
| Input | `mcp-scope.yaml` + OpenAPI spec | the same `mcp-scope.yaml` (its `spec.source` is the spec) |
| Tool names, descriptions, parameters | from the scope | from the scope (unchanged) |
| Tool execution | generated httpx client | node-wire connector: validation, retries, circuit breaker, error taxonomy, telemetry |
| Auth | ToolHive | ToolHive (the forwarded credential is relayed into the connector's auth placement) |
| Tenancy | none | many tenants per backend, one ToolHive proxy per tenant (`X-Tenant-ID`) |
| Output | project + `MCPServer` manifests | project + backend, NetworkPolicy, per-tenant `MCPRemoteProxy` manifests |

## Quick start

```bash
# Prerequisites: Python 3.13, uv, Docker, cibuildwheel (pip install 'cibuildwheel==4.2.1')
uv run nw gen-stacklok --scope path/to/mcp-scope.yaml --connector-id pet_store
docker build -t petstore-mcp nw-stacklok-builder/out/petstore-mcp
```

The scope is a normal stacklok scope (write it by hand or with stacklok's `/ai-scoping` skill).
`--connector-id` binds it to a node-wire connector; alternatively add the block to the scope:

```yaml
runtime:
  type: node_wire
  connector_id: pet_store
```

`nw gen-stacklok` runs three stages:

1. **Connector** — nw-connector-builder generates `node_wire_<id>` from `spec.source`
   (`spec.base_url` is the connector's base URL). The connector covers every operation; the scope
   decides which ones become tools. `--force` regenerates an existing connector.
2. **Wheels** — `scripts/build-packages.sh --musllinux` builds cp313 musllinux wheels for
   `node-wire-runtime`, `node-wire-bindings`, `node-wire-toolhive` and the connector (the
   generated image runs stacklok's DHI Alpine base). Arch defaults to the host;
   `NW_MUSLLINUX_ARCHS="x86_64 aarch64"` builds both. `--no-wheel` reuses wheels already in `dist/`.
3. **Server** — stacklok's own scope validator checks the scope against the spec, then the
   vendored stacklok generator writes `nw-stacklok-builder/out/<server>-mcp/`
   and runs `uv lock` there (`--no-lock` skips it). Every scoped `METHOD /path` must map to a
   connector action and every scoped parameter to an action input; mismatches are reported
   together before anything is written.

## What the generated server does

- **Tools** keep stacklok's names, signatures and descriptions; each calls
  `self._client.run("<action>", {...})`, where `client.py` is a node-wire client
  (`node_wire_toolhive.NodeWireClient`).
- **Auth follows ToolHive.** The template's `TokenPassthroughMiddleware` rejects requests without
  a bearer token (401). The credential ToolHive forwards as `Authorization: Bearer` is relayed on
  every call into the connector's own auth placement — bearer, a named header (e.g. `api_key`),
  or a query parameter — and is never cached. The connector must be listed in
  `NW_UPSTREAM_BEARER_CONNECTORS` (set in the image). node-wire does not refresh tokens or run
  OAuth flows here; ToolHive owns the token lifecycle.
- **Tenants.** `NW_MULTITENANCY_ENABLED=true`; the tenant comes from `X-Tenant-ID`, which each
  ToolHive tenant proxy sets. Tenant configs (base URL, auth placement, named configs) are read
  from `NW_TENANTS_PATH` (`/app/tenants/tenants.yaml`; format in
  `config/tenants.example.yaml`). `nw_list_configs` / `nw_select_config` choose a named config
  for the MCP session. No upstream credentials go in the tenants file.
- Headers, the bearer token and the session id are read from the MCP SDK's per-message request
  context — not from middleware contextvars, which under stateful streamable HTTP keep the
  session's *first* request (a refreshed token would never be seen).

## Deploying behind ToolHive

`deploy/` holds:

| File | Purpose |
| --- | --- |
| `backend.yaml` | Deployment + ClusterIP Service for the server (one for all tenants) |
| `networkpolicy.yaml` | Only the tenant proxies may reach the backend |
| `tenant-proxy.yaml` | `MCPRemoteProxy` template — one per tenant; `headerForward` sets `X-Tenant-ID` |
| `tenants-secret.yaml` | Tenant configs mounted at `NW_TENANTS_PATH` |
| `mcpexternalauthconfig.yaml`, `mcpoidcconfig.yaml`, `secret.yaml` | stacklok's auth manifests, per the scope's `auth.type` |

Why this is safe: ToolHive's `header-forward` middleware sets each configured header with
`Header.Set` on every request, overriding a client-supplied `X-Tenant-ID` (verified in
`stacklok/toolhive` `pkg/transport/middleware/header_forward.go`), and the NetworkPolicy keeps the
backend reachable only through those proxies. Never add the tenant header to a vMCP
`passthroughHeaders` allowlist.

## Verifying a running server

```bash
docker run -d -p 8100:8100 -v $PWD/tenants.yaml:/app/tenants/tenants.yaml:ro petstore-mcp
uv run python scripts/verify_stacklok_server.py --scope path/to/mcp-scope.yaml \
  --tool find_pets_by_status --arguments '{"status": "available"}'
```

The script calls the server like a ToolHive tenant proxy and checks the tool list, one live call,
tenant enforcement and bearer enforcement. `.github/workflows/stacklok-e2e.yml` runs the same flow
on the Petstore scope.

## Limits

- The scope needs an OpenAPI spec (Swagger 2.0 is converted); hand-written connectors without a
  spec (smtp, ms_teams) cannot be scoped.
- Bindings-only features are not carried over: tool-search mode, per-caller scope policy
  (ToolHive authorizes callers; the image sets `NW_MCP_SCOPE_POLICY_DEFAULT=allow`), REST, gRPC.
- The wheels only install on Linux of the built architectures; run the server from its image.
- The Kubernetes manifests are templates: the operator's handling of `podTemplateSpec` labels
  (used by the NetworkPolicy) should be checked in your cluster (`deploy/README.md`).

See also: [runbook](nw-cli-runbook.md), [decisions](stacklok-mcp-builder-requirements.md), [nw CLI](nw-cli.md),
[MCP host builder](mcp-servers.md).
