<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Cerner FHIR connector

> **Connector ID:** `fhir_cerner`
> **Source:** `src/node_wire_fhir_cerner/`
> **Versions:** Oracle Health (Cerner) Millennium FHIR R4 (FHIR 4.0.1, US Core), written against the capability statement of November 2026; Python 3.11+

Hand-written. `logic.py` holds the actions, `schema.py` the input models. Every call goes through one helper (`_send`).
**129 actions**: every R4 read, search, create, update, patch and delete that Oracle's FHIR R4 API documentation lists for
Millennium's 42 resource types, plus the operations (`$autogen-ccd-if`, `$docref`, `$health-cards-issue`, the charge item
operations, batch) and the bulk export calls. The original five actions keep their names and shape. DSTU2 is not covered:
Oracle no longer supports it. The Oracle Health AI Application Suite APIs, the patient and provider public endpoints and the
SMART app launch flow are separate products and are not covered either.

## Configuration

| Setting | Where | Notes |
|---|---|---|
| `CERNER_FHIR_BASE_URL` | env, or tenant setting | The FHIR R4 root of the tenant, for example `https://fhir-ehr-code.cerner.com/r4/<tenant-id>`. Read through the secret provider as `cerner_fhir_base_url`. **Server-side only**: no action accepts it |
| `CERNER_TOKEN_URL` | env, or tenant setting | The `smart-v1` token endpoint, `https://authorization.cerner.com/tenants/<tenant-id>/protocols/oauth2/profiles/smart-v1/token`. A URL containing `/hosts/` is refused |
| `CERNER_CLIENT_ID` | secret | Client id of the system account registered with Cerner |
| `CERNER_KID` | secret | Key id of the public key registered with Cerner |
| `CERNER_PRIVATE_KEY` | secret | PEM private key that signs the client assertion |
| `CERNER_SCOPES` | secret (optional) | Space-separated scopes of the token request. Overrides `scopes` in `connectors.yaml` |
| `auth` | `connectors.yaml` | `provider: oauth2`, `grant_method: private_key_jwt`, `algorithm: RS384` (SMART backend services). The runtime signs the JWT, fetches the token and caches it; the connector only asks for the `Authorization` header |
| `NW_TIMEOUT` | env | Request timeout in seconds, default `30` |

**Scopes.** Cerner checks the scopes of the token request: `system/<Resource>.read` for reads and searches,
`system/<Resource>.write` for creates, updates, patches and deletes (for example `system/Observation.read`). An action only works
for a resource whose scope is in `CERNER_SCOPES` (or the `scopes` list in `connectors.yaml`, which ships with four) and is
granted to the system account in Cerner. A missing scope is a 403, reported as `CERNER_AUTH_ERROR`. Observation updates need
both scopes. Bulk export needs the read scope of every type in `type`, and a JWT-authenticated account.

