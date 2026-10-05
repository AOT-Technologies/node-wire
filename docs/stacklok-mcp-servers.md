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

Install the dev toolchain as in [Installation](installation.md#3-install-dependencies); its `dev`
group already includes cibuildwheel, which builds the image's wheels. On top of that,
`nw gen-stacklok` needs Docker running (wheel builds and the image) and, for Phase 1 only,
Claude Code (`claude` on `PATH`). A run with `--scope` doesn't need Claude Code.

One command runs stacklok's Phases 1–3 and pauses for the Phase 2 human review:

```bash
uv run nw gen-stacklok \
  --path https://petstore3.swagger.io/api/v3/openapi.json \
  --connector-id pet_store \
  --workflow "Shoppers browse available pets by status" \
  --workflow "Store clerks place orders and look up order status" \
  --auth-hint "API key in the api_key header"
```

On a terminal, pick **Generate the MCP server** in the review menu. With `--headless`, or
without a terminal, the command always stops at the review and prints the command that resumes
it from Phase 3. Read the scope, then run that command:

```bash
uv run nw gen-stacklok --scope nw-stacklok-builder/scoping/pet_store/mcp-scope.yaml \
  --connector-id pet_store
```

Then build the image with `nw docker-build`, the command the CLI prints:

```bash
uv run nw docker-build --connector-id pet_store
```

It finds the server generated for the connector and names the image after the project folder.
Plain `docker build -t <server>-mcp nw-stacklok-builder/out/<server>-mcp` builds the same image.
Next, check it as in [Verifying a running server](#verifying-a-running-server).

### Names

| Name | Comes from | Example |
| --- | --- | --- |
| `<id>` | `--connector-id`, or the scope's `runtime.connector_id` | `pet_store` |
| Scope and Phase 1 notes | `nw-stacklok-builder/scoping/<id>/` (`mcp-scope.yaml`, `scoping-summary.md`) | `nw-stacklok-builder/scoping/pet_store/mcp-scope.yaml` |
| `<server>` | the scope's `server.name`, a DNS label that Phase 1 picks and the Phase 2 review shows. It is not derived from `<id>` | `petstore` |
| Project folder | `nw-stacklok-builder/out/<server>-mcp/` (`--output-dir` changes the parent folder) | `nw-stacklok-builder/out/petstore-mcp/` |
| Image | `<server>-mcp:latest` | `petstore-mcp:latest` |

To read `<server>` from a saved scope, run
`grep -A1 '^server:' nw-stacklok-builder/scoping/<id>/mcp-scope.yaml`.

### What it changes in the repository

The connector stage generates the connector and wires it in, like `nw gen-all` does
([When wire is enabled](cli/nw-cli.md#when-wire-is-enabled)). `gen-stacklok` has no `--no-wire`.

| Path | Change |
| --- | --- |
| `config/connectors.yaml` | adds the `<id>:` entry (tracked file) |
| `sample.env` | adds `<id>` to `NW_ALLOWED_CONNECTORS`, plus the connector's secret names (tracked file) |
| `src/node_wire_<id>/`, `packages/connectors/<id>/` | connector source and its wheels (new) |
| `nw-stacklok-builder/scoping/<id>/` | the scope and `scoping-summary.md` (new) |
| `nw-stacklok-builder/specs/<id>.openapi.json`, `nw-stacklok-builder/out/<server>-mcp/` | the prepared spec and the server project (gitignored) |

Unlike `gen-all`, it doesn't add the connector to `ALL_PACKAGES` in `scripts/build-packages.sh`.
For a throwaway run, undo what it changed in `config/connectors.yaml` and `sample.env`
(`git diff config/connectors.yaml sample.env` shows it): delete the added `<id>:` block and secret
lines, and remove `,<id>` from the existing `NW_ALLOWED_CONNECTORS` line. Don't `git checkout`
those files, which would also discard your other edits there. Then delete every path in the table
— the gitignored ones too, since `git status` won't show them — plus the `tenants.yaml` you created
for verification, and the image (`docker rmi <server>-mcp`). Leave `packages/runtime/dist/`,
`packages/bindings/dist/` and `packages/toolhive/dist/` alone; other builds reuse those wheels.

### The three phases

1. **Phase 1, AI scoping.** The spec is prepared: downloaded, converted from Swagger 2.0 and made
   strict OpenAPI 3.0, with the original recorded under `x-nw-source`. Then Claude Code opens with
   stacklok's `ai-scoping` skill, which uses its `spec-analyzer` and `endpoint-scoper` agents and
   `mcp-builder analyze` / `validate`, all vendored in `nw-stacklok-builder/`.
   - **Interactive (the default with a terminal):** the skill runs as in stacklok's flow. It asks
     for workflows and auth, and stops at its gates for group selection, tool approval and auth.
     `--workflow`, `--auth-hint` and `--scoping-notes` are offered as starting answers. Exit the
     session (`/exit`) when it's done, and `nw` continues.
   - **`--headless`, or no terminal:** `claude -p` runs unattended. The flags are the final
     answers; without `--workflow` the AI proposes workflows. The gates take the AI's own
     recommendation, and each one is recorded in `scoping-summary.md`.
   - Claude Code is pre-approved only for file edits inside the scoping work directory and stacklok's
     CLI; headless runs refuse anything else (`--permission-mode dontAsk`), since the spec is
     untrusted input.
   - Output goes to `nw-stacklok-builder/scoping/<id>/`. A second run reuses it; `--rescope` redoes it.
2. **Phase 2, human review.** The command pauses with a summary of the scope and a menu to
   generate, inspect, edit, rescope or stop; without a terminal it always stops here. The summary
   and the options are described in [nw CLI](cli/nw-cli.md#nw-gen-stacklok).
3. **Phase 3, generate**, as below.

stacklok's `/ai-validation` (Phase 4) isn't included, because it reviews the httpx client that
node-wire mode replaces. Use the checks in [Verifying a running server](#verifying-a-running-server). To run the skill
yourself instead, `scripts/install-stacklok-skills.sh` links it into `.claude/`.

A hand-written or reviewed scope works too. `--connector-id` binds it to a node-wire connector;
alternatively, add the block to the scope:

```yaml
runtime:
  type: node_wire
  connector_id: pet_store
```

`nw gen-stacklok` runs three stages:

1. **Connector** — nw-connector-builder generates `node_wire_<id>` from `spec.source`
   (`spec.base_url` is the connector's base URL). The connector covers every operation; the scope
   decides which ones become tools. The connector is always regenerated from the scope's spec, so
   endpoints map onto it; a hand-written connector with the same id is never overwritten.
2. **Wheels for the MCP image**: compiled wheels for `node-wire-runtime`, `node-wire-bindings`,
   `node-wire-toolhive` and the connector.
   - They must match the image's Python ABI and C library, because node-wire wheels are
     binary-only. The target is read from the template's base image: stacklok's
     `dhi.io/python:3.13-alpine` gives **cp313 musllinux**.
   - Built with cibuildwheel in Docker (`scripts/build-packages.sh --cibw-linux`).
   - A package whose sources haven't changed since its last build, and that has wheels for every
     requested architecture, is reused rather than recompiled. The source hash is kept in
     `dist/.nw-source-<target>.sha256`. In practice, repeat runs rebuild only what changed, often
     nothing.
   - The architecture defaults to the host; `NW_WHEEL_ARCHS="x86_64 aarch64"` builds both.
   - `--no-wheel` skips the stage and uses whatever is in `dist/`.
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
  from `NW_TENANTS_PATH` (`/app/tenants/tenants.yaml`). The format is in the example the
  generator writes to the project, `nw-stacklok-builder/out/<server>-mcp/config/tenants.example.yaml`
  (not the repository's `config/`). `nw_list_configs` / `nw_select_config` choose a named config
  for the MCP session. No upstream credentials go in the tenants file. When `NW_PROXY_SECRET` is
  set (the generated `backend.yaml` sets it), the tenant header is only accepted from requests
  that also carry a matching `X-NW-Proxy-Secret`; others fail with `PROXY_AUTH_FAILED`. Every
  request fails that way while `proxy-secret.yaml` still holds the generated placeholder.
- Headers, the bearer token and the session id are read from the MCP SDK's per-message request
  context — not from middleware contextvars, which under stateful streamable HTTP keep the
  session's *first* request (a refreshed token would never be seen).
- **Errors and logs.** A failed tool call reaches the MCP client as
  `CODE [CATEGORY]: message (trace_id=...)`, e.g.
  `VALIDATION_ERROR [BUSINESS]: Input validation failed; ... (trace_id=5f0c...)`, the same codes
  as on every other surface ([Errors](errors.md)). The server logs the same `trace_id`. Each connector run writes `runtime.base_connector` lines with `trace_id`,
  `connector_id`, `action`, `audit_event` (`invocation_start`, `invocation_success`,
  `invocation_failure`, `invocation_validation_failure`, `policy_denial`), `error_code`,
  `error_category` and `duration_ms`. Failures that happen before the connector runs (missing
  tenant, bad proxy secret, unknown config, connector not exposed) are logged by
  `node_wire_toolhive` with `audit_event=invocation_rejected`, and arguments that don't fit the
  tool with `audit_event=invocation_validation_failure`. node-wire's log redaction is on.
  These are the server's own logs (`docker logs <container>`); `thv logs <workload>` shows only
  the ToolHive proxy.
- **Telemetry.** Set `OTEL_EXPORTER_OTLP_ENDPOINT` (e.g. `http://otel-collector:4318`) to export
  node-wire's traces (`connector.run` spans), metrics and logs over OTLP/HTTP with
  `service.name=<server>-mcp`. When it is unset, or `OTEL_SDK_DISABLED=true`, nothing is exported.

## Deploying behind ToolHive

`deploy/` holds:

| File | Purpose |
| --- | --- |
| `backend.yaml` | Deployment + ClusterIP Service for the server (one for all tenants) |
| `networkpolicy.yaml` | Only the tenant proxies may reach the backend |
| `proxy-secret.yaml` | Shared secret every proxy sends as `X-NW-Proxy-Secret`; the backend reads it as `NW_PROXY_SECRET` |
| `tenant-proxy.yaml` | `MCPRemoteProxy` template — one per tenant; `headerForward` sets `X-Tenant-ID` |
| `tenants-secret.yaml` | Tenant configs mounted at `NW_TENANTS_PATH` |
| `mcpexternalauthconfig.yaml`, `mcpoidcconfig.yaml`, `secret.yaml` | stacklok's auth manifests, per the scope's `auth.type` |

Why this is safe: ToolHive's `header-forward` middleware sets each configured header with
`Header.Set` on every request, overriding a client-supplied `X-Tenant-ID` (verified in
`stacklok/toolhive` `pkg/transport/middleware/header_forward.go`). Two layers keep other pods from
claiming a tenant:

- the NetworkPolicy admits only the proxy pods. This needs a CNI that enforces NetworkPolicy
  (Calico, Cilium, ...); on one that doesn't, the policy does nothing;
- every proxy sends the shared secret from `proxy-secret.yaml` (`headerForward.addHeadersFromSecret`),
  and the backend rejects the tenant header without it.

Never add the tenant header or `X-NW-Proxy-Secret` to a vMCP `passthroughHeaders` allowlist.

## Verifying a running server

`scripts/verify_stacklok_server.py` calls the server the way a ToolHive tenant proxy does and
checks four things:

- `tools/list` is exactly the scope's tools plus `nw_list_configs` and `nw_select_config`;
- one live tool call succeeds;
- a call without `X-Tenant-ID` is rejected;
- a request without a bearer token gets 401.

The server must run as the image ships, with multitenancy on and a tenants file.
`.github/workflows/stacklok-e2e.yml` runs the same flow on the Petstore scope.

From the repository root, with the image built as in [Quick start](#quick-start):

1. Create `tenants.yaml` from the generated example. It defines one tenant, `example-tenant`,
   with the scope's base URL and auth placement, and no credentials:

    ```bash
    cp nw-stacklok-builder/out/<server>-mcp/config/tenants.example.yaml tenants.yaml
    ```

    Edit it to add tenants or change a base URL. `tenants.yaml` at the repository root is your
    own file; don't commit it.

2. Start the server on port 8100 with the file mounted. Remove any earlier container of the same
   name first ("No such container" is fine):

    ```bash
    docker rm -f <server>-mcp
    docker run -d --name <server>-mcp -p 8100:8100 \
      -v "$PWD/tenants.yaml:/app/tenants/tenants.yaml:ro" <server>-mcp
    ```

3. Run the checks. `--tenant` must be a key under `tenants:` in the file. `--token` is the
   credential ToolHive would forward; the server relays it to the upstream API. Petstore doesn't
   check it, so the default `verify-token` works there. For an API that does, pass a real one.
   Pick a read-only tool from the scope for `--tool`:

    ```bash
    uv run python scripts/verify_stacklok_server.py \
      --scope nw-stacklok-builder/scoping/<id>/mcp-scope.yaml \
      --tenant example-tenant \
      --tool find_pets_by_status --arguments '{"status": "available"}'
    ```

    It prints `PASS: ...` and exits 0, or prints one `FAIL: ...` line per failed check and
    exits 1. `--url` defaults to `http://127.0.0.1:8100/mcp`.

4. Read the server's logs with `docker logs <server>-mcp`, then remove the container with
   `docker rm -f <server>-mcp`.

At the end of a run, `nw gen-stacklok` prints a different `docker run`: port 8200 with
`NW_MULTITENANCY_ENABLED=false`, then `thv run` to put a local ToolHive proxy in front of it.
That single-tenant container is for trying the tools from an MCP client. It needs no tenants
file, but it fails the script's checks: it lists no `nw_*` config tools and it doesn't require
`X-Tenant-ID`. Use the multi-tenant run above to verify the server.

## Limits

- The scope needs an OpenAPI spec (Swagger 2.0 is converted); hand-written connectors without a
  spec (smtp, ms_teams) cannot be scoped.
- Bindings-only features are not carried over: tool-search mode, per-caller scope policy
  (ToolHive authorizes callers; the image sets `NW_MCP_SCOPE_POLICY_DEFAULT=allow`), REST, gRPC.
- The wheels only install on Linux of the built architectures; run the server from its image.
- The Kubernetes manifests are templates: the operator's handling of `podTemplateSpec` labels
  (used by the NetworkPolicy) should be checked in your cluster (`deploy/README.md`).

See also: [why it is built this way (ADR 0001)](adr/0001-stacklok-mcp-builder-on-node-wire.md), [nw CLI](cli/nw-cli.md),
[MCP host builder](cli/nw-mcp-builder.md).
