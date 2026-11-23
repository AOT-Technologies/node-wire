<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# n8n connector

> **Connector ID:** `n8n`
> **Source:** `src/node_wire_n8n/`
> **Versions:** n8n public API v1 (spec 1.1.1), written against n8n 2.47.7; Python 3.11+

Hand-written. `logic.py` holds the actions, `schema.py` the input models. Every API call goes through one helper (`_call`);
`trigger_workflow` calls a workflow's own webhook. **173 actions**: every operation of the n8n 2.47.7 public API (172) except
the 3 the spec marks deprecated (activate and deactivate a workflow, the old version read), plus `trigger_workflow`.

## Configuration

| Setting | Where | Notes |
|---|---|---|
| `instance_url` | `connectors.yaml` | The n8n instance root, for example `https://n8n.example.com`. **Server-side only**: no action accepts it. Not called `base_url`, because the config store allows that key only for REST connectors |
| `N8N_API_KEY` | secret | Created in n8n under Settings > n8n API. Sent as `X-N8N-API-KEY` |
| `auth` | `connectors.yaml` | `provider: static_token`, `secret_key: N8N_API_KEY`, `header_name: X-N8N-API-KEY`, `prefix: ""` |
| `NW_TIMEOUT` | env | Request timeout in seconds, default `30` |

There is no private-address guard, so an n8n on `localhost` or a private network works. The API key is only ever sent to the
configured `instance_url`. The shipped config has n8n **disabled** and on REST only, because the key grants instance-admin
actions. Limit what a deployment can do with a scoped API key (n8n 2.14 and later, all plans) and an
[exposure profile](../../docs/exposure.md); the scopes each group needs are listed below.

Profiles shipped in `expose/n8n/`: `n8n-readonly` (66 actions, every `GET`) and `n8n-workflows` (30 actions: workflows,
executions, tags and `trigger_workflow`, no deletes). Use one with `NW_EXPOSE_PROFILE` before adding `grpc` or `mcp` to `exposed_via`.

## How a request is built

- Fields named in the path go in the path (URL-quoted). Other fields go to the query for `GET` and `DELETE`, and to the JSON
  body otherwise. Fields you leave out are not sent.
- Field names are snake_case; the wire name is the camelCase of the field name (`workflow_id` -> `workflowId`). One name differs
  and is set explicitly (`ignore_ssl` -> `ignoreSSL`).
- Some bodies are one free-form value (a list of users, a log-streaming destination, an environment, a list of tag ids). They
  are a single field named after what they hold (`users`, `destination`, `environment`, `tags`).
- `idempotency_key` (on the 41 POST operations that support it) is sent as the `Idempotency-Key` header.
- Lists use `limit` (1 to 250, n8n default 100) and `cursor`; the next cursor is `nextCursor` in the response body.
- Every action returns `status_code`, `headers` and `body`. 4xx and 5xx answers are errors whose message includes n8n's own
  explanation. The runtime retries the retryable ones up to 3 times:

  | Answer | Error code | Category |
  |---|---|---|
  | 429 | `N8N_RATE_LIMITED` | retryable |
  | 502, 503, 504 on `GET` and `DELETE` | `N8N_UNAVAILABLE` | retryable (not on writes: a gateway error on a `POST` may have run it) |
  | 401, and 403 for a key that lacks the scope | `N8N_AUTH_ERROR` | auth |
  | any other 4xx or 5xx, and a 403 licence message | `N8N_HTTP_STATUS_ERROR` | business |
  | connection failure, connect or pool timeout; any other timeout on a read, a delete or a write sent with an `idempotency_key` | `N8N_CONNECT_ERROR`, `N8N_TIMEOUT` | retryable |
  | timeout after the request was sent, on a write without an `idempotency_key` | `N8N_TIMEOUT_OUTCOME_UNKNOWN` | not retried: n8n may have run it |
- Calls share the runtime's pooled HTTP client, so connections are reused.
- **Package files are base64.** `import_package` and `import_package_selection` take the `.n8np` archive as base64 in the
  `package` field and send it as a multipart upload; their other fields are form fields. `export_package` returns the
  archive as a base64 string in `body`.

## `trigger_workflow`

Calls a workflow's webhook. It does **not** send the n8n API key.

