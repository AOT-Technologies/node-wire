<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# node-wire-fhir-epic

Epic FHIR R4 connector for Node Wire. Lets an agent or app read, search, create and update the
resources Epic publishes (patients, encounters, observations, documents and more) and run its bulk export. It connects as a SMART on
FHIR backend service (OAuth 2.0 client credentials with a signed JWT).

Part of [Node Wire](https://github.com/AOT-Technologies/node-wire). Every call runs through the
Node Wire runtime, so it gets input validation, retries with a circuit breaker, scope policy,
a shared error taxonomy, and OpenTelemetry tracing. A Node Wire host exposes each action over REST,
gRPC and MCP.

## Install

```bash
pip install node-wire-fhir-epic
```

This pulls in [`node-wire-runtime`](https://pypi.org/project/node-wire-runtime/). Pre-releases
are published as `X.Y.ZbN`; install one with `pip install --pre node-wire-fhir-epic`. To install
every connector at once, use [`node-wire-connectors`](https://pypi.org/project/node-wire-connectors/).

The wheel is pure Python (`py3-none-any`) and installs on CPython 3.11, 3.12 and 3.13 on any
OS and CPU.

## Actions

The connector registers as `fhir_epic` and has **137 actions** for Epic on FHIR R4. MCP tool names are
`<connector_id>_<action>`.

| Group | Actions |
|---|---|
| Original five | `read_patient`, `search_patients`, `search_encounter`, `create_document_reference`, `search_document_reference` |
| Read and search (US Core and other resources) | `read_<resource>` and `search_<resource>`, for example `read_observation`, `search_condition`, `search_medication_request`; every Epic search parameter is a field |
| Write | `create_<resource>` and `update_<resource>`, taking the FHIR resource as JSON |
| Operations | `patient_match`, `patient_summary`, `value_set_expand`, `bulk_export_group`, `bulk_export_status`, `bulk_export_file`, `bulk_export_delete`, `fetch_next_page` |

The full list, request rules and error codes are in the
[connector README](https://github.com/AOT-Technologies/node-wire/blob/main/src/node_wire_fhir_epic/README.md).
The original five accept common aliases that language models produce, such as `patientId` for
`resource_id`. `search_encounter` requires a patient filter and refuses to call Epic without one. Any search of a
patient-scoped resource needs a patient, a subject or an id.

## Configuration

Add the connector to `config/connectors.yaml`:

```yaml
connectors:
  fhir_epic:
    enabled: true
    exposed_via: ["rest", "grpc", "mcp"]
    auth:
      provider: oauth2
      grant_method: private_key_jwt
      token_url_secret: EPIC_TOKEN_URL
      client_id_secret: EPIC_CLIENT_ID
      private_key_secret: EPIC_PRIVATE_KEY
      kid_secret: EPIC_KID
      algorithm: RS384
```

Secrets are read through the configured secret provider (environment variables by default):

| Variable | Purpose |
|---|---|
| `EPIC_FHIR_BASE_URL` | Base URL of the Epic FHIR R4 endpoint |
| `EPIC_TOKEN_URL` | OAuth 2.0 token endpoint |
| `EPIC_CLIENT_ID` | Client ID of the backend app registered with Epic |
| `EPIC_KID` | Key ID of the public key registered with Epic |
| `EPIC_PRIVATE_KEY` | RSA private key (PEM) that signs the client assertion |

## Usage

Set `NW_ALLOWED_CONNECTORS=fhir_epic` (comma-separate several). Node Wire loads only the
connectors listed there, and discovers them through the `node_wire.connectors` entry point.
A Node Wire host (the REST, gRPC or MCP server, or `ConnectorFactory`) then builds the connector
from the configuration above. See [Use a connector](https://aot-technologies.github.io/node-wire/connectors/).

## Security notes

Logs record the operation, the URL path, the HTTP status and result counts, never request parameters or FHIR
resource bodies. The connector can write to a patient chart: limit a deployment with an exposure profile. See [Privacy](https://aot-technologies.github.io/node-wire/privacy/).

## Documentation

- [Node Wire on GitHub](https://github.com/AOT-Technologies/node-wire): source, issues and full documentation
- [Connector reference](https://aot-technologies.github.io/node-wire/connector-reference/#fhir_epic)
- [Configuration](https://aot-technologies.github.io/node-wire/configuration/)
- [Error codes](https://aot-technologies.github.io/node-wire/errors/)
- [Changelog](https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md)

## License

Apache-2.0. See [LICENSE](https://github.com/AOT-Technologies/node-wire/blob/main/LICENSE).
