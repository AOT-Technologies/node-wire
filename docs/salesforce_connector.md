<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Salesforce connector (`src/node_wire_salesforce`)

The Salesforce connector provides a secure, asynchronous interface to Salesforce: CRM records (Leads and Contacts), the rest of the REST API and Bulk API 2.0. It has 150 actions, written against API **v68.0 (Winter '27)**. It uses Node Wire's `OAuth2AuthProvider` to refresh tokens automatically, so it fits agentic workflows and medical-to-CRM pipelines.

## Capabilities

The connector exposes full CRUD (Create, Read, Update, Delete) operations for the two most common Salesforce objects used in healthcare and enterprise outreach:

| Action | Description |
|---|---|
| `create_lead` | Create a new Lead record. Requires `LastName` and `Company`. |
| `read_lead` | Fetch a single Lead record by ID. |
| `update_lead` | Update specific fields on an existing Lead. |
| `delete_lead` | Remove a Lead record. |
| `create_contact` | Create a new Contact record. Requires `LastName`. |
| `read_contact` | Fetch a single Contact record by ID. |
| `update_contact` | Update specific fields on an existing Contact. |
| `delete_contact` | Remove a Contact record. |

### REST API and Bulk API 2.0 (142 actions)

Every operation of the REST API Developer Guide and the Bulk API 2.0 guide (v68.0) that is not deprecated, in beta or a plain
"return headers" (HEAD) variant. Fields named in the path go in the path, the others go to the query (`GET`, `DELETE`) or the JSON body.
Lists are comma separated in the query. Open-ended options are one map (`parameters` for a query, a named field for a body). Responses are
`status_code`, `headers` and `body`; a binary answer (a blob field) is base64 and Bulk API 2.0 results are CSV text.

| Group | Actions |
|---|---|
| Org and discovery (10) | `list_api_versions`, `list_resources`, `get_limits`, `get_record_counts`, `describe_global`, `get_tabs`, `get_theme`, `get_app_menu_types`, `get_app_menu_items`, `get_mobile_app_menu_items` |
| sObject metadata (13) | `get_sobject_info`, `describe_sobject`, `get_deleted_records`, `get_updated_records`, `get_named_layout`, `get_layouts`, `get_layouts_for_record_type`, `get_global_publisher_layouts`, `get_compact_layouts`, `get_compact_layouts_for_objects`, `get_approval_layouts`, `get_approval_layout`, `get_platform_action` |
| sObject records (12) | `create_record`, `get_record`, `update_record`, `delete_record`, `get_record_by_external_id`, `upsert_record_by_external_id`, `delete_record_by_external_id`, `get_blob_field`, `get_rich_text_image`, `get_related_records`, `update_related_record`, `delete_related_record` |
| Collections (6) | `create_records`, `get_records`, `get_records_with_body`, `update_records`, `upsert_records`, `delete_records` |
| Composite (5) | `composite`, `get_composite_resources`, `composite_graph`, `composite_batch`, `create_record_tree` |
| Query and search (13) | `query`, `query_more`, `query_all`, `query_all_more`, `search`, `parameterized_search_get`, `parameterized_search_post`, `get_search_scope_order`, `get_search_result_layouts`, `search_suggestions`, `search_title_matches`, `search_suggested_queries`, `run_named_query` |
| List views and recent (7) | `list_list_views`, `get_list_view`, `describe_list_view`, `get_list_view_results`, `get_recent_list_views`, `get_recent_items`, `get_relevant_items` |
| Quick actions (8) | `list_quick_actions`, `create_record_with_quick_action`, `list_sobject_quick_actions`, `get_sobject_quick_action`, `invoke_sobject_quick_action`, `describe_sobject_quick_action`, `get_quick_action_default_values`, `get_quick_action_default_values_for_record` |
| Process and automation (11) | `list_process_approvals`, `submit_process_approval`, `list_process_rules`, `trigger_process_rules`, `list_process_rules_for_sobject`, `get_process_rule`, `trigger_process_rule`, `list_invocable_actions`, `list_standard_invocable_actions`, `list_custom_invocable_actions`, `deploy_data_kit_components` |
| Product schedules (3) | `get_product_schedules`, `create_product_schedules`, `delete_product_schedules` |
| Knowledge and support (9) | `get_support_resources`, `list_data_category_groups`, `get_data_category_detail`, `list_knowledge_articles`, `get_knowledge_article`, `get_knowledge_language_settings`, `get_suggested_articles`, `get_suggested_articles_for_record`, `get_embedded_service_config` |
| Platform events (2) | `get_event_schema_by_name`, `get_event_schema_by_id` |
| Consent and privacy (5) | `compile_consent`, `compile_multi_consent`, `write_consent`, `request_portability_data`, `get_portability_status` |
| Scheduler (3) | `get_scheduling_resources`, `get_appointment_slots`, `get_appointment_candidates` |
| Survey translations (6) | `set_survey_field_translation`, `set_survey_field_translations`, `delete_survey_field_translation`, `delete_survey_field_translations`, `get_survey_field_translation`, `get_survey_field_translations` |
| User passwords (6) | `get_user_password_status`, `set_user_password`, `reset_user_password`, `get_self_service_user_password_status`, `set_self_service_user_password`, `reset_self_service_user_password` |
| Lightning usage (6) | `get_lightning_toggle_metrics`, `get_lightning_usage_by_app_type`, `get_lightning_usage_by_browser`, `get_lightning_usage_by_page`, `get_lightning_usage_by_flexipage`, `get_lightning_exit_by_page` |
| Bulk API 2.0 ingest (10) | `create_ingest_job`, `upload_ingest_job_data`, `complete_ingest_job`, `abort_ingest_job`, `delete_ingest_job`, `get_ingest_job`, `list_ingest_jobs`, `get_ingest_successful_results`, `get_ingest_failed_results`, `get_ingest_unprocessed_records` |
| Bulk API 2.0 query (7) | `create_query_job`, `get_query_job`, `abort_query_job`, `delete_query_job`, `list_query_jobs`, `get_query_job_results`, `get_query_job_result_pages` |

Sources: the REST API Developer Guide and the Bulk API 2.0 and Bulk API Developer Guide, version 68.0 (Winter '27, October 2026).
Not included: the Tooling, UI, Connect, GraphQL, Metadata and Analytics APIs, which are separate APIs.

## Configuration

Add the following to your `config/connectors.yaml`:

```yaml
connectors:
  salesforce:
    enabled: true          # the shipped config has this off: set it to true to use the connector
    exposed_via: ["rest"]  # add grpc and mcp only together with an exposure profile (below)
    # api_version: v68.0   # optional; v68.0 is the default
    auth:
      provider: oauth2
      grant_method: refresh_token
      token_url_secret: SALESFORCE_TOKEN_URL
      client_id_secret: SALESFORCE_CLIENT_ID
      client_secret_secret: SALESFORCE_CLIENT_SECRET
      refresh_token_secret: SALESFORCE_REFRESH_TOKEN
```

## Environment variables

The following secrets must be provided (for example, in `.env` or via your secret manager):

| Variable | Example |
|---|---|
| `SALESFORCE_INSTANCE_URL` | `https://your-domain.my.salesforce.com` |
| `SALESFORCE_TOKEN_URL` | `https://login.salesforce.com/services/oauth2/token` |
| `SALESFORCE_CLIENT_ID` | `3MVG9...` |
| `SALESFORCE_CLIENT_SECRET` | `A1B2...` |
| `SALESFORCE_REFRESH_TOKEN` | `5Aep...` |

## Example usage

### REST API

The following request creates a lead:

```bash
curl -X POST http://localhost:8000/connectors/salesforce/create_lead \
  -H "X-API-Key: <your-api-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "LastName": "Doe",
    "Company": "Acme Corp",
    "Email": "john.doe@example.com",
    "Status": "Open - Not Contacted"
  }'
```

Replace `<your-api-key>` with your `NW_REST_API_KEY`.

### Agentic (MCP)

If registered via MCP, the agent can call the advertised tool `salesforce_create_lead` (legacy dotted form `salesforce.create_lead` still invokes but isn't what `tools/list` advertises) with the following arguments:

```json
{
  "LastName": "Smith",
  "Company": "HealthTech",
  "Email": "jane@smith.com"
}
```

## Playground interface

When the connector is enabled, the Node Wire playground shows a **Salesforce** card that lists every action of the connector (150).
Pick an action, fill its fields and run it. Open the playground at `http://localhost:8000/playground/` (when running locally), then select
**Connectors** → **Salesforce**. Salesforce is off in the shipped config, so set `enabled: true` first (see below).

## Limiting what a deployment can do

The connector has 150 actions, including password resets, bulk deletes and composite writes. The shipped `config/connectors.yaml` therefore
has Salesforce **disabled** and on REST only. Before you turn on `grpc` or `mcp`, pick an [exposure profile](exposure.md) with
`NW_EXPOSE_PROFILE`; a hidden action is not listed and is refused if called. Profiles in `expose/salesforce/`:

| Profile | Actions | What it allows |
|---|---|---|
| `salesforce-readonly` | 101 | Every read (`GET`) plus five reads that use a request body, with no password actions. Nothing changes in the org |
| `salesforce-records` | 37 | Create, read, update and delete records, query, search and describe, plus the 8 lead and contact actions. No bulk deletes, no passwords |
| `salesforce-leads-contacts` | 8 | Only the original lead and contact actions |
| `salesforce-bulk` | 17 | Only Bulk API 2.0 |

`NW_EXPOSE_PROFILE` is enforced on REST, gRPC and MCP alike (a hidden action is not listed and is refused if called), so the safe setting is
not just a comment. It is one name for the whole server: a loaded connector with no file of that name exposes nothing. See
[Action exposure profiles](exposure.md).

Also use a Salesforce user with only the permissions the deployment needs: the connector can do whatever that user can.

### Upgrading from the lead and contact connector

The shipped `config/connectors.yaml` used to enable Salesforce on REST, gRPC and MCP. It is now off and REST-only. To keep using it
over gRPC or MCP as before, set this in your own `connectors.yaml`, and limit it to the original 8 actions:

```yaml
  salesforce:
    enabled: true
    exposed_via: ["rest", "grpc", "mcp"]
```

```bash
NW_EXPOSE_PROFILE=salesforce-leads-contacts
```

## Errors

| Answer | Code | Category |
|---|---|---|
| 401 | `SALESFORCE_AUTH_ERROR` | auth |
| 429 | `SALESFORCE_RATE_LIMITED` | retryable |
| 502, 503, 504 on `GET` and `DELETE` | `SALESFORCE_TRANSIENT_ERROR` | retryable |
| any other 4xx or 5xx | `SALESFORCE_API_ERROR` | business |
| timeout, connection failure | `SALESFORCE_TIMEOUT`, `SALESFORCE_CONNECT_ERROR` | retryable |

The message carries Salesforce's own `errorCode` and `message`. The 8 lead and contact actions use the same request path and rules as the rest, so a 503 on `create_lead` is `SALESFORCE_API_ERROR`, not retried (a write may already have run). Before this version any 5xx on them was retryable.

## Security note

- **OAuth2**: Tokens are never logged or persisted to disk. `OAuth2AuthProvider` holds the resolved access token as a plain in-process value, scoped to the provider instance's lifetime — there is no additional encryption-at-rest or secure-memory mechanism.
- **Refresh Token Support**: The connector is configured to use `grant_method: refresh_token`, ensuring it can stay authenticated for long-running agentic tasks.
- **Traceability**: All actions are logged with a `trace_id` for auditing and idempotency tracking.
- **PII Protection**: Ensure your logging levels are set correctly; by default, the connector logs the metadata of the transaction but not the full PII payload.

## Related

- [Connector reference — catalog](connector-reference.md#connector-catalog): every shipped connector's actions, auth and secrets.
- [Use a connector](connectors.md): calling a connector in-process or over REST, per tenant.