| Field | Notes |
|---|---|
| `webhook_url` | Full webhook URL. Its scheme, host and port must equal the configured `instance_url`, otherwise the call is refused |
| `method` | `GET`, `POST` (default), `PUT`, `PATCH`, `DELETE` |
| `payload` | Query parameters for `GET`, JSON body otherwise |
| `auth_type` | `none` (default), `header` (`auth_header_name`, `auth_header_value`) or `basic` (`username`, `password`) |

A non-JSON answer is returned as text.

## Actions

Actions marked **(required)** must always exist. Many areas need an n8n licence (variables, projects, source control, SSO,
log streaming, policies, promotions and others); on an unlicensed instance they answer 403.


### Wave 1

**Workflows (16)**

`list_workflows` **(required)**, `create_workflow`, `get_workflow` **(required)**, `update_workflow`, `delete_workflow`, `archive_workflow`, `unarchive_workflow`, `transfer_workflow`, `publish_workflow`, `unpublish_workflow`, `list_workflow_history`, `get_workflow_version`, `list_workflow_tags`, `update_workflow_tags`, `list_workflow_dependencies`, `trigger_workflow` **(required)**

Key scopes: `workflow:activate`, `workflow:create`, `workflow:deactivate`, `workflow:delete`, `workflow:list`, `workflow:move`, `workflow:read`, `workflow:update`, `workflowTags:list`, `workflowTags:update`

**Executions (8)**

`list_executions` **(required)**, `get_execution` **(required)**, `delete_execution`, `list_execution_tags`, `update_execution_tags`, `stop_many_executions`, `stop_execution`, `retry_execution`

Key scopes: `execution:delete`, `execution:list`, `execution:read`, `execution:retry`, `execution:stop`, `executionTags:list`, `executionTags:update`

**Tags (5)**

`list_tags`, `create_tag`, `get_tag`, `update_tag`, `delete_tag`

Key scopes: `tag:create`, `tag:delete`, `tag:list`, `tag:read`, `tag:update`

**Variables (4)**

`list_variables`, `create_variable`, `update_variable`, `delete_variable`

Key scopes: `variable:create`, `variable:delete`, `variable:list`, `variable:update`

**Credentials (9)**

`list_credentials`, `create_credential`, `get_credential`, `update_credential`, `delete_credential`, `transfer_credential`, `test_credential`, `get_credential_type`, `list_credential_dependencies`

Key scopes: `credential:create`, `credential:delete`, `credential:list`, `credential:move`, `credential:read`, `credential:update`

**Folders (5)**

`list_folders`, `create_folder`, `get_folder`, `update_folder`, `delete_folder`

Key scopes: `folder:create`, `folder:delete`, `folder:list`, `folder:read`, `folder:update`

**Data tables (16)**

`list_data_tables`, `create_data_table`, `get_data_table`, `update_data_table`, `delete_data_table`, `list_data_table_dependencies`, `list_data_table_columns`, `create_data_table_column`, `update_data_table_column`, `delete_data_table_column`, `list_data_table_rows`, `create_data_table_rows`, `upsert_data_table_row`, `update_data_table_rows`, `clear_data_table_rows`, `delete_data_table_rows`

Key scopes: `dataTable:create`, `dataTable:delete`, `dataTable:list`, `dataTable:read`, `dataTable:update`, `dataTableColumn:create`, `dataTableColumn:delete`, `dataTableColumn:read`, `dataTableColumn:update`, `dataTableRow:create`, `dataTableRow:delete`, `dataTableRow:read`, `dataTableRow:update`, `dataTableRow:upsert`

**Evaluations (5)**

`list_test_runs`, `create_test_run`, `get_test_run`, `list_test_cases`, `cancel_test_run`

Key scopes: `testRun:cancel`, `testRun:create`, `testRun:list`, `testRun:read`


### Wave 2

**Projects (8)**

`list_projects`, `create_project`, `update_project`, `delete_project`, `list_project_users`, `add_users_to_project`, `change_user_role_in_project`, `delete_user_from_project`

Key scopes: `project:create`, `project:delete`, `project:list`, `project:manageMembers`, `project:update`, `user:list`

**Roles (5)**

`list_roles`, `create_role`, `get_role`, `update_role`, `delete_role`

Key scopes: `role:list`, `role:manage`, `role:manageProject`, `role:read`

**Role mapping rules (5)**

`list_role_mapping_rules`, `create_role_mapping_rule`, `move_role_mapping_rule`, `update_role_mapping_rule`, `delete_role_mapping_rule`

Key scopes: `roleMappingRule:create`, `roleMappingRule:delete`, `roleMappingRule:list`, `roleMappingRule:update`

**Users (5)**

