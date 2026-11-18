# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
"""Run every action against a mock HTTP transport, so no n8n instance is needed (CI has none)."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import httpx
import pytest

from node_wire_n8n import logic
from node_wire_n8n.logic import N8nConnector
from node_wire_runtime.auth import StaticTokenAuthProvider

BASE = "http://n8n.test"

ROWS: list[tuple[str, dict[str, Any], str, str, dict[str, str], Any]] = [
    ("list_workflows", {}, "GET", "/api/v1/workflows", {}, None),
    (
        "create_workflow",
        {"name": "v", "nodes": [{"k": "v"}], "connections": {"k": "v"}, "settings": {"k": "v"}},
        "POST",
        "/api/v1/workflows",
        {},
        {"name": "v", "nodes": [{"k": "v"}], "connections": {"k": "v"}, "settings": {"k": "v"}},
    ),
    (
        "get_workflow",
        {"workflow_id": "workflow_id1"},
        "GET",
        "/api/v1/workflows/workflow_id1",
        {},
        None,
    ),
    (
        "update_workflow",
        {
            "workflow_id": "workflow_id1",
            "name": "v",
            "nodes": [{"k": "v"}],
            "connections": {"k": "v"},
            "settings": {"k": "v"},
        },
        "PUT",
        "/api/v1/workflows/workflow_id1",
        {},
        {"name": "v", "nodes": [{"k": "v"}], "connections": {"k": "v"}, "settings": {"k": "v"}},
    ),
    (
        "delete_workflow",
        {"workflow_id": "workflow_id1"},
        "DELETE",
        "/api/v1/workflows/workflow_id1",
        {},
        None,
    ),
    (
        "archive_workflow",
        {"workflow_id": "workflow_id1"},
        "POST",
        "/api/v1/workflows/workflow_id1/archive",
        {},
        None,
    ),
    (
        "unarchive_workflow",
        {"workflow_id": "workflow_id1"},
        "POST",
        "/api/v1/workflows/workflow_id1/unarchive",
        {},
        None,
    ),
    (
        "transfer_workflow",
        {"workflow_id": "workflow_id1", "destination_project_id": "v"},
        "PUT",
        "/api/v1/workflows/workflow_id1/transfer",
        {},
        {"destinationProjectId": "v"},
    ),
    (
        "publish_workflow",
        {"workflow_id": "workflow_id1"},
        "POST",
        "/api/v1/workflows/workflow_id1/publish",
        {},
        None,
    ),
    (
        "unpublish_workflow",
        {"workflow_id": "workflow_id1"},
        "POST",
        "/api/v1/workflows/workflow_id1/unpublish",
        {},
        None,
    ),
    (
        "list_workflow_history",
        {"workflow_id": "workflow_id1"},
        "GET",
        "/api/v1/workflows/workflow_id1/history",
        {},
        None,
    ),
    (
        "get_workflow_version",
        {"workflow_id": "workflow_id1", "workflow_version_id": "workflow_version_id1"},
        "GET",
        "/api/v1/workflows/workflow_id1/versions/workflow_version_id1",
        {},
        None,
    ),
    (
        "list_workflow_tags",
        {"workflow_id": "workflow_id1"},
        "GET",
        "/api/v1/workflows/workflow_id1/tags",
        {},
        None,
    ),
    (
        "update_workflow_tags",
        {"workflow_id": "workflow_id1", "tags": [{"id": "t1"}]},
        "PUT",
        "/api/v1/workflows/workflow_id1/tags",
        {},
        [{"id": "t1"}],
    ),
    (
        "list_workflow_dependencies",
        {"workflow_id": "workflow_id1"},
        "GET",
        "/api/v1/workflows/workflow_id1/dependencies",
        {},
        None,
    ),
    ("list_executions", {}, "GET", "/api/v1/executions", {}, None),
    ("get_execution", {"execution_id": 7}, "GET", "/api/v1/executions/7", {}, None),
    ("delete_execution", {"execution_id": 7}, "DELETE", "/api/v1/executions/7", {}, None),
    ("list_execution_tags", {"execution_id": 7}, "GET", "/api/v1/executions/7/tags", {}, None),
    (
        "update_execution_tags",
        {"execution_id": 7, "tags": [{"id": "t1"}]},
        "PUT",
        "/api/v1/executions/7/tags",
        {},
        [{"id": "t1"}],
    ),
    (
        "stop_many_executions",
        {"status": ["queued"]},
        "POST",
        "/api/v1/executions/stop",
        {},
        {"status": ["queued"]},
    ),
    ("stop_execution", {"execution_id": 7}, "POST", "/api/v1/executions/7/stop", {}, None),
    ("retry_execution", {"execution_id": 7}, "POST", "/api/v1/executions/7/retry", {}, None),
    ("list_tags", {}, "GET", "/api/v1/tags", {}, None),
    ("create_tag", {"name": "v"}, "POST", "/api/v1/tags", {}, {"name": "v"}),
    ("get_tag", {"tag_id": "tag_id1"}, "GET", "/api/v1/tags/tag_id1", {}, None),
    (
        "update_tag",
        {"tag_id": "tag_id1", "name": "v"},
        "PUT",
        "/api/v1/tags/tag_id1",
        {},
        {"name": "v"},
    ),
    ("delete_tag", {"tag_id": "tag_id1"}, "DELETE", "/api/v1/tags/tag_id1", {}, None),
    ("list_variables", {}, "GET", "/api/v1/variables", {}, None),
    (
        "create_variable",
        {"key": "v", "value": "v"},
        "POST",
        "/api/v1/variables",
        {},
        {"key": "v", "value": "v"},
    ),
    (
        "update_variable",
        {"id": "id1", "key": "v", "value": "v"},
        "PUT",
        "/api/v1/variables/id1",
        {},
        {"key": "v", "value": "v"},
    ),
    ("delete_variable", {"id": "id1"}, "DELETE", "/api/v1/variables/id1", {}, None),
    ("list_credentials", {}, "GET", "/api/v1/credentials", {}, None),
    (
        "create_credential",
        {"name": "v", "type": "v", "data": {"k": "v"}},
        "POST",
        "/api/v1/credentials",
        {},
        {"name": "v", "type": "v", "data": {"k": "v"}},
    ),
    (
        "get_credential",
        {"credential_id": "credential_id1"},
        "GET",
        "/api/v1/credentials/credential_id1",
        {},
        None,
    ),
    (
        "update_credential",
        {"credential_id": "credential_id1"},
        "PATCH",
        "/api/v1/credentials/credential_id1",
        {},
        {},
    ),
    (
        "delete_credential",
        {"credential_id": "credential_id1"},
        "DELETE",
        "/api/v1/credentials/credential_id1",
        {},
        None,
    ),
    (
        "transfer_credential",
        {"credential_id": "credential_id1", "destination_project_id": "v"},
        "PUT",
        "/api/v1/credentials/credential_id1/transfer",
        {},
        {"destinationProjectId": "v"},
    ),
    (
        "test_credential",
        {"credential_id": "credential_id1"},
        "POST",
        "/api/v1/credentials/credential_id1/test",
        {},
        None,
    ),
    (
        "get_credential_type",
        {"credential_type_name": "credential_type_name1"},
        "GET",
        "/api/v1/credentials/schema/credential_type_name1",
        {},
        None,
    ),
    (
        "list_credential_dependencies",
        {"credential_id": "credential_id1"},
        "GET",
        "/api/v1/credentials/credential_id1/dependencies",
        {},
        None,
    ),
    (
        "list_folders",
        {"project_id": "project_id1"},
        "GET",
        "/api/v1/projects/project_id1/folders",
        {},
        None,
    ),
    (
        "create_folder",
        {"project_id": "project_id1", "name": "v"},
        "POST",
        "/api/v1/projects/project_id1/folders",
        {},
        {"name": "v"},
    ),
    (
        "get_folder",
        {"project_id": "project_id1", "folder_id": "folder_id1"},
        "GET",
        "/api/v1/projects/project_id1/folders/folder_id1",
        {},
        None,
    ),
    (
        "update_folder",
        {"project_id": "project_id1", "folder_id": "folder_id1"},
        "PATCH",
        "/api/v1/projects/project_id1/folders/folder_id1",
        {},
        {},
    ),
    (
        "delete_folder",
        {"project_id": "project_id1", "folder_id": "folder_id1"},
        "DELETE",
        "/api/v1/projects/project_id1/folders/folder_id1",
        {},
        None,
    ),
    ("list_data_tables", {}, "GET", "/api/v1/data-tables", {}, None),
    (
        "create_data_table",
        {"name": "v", "columns": [{"k": "v"}]},
        "POST",
        "/api/v1/data-tables",
        {},
        {"name": "v", "columns": [{"k": "v"}]},
    ),
    (
        "get_data_table",
        {"data_table_id": "data_table_id1"},
        "GET",
        "/api/v1/data-tables/data_table_id1",
        {},
        None,
    ),
    (
        "update_data_table",
        {"data_table_id": "data_table_id1", "name": "v"},
        "PATCH",
        "/api/v1/data-tables/data_table_id1",
        {},
        {"name": "v"},
    ),
    (
        "delete_data_table",
        {"data_table_id": "data_table_id1"},
        "DELETE",
        "/api/v1/data-tables/data_table_id1",
        {},
        None,
    ),
    (
        "list_data_table_dependencies",
        {"data_table_id": "data_table_id1"},
        "GET",
        "/api/v1/data-tables/data_table_id1/dependencies",
        {},
        None,
    ),
    (
        "list_data_table_columns",
        {"data_table_id": "data_table_id1"},
        "GET",
        "/api/v1/data-tables/data_table_id1/columns",
        {},
        None,
    ),
    (
        "create_data_table_column",
        {"data_table_id": "data_table_id1", "name": "v", "type": "string"},
        "POST",
        "/api/v1/data-tables/data_table_id1/columns",
        {},
        {"name": "v", "type": "string"},
    ),
    (
        "update_data_table_column",
        {"data_table_id": "data_table_id1", "column_id": "column_id1"},
        "PATCH",
        "/api/v1/data-tables/data_table_id1/columns/column_id1",
        {},
        {},
    ),
    (
        "delete_data_table_column",
        {"data_table_id": "data_table_id1", "column_id": "column_id1"},
        "DELETE",
        "/api/v1/data-tables/data_table_id1/columns/column_id1",
        {},
        None,
    ),
    (
        "list_data_table_rows",
        {"data_table_id": "data_table_id1"},
        "GET",
        "/api/v1/data-tables/data_table_id1/rows",
        {},
        None,
    ),
    (
        "create_data_table_rows",
        {"data_table_id": "data_table_id1", "data": [{"k": "v"}]},
        "POST",
        "/api/v1/data-tables/data_table_id1/rows",
        {},
        {"data": [{"k": "v"}]},
    ),
    (
        "upsert_data_table_row",
        {"data_table_id": "data_table_id1", "filter": {"k": "v"}, "data": {"k": "v"}},
        "POST",
        "/api/v1/data-tables/data_table_id1/rows/upsert",
        {},
        {"filter": {"k": "v"}, "data": {"k": "v"}},
    ),
    (
        "update_data_table_rows",
        {"data_table_id": "data_table_id1", "filter": {"k": "v"}, "data": {"k": "v"}},
        "PATCH",
        "/api/v1/data-tables/data_table_id1/rows/update",
        {},
        {"filter": {"k": "v"}, "data": {"k": "v"}},
    ),
    (
        "clear_data_table_rows",
        {"data_table_id": "data_table_id1"},
        "DELETE",
        "/api/v1/data-tables/data_table_id1/rows/clear",
        {},
        None,
    ),
    (
        "delete_data_table_rows",
        {"data_table_id": "data_table_id1", "filter": "v"},
        "DELETE",
        "/api/v1/data-tables/data_table_id1/rows/delete",
        {"filter": "v"},
        None,
    ),
    (
        "list_test_runs",
        {"workflow_id": "workflow_id1"},
        "GET",
        "/api/v1/workflows/workflow_id1/test-runs",
        {},
        None,
    ),
    (
        "create_test_run",
        {"workflow_id": "workflow_id1"},
        "POST",
        "/api/v1/workflows/workflow_id1/test-runs",
        {},
        None,
    ),
    (
        "get_test_run",
        {"workflow_id": "workflow_id1", "run_id": "run_id1"},
        "GET",
        "/api/v1/workflows/workflow_id1/test-runs/run_id1",
        {},
        None,
    ),
    (
        "list_test_cases",
        {"workflow_id": "workflow_id1", "run_id": "run_id1"},
        "GET",
        "/api/v1/workflows/workflow_id1/test-runs/run_id1/test-cases",
        {},
        None,
    ),
    (
        "cancel_test_run",
        {"workflow_id": "workflow_id1", "run_id": "run_id1"},
        "POST",
        "/api/v1/workflows/workflow_id1/test-runs/run_id1/cancel",
        {},
        None,
    ),
    ("list_projects", {}, "GET", "/api/v1/projects", {}, None),
    ("create_project", {"name": "v"}, "POST", "/api/v1/projects", {}, {"name": "v"}),
    (
        "update_project",
        {"project_id": "project_id1", "name": "v"},
        "PUT",
        "/api/v1/projects/project_id1",
        {},
        {"name": "v"},
    ),
    (
        "delete_project",
        {"project_id": "project_id1"},
        "DELETE",
        "/api/v1/projects/project_id1",
        {},
        None,
    ),
    (
        "list_project_users",
        {"project_id": "project_id1"},
        "GET",
        "/api/v1/projects/project_id1/users",
        {},
        None,
    ),
    (
        "add_users_to_project",
        {"project_id": "project_id1", "relations": [{"k": "v"}]},
        "POST",
        "/api/v1/projects/project_id1/users",
        {},
        {"relations": [{"k": "v"}]},
    ),
    (
        "change_user_role_in_project",
        {"project_id": "project_id1", "user_id": "user_id1", "role": "v"},
        "PATCH",
        "/api/v1/projects/project_id1/users/user_id1",
        {},
        {"role": "v"},
    ),
    (
        "delete_user_from_project",
        {"project_id": "project_id1", "user_id": "user_id1"},
        "DELETE",
        "/api/v1/projects/project_id1/users/user_id1",
        {},
        None,
    ),
    ("list_roles", {}, "GET", "/api/v1/roles", {}, None),
    (
        "create_role",
        {"display_name": "v", "role_type": "project", "scopes": ["v"]},
        "POST",
        "/api/v1/roles",
        {},
        {"displayName": "v", "roleType": "project", "scopes": ["v"]},
    ),
    ("get_role", {"role_slug": "role_slug1"}, "GET", "/api/v1/roles/role_slug1", {}, None),
    (
        "update_role",
        {"role_slug": "role_slug1", "display_name": "v", "description": "v", "scopes": ["v"]},
        "PUT",
        "/api/v1/roles/role_slug1",
        {},
        {"displayName": "v", "description": "v", "scopes": ["v"]},
    ),
    ("delete_role", {"role_slug": "role_slug1"}, "DELETE", "/api/v1/roles/role_slug1", {}, None),
    ("list_role_mapping_rules", {}, "GET", "/api/v1/role-mapping-rules", {}, None),
    (
        "create_role_mapping_rule",
        {"expression": "v", "role": "v", "type": "instance"},
        "POST",
        "/api/v1/role-mapping-rules",
        {},
        {"expression": "v", "role": "v", "type": "instance"},
    ),
    (
        "move_role_mapping_rule",
        {"role_mapping_rule_id": "role_mapping_rule_id1", "target_index": 1},
        "POST",
        "/api/v1/role-mapping-rules/role_mapping_rule_id1/move",
        {},
        {"targetIndex": 1},
    ),
    (
        "update_role_mapping_rule",
        {"role_mapping_rule_id": "role_mapping_rule_id1"},
        "PATCH",
        "/api/v1/role-mapping-rules/role_mapping_rule_id1",
        {},
        None,
    ),
    (
        "delete_role_mapping_rule",
        {"role_mapping_rule_id": "role_mapping_rule_id1"},
        "DELETE",
        "/api/v1/role-mapping-rules/role_mapping_rule_id1",
        {},
        None,
    ),
    ("list_users", {}, "GET", "/api/v1/users", {}, None),
    ("create_user", {"users": [{"k": "v"}]}, "POST", "/api/v1/users", {}, [{"k": "v"}]),
    ("get_user", {"user_id": "user_id1"}, "GET", "/api/v1/users/user_id1", {}, None),
    ("delete_user", {"user_id": "user_id1"}, "DELETE", "/api/v1/users/user_id1", {}, None),
    (
        "change_role",
        {"user_id": "user_id1", "new_role_name": "x"},
        "PATCH",
        "/api/v1/users/user_id1/role",
        {},
        {"newRoleName": "x"},
    ),
    ("generate_audit", {}, "POST", "/api/v1/audit", {}, None),
    ("get_discover", {}, "GET", "/api/v1/discover", {}, None),
    ("get_insights_summary", {}, "GET", "/api/v1/insights/summary", {}, None),
    ("list_community_packages", {}, "GET", "/api/v1/community-packages", {}, None),
    (
        "install_community_package",
        {"name": "v"},
        "POST",
        "/api/v1/community-packages",
        {},
        {"name": "v"},
    ),
    (
        "update_community_package",
        {"name": "name1"},
        "PATCH",
        "/api/v1/community-packages/name1",
        {},
        None,
    ),
    (
        "uninstall_community_package",
        {"name": "name1"},
        "DELETE",
        "/api/v1/community-packages/name1",
        {},
        None,
    ),
    (
        "get_source_control_status",
        {"direction": "push"},
        "GET",
        "/api/v1/source-control/status",
        {"direction": "push"},
        None,
    ),
    (
        "push_source_control",
        {"commit_message": "v", "file_names": [{"k": "v"}]},
        "POST",
        "/api/v1/source-control/push",
        {},
        {"commitMessage": "v", "fileNames": [{"k": "v"}]},
    ),
    ("pull_source_control", {}, "POST", "/api/v1/source-control/pull", {}, {}),
    ("get_ldap_configuration", {}, "GET", "/api/v1/settings/ldap", {}, None),
    (
        "update_ldap_configuration",
        {
            "login_enabled": True,
            "login_label": "v",
            "connection_url": "v",
            "allow_unauthorized_certs": True,
            "connection_security": "none",
            "connection_port": 1,
            "base_dn": "v",
            "binding_admin_dn": "v",
            "binding_admin_password": "v",
            "first_name_attribute": "v",
            "last_name_attribute": "v",
            "email_attribute": "v",
            "login_id_attribute": "v",
            "ldap_id_attribute": "v",
            "user_filter": "v",
            "synchronization_enabled": True,
            "synchronization_interval": 1,
            "search_page_size": 1,
            "search_timeout": 1,
            "enforce_email_uniqueness": True,
        },
        "PUT",
        "/api/v1/settings/ldap",
        {},
        {
            "loginEnabled": True,
            "loginLabel": "v",
            "connectionUrl": "v",
            "allowUnauthorizedCerts": True,
            "connectionSecurity": "none",
            "connectionPort": 1,
            "baseDn": "v",
            "bindingAdminDn": "v",
            "bindingAdminPassword": "v",
            "firstNameAttribute": "v",
            "lastNameAttribute": "v",
            "emailAttribute": "v",
            "loginIdAttribute": "v",
            "ldapIdAttribute": "v",
            "userFilter": "v",
            "synchronizationEnabled": True,
            "synchronizationInterval": 1,
            "searchPageSize": 1,
            "searchTimeout": 1,
            "enforceEmailUniqueness": True,
        },
    ),
    ("get_ldap_sync", {}, "GET", "/api/v1/settings/ldap/sync", {}, None),
    ("run_ldap_sync", {"type": "live"}, "POST", "/api/v1/settings/ldap/sync", {}, {"type": "live"}),
    ("get_saml_configuration", {}, "GET", "/api/v1/settings/sso/saml", {}, None),
    (
        "update_saml_configuration",
        {
            "mapping": {"k": "v"},
            "metadata": "v",
            "metadata_url": "v",
            "ignore_ssl": True,
            "login_binding": "redirect",
            "login_enabled": True,
            "login_label": "v",
            "authn_requests_signed": True,
            "want_assertions_signed": True,
            "want_message_signed": True,
            "email_verified_required": True,
            "signing_private_key": "v",
            "signing_certificate": "v",
            "acs_binding": "redirect",
            "signature_config": {"k": "v"},
            "relay_state": "v",
        },
        "PUT",
        "/api/v1/settings/sso/saml",
        {},
        {
            "mapping": {"k": "v"},
            "metadata": "v",
            "metadataUrl": "v",
            "ignoreSSL": True,
            "loginBinding": "redirect",
            "loginEnabled": True,
            "loginLabel": "v",
            "authnRequestsSigned": True,
            "wantAssertionsSigned": True,
            "wantMessageSigned": True,
            "emailVerifiedRequired": True,
            "signingPrivateKey": "v",
            "signingCertificate": "v",
            "acsBinding": "redirect",
            "signatureConfig": {"k": "v"},
            "relayState": "v",
        },
    ),
    ("get_oidc_configuration", {}, "GET", "/api/v1/settings/sso/oidc", {}, None),
    (
        "set_oidc_configuration",
        {
            "client_id": "v",
            "client_secret": "v",
            "discovery_endpoint": "v",
            "login_enabled": True,
            "prompt": "none",
            "authentication_context_class_reference": ["v"],
            "additional_scopes": "v",
            "email_verified_required": True,
            "rp_initiated_logout_enabled": True,
        },
        "PUT",
        "/api/v1/settings/sso/oidc",
        {},
        {
            "clientId": "v",
            "clientSecret": "v",
            "discoveryEndpoint": "v",
            "loginEnabled": True,
            "prompt": "none",
            "authenticationContextClassReference": ["v"],
            "additionalScopes": "v",
            "emailVerifiedRequired": True,
            "rpInitiatedLogoutEnabled": True,
        },
    ),
    ("get_otel_settings", {}, "GET", "/api/v1/settings/otel", {}, None),
    (
        "update_otel_settings",
        {
            "enabled": True,
            "exporter_endpoint": "v",
            "exporter_tracing_path": "v",
            "exporter_service_name": "v",
            "exporter_headers": "v",
            "traces_sample_rate": 1,
            "startup_connectivity_timeout_ms": 1,
            "include_node_spans": True,
            "inject_outbound": True,
            "production_executions_only": True,
        },
        "PUT",
        "/api/v1/settings/otel",
        {},
        {
            "enabled": True,
            "exporterEndpoint": "v",
            "exporterTracingPath": "v",
            "exporterServiceName": "v",
            "exporterHeaders": "v",
            "tracesSampleRate": 1,
            "startupConnectivityTimeoutMs": 1,
            "includeNodeSpans": True,
            "injectOutbound": True,
            "productionExecutionsOnly": True,
        },
    ),
    (
        "test_otel_trace",
        {
            "exporter_endpoint": "v",
            "exporter_tracing_path": "v",
            "exporter_service_name": "v",
            "exporter_headers": "v",
            "startup_connectivity_timeout_ms": 1,
        },
        "POST",
        "/api/v1/settings/otel/test-trace",
        {},
        {
            "exporterEndpoint": "v",
            "exporterTracingPath": "v",
            "exporterServiceName": "v",
            "exporterHeaders": "v",
            "startupConnectivityTimeoutMs": 1,
        },
    ),
    ("get_rate_limit_settings", {}, "GET", "/api/v1/settings/rate-limits", {}, None),
    (
        "update_rate_limit_settings",
        {
            "enabled": True,
            "global_read_limit": 1,
            "global_read_window_seconds": 1,
            "global_write_limit": 1,
            "global_write_window_seconds": 1,
            "endpoint_read_limit": 1,
            "endpoint_read_window_seconds": 1,
            "endpoint_write_limit": 1,
            "endpoint_write_window_seconds": 1,
        },
        "PUT",
        "/api/v1/settings/rate-limits",
        {},
        {
            "enabled": True,
            "globalReadLimit": 1,
            "globalReadWindowSeconds": 1,
            "globalWriteLimit": 1,
            "globalWriteWindowSeconds": 1,
            "endpointReadLimit": 1,
            "endpointReadWindowSeconds": 1,
            "endpointWriteLimit": 1,
            "endpointWriteWindowSeconds": 1,
        },
    ),
    ("get_security_policy", {}, "GET", "/api/v1/settings/security-policy", {}, None),
    (
        "update_security_policy",
        {
            "personal_space_publishing": True,
            "personal_space_sharing": True,
            "redaction_enforcement": {"k": "v"},
        },
        "PUT",
        "/api/v1/settings/security-policy",
        {},
        {
            "personalSpacePublishing": True,
            "personalSpaceSharing": True,
            "redactionEnforcement": {"k": "v"},
        },
    ),
    (
        "list_log_streaming_event_types",
        {},
        "GET",
        "/api/v1/settings/log-streaming/event-types",
        {},
        None,
    ),
    (
        "list_log_streaming_destinations",
        {},
        "GET",
        "/api/v1/settings/log-streaming/destinations",
        {},
        None,
    ),
    (
        "create_log_streaming_destination",
        {"destination": {"k": "v"}},
        "POST",
        "/api/v1/settings/log-streaming/destinations",
        {},
        {"k": "v"},
    ),
    (
        "get_log_streaming_destination",
        {"destination_id": "destination_id1"},
        "GET",
        "/api/v1/settings/log-streaming/destinations/destination_id1",
        {},
        None,
    ),
    (
        "update_log_streaming_destination",
        {"destination_id": "destination_id1", "destination": {"k": "v"}},
        "PUT",
        "/api/v1/settings/log-streaming/destinations/destination_id1",
        {},
        {"k": "v"},
    ),
    (
        "delete_log_streaming_destination",
        {"destination_id": "destination_id1"},
        "DELETE",
        "/api/v1/settings/log-streaming/destinations/destination_id1",
        {},
        None,
    ),
    (
        "test_log_streaming_destination",
        {"destination_id": "destination_id1"},
        "POST",
        "/api/v1/settings/log-streaming/destinations/destination_id1/test",
        {},
        None,
    ),
    (
        "get_credential_type_instance_policy",
        {},
        "GET",
        "/api/v1/credential-type-policies/instance",
        {},
        None,
    ),
    (
        "update_credential_type_instance_policy",
        {"rules": [{"k": "v"}], "default_action": "allow", "version": 1},
        "PUT",
        "/api/v1/credential-type-policies/instance",
        {},
        {"rules": [{"k": "v"}], "defaultAction": "allow", "version": 1},
    ),
    (
        "get_credential_type_project_policy",
        {"project_id": "project_id1"},
        "GET",
        "/api/v1/credential-type-policies/projects/project_id1",
        {},
        None,
    ),
    (
        "update_credential_type_project_policy",
        {
            "project_id": "project_id1",
            "rules": [{"k": "v"}],
            "default_action": "allow",
            "version": 1,
        },
        "PUT",
        "/api/v1/credential-type-policies/projects/project_id1",
        {},
        {"rules": [{"k": "v"}], "defaultAction": "allow", "version": 1},
    ),
    (
        "list_credential_type_policy_documents",
        {},
        "GET",
        "/api/v1/credential-type-policies/policies",
        {},
        None,
    ),
    (
        "create_credential_type_policy_document",
        {"rules": [{"k": "v"}]},
        "POST",
        "/api/v1/credential-type-policies/policies",
        {},
        {"rules": [{"k": "v"}]},
    ),
    (
        "get_credential_type_policy_document",
        {"policy_id": "policy_id1"},
        "GET",
        "/api/v1/credential-type-policies/policies/policy_id1",
        {},
        None,
    ),
    (
        "update_credential_type_policy_document",
        {"policy_id": "policy_id1", "rules": [{"k": "v"}], "version": 1},
        "PUT",
        "/api/v1/credential-type-policies/policies/policy_id1",
        {},
        {"rules": [{"k": "v"}], "version": 1},
    ),
    (
        "delete_credential_type_policy_document",
        {"policy_id": "policy_id1"},
        "DELETE",
        "/api/v1/credential-type-policies/policies/policy_id1",
        {},
        None,
    ),
    (
        "list_credential_type_policy_attachments",
        {"scope_id": "scope_id1"},
        "GET",
        "/api/v1/credential-type-policies/scopes/scope_id1/attachments",
        {},
        None,
    ),
    (
        "replace_credential_type_policy_attachments",
        {"scope_id": "scope_id1", "attachments": [{"k": "v"}], "version": 1},
        "PUT",
        "/api/v1/credential-type-policies/scopes/scope_id1/attachments",
        {},
        {"attachments": [{"k": "v"}], "version": 1},
    ),
    ("get_node_type_instance_policy", {}, "GET", "/api/v1/node-type-policies/instance", {}, None),
    (
        "update_node_type_instance_policy",
        {"rules": [{"k": "v"}], "default_action": "allow", "version": 1},
        "PUT",
        "/api/v1/node-type-policies/instance",
        {},
        {"rules": [{"k": "v"}], "defaultAction": "allow", "version": 1},
    ),
    (
        "get_node_type_project_policy",
        {"project_id": "project_id1"},
        "GET",
        "/api/v1/node-type-policies/projects/project_id1",
        {},
        None,
    ),
    (
        "update_node_type_project_policy",
        {
            "project_id": "project_id1",
            "rules": [{"k": "v"}],
            "default_action": "allow",
            "version": 1,
        },
        "PUT",
        "/api/v1/node-type-policies/projects/project_id1",
        {},
        {"rules": [{"k": "v"}], "defaultAction": "allow", "version": 1},
    ),
    ("list_node_type_policy_documents", {}, "GET", "/api/v1/node-type-policies/policies", {}, None),
    (
        "create_node_type_policy_document",
        {"rules": [{"k": "v"}]},
        "POST",
        "/api/v1/node-type-policies/policies",
        {},
        {"rules": [{"k": "v"}]},
    ),
    (
        "get_node_type_policy_document",
        {"policy_id": "policy_id1"},
        "GET",
        "/api/v1/node-type-policies/policies/policy_id1",
        {},
        None,
    ),
    (
        "update_node_type_policy_document",
        {"policy_id": "policy_id1", "rules": [{"k": "v"}], "version": 1},
        "PUT",
        "/api/v1/node-type-policies/policies/policy_id1",
        {},
        {"rules": [{"k": "v"}], "version": 1},
    ),
    (
        "delete_node_type_policy_document",
        {"policy_id": "policy_id1"},
        "DELETE",
        "/api/v1/node-type-policies/policies/policy_id1",
        {},
        None,
    ),
    (
        "list_node_type_policy_attachments",
        {"scope_id": "scope_id1"},
        "GET",
        "/api/v1/node-type-policies/scopes/scope_id1/attachments",
        {},
        None,
    ),
    (
        "replace_node_type_policy_attachments",
        {"scope_id": "scope_id1", "attachments": [{"k": "v"}], "version": 1},
        "PUT",
        "/api/v1/node-type-policies/scopes/scope_id1/attachments",
        {},
        {"attachments": [{"k": "v"}], "version": 1},
    ),
    (
        "create_promotion_provider",
        {"name": "v", "type": "git", "auth": "x"},
        "POST",
        "/api/v1/promotions/providers",
        {},
        {"name": "v", "type": "git", "auth": "x"},
    ),
    ("list_promotion_providers", {}, "GET", "/api/v1/promotions/providers", {}, None),
    (
        "get_promotion_provider",
        {"promotion_provider_id": "promotion_provider_id1"},
        "GET",
        "/api/v1/promotions/providers/promotion_provider_id1",
        {},
        None,
    ),
    (
        "update_promotion_provider",
        {"promotion_provider_id": "promotion_provider_id1"},
        "PUT",
        "/api/v1/promotions/providers/promotion_provider_id1",
        {},
        {},
    ),
    (
        "delete_promotion_provider",
        {"promotion_provider_id": "promotion_provider_id1"},
        "DELETE",
        "/api/v1/promotions/providers/promotion_provider_id1",
        {},
        None,
    ),
    (
        "list_promotion_provider_repositories",
        {"promotion_provider_id": "promotion_provider_id1"},
        "GET",
        "/api/v1/promotions/providers/promotion_provider_id1/repositories",
        {},
        None,
    ),
    (
        "list_promotion_provider_repository_branches",
        {"promotion_provider_id": "promotion_provider_id1", "repository_id": "repository_id1"},
        "GET",
        "/api/v1/promotions/providers/promotion_provider_id1/repositories/repository_id1/branches",
        {},
        None,
    ),
    (
        "create_promotion_environment",
        {"environment": {"k": "v"}},
        "POST",
        "/api/v1/promotions/environments",
        {},
        {"k": "v"},
    ),
    ("list_promotion_environments", {}, "GET", "/api/v1/promotions/environments", {}, None),
    (
        "get_promotion_environment",
        {"promotion_environment_id": "promotion_environment_id1"},
        "GET",
        "/api/v1/promotions/environments/promotion_environment_id1",
        {},
        None,
    ),
    (
        "update_promotion_environment",
        {"promotion_environment_id": "promotion_environment_id1"},
        "PUT",
        "/api/v1/promotions/environments/promotion_environment_id1",
        {},
        {},
    ),
    (
        "delete_promotion_environment",
        {"promotion_environment_id": "promotion_environment_id1"},
        "DELETE",
        "/api/v1/promotions/environments/promotion_environment_id1",
        {},
        None,
    ),
    (
        "connect_promotion_checkout",
        {"promotion_environment_id": "promotion_environment_id1"},
        "POST",
        "/api/v1/promotions/environments/promotion_environment_id1/connect",
        {},
        None,
    ),
    (
        "disconnect_promotion_checkout",
        {"promotion_environment_id": "promotion_environment_id1"},
        "POST",
        "/api/v1/promotions/environments/promotion_environment_id1/disconnect",
        {},
        None,
    ),
    (
        "list_promotion_environment_projects",
        {"promotion_environment_id": "promotion_environment_id1"},
        "GET",
        "/api/v1/promotions/environments/promotion_environment_id1/projects",
        {},
        None,
    ),
    (
        "list_promotion_environment_missing_projects",
        {"promotion_environment_id": "promotion_environment_id1"},
        "GET",
        "/api/v1/promotions/environments/promotion_environment_id1/missing-projects",
        {},
        None,
    ),
    (
        "add_project_to_promotion_environment",
        {"promotion_environment_id": "promotion_environment_id1", "project_id": "project_id1"},
        "POST",
        "/api/v1/promotions/environments/promotion_environment_id1/projects/project_id1",
        {},
        None,
    ),
    (
        "remove_project_from_promotion_environment",
        {"promotion_environment_id": "promotion_environment_id1", "project_id": "project_id1"},
        "DELETE",
        "/api/v1/promotions/environments/promotion_environment_id1/projects/project_id1",
        {},
        None,
    ),
    (
        "promote_package",
        {"promotion_environment_id": "promotion_environment_id1"},
        "POST",
        "/api/v1/promotions/environments/promotion_environment_id1/promote",
        {},
        None,
    ),
    (
        "apply_package",
        {"promotion_environment_id": "promotion_environment_id1"},
        "POST",
        "/api/v1/promotions/environments/promotion_environment_id1/apply",
        {},
        None,
    ),
    (
        "continue_apply_package",
        {"promotion_environment_id": "promotion_environment_id1", "expected_source": {"k": "v"}},
        "POST",
        "/api/v1/promotions/environments/promotion_environment_id1/apply/continue",
        {},
        {"expectedSource": {"k": "v"}},
    ),
    (
        "get_promotion_changes",
        {"project_id": "project_id1", "direction": "apply"},
        "GET",
        "/api/v1/promotions/projects/project_id1/changes/apply",
        {},
        None,
    ),
    (
        "promote_project_selection",
        {"project_id": "project_id1", "workflow_ids": ["v"]},
        "POST",
        "/api/v1/promotions/projects/project_id1/promote",
        {},
        {"workflowIds": ["v"]},
    ),
    (
        "apply_project_selection",
        {"project_id": "project_id1", "workflow_ids": ["v"]},
        "POST",
        "/api/v1/promotions/projects/project_id1/apply",
        {},
        {"workflowIds": ["v"]},
    ),
    (
        "continue_apply_project_selection",
        {"project_id": "project_id1", "workflow_ids": ["v"], "expected_source": {"k": "v"}},
        "POST",
        "/api/v1/promotions/projects/project_id1/apply/continue",
        {},
        {"workflowIds": ["v"], "expectedSource": {"k": "v"}},
    ),
    ("export_package", {}, "POST", "/api/v1/n8n-packages/export", {}, {}),
]


class Secrets:
    def get_secret(self, key: str) -> str:
        return {"N8N_API_KEY": "key-123"}[key]


class Wire:
    """What the mock n8n saw, and what it answers next."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.json: Any = {"ok": True}
        self.content: bytes | None = None
        self.headers: dict[str, str] = {}
        self.error: Exception | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error:
            raise self.error
        if self.content is not None:
            return httpx.Response(self.status, content=self.content, headers=self.headers)
        return httpx.Response(self.status, json=self.json)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    @property
    def body(self) -> Any:
        return json.loads(self.last.content) if self.last.content else None


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    w = Wire()
    client = httpx.AsyncClient(transport=httpx.MockTransport(w.handler))
    monkeypatch.setattr(logic, "_shared_client", lambda trust_env: client)
    return w


