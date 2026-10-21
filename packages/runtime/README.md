<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# node-wire-runtime

The core of [Node Wire](https://github.com/AOT-Technologies/node-wire): a framework for writing
connectors, the adapters that let AI agents and applications call external systems safely.
A connector declares typed actions; the runtime wraps every call in the same pipeline of
validation, policy, resilience, error mapping and telemetry. A Node Wire host then exposes those
actions as REST endpoints, gRPC methods and MCP tools.

Install this package to write your own connector, or as the base for the
[official connectors](#official-connectors).

## What it provides

| Area | What you get |
|---|---|
| Connector framework | `BaseConnector` and `RestConnector` base classes, the `@nw_action` decorator, Pydantic input and output models, and discovery of installed connectors through the `node_wire.connectors` entry point |
| Resilience | Retries with backoff (Tenacity), a per-tenant circuit breaker, and rate limiting |
| Errors | One `ConnectorResponse` shape for every call, and a shared error taxonomy (`RETRYABLE`, `BUSINESS`, `AUTH`, `FATAL`) with stable error codes |
| Authentication | Providers for no auth, static tokens and credentials, API keys in query strings, OAuth 2.0 (client credentials with a client secret or private-key JWT, and refresh tokens) and Google service accounts |
| Secrets | A pluggable `SecretProvider`: environment variables built in; AWS Secrets Manager, HashiCorp Vault, Azure Key Vault and GCP Secret Manager as extras |
| Multi-tenancy | A runtime config store of named per-tenant configs, tenant-pinned connector instances, and tenant-scoped secrets |
| Policy | A policy hook on every call, including MCP scope policies |
| Observability | OpenTelemetry traces and metrics, optional Traceloop / OpenLLMetry, and log redaction |
| Egress safety | Helpers that block requests to loopback, private, link-local and cloud-metadata addresses |

## Install

```bash
pip install node-wire-runtime
```

Cloud secret backends are optional extras. Install only the ones you use:

```bash
pip install "node-wire-runtime[aws]"    # AWS Secrets Manager (boto3)
pip install "node-wire-runtime[vault]"  # HashiCorp Vault (hvac)
pip install "node-wire-runtime[azure]"  # Azure Key Vault
pip install "node-wire-runtime[gcp]"    # GCP Secret Manager
```

Pre-releases are published as `X.Y.ZbN`; install one with `pip install --pre node-wire-runtime`.

The wheel is pure Python (`py3-none-any`) and installs on CPython 3.11, 3.12 and 3.13 on any
OS and CPU.

## Write a connector

A connector is a `BaseConnector` subclass with one `@nw_action` method per operation. Each action
takes a Pydantic input model and returns an output model; `run()` validates the payload, calls the
action and returns a `ConnectorResponse`, never raising for a failed call.

```python
import asyncio
from typing import Literal

from pydantic import BaseModel

from node_wire_runtime import BaseConnector, EnvSecretProvider, nw_action


class GreetInput(BaseModel):
    action: Literal["greet"] = "greet"
    name: str


class GreetOutput(BaseModel):
    message: str


class GreeterConnector(BaseConnector):
    connector_id = "greeter"
    output_model = GreetOutput

    @nw_action("greet")
    async def greet(self, params: GreetInput, *, trace_id: str) -> GreetOutput:
        greeting = self.secret_provider.get_secret("GREETING")
        return GreetOutput(message=f"{greeting}, {params.name}!")


async def main() -> None:
    connector = GreeterConnector(secret_provider=EnvSecretProvider())

    response = await connector.run({"action": "greet", "name": "Ada"})
    print(response.success, response.data)  # True {'message': 'Hello, Ada!'}

    response = await connector.run({"action": "greet"})  # missing "name"
    print(response.success, response.error_code)  # False VALIDATION_ERROR


asyncio.run(main())
```

Run it with `GREETING=Hello`. To ship a connector as its own package, register the module under
the `node_wire.connectors` entry point; see
[Build a connector](https://aot-technologies.github.io/node-wire/connectors-build/).

## Use an installed connector

Installed connectors are loaded by `auto_register()`, which imports only the ones named in
`NW_ALLOWED_CONNECTORS` (comma-separated). With nothing listed, it loads nothing.

```python
import asyncio
import os

os.environ["NW_ALLOWED_CONNECTORS"] = "http_generic"
os.environ["NW_HTTP_GENERIC_ALLOWED_HOSTS"] = "httpbin.org"

from node_wire_runtime import get_connector_registry
from node_wire_runtime.connector_registry import auto_register


async def main() -> None:
    auto_register()
    connector = get_connector_registry()["http_generic"]()
    response = await connector.run(
        {"action": "request", "method": "GET", "url": "https://httpbin.org/get"}
    )
    print(response.success, response.data["status_code"])  # True 200


asyncio.run(main())
```

This needs `pip install node-wire-http`. Connectors that use credentials are normally built by a
Node Wire host (`ConnectorFactory`, the REST, gRPC or MCP server) from `config/connectors.yaml`;
see [Use a connector](https://aot-technologies.github.io/node-wire/connectors/).

## Official connectors

| Package | Connects to |
|---|---|
| [`node-wire-http`](https://pypi.org/project/node-wire-http/) | Any REST endpoint, with SSRF protection |
| [`node-wire-smtp`](https://pypi.org/project/node-wire-smtp/) | SMTP email |
| [`node-wire-stripe`](https://pypi.org/project/node-wire-stripe/) | Stripe payments, subscriptions and refunds |
| [`node-wire-slack`](https://pypi.org/project/node-wire-slack/) | Slack messages, DMs and file uploads |
| [`node-wire-salesforce`](https://pypi.org/project/node-wire-salesforce/) | Salesforce Leads and Contacts |
| [`node-wire-google-drive`](https://pypi.org/project/node-wire-google-drive/) | Google Drive files and sharing |
| [`node-wire-fhir-epic`](https://pypi.org/project/node-wire-fhir-epic/) | Epic FHIR R4 |
| [`node-wire-fhir-cerner`](https://pypi.org/project/node-wire-fhir-cerner/) | Oracle Health (Cerner) FHIR R4 |

[`node-wire-connectors`](https://pypi.org/project/node-wire-connectors/) installs this runtime
and all eight.

## Stability

The 1.x public API is listed in the [Public API reference](https://aot-technologies.github.io/node-wire/public-api/).
Anything not listed there, or named with a leading underscore, is internal. See the
[versioning policy](https://aot-technologies.github.io/node-wire/versioning/).

## Documentation

- [Node Wire on GitHub](https://github.com/AOT-Technologies/node-wire): source, issues and full documentation
- [Node Wire documentation](https://aot-technologies.github.io/node-wire/)
- [Build a connector](https://aot-technologies.github.io/node-wire/connectors-build/)
- [Configuration](https://aot-technologies.github.io/node-wire/configuration/)
- [Error codes](https://aot-technologies.github.io/node-wire/errors/)
- [Changelog](https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md)

## License

Apache-2.0. See [LICENSE](https://github.com/AOT-Technologies/node-wire/blob/main/LICENSE).