`list_users`, `create_user`, `get_user`, `delete_user`, `change_role`

Key scopes: `user:changeRole`, `user:create`, `user:delete`, `user:list`, `user:read`

**Audit (1)**

`generate_audit`

Key scopes: `securityAudit:generate`

**Discover (1)**

`get_discover`

**Insights (1)**

`get_insights_summary`

Key scopes: `insights:read`

**Community packages (4)**

`list_community_packages`, `install_community_package`, `update_community_package`, `uninstall_community_package`

Key scopes: `communityPackage:install`, `communityPackage:list`, `communityPackage:uninstall`, `communityPackage:update`

**Source control (3)**

`get_source_control_status`, `push_source_control`, `pull_source_control`

Key scopes: `sourceControl:pull`, `sourceControl:push`, `sourceControl:read`


### Wave 3

**LDAP (4)**

`get_ldap_configuration`, `update_ldap_configuration`, `get_ldap_sync`, `run_ldap_sync`

Key scopes: `ldap:manage`, `ldap:sync`

**SAML (2)**

`get_saml_configuration`, `update_saml_configuration`

Key scopes: `saml:manage`

**OIDC (2)**

`get_oidc_configuration`, `set_oidc_configuration`

Key scopes: `oidc:manage`

**OTel (3)**

`get_otel_settings`, `update_otel_settings`, `test_otel_trace`

Key scopes: `otel:manage`

**Rate limits (2)**

`get_rate_limit_settings`, `update_rate_limit_settings`

Key scopes: `securitySettings:manage`

**Security policy (2)**

`get_security_policy`, `update_security_policy`

Key scopes: `securitySettings:manage`

**Log streaming (7)**

`list_log_streaming_event_types`, `list_log_streaming_destinations`, `create_log_streaming_destination`, `get_log_streaming_destination`, `update_log_streaming_destination`, `delete_log_streaming_destination`, `test_log_streaming_destination`

Key scopes: `eventBusDestination:create`, `eventBusDestination:delete`, `eventBusDestination:list`, `eventBusDestination:read`, `eventBusDestination:test`, `eventBusDestination:update`


### Wave 4

**Credential type policy (11)** _Preview: breaking changes may occur without a major version bump._

`get_credential_type_instance_policy`, `update_credential_type_instance_policy`, `get_credential_type_project_policy`, `update_credential_type_project_policy`, `list_credential_type_policy_documents`, `create_credential_type_policy_document`, `get_credential_type_policy_document`, `update_credential_type_policy_document`, `delete_credential_type_policy_document`, `list_credential_type_policy_attachments`, `replace_credential_type_policy_attachments`

Key scopes: `credentialTypePolicy:manage`

**Node type policy (11)** _Preview: breaking changes may occur without a major version bump._

`get_node_type_instance_policy`, `update_node_type_instance_policy`, `get_node_type_project_policy`, `update_node_type_project_policy`, `list_node_type_policy_documents`, `create_node_type_policy_document`, `get_node_type_policy_document`, `update_node_type_policy_document`, `delete_node_type_policy_document`, `list_node_type_policy_attachments`, `replace_node_type_policy_attachments`

Key scopes: `nodeTypePolicy:manage`


### Wave 5

**Promotions (25)**

`create_promotion_provider`, `list_promotion_providers`, `get_promotion_provider`, `update_promotion_provider`, `delete_promotion_provider`, `list_promotion_provider_repositories`, `list_promotion_provider_repository_branches`, `create_promotion_environment`, `list_promotion_environments`, `get_promotion_environment`, `update_promotion_environment`, `delete_promotion_environment`, `connect_promotion_checkout`, `disconnect_promotion_checkout`, `list_promotion_environment_projects`, `list_promotion_environment_missing_projects`, `add_project_to_promotion_environment`, `remove_project_from_promotion_environment`, `promote_package`, `apply_package`, `continue_apply_package`, `get_promotion_changes`, `promote_project_selection`, `apply_project_selection`, `continue_apply_project_selection`

Key scopes: `promotions:apply`, `promotions:connect`, `promotions:create`, `promotions:delete`, `promotions:discover`, `promotions:list`, `promotions:manageProjects`, `promotions:promote`, `promotions:read`, `promotions:update`

**n8n packages (3)** _Beta: breaking changes may occur without a major version bump._

`import_package`, `import_package_selection`, `export_package`

Key scopes: `agent:export`, `agent:import`, `project:export`, `workflow:export`, `workflow:import`