def make(**config: Any) -> N8nConnector:
    secrets = Secrets()
    return N8nConnector(
        secret_provider=secrets,
        config={"instance_url": BASE, **config},
        auth_provider=StaticTokenAuthProvider(
            secret_provider=secrets,
            secret_key="N8N_API_KEY",
            header_name="X-N8N-API-KEY",
            prefix="",
        ),
    )


UPLOADS = {"import_package", "import_package_selection"}
ROW = {r[0]: r for r in ROWS}


def test_rows_cover_every_api_action() -> None:
    assert {r[0] for r in ROWS} | UPLOADS | {"trigger_workflow"} == set(
        N8nConnector.sdk_action_metas()
    )


@pytest.mark.parametrize(("action", "inp", "method", "path", "query", "body"), ROWS)
async def test_action_sends_the_documented_request(
    wire: Wire, action: str, inp: dict, method: str, path: str, query: dict, body: Any
) -> None:
    result = await make().run({"action": action, **inp})
    assert result.success, result.message
    request = wire.last
    assert (request.method, request.url.path) == (method, path)
    assert dict(request.url.params) == query
    assert wire.body == body
    assert request.headers["X-N8N-API-KEY"] == "key-123"
    assert str(request.url).startswith(BASE + "/api/v1/")


# ---- where optional fields go -------------------------------------------------------------