The token is only ever sent to the configured server: `fetch_next_page` and the bulk calls take a URL and refuse one whose
scheme, host or port differ from `CERNER_FHIR_BASE_URL`. The one exception is the file download link Cerner answers with:
`bulk_export_file` follows it **without** the token, and only if it is https and not a private, loopback or link-local address (the runtime's outbound check, which also honours `NW_REST_ALLOWED_HOSTS`). The shipped config exposes the connector on REST, gRPC and
MCP; with 129 actions, many of them writes to a patient chart, limit what a deployment can do with an
[exposure profile](../../docs/exposure.md) (`NW_EXPOSE_PROFILE`).

## How a request is built

- The resource type is fixed by the action. A resource id goes in the path (URL-quoted) and may only hold letters, digits,
  `-` and `.`.
- **Search fields are Cerner's own search parameters** (from its capability statement), snake_case of the parameter name:
  `count` is `_count`, `id` is `_id`, `last_updated` is `_lastUpdated`, `physical_type` is `-physicalType` and
  `date_or_req_period` is `-date-or-req-period`. A resource has a `count` field only if Cerner lists `_count` for it. Values are
  FHIR search strings: a comma means "or", dates take a prefix (`ge2024-01-01`), tokens take `system|code`. Fields you leave out
  are not sent. `search_consent` and `search_slot` also take `include`.
- **A search must be scoped.** Where Cerner's capability statement says one of some parameters is required, the action
  refuses a search without one of them (for example `patient`, `subject` or `id` for `search_condition`; `id` or `service_type`
  for `search_slot`). A resource without such a rule needs at least one parameter.
- **Writes take the FHIR resource as JSON** in `resource`; `resourceType` is filled in (a different one is refused).
- **Updates, patches and deletes need `if_match`**, the version of the resource. Every read, create, update and patch returns it
  as `etag` (for example `W/"3"`); pass it on. A bare number such as `3` is sent as `W/"3"`. `update_*` also takes
  `resource_id`, and the body's `id` is set to it; fields left out of the body are removed from the record. `patch_*` takes
  `operations`, an RFC 6902 JSON Patch list, and only the paths listed on the resource's page in Cerner's documentation work.
- A search returns `resources`, `total` and `next_url`; pass `next_url` to `fetch_next_page`. A read returns `resource` and
  `etag`. A create returns `resource_id` (from the `Location` header) and the body when Cerner sends one.
- Every action returns the same output model, with the fields the action does not use left empty.
- Calls share the runtime's pooled HTTP client. An error answer is an error whose message carries Cerner's own
  `OperationOutcome` text. The runtime retries the retryable ones up to 3 times:

  | Answer | Error code | Category |
  |---|---|---|
  | 429 | `CERNER_RATE_LIMITED` | retryable |
  | 502, 503, 504 on `GET` and `DELETE` | `CERNER_UNAVAILABLE` | retryable |
  | any other 5xx (500, and 502, 503, 504 on a write) | `CERNER_SERVER_ERROR` | fatal, not retried: a gateway error on a `POST` may have stored the record |
  | 401, 403 (the app or token has no scope or access for this API) | `CERNER_AUTH_ERROR` | auth |
  | any other 4xx, and a bad input | `CERNER_HTTP_ERROR`, `CERNER_VALIDATION_ERROR` | business |
  | connection failure, connect or pool timeout; any other timeout on a read, an update, a delete, a `$health-cards-issue` | `CERNER_CONNECT_ERROR`, `CERNER_TIMEOUT` | retryable |
  | timeout after a create, a patch, a charge item operation, a batch or a bulk kick-off was sent | `CERNER_TIMEOUT_OUTCOME_UNKNOWN` | not retried: a repeat could store the record twice, and Cerner allows one bulk export at a time |

- **Privacy:** logs record the operation, the method, the URL path, the HTTP status and counts, never query values, request
  bodies or answers (patient data). The `httpx` logger is set to WARNING because it would log every URL with its query string.

## The original five actions

`read_patient`, `search_patients`, `search_encounter`, `create_document_reference` and `search_document_reference` keep their
names, inputs (including `search_params`, the name fields and the LLM-alias normalizers) and Cerner's rules. Differences:
a search returns `next_url`, `search_patients` fetches at most 50 ids, five requests at a time, and errors follow the table above.

`create_document_reference` fills in what Cerner's validation requires: it base64-encodes `text` when no `data` is given, adds
`;charset=utf-8` to `text/*` content types, adds `docStatus`, `userSelected`, the type `text` and an `authenticator` (the first
author), and adds a one-hour `context.period` when an encounter is given without one. It refuses a LOINC document type (Cerner
needs its CodeSet 72), a missing display, attachment title, text or data, and a missing author.

## Bulk export

Cerner has two exports: `bulk_export_group` (needs the id of a group the organisation set up, Bulk Data 1.0.1) and
`bulk_export_patients` (a list of up to 20,000 patient ids, Bulk Data 2.0.0). Then `bulk_export_status` until it returns the
manifest (`resource.output`), `bulk_export_file` for each file and `bulk_export_delete` once everything is downloaded. Only
one export per app and tenant can run at a time, files expire after 30 days, and exports need a JWT-authenticated account.
`type`, `since` and `type_filter` keep the files small. A file is returned as NDJSON text, 20 MB at most; a larger one is refused
while it is being read, so narrow the export.

## Actions

Every parameter in Cerner's capability statement for a resource is a field of its search action.

### Wave 0 (5)

`read_patient`, `search_patients`, `search_encounter`, `create_document_reference`, `search_document_reference`

### Wave 1: clinical resources, read and search (36 new)

Reads (19): `read_allergy_intolerance`, `read_care_plan`, `read_care_team`, `read_condition`, `read_device`, `read_diagnostic_report`, `read_document_reference`, `read_encounter`, `read_goal`, `read_immunization`, `read_location`, `read_medication_request`, `read_observation`, `read_organization`, `read_practitioner`, `read_procedure`, `read_provenance`, `read_service_request`, `read_specimen`

Searches (17): `search_allergy_intolerance`, `search_care_plan`, `search_care_team`, `search_condition`, `search_device`, `search_diagnostic_report`, `search_goal`, `search_immunization`, `search_location`, `search_medication_request`, `search_observation`, `search_organization`, `search_practitioner`, `search_procedure`, `search_provenance`, `search_service_request`, `search_specimen`

### Wave 2: other resources, read and search (38)

Reads (21): `read_account`, `read_appointment`, `read_charge_item`, `read_communication`, `read_consent`, `read_coverage`, `read_family_member_history`, `read_insurance_plan`, `read_medication_administration`, `read_medication_dispense`, `read_nutrition_order`, `read_person`, `read_questionnaire`, `read_questionnaire_response`, `read_related_person`, `read_schedule`, `read_slot`, `read_binary`, `read_media`, `read_operation_definition`, `read_structure_definition`

Searches (17): `search_account`, `search_appointment`, `search_charge_item`, `search_communication`, `search_consent`, `search_coverage`, `search_family_member_history`, `search_insurance_plan`, `search_medication_administration`, `search_medication_dispense`, `search_nutrition_order`, `search_person`, `search_questionnaire`, `search_questionnaire_response`, `search_related_person`, `search_schedule`, `search_slot`

`read_binary`, `read_media`, `read_operation_definition` and `read_structure_definition` have no search.

### Wave 3: writes (33 new)

Creates (17): `create_allergy_intolerance`, `create_appointment`, `create_communication`, `create_condition`, `create_coverage`, `create_diagnostic_report`, `create_encounter`, `create_family_member_history`, `create_immunization`, `create_medication_request`, `create_observation`, `create_organization`, `create_patient`, `create_practitioner`, `create_procedure`, `create_provenance`, `create_related_person`

Updates (7): `update_allergy_intolerance`, `update_condition`, `update_document_reference`, `update_family_member_history`, `update_immunization`, `update_observation`, `update_questionnaire_response`

Patches (8): `patch_appointment`, `patch_communication`, `patch_coverage`, `patch_encounter`, `patch_medication_request`, `patch_patient`, `patch_related_person`, `patch_slot`

Deletes (1): `delete_coverage`

### Wave 4: operations (17)

`bulk_export_delete`, `bulk_export_file`, `bulk_export_group`, `bulk_export_patients`, `bulk_export_status`, `charge_item_create`, `charge_item_credit`, `charge_item_modify`, `create_financial_transaction`, `docref`, `execute_batch`, `fetch_next_page`, `get_capability_statement`, `get_caregiver_organizations`, `get_ccd`, `get_smart_configuration`, `health_cards_issue`

`get_ccd` generates a Continuity of Care Document (`xml: true` returns the XML as text); `docref` is the Argonaut `$docref`
document search; `health_cards_issue`, `charge_item_create`, `charge_item_credit` and `charge_item_modify` take the operation's
`Parameters` resource in `parameters`; `create_financial_transaction` creates a `Basic` resource (it needs the Basic and
FinancialTransaction scopes and Cerner's Patient Accounting); `execute_batch` posts a batch Bundle, in which Cerner supports only
some operations, for example creating a Provenance. The write bodies and operation parameters come from Oracle's API
documentation and are not all checked against a live tenant.
