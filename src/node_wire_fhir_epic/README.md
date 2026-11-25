<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Epic FHIR connector

> **Connector ID:** `fhir_epic`
> **Source:** `src/node_wire_fhir_epic/`
> **Versions:** Epic on FHIR R4 (FHIR 4.0.1, US Core 6.1.0), written against the Epic November 2026 capability statement; Python 3.11+

Hand-written. `logic.py` holds the actions, `schema.py` the input models. Every call goes through one helper (`_send`).
**137 actions**: every R4 read, search, create and update Epic lists for its 57 public resource types (129, among
them the original five, which keep their names and shape), plus Patient `$match` and `$summary`, `ValueSet/$expand`, the four bulk
export calls and `fetch_next_page`. DSTU2 and STU3 are not covered, and neither are the operations that exist only
there (`Appointment/$book`, `$find`, `Slot`, `Schedule`). The CDS Hooks variants, the COVID SMART Scheduling Links and
`Device/$auxiliary-function` are left out on purpose. ConceptMap, Measure and MeasureReport are in the capability
statement but not on Epic's public API list, so they are left out too.

## Configuration

| Setting | Where | Notes |
|---|---|---|
| `EPIC_FHIR_BASE_URL` | env, or tenant setting | The FHIR R4 root, for example `https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4`. Read through the secret provider as `epic_fhir_base_url`. **Server-side only**: no action accepts it |
| `EPIC_TOKEN_URL` | env, or tenant setting | The OAuth 2.0 token endpoint |
| `EPIC_CLIENT_ID` | secret | Client id of the backend app registered with Epic |
| `EPIC_KID` | secret | Key id of the public key registered with Epic |
| `EPIC_PRIVATE_KEY` | secret | PEM private key that signs the client assertion |
| `auth` | `connectors.yaml` | `provider: oauth2`, `grant_method: private_key_jwt`, `algorithm: RS384` (SMART backend services). The runtime signs the JWT, fetches the token and caches it; the connector only asks for the `Authorization` header |
| `NW_TIMEOUT` | env | Request timeout in seconds, default `30` |

The token is only ever sent to the configured server: `fetch_next_page` and the bulk calls take a URL and refuse one whose
scheme, host or port differ from `EPIC_FHIR_BASE_URL`. Epic backend apps get their access from the app registration, not
from a scope in the request, so enable the R4 API of each action you use there (for example `Observation.Search (Labs) (R4)`;
the bulk calls need the four Bulk Data APIs and the R4 search API of every resource exported). The shipped config exposes the
connector on REST, gRPC and MCP; with 137 actions, many of them writes to a patient chart, limit what a deployment can do
with an [exposure profile](../../docs/exposure.md) (`NW_EXPOSE_PROFILE`).

## How a request is built

- The resource type is fixed by the action. A resource id goes in the path (URL-quoted) and may only hold letters, digits,
  `-` and `.`.
- **Search fields are Epic's own search parameters** (from its capability statement), snake_case of the parameter name:
  `count` is `_count`, `id` is `_id`, `combo_code` is `combo-code`, `medication_code` is `medication.code`. Values are FHIR search
  strings: a comma means "or", dates take a prefix (`ge2024-01-01`), tokens take `system|code`. Fields you leave out are not
  sent. A parameter that is also called `action` (Consent) is the field `action_`.
- `include` and `revinclude` (lists) go out as repeated `_include` and `_revinclude`.
- **A search must be scoped.** A resource with a `patient` parameter needs `patient`, `subject` or `id`; Observation also needs
  `category` or `code` (without `id`); a resource without one needs at least one parameter. Epic ignores everything but
  `_id` when `_id` is present, so `id` alone is always accepted.
- **Writes take the FHIR resource as JSON** in `resource`; `resourceType` is filled in (a different one is refused). `update_*`
  also takes `resource_id`, and the body's `id` is set to it (a different one is refused).
- A search returns `resources`, `total` and `next_url`; pass `next_url` to `fetch_next_page`. A read returns `resource`. A create
  returns `resource_id` (from the `Location` header) and the body when Epic sends one.
- Every action returns the same output model, with the fields the action does not use left empty.
- Calls share the runtime's pooled HTTP client. An error answer is an error whose message carries Epic's own
  `OperationOutcome` diagnostics. The runtime retries the retryable ones up to 3 times:

  | Answer | Error code | Category |
  |---|---|---|
  | 429 | `FHIR_RATE_LIMITED` | retryable |
  | 502, 503, 504 on `GET` and `DELETE` | `FHIR_UNAVAILABLE` | retryable (not on writes: a gateway error on a `POST` may have stored it) |
  | 401, 403 (the Epic app is not authorised for this API) | `FHIR_AUTH_ERROR` | auth |
  | any other 4xx or 5xx, and a bad input | `FHIR_HTTP_ERROR`, `FHIR_INVALID_REQUEST` | business |
  | connection failure, connect or pool timeout; any other timeout on a read, an update, a `$match` | `FHIR_CONNECT_ERROR`, `FHIR_TIMEOUT` | retryable |
  | timeout after a create or a bulk kick-off was sent | `FHIR_TIMEOUT_OUTCOME_UNKNOWN` | not retried: Epic has no conditional create, so a repeat could store the record twice, and a kick-off counts against Epic's one request per group per day |