async def test_list_filters_go_to_the_query_as_the_api_spells_them(wire: Wire) -> None:
    await make().run(
        {
            "action": "list_workflows",
            "active": True,
            "limit": 5,
            "cursor": "abc",
            "project_id": "p1",
            "exclude_pinned_data": False,
        }
    )
    assert dict(wire.last.url.params) == {
        "active": "true",
        "limit": "5",
        "cursor": "abc",
        "projectId": "p1",
        "excludePinnedData": "false",
    }


async def test_unset_fields_are_not_sent(wire: Wire) -> None:
    await make().run({"action": "list_executions"})
    assert wire.last.url.query == b""


async def test_execution_filters(wire: Wire) -> None:
    await make().run(
        {
            "action": "list_executions",
            "workflow_id": "w1",
            "status": "waiting",
            "include_data": True,
            "started_after": "2026-01-01T00:00:00Z",
        }
    )
    assert dict(wire.last.url.params) == {
        "workflowId": "w1",
        "status": "waiting",
        "includeData": "true",
        "startedAfter": "2026-01-01T00:00:00Z",
    }
    await make().run({"action": "get_execution", "execution_id": 9, "include_data": True})
    assert (wire.last.url.path, dict(wire.last.url.params)) == (
        "/api/v1/executions/9",
        {"includeData": "true"},
    )


