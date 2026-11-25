<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# node-wire-salesforce

Salesforce connector for Node Wire. Lets an agent or app work with Leads, Contacts and any other
object through the Salesforce REST API v68.0 and Bulk API 2.0 (150 actions). It authenticates with an OAuth 2.0 refresh
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

The wheel is pure Python (`py3-none-any`) and installs on CPython 3.11, 3.12 and 3.13 on any
OS and CPU.

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

The other 142 actions cover the REST API and Bulk API 2.0, by group:

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

Exposure profiles that limit the actions (read-only, records, leads and contacts, bulk) are in `expose/salesforce/` in the repository.
The API version is `v68.0` unless `api_version` is set in `connectors.yaml`. The instance URL and token are
server-side only: no action accepts them.

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
