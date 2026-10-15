<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# node-wire-salesforce

Salesforce CRM connector for Node Wire. Lets an agent or app create, read, update and delete
Leads and Contacts through the Salesforce REST API. It authenticates with an OAuth 2.0 refresh
token, so long-running agents stay signed in.

Part of [Node Wire](https://github.com/AOT-Technologies/node-wire). Every call runs through the
Node Wire runtime, so it gets input validation, retries with a circuit breaker, scope policy,
a shared error taxonomy, and OpenTelemetry tracing. A Node Wire host exposes each action over REST,
gRPC and MCP.

## Install

```bash
pip install node-wire-salesforce
```

This pulls in [`node-wire-runtime`](https://pypi.org/project/node-wire-runtime/). Pre-releases
are published as `X.Y.ZbN`; install one with `pip install --pre node-wire-salesforce`. To install
every connector at once, use [`node-wire-connectors`](https://pypi.org/project/node-wire-connectors/).

Wheels are compiled with Cython and contain no Python source. They are built for CPython 3.11,
3.12 and 3.13 on Linux x86_64 (manylinux and musllinux), macOS arm64 and Windows x64.

## Actions

The connector registers as `salesforce`. MCP tool names are `<connector_id>_<action>`, with
dots replaced by underscores.

| Action | MCP tool | What it does |
|---|---|---|
| `create_lead` | `salesforce_create_lead` | Creates a Lead (`last_name` and `company` are required) |
| `read_lead` | `salesforce_read_lead` | Reads a Lead by record ID |
| `update_lead` | `salesforce_update_lead` | Updates fields on a Lead |
| `delete_lead` | `salesforce_delete_lead` | Deletes a Lead |
| `create_contact` | `salesforce_create_contact` | Creates a Contact (`last_name` is required), optionally under an Account |
| `read_contact` | `salesforce_read_contact` | Reads a Contact by record ID |
| `update_contact` | `salesforce_update_contact` | Updates fields on a Contact |
| `delete_contact` | `salesforce_delete_contact` | Deletes a Contact |

Fields accept both snake_case and Salesforce API names (`last_name` or `LastName`). Record IDs
accept `record_id`, `id`, or `lead_id` / `contact_id`.

## Configuration

Add the connector to `config/connectors.yaml`:

```yaml
connectors:
  salesforce:
    enabled: true
    exposed_via: ["rest", "grpc", "mcp"]
    auth:
      provider: oauth2
      grant_method: refresh_token
      token_url_secret: SALESFORCE_TOKEN_URL
      client_id_secret: SALESFORCE_CLIENT_ID
      client_secret_secret: SALESFORCE_CLIENT_SECRET
      refresh_token_secret: SALESFORCE_REFRESH_TOKEN
```

Secrets are read through the configured secret provider (environment variables by default):

| Variable | Purpose |
|---|---|
| `SALESFORCE_INSTANCE_URL` | Your org's URL, e.g. `https://your-domain.my.salesforce.com` |
| `SALESFORCE_TOKEN_URL` | OAuth 2.0 token endpoint, e.g. `https://login.salesforce.com/services/oauth2/token` |
| `SALESFORCE_CLIENT_ID` | Consumer key of the connected app |
| `SALESFORCE_CLIENT_SECRET` | Consumer secret of the connected app |
| `SALESFORCE_REFRESH_TOKEN` | Refresh token issued to the connected app |

## Usage

Set `NW_ALLOWED_CONNECTORS=salesforce` (comma-separate several). Node Wire loads only the
connectors listed there, and discovers them through the `node_wire.connectors` entry point.
A Node Wire host (the REST, gRPC or MCP server, or `ConnectorFactory`) then builds the connector
from the configuration above. See [Use a connector](https://aot-technologies.github.io/node-wire/connectors/).

## Documentation

- [Node Wire on GitHub](https://github.com/AOT-Technologies/node-wire): source, issues and full documentation
- [Salesforce setup guide](https://aot-technologies.github.io/node-wire/salesforce_connector/): configuration and examples
- [Connector reference](https://aot-technologies.github.io/node-wire/connector-reference/#salesforce)
- [Configuration](https://aot-technologies.github.io/node-wire/configuration/)
- [Error codes](https://aot-technologies.github.io/node-wire/errors/)
- [Changelog](https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md)

## License

Apache-2.0. See [LICENSE](https://github.com/AOT-Technologies/node-wire/blob/main/LICENSE).