async def test_a_put_can_have_one_query_field_and_a_body(wire: Wire) -> None:
    await make().run(
        {
            "action": "update_workflow",
            "workflow_id": "w1",
            "name": "n",
            "nodes": [],
            "connections": {},
            "settings": {},
            "publish_if_active": True,
        }
    )
    assert dict(wire.last.url.params) == {"publishIfActive": "true"}
    assert "publishIfActive" not in wire.body
    assert wire.body["name"] == "n"


async def test_whole_body_list_for_tags(wire: Wire) -> None:
    await make().run(
        {"action": "update_execution_tags", "execution_id": 3, "tags": [{"id": "a"}, {"id": "b"}]}
    )
    assert wire.body == [{"id": "a"}, {"id": "b"}]


async def test_a_null_can_be_sent_on_purpose(wire: Wire) -> None:
    await make().run(
        {"action": "update_folder", "project_id": "p", "folder_id": "f", "parent_folder_id": None}
    )
    assert wire.body == {"parentFolderId": None}


async def test_path_values_are_quoted(wire: Wire) -> None:
    await make().run({"action": "get_workflow", "workflow_id": "a/b c"})
    assert wire.last.url.raw_path.split(b"?")[0] == b"/api/v1/workflows/a%2Fb%20c"


# ---- idempotency --------------------------------------------------------------------------


