<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# stacklok MCP builder on node-wire: requirements and decisions

Status: **implemented** (2026-09-26).

- How to use it: [stacklok MCP servers](stacklok-mcp-servers.md).
- What was vendored and changed: `nw-stacklok-builder/UPSTREAM.md`.

## Requirement

Build MCP servers with **stacklok's mcp-builder**, modified so every tool runs on the
**node-wire runtime and a node-wire connector**, not stacklok's generated HTTP client.

- stacklok's generator is the generator.
- node-wire's own generators (nw-connector-builder, nw-mcp-builder) and stacklok's code do not call
  each other; the connector package is the only thing they share.
- node-wire features such as multitenancy are kept.

## Decisions

| Decision | Why |
|---|---|
| One command, `nw gen-stacklok`, runs connector codegen → wheels → stacklok generation | The user asked for one step. The connector is built from the scope's own `spec.source` and `spec.base_url`. |
| stacklok/mcp-builder and mcp-template-py are **vendored**, not fetched at build time, with only the files generation needs | Simpler than fetching every run. There is no PyPI package: the PyPI name `mcp-builder` belongs to an unrelated project. |
| Vendored files only gain hook calls into `nw_stacklok/`. The pristine copy is committed first. | Keeps the diff against upstream small and reviewable. Moving to a new upstream commit stays a re-copy plus re-apply. |
| **Auth follows ToolHive.** ToolHive is the only source of upstream credentials; node-wire's own inbound auth is off in these servers. | This is stacklok's model: "the generated server performs no authentication". It avoids two sources of credentials, and avoids `Authorization` meaning two things. |
| The credential relay lives in an **optional layer**, `node-wire-toolhive`, plus one additive bindings argument, `ConnectorFactory(auth_provider_hook=)`. The runtime is unchanged. | The user asked for a layer, not a runtime change. The runtime's existing auth providers already format bearer, header and query credentials. |
| A relayed credential is read on every call and **never cached** | The factory keeps one connector per (tenant, config), so a cached credential would be sent for the next caller (spike S1). |
| Headers are read from the MCP SDK's per-message `request_ctx`, not from middleware contextvars | Under stateful streamable HTTP, the template's contextvar keeps a session's first request, so a refreshed token would be ignored. |
| **Multitenancy:** one backend serves all tenants. Each tenant has its own ToolHive `MCPRemoteProxy`, whose `headerForward` sets `X-Tenant-ID`. | ToolHive has no claim-to-header mapping, and its header injection is static per proxy. Its `header-forward` middleware uses `Header.Set`, which overrides a client-supplied header (verified at toolhive `dfb0713`). A NetworkPolicy admits only the proxies. |
| Tenant choice is fixed by the proxy. Only `nw_list_configs` / `nw_select_config` are exposed. | Per-session selection only picks a named config within the tenant. |
| **Python 3.13** for the whole repo | stacklok requires it, and nw-cli calls the vendored generator in-process. Package versions are unchanged for now (versioning deferred). |
| stacklok's image defaults (DHI Alpine) are kept, so wheels are cp313 **musllinux** | The user chose stacklok defaults. The DHI image has no Python headers, so the wheels come from cibuildwheel (`build-packages.sh --musllinux`). |

## Not carried over

- Bindings-only features: tool-search mode, per-caller scope policy (ToolHive authorizes callers), REST and gRPC.
- node-wire token refresh and OAuth flows: ToolHive owns the token lifecycle.
- Hand-written connectors without an OpenAPI spec (smtp, ms_teams), which a scope cannot reference.

## Fixed along the way (bindings)

- `apikey_query` auth ignored tenant-scoped secrets.
- A `connectors.yaml` `base_url` was ignored for YAML-bootstrapped configs.

## Open

- **Kubernetes verification** (spike S2), needing a kind cluster with the ToolHive operator:
  - whether the operator applies `podTemplateSpec` labels to proxy pods (the NetworkPolicy
    selects on them);
  - how remote-proxy auth delivers the upstream token to an in-cluster backend.
- **Versioning** for dropping Python 3.11/3.12 from published wheels (minor release or `2.0.0`).
- **Upstream report** to stacklok about the template's stale per-session token.
