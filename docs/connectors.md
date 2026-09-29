<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Use a connector

Call a connector that already ships — in-process from Python or over REST — and control which tenants may use it through the runtime config store. Every connector works the same way; only the payload and the `config` / `auth` blocks change.

| Connector | Setup guide |
|---|---|
| Google Drive | [Google Drive](google_drive_connector.md) |
| Salesforce | [Salesforce](salesforce_connector.md) |
| Slack | [Slack](slack_connector.md) |
| SMTP, Stripe, FHIR Epic, FHIR Cerner, HTTP Generic | [Connector inventory](connector-reference.md#connector-inventory) · each package's `README.md` under `src/node_wire_*/` |

Writing your own connector? See [Build a connector](connectors-build.md).

The example below uses **`http_generic`** for two tenants.

## In-process (Python)

```python
import asyncio
import os

os.environ["NW_ALLOWED_CONNECTORS"] = "http_generic"
os.environ["NW_MCP_SCOPE_POLICY_DEFAULT"] = "allow"  # local only — see note below
os.environ["NW_HTTP_GENERIC_ALLOWED_HOSTS"] = "httpbin.org"

from bindings.factory import ConnectorFactory
from node_wire_runtime.config_store import ConfigNotFoundError
from node_wire_runtime.connector_registry import auto_register


async def main() -> None:
    auto_register()
    factory = ConnectorFactory()
    factory.load()  # reads config/connectors.yaml into the __default__ tenant
    store = factory.store

    # 1. Entitle two tenants. http_generic takes the whole request in the payload,
    #    so its config block is empty — the entry itself is what grants access.
    store.create("acme", "http_generic", {"name": "default", "config": {}})
    store.create("globex", "http_generic", {"name": "default", "config": {}})

    # 2. Resolve a tenant-pinned instance and run an action.
    acme = await factory.get("http_generic", tenant_id="acme")
    response = await acme.run(
        {"action": "request", "method": "GET", "url": "https://httpbin.org/get",
         "params": {"q": "node-wire"}}
    )
    print("acme:", response.success, response.data["status_code"])

    # 3. A tenant with no config is not entitled — the factory fails closed.
    try:
        await factory.get("http_generic", tenant_id="initech")
    except ConfigNotFoundError as exc:
        print("initech:", exc)

    # 4. An instance pinned to acme refuses to run for another tenant.
    mismatch = await acme.run(
        {"action": "request", "method": "GET", "url": "https://httpbin.org/get"},
        tenant_id="globex",
    )
    print("mismatch:", mismatch.success, mismatch.error_code)

    # 5. Revoke access at runtime: deleting the config drops the cached instance.
    store.delete("acme", "http_generic", "default")
    try:
        await factory.get("http_generic", tenant_id="acme")
    except ConfigNotFoundError as exc:
        print("acme after delete:", exc)


asyncio.run(main())
```

Output:

```text
acme: True 200
initech: no config for tenant 'initech' / connector 'http_generic'
mismatch: False TENANT_MISMATCH
acme after delete: no config for tenant 'acme' / connector 'http_generic'
```

What each step relies on:

| Step | Behaviour |
|---|---|
| `factory.load()` | Reads `config/connectors.yaml` (or `NW_CONFIG_PATH`) once and copies each enabled connector into the store as the `__default__` tenant. Tenants you add later live only in the store. |
| `store.create(tenant, connector, doc)` | Adds a named config. The first config for a `(tenant, connector)` pair becomes its default. **A config existing is what entitles the tenant** — there is no separate allow list. |
| `factory.get(connector, tenant_id=…)` | Resolves the tenant's config (the default, or `config_name=…`), builds the instance with that config's secrets and auth, and caches it per `(tenant, connector, config)`. Omitting `tenant_id` means `__default__`, never "the current request". |
| `run(payload)` | Validation → policy → retries → error mapping. The instance is pinned to its tenant, so leave `tenant_id` off; a different one returns `TENANT_MISMATCH` without calling out. |
| `store.update` / `store.delete` | Take effect on the next `get()` — the cached instance for that config is dropped at once. |

The store is in-process memory. Anything you add with `store.create()` is gone on restart unless you persist it. The REST config API below writes to `NW_TENANTS_PATH` for you.

**Scope policy.** `NW_MCP_SCOPE_POLICY_DEFAULT=allow` is only for local scripts. With the default `deny`, a `run()` without caller scopes fails with `POLICY_DENIED`. In a real host, pass `scopes=("mcp:http_generic.request",)` (or `principal`) to `run()`. See [Security](connectors-build.md#security-rest-plugins-secrets).

**Connectors that read their config.** For connectors such as `stripe` or `fhir_epic`, the same calls apply. Put per-tenant settings in the doc's `config` block and the credentials setup in its `auth` block (see [Auth blocks](connectors-build.md#auth-blocks-in-connectorsyaml)). Per-tenant secrets use `NW_{TENANT}_{CONNECTOR}_{KEY}` — see [Multi-tenancy](configuration.md#multi-tenancy). A tenant can hold several named configs (for example `sandbox` and `live`); select one with `factory.get(..., config_name="live")`.

## Over REST

The REST binding wraps the same store. Start the API with multi-tenancy on (`NW_REST_AUTH_DISABLED` is for local use only):

```bash
NW_MULTITENANCY_ENABLED=true NW_REST_AUTH_DISABLED=true \
NW_ALLOWED_CONNECTORS=http_generic NW_MCP_SCOPE_POLICY_DEFAULT=allow \
MODE=API uv run node-wire
```

The tenant always comes from the `X-Tenant-ID` header (or the caller's JWT tenant claim), never from the URL:

```bash
# Entitle acme (writes to NW_TENANTS_PATH, default config/tenants.yaml)
curl -X POST localhost:8000/v1/connectors/http_generic/configs \
  -H 'X-Tenant-ID: acme' -H 'Content-Type: application/json' \
  -d '{"name": "default", "config": {}}'
# → 201 {"name": "default", "default": true}

# Call the connector as acme
curl -X POST localhost:8000/connectors/http_generic/request \
  -H 'X-Tenant-ID: acme' -H 'Content-Type: application/json' \
  -d '{"method": "GET", "url": "https://httpbin.org/get"}'
# → 200 {"success": true, "data": {"status_code": 200, ...}}

# A tenant with no config
curl -X POST localhost:8000/connectors/http_generic/request \
  -H 'X-Tenant-ID: initech' -H 'Content-Type: application/json' \
  -d '{"method": "GET", "url": "https://httpbin.org/get"}'
# → 403
```

Leaving out `X-Tenant-ID` returns `400` while multi-tenancy is on. Add `"config_name": "<name>"` to the request body to pick a named config. The rest of the config API is `GET`/`PUT`/`DELETE /v1/connectors/{cid}/configs/{name}` and `PUT …/{name}/default`. For MCP, the same store sits behind the `nw_list_tenants` / `nw_select_tenant` / `nw_select_config` tools — see [nw-mcp-builder — Multi-tenancy](cli/nw-mcp-builder.md#multi-tenancy-mcp).

---

## Related documentation

| Doc | When to read it |
|-----|-----------------|
| [connector-reference.md](connector-reference.md) | `ConnectorFactory` / registry APIs, `connectors.yaml` schema, connector inventory |
| [connector-bindings.md](connector-bindings.md) | How actions map to REST routes, MCP tools and gRPC methods |
| [configuration.md](configuration.md#multi-tenancy) | Multi-tenancy variables and tenant secrets |
| [Build a connector](connectors-build.md) | Writing a new connector by hand |