async def test_idempotency_key_is_sent_as_a_header_on_posts(wire: Wire) -> None:
    await make().run({"action": "create_tag", "name": "t", "idempotency_key": "k-1"})
    assert wire.last.headers["Idempotency-Key"] == "k-1"
    assert wire.body == {"name": "t"}


async def test_idempotency_key_must_be_visible_ascii(wire: Wire) -> None:
    result = await make().run({"action": "create_tag", "name": "t", "idempotency_key": "bad key"})
    assert result.error_code == "VALIDATION_ERROR"
    assert not wire.requests


# ---- config and input ---------------------------------------------------------------------


@pytest.mark.parametrize("field", ["base_url", "instance_url"])
async def test_the_instance_url_is_config_only_and_never_an_input(wire: Wire, field: str) -> None:
    result = await make().run({"action": "list_tags", field: "http://evil.example"})
    assert result.error_code == "VALIDATION_ERROR"
    assert not wire.requests


async def test_instance_url_trailing_slash_is_trimmed(wire: Wire) -> None:
    await make(instance_url=BASE + "/").run({"action": "list_tags"})
    assert str(wire.last.url) == BASE + "/api/v1/tags"


async def test_missing_instance_url_is_an_error(wire: Wire) -> None:
    result = await make(instance_url="").run({"action": "list_tags"})
    assert result.error_code == "N8N_INVALID_REQUEST"
    assert not wire.requests