- **Privacy:** logs record the operation, the method, the URL path, the HTTP status and counts, never query values, request
  bodies or answers (patient data). The `httpx` logger is set to WARNING because it would log every URL with its query string.

## The original five actions

`read_patient`, `search_patients`, `search_encounter`, `create_document_reference` and `search_document_reference` keep their
names, inputs (including `search_params` and the name fields) and the LLM-alias normalizers. Differences: a search returns
`next_url`, `search_patients` fetches at most 50 ids at once, and errors follow the table above.

## Bulk export

Epic supports only the group export, on bulk data 1.0.1 with some 1.1.0 features: `bulk_export_group` (needs the id of a
group the organisation set up), then `bulk_export_status` until it returns the manifest (`resource.output`), then
`bulk_export_file` for each file (NDJSON text, 20 MB at most) and `bulk_export_delete` once everything is downloaded. Poll every
10 minutes (groups up to 100 patients) or 30 minutes. `type` and `type_filter` keep the files small; `type_filter` needs `type`.
A group can be exported once in 24 hours unless the organisation changes that.

## Actions

Every parameter in Epic's capability statement for a resource is a field of its search action.

### Wave 0 (5)

`create_document_reference`, `read_patient`, `search_document_reference`, `search_encounter`, `search_patients`

### Wave 1: US Core, read and search (47 new)

Reads (25): `read_allergy_intolerance`, `read_binary`, `read_care_plan`, `read_care_team`, `read_condition`, `read_coverage`, `read_device`, `read_diagnostic_report`, `read_document_reference`, `read_encounter`, `read_goal`, `read_immunization`, `read_location`, `read_medication`, `read_medication_dispense`, `read_medication_request`, `read_observation`, `read_organization`, `read_practitioner`, `read_practitioner_role`, `read_procedure`, `read_provenance`, `read_related_person`, `read_service_request`, `read_specimen`

Searches (22): `search_allergy_intolerance`, `search_binary`, `search_care_plan`, `search_care_team`, `search_condition`, `search_coverage`, `search_device`, `search_diagnostic_report`, `search_goal`, `search_immunization`, `search_location`, `search_medication`, `search_medication_dispense`, `search_medication_request`, `search_observation`, `search_organization`, `search_practitioner`, `search_practitioner_role`, `search_procedure`, `search_related_person`, `search_service_request`, `search_specimen`

`search_binary` takes only `id` and `count`. `read_provenance` has no search.

### Wave 2: other resources, read and search (61)

Reads (31): `read_account`, `read_adverse_event`, `read_appointment`, `read_body_structure`, `read_claim`, `read_communication`, `read_consent`, `read_contract`, `read_device_request`, `read_device_use_statement`, `read_endpoint`, `read_episode_of_care`, `read_explanation_of_benefit`, `read_family_member_history`, `read_flag`, `read_group`, `read_imaging_study`, `read_immunization_recommendation`, `read_list`, `read_media`, `read_medication_administration`, `read_medication_statement`, `read_nutrition_order`, `read_questionnaire`, `read_questionnaire_response`, `read_request_group`, `read_research_study`, `read_research_subject`, `read_substance`, `read_task`, `read_value_set`

Searches (30): `search_account`, `search_adverse_event`, `search_appointment`, `search_body_structure`, `search_claim`, `search_communication`, `search_consent`, `search_contract`, `search_device_request`, `search_device_use_statement`, `search_endpoint`, `search_episode_of_care`, `search_explanation_of_benefit`, `search_family_member_history`, `search_flag`, `search_group`, `search_imaging_study`, `search_immunization_recommendation`, `search_list`, `search_media`, `search_medication_administration`, `search_medication_statement`, `search_nutrition_order`, `search_questionnaire`, `search_questionnaire_response`, `search_request_group`, `search_research_study`, `search_research_subject`, `search_substance`, `search_task`

`read_value_set` has no search (use `value_set_expand`).

### Wave 3: writes (16 new)

Creates (9): `create_allergy_intolerance`, `create_body_structure`, `create_communication`, `create_condition`, `create_observation`, `create_patient`, `create_procedure`, `create_questionnaire_response`, `create_service_request`

Updates (7): `update_body_structure`, `update_diagnostic_report`, `update_document_reference`, `update_observation`, `update_procedure`, `update_service_request`, `update_task`

### Wave 4: operations (8)

`bulk_export_delete`, `bulk_export_file`, `bulk_export_group`, `bulk_export_status`, `fetch_next_page`, `patient_match`, `patient_summary`, `value_set_expand`

`patient_match` sends a `Parameters` body; `patient_summary` is the International Patient Summary; `value_set_expand` takes the
`url` of a value set Epic supports. These three and the bulk calls come from Epic's public API list rather than its capability
statement, and need checking against a live server.

Epic's own API names group several of these by use (Observation Labs, Vital Signs, Social History, ...). They share one endpoint, so
there is one action per endpoint, and `category` or `code` picks the kind; the `category` field lists the values.
