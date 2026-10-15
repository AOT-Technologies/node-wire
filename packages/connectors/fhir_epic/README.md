<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# node-wire-fhir-epic

Epic FHIR R4 connector for Node Wire. Lets an agent or app read and search patients, search
encounters, and create and search document references in an Epic EHR. It connects as a SMART on
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

Wheels are compiled with Cython and contain no Python source. They are built for CPython 3.11,
3.12 and 3.13 on Linux x86_64 (manylinux and musllinux), macOS arm64 and Windows x64.

## Actions

The connector registers as `fhir_epic`. MCP tool names are `<connector_id>_<action>`, with
dots replaced by underscores.

| Action | MCP tool | What it does |
|---|---|---|
| `read_patient` | `fhir_epic_read_patient` | Reads one Patient by ID (`{"resource_id": "..."}`) |
| `search_patients` | `fhir_epic_search_patients` | Finds Patients by ID list or by name and birth date |
| `search_encounter` | `fhir_epic_search_encounter` | Finds a patient's Encounters, optionally filtered by status and date |
| `create_document_reference` | `fhir_epic_create_document_reference` | Writes a clinical document (a DocumentReference with attachment) for a patient |
| `search_document_reference` | `fhir_epic_search_document_reference` | Finds DocumentReferences with FHIR search parameters |

Arguments accept common aliases that language models produce, such as `patientId` for
`resource_id`. `search_encounter` requires a patient filter and refuses to call Epic without one.

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

Logs record the operation mode, HTTP status and result counts, never request parameters or FHIR
resource bodies. See [Privacy](https://aot-technologies.github.io/node-wire/privacy/).

## Documentation

- [Connector reference](https://aot-technologies.github.io/node-wire/connector-reference/#fhir_epic-and-fhir_cerner)
- [Configuration](https://aot-technologies.github.io/node-wire/configuration/)
- [Error codes](https://aot-technologies.github.io/node-wire/errors/)
- [Changelog](https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md)

## License

Apache-2.0. See [LICENSE](https://github.com/AOT-Technologies/node-wire/blob/main/LICENSE).