# ---- responses and errors -----------------------------------------------------------------


async def test_response_is_status_headers_and_body(wire: Wire) -> None:
    wire.json = {"data": [1]}
    result = await make().run({"action": "list_tags"})
    assert result.data["status_code"] == 200
    assert result.data["body"] == {"data": [1]}
    assert "content-type" in result.data["headers"]


async def test_empty_and_text_bodies(wire: Wire) -> None:
    wire.content = b""
    assert (await make().run({"action": "delete_tag", "tag_id": "t"})).data["body"] is None
    wire.content = b"plain"
    assert (await make().run({"action": "list_tags"})).data["body"] == "plain"


@pytest.mark.parametrize("status", [400, 404, 409, 500])
async def test_http_errors_are_business_errors(wire: Wire, status: int) -> None:
    wire.status = status
    result = await make().run({"action": "get_workflow", "workflow_id": "w"})
    assert (result.error_code, result.error_category) == ("N8N_HTTP_STATUS_ERROR", "BUSINESS")


@pytest.mark.parametrize(
    ("status", "text", "code", "category"),
    [
        (429, "slow down", "N8N_RATE_LIMITED", "RETRYABLE"),
        (401, "bad key", "N8N_AUTH_ERROR", "AUTH"),
        (403, "missing scope workflow:read", "N8N_AUTH_ERROR", "AUTH"),
        (403, "Your license does not allow for feat:x", "N8N_HTTP_STATUS_ERROR", "BUSINESS"),
    ],
)
async def test_status_decides_the_category(
    wire: Wire, status: int, text: str, code: str, category: str
) -> None:
    wire.status = status
    wire.json = {"message": text}
    result = await make().run({"action": "get_workflow", "workflow_id": "w"})
    assert (result.error_code, result.error_category) == (code, category)


