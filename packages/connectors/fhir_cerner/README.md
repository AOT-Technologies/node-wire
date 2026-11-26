<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# node-wire-fhir-cerner

Oracle Health (Cerner) FHIR R4 connector for Node Wire. Lets an agent or app read, search, create, update, patch and
delete the resources Millennium publishes (patients, encounters, observations, documents and more) and run its bulk export. It
connects as a SMART on FHIR backend service (OAuth 2.0 client credentials with a signed JWT).

Part of [Node Wire](https://github.com/AOT-Technologies/node-wire). Every call runs through the
Node Wire runtime, so it gets input validation, retries with a circuit breaker, scope policy,
a shared error taxonomy, and OpenTelemetry tracing. A Node Wire host exposes each action over REST,
gRPC and MCP.

## Install

```bash
pip install node-wire-fhir-cerner
```

This pulls in [`node-wire-runtime`](https://pypi.org/project/node-wire-runtime/). Pre-releases
are published as `X.Y.ZbN`; install one with `pip install --pre node-wire-fhir-cerner`. To install
every connector at once, use [`node-wire-connectors`](https://pypi.org/project/node-wire-connectors/).

The wheel is pure Python (`py3-none-any`) and installs on CPython 3.11, 3.12 and 3.13 on any
OS and CPU.

## Actions

The connector registers as `fhir_cerner` and has **129 actions** for Millennium's FHIR R4 API. MCP tool names are
`<connector_id>_<action>`.

| Group | Actions |
|---|---|
| Original five | `read_patient`, `search_patients`, `search_encounter`, `create_document_reference`, `search_document_reference` |
| Read and search | `read_<resource>` and `search_<resource>`, for example `read_observation`, `search_condition`, `search_medication_request`; every Cerner search parameter is a field |
| Write | `create_<resource>`, `update_<resource>` (PUT), `patch_<resource>` (JSON Patch) and `delete_coverage`; updates, patches and deletes take `if_match`, the resource version |
| Operations | `get_ccd`, `docref`, `health_cards_issue`, `charge_item_create`, `charge_item_credit`, `charge_item_modify`, `create_financial_transaction`, `execute_batch`, `get_capability_statement`, `get_smart_configuration`, `get_caregiver_organizations`, `fetch_next_page` |
| Bulk export | `bulk_export_group`, `bulk_export_patients`, `bulk_export_status`, `bulk_export_file`, `bulk_export_delete` |

The full list, request rules, scopes and error codes are in the
[connector README](https://github.com/AOT-Technologies/node-wire/blob/main/src/node_wire_fhir_cerner/README.md).
The original five accept common aliases that language models produce, such as `patientId` for
`resource_id`. `search_encounter` requires a patient filter and refuses to call Cerner without one. Cerner checks the scopes of
the token request, so an action works only for a resource whose `system/<Resource>.read` or `.write` scope is configured.

## Configuration

Add the connector to `config/connectors.yaml`:

```yaml
connectors:
  fhir_cerner:
    enabled: true
    exposed_via: ["rest", "grpc", "mcp"]
    auth:
      provider: oauth2
      grant_method: private_key_jwt
      token_url_secret: CERNER_TOKEN_URL
      client_id_secret: CERNER_CLIENT_ID
      private_key_secret: CERNER_PRIVATE_KEY
      kid_secret: CERNER_KID
      algorithm: RS384
      scopes_secret: CERNER_SCOPES
      scopes:
      - system/Patient.read
      - system/Encounter.read
      - system/DocumentReference.read
      - system/DocumentReference.write
```

Secrets are read through the configured secret provider (environment variables by default):

| Variable | Purpose |
|---|---|
| `CERNER_FHIR_BASE_URL` | Base URL of the Cerner FHIR R4 endpoint |
| `CERNER_TOKEN_URL` | OAuth 2.0 token endpoint |
| `CERNER_CLIENT_ID` | Client ID of the system account registered in the Cerner Code Console |
| `CERNER_KID` | Key ID of the public key registered with Cerner |
| `CERNER_PRIVATE_KEY` | RSA private key (PEM) that signs the client assertion |
| `CERNER_SCOPES` | Optional. Space-separated scopes; overrides `scopes` in the YAML |

## Usage

Set `NW_ALLOWED_CONNECTORS=fhir_cerner` (comma-separate several). Node Wire loads only the
connectors listed there, and discovers them through the `node_wire.connectors` entry point.
A Node Wire host (the REST, gRPC or MCP server, or `ConnectorFactory`) then builds the connector
from the configuration above. See [Use a connector](https://aot-technologies.github.io/node-wire/connectors/).

## Security notes

Logs record the operation, HTTP status and result counts, never request parameters or FHIR
resource bodies. A bulk file link is followed without the access token. See [Privacy](https://aot-technologies.github.io/node-wire/privacy/).

## Documentation

- [Node Wire on GitHub](https://github.com/AOT-Technologies/node-wire): source, issues and full documentation
- [Connector reference](https://aot-technologies.github.io/node-wire/connector-reference/#fhir_cerner)
- [Configuration](https://aot-technologies.github.io/node-wire/configuration/)
- [Error codes](https://aot-technologies.github.io/node-wire/errors/)
- [Changelog](https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md)

## License

Apache-2.0. See [LICENSE](https://github.com/AOT-Technologies/node-wire/blob/main/LICENSE).
