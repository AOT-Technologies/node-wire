<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# node-wire-stripe

Stripe connector for Node Wire. Lets an agent or app take payments, manage subscriptions and
issue refunds through the official Stripe Python SDK.

Part of [Node Wire](https://github.com/AOT-Technologies/node-wire). Every call runs through the
Node Wire runtime, so it gets input validation, retries with a circuit breaker, scope policy,
a shared error taxonomy, and OpenTelemetry tracing. A Node Wire host exposes each action over REST,
gRPC and MCP.

## Install

```bash
pip install node-wire-stripe
```

This pulls in [`node-wire-runtime`](https://pypi.org/project/node-wire-runtime/). Pre-releases
are published as `X.Y.ZbN`; install one with `pip install --pre node-wire-stripe`. To install
every connector at once, use [`node-wire-connectors`](https://pypi.org/project/node-wire-connectors/).

Wheels are compiled with Cython and contain no Python source. They are built for CPython 3.11,
3.12 and 3.13 on Linux x86_64 (manylinux and musllinux), macOS arm64 and Windows x64.

## Actions

The connector registers as `stripe`. MCP tool names are `<connector_id>_<action>`, with
dots replaced by underscores.

| Action | MCP tool | What it does |
|---|---|---|
| `create_payment_intent` | `stripe_create_payment_intent` | Creates a PaymentIntent for a one-time payment, optionally confirming it |
| `charge` | `stripe_charge` | Creates a charge against a card source (the legacy Charges API) |
| `create_subscription` | `stripe_create_subscription` | Subscribes a customer to a price |
| `cancel_subscription` | `stripe_cancel_subscription` | Cancels a subscription now, or at the end of the current period |
| `issue_refund` | `stripe_issue_refund` | Refunds a charge or PaymentIntent in full or in part |

Amounts are integers in the currency's smallest unit (cents for USD), and currencies are
three-letter lowercase ISO codes. Every action sends Stripe an idempotency key (the
`idempotency_key` you pass, or else the call's trace ID), so a retried call does not repeat the
operation.

## Configuration

Add the connector to `config/connectors.yaml`:

```yaml
connectors:
  stripe:
    enabled: true
    exposed_via: ["rest", "grpc", "mcp"]
    auth:
      provider: static_token
      secret_key: stripe_api_key
      header_name: Authorization
      prefix: ""  # Stripe expects the raw key
```

Secrets are read through the configured secret provider (environment variables by default):

| Variable | Purpose |
|---|---|
| `STRIPE_API_KEY` | Stripe secret key (`sk_test_...` or `sk_live_...`) |

## Usage

Set `NW_ALLOWED_CONNECTORS=stripe` (comma-separate several). Node Wire loads only the
connectors listed there, and discovers them through the `node_wire.connectors` entry point.
A Node Wire host (the REST, gRPC or MCP server, or `ConnectorFactory`) then builds the connector
from the configuration above. See [Use a connector](https://aot-technologies.github.io/node-wire/connectors/).

## Documentation

- [Connector reference](https://aot-technologies.github.io/node-wire/connector-reference/#stripe)
- [Configuration](https://aot-technologies.github.io/node-wire/configuration/)
- [Error codes](https://aot-technologies.github.io/node-wire/errors/)
- [Changelog](https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md)

## License

Apache-2.0. See [LICENSE](https://github.com/AOT-Technologies/node-wire/blob/main/LICENSE).