@pytest.mark.parametrize("status", [502, 503, 504])
@pytest.mark.parametrize(
    ("payload", "retryable"),
    [
        ({"action": "get_workflow", "workflow_id": "w"}, True),
        ({"action": "delete_tag", "tag_id": "t"}, True),
        ({"action": "create_tag", "name": "x"}, False),
    ],
)
async def test_gateway_errors_retry_only_safe_methods(
    wire: Wire, status: int, payload: dict[str, Any], retryable: bool
) -> None:
    wire.status = status
    result = await make().run(payload)
    expected = (
        ("N8N_UNAVAILABLE", "RETRYABLE") if retryable else ("N8N_HTTP_STATUS_ERROR", "BUSINESS")
    )
    assert (result.error_code, result.error_category) == expected


async def test_the_error_carries_n8ns_own_explanation(wire: Wire) -> None:
    wire.status = 403
    wire.json = {"message": "Your license does not allow for feat:projectRole:admin."}
    result = await make().run({"action": "list_projects"})
    assert "403" in result.message
    assert "Your license does not allow for feat:projectRole:admin." in result.message


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (httpx.ReadTimeout("slow"), "N8N_TIMEOUT"),
        (httpx.ConnectError("refused"), "N8N_CONNECT_ERROR"),
    ],
)
async def test_transport_errors_are_retryable(wire: Wire, error: Exception, code: str) -> None:
    wire.error = error
    result = await make().run({"action": "list_tags"})
    assert (result.error_code, result.error_category) == (code, "RETRYABLE")


# ---- bodies that are not a plain object, and wire names that are not camelCase ---------------


async def test_a_whole_body_list_or_object_is_sent_as_the_body(wire: Wire) -> None:
    users = [{"email": "a@example.com", "role": "global:member"}]
    await make().run({"action": "create_user", "users": users})
    assert wire.body == users
    destination = {"type": "webhook", "url": "http://hook.example"}
    await make().run({"action": "create_log_streaming_destination", "destination": destination})
    assert wire.body == destination
    await make().run(
        {
            "action": "update_log_streaming_destination",
            "destination_id": "d1",
            "destination": destination,
        }
    )
    assert (wire.last.method, wire.last.url.path) == (
        "PUT",
        "/api/v1/settings/log-streaming/destinations/d1",
    )


async def test_a_wire_name_that_is_not_camel_case_is_kept(wire: Wire) -> None:
    await make().run({"action": "update_saml_configuration", **ROW["update_saml_configuration"][1]})
    assert wire.body["ignoreSSL"] is True
    assert "ignoreSsl" not in wire.body


async def test_a_community_package_name_with_a_slash_is_quoted(wire: Wire) -> None:
    await make().run({"action": "uninstall_community_package", "name": "@scope/pkg"})
    assert wire.last.url.raw_path == b"/api/v1/community-packages/%40scope%2Fpkg"


# ---- package import (multipart) and export (binary answer) ------------------------------------

ARCHIVE = b"\x1f\x8b\x08fake-archive"


async def test_import_package_is_a_multipart_upload(wire: Wire) -> None:
    result = await make().run(
        {
            "action": "import_package",
            "package": base64.b64encode(ARCHIVE).decode(),
            "conflict_policy": "fail",
            "project_id": "p1",
            "idempotency_key": "imp-1",
        }
    )
    assert result.success
    request = wire.last
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert request.headers["Idempotency-Key"] == "imp-1"
    body = request.content
    assert b'name="package"; filename="package.n8np"' in body
    assert ARCHIVE in body
    assert b'name="projectId"' in body and b"p1" in body
    assert b'name="conflictPolicy"' in body and b"fail" in body
    assert b"folderId" not in body  # fields you leave out are not sent


async def test_import_package_selection_sends_the_selection_as_form_fields(wire: Wire) -> None:
    await make().run(
        {
            "action": "import_package_selection",
            "package": base64.b64encode(ARCHIVE).decode(),
            "selected_project_id": "src-p",
            "selected_workflow_ids": '["w1","w2"]',
        }
    )
    assert (wire.last.method, wire.last.url.path) == (
        "POST",
        "/api/v1/n8n-packages/import-selection",
    )
    assert b'name="selectedWorkflowIds"' in wire.last.content
    assert b'["w1","w2"]' in wire.last.content


async def test_import_package_refuses_a_file_that_is_not_base64(wire: Wire) -> None:
    result = await make().run(
        {"action": "import_package", "package": "not base64!!", "conflict_policy": "fail"}
    )
    assert result.error_code == "N8N_INVALID_REQUEST"
    assert not wire.requests


async def test_export_package_returns_the_archive_as_base64(wire: Wire) -> None:
    wire.content = ARCHIVE
    wire.headers = {"content-type": "application/gzip"}
    result = await make().run({"action": "export_package", "workflow_ids": ["w1"]})
    assert result.data["body"] == base64.b64encode(ARCHIVE).decode()
    assert wire.body == {"workflowIds": ["w1"]}


# ---- trigger_workflow ---------------------------------------------------------------------


async def test_trigger_posts_json_without_the_api_key(wire: Wire) -> None:
    result = await make().run(
        {
            "action": "trigger_workflow",
            "webhook_url": BASE + "/webhook/abc",
            "payload": {"a": 1},
        }
    )
    assert result.success
    assert (wire.last.method, wire.last.url.path, wire.body) == ("POST", "/webhook/abc", {"a": 1})
    assert "X-N8N-API-KEY" not in wire.last.headers


async def test_trigger_get_sends_the_payload_as_query(wire: Wire) -> None:
    await make().run(
        {
            "action": "trigger_workflow",
            "webhook_url": BASE + "/webhook/abc",
            "method": "get",
            "payload": {"q": "1"},
        }
    )
    assert wire.last.method == "GET"
    assert dict(wire.last.url.params) == {"q": "1"}
    assert wire.last.content == b""


async def test_trigger_header_and_basic_auth(wire: Wire) -> None:
    await make().run(
        {
            "action": "trigger_workflow",
            "webhook_url": BASE + "/webhook/abc",
            "auth_type": "header",
            "auth_header_name": "X-Token",
            "auth_header_value": "tok",
        }
    )
    assert wire.last.headers["X-Token"] == "tok"
    await make().run(
        {
            "action": "trigger_workflow",
            "webhook_url": BASE + "/webhook/abc",
            "auth_type": "basic",
            "username": "u",
            "password": "p",
        }
    )
    assert wire.last.headers["Authorization"].startswith("Basic ")


async def test_trigger_returns_text_when_the_answer_is_not_json(wire: Wire) -> None:
    wire.content = b"done"
    result = await make().run({"action": "trigger_workflow", "webhook_url": BASE + "/webhook/a"})
    assert result.data["body"] == "done"


@pytest.mark.parametrize(
    "url",
    ["http://evil.example/webhook/abc", "https://n8n.test/webhook/abc", "http://n8n.test:81/x"],
)
async def test_trigger_refuses_another_host(wire: Wire, url: str) -> None:
    result = await make().run({"action": "trigger_workflow", "webhook_url": url})
    assert result.error_code == "N8N_INVALID_REQUEST"
    assert not wire.requests


@pytest.mark.parametrize(
    "payload",
    [
        {"webhook_url": "ftp://n8n.test/x"},
        {"webhook_url": BASE + "/x", "auth_type": "header"},
        {"webhook_url": BASE + "/x", "auth_type": "basic"},
        {"webhook_url": BASE + "/x", "method": "TRACE"},
    ],
)
async def test_trigger_input_is_validated(wire: Wire, payload: dict) -> None:
    result = await make().run({"action": "trigger_workflow", **payload})
    assert result.error_code == "VALIDATION_ERROR"
    assert not wire.requests


async def test_secrets_are_not_logged(wire: Wire, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    connector = make()
    await connector.run({"action": "list_tags"})
    await connector.run(
        {
            "action": "trigger_workflow",
            "webhook_url": BASE + "/webhook/abc",
            "auth_type": "basic",
            "username": "u",
            "password": "pw-secret",
        }
    )
    assert "pw-secret" not in caplog.text
    assert "key-123" not in caplog.text
