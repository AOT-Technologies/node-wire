#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Salesforce connector tests: the action table, request building, responses and errors."""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from node_wire_runtime import SecretProvider
from node_wire_salesforce import logic
from node_wire_salesforce.logic import SalesforceConnector, SalesforceTransientError
from node_wire_salesforce.normalizers import (
    normalize_salesforce_read_delete_contact,
    normalize_salesforce_read_delete_lead,
    normalize_salesforce_update_contact,
    normalize_salesforce_update_lead,
)
from node_wire_salesforce.schema import (
    CreateContactInput,
    CreateLeadInput,
    CreateRecordInput,
    DeleteContactInput,
    DeleteLeadInput,
    GetLimitsInput,
    GetRecordInput,
    ReadContactInput,
    ReadLeadInput,
    SetUserPasswordInput,
    UpdateContactInput,
    UpdateLeadInput,
    UploadIngestJobDataInput,
)

BASE = "https://acme.my.salesforce.com"

CSV_RESULTS = {
    "get_ingest_successful_results",
    "get_ingest_failed_results",
    "get_ingest_unprocessed_records",
    "get_query_job_results",
}

# (action, input, method, path, query, body, headers, csv text)
ROWS: list[
    tuple[str, dict[str, Any], str, str, dict[str, str], Any, dict[str, str], str | None]
] = [
    ("list_api_versions", {}, "GET", "/services/data", {}, None, {}, None),
    ("list_resources", {}, "GET", "/services/data/v68.0", {}, None, {}, None),
    ("get_limits", {}, "GET", "/services/data/v68.0/limits", {}, None, {}, None),
    (
        "get_record_counts",
        {"sobjects": ["a", "b"]},
        "GET",
        "/services/data/v68.0/limits/recordCount",
        {"sobjects": "a,b"},
        None,
        {},
        None,
    ),
    ("describe_global", {}, "GET", "/services/data/v68.0/sobjects", {}, None, {}, None),
    ("get_tabs", {}, "GET", "/services/data/v68.0/tabs", {}, None, {}, None),
    ("get_theme", {}, "GET", "/services/data/v68.0/theme", {}, None, {}, None),
    ("get_app_menu_types", {}, "GET", "/services/data/v68.0/appMenu", {}, None, {}, None),
    (
        "get_app_menu_items",
        {},
        "GET",
        "/services/data/v68.0/appMenu/AppSwitcher",
        {},
        None,
        {},
        None,
    ),
    (
        "get_mobile_app_menu_items",
        {},
        "GET",
        "/services/data/v68.0/appMenu/Salesforce1",
        {},
        None,
        {},
        None,
    ),
    (
        "get_sobject_info",
        {"sobject": "Account"},
        "GET",
        "/services/data/v68.0/sobjects/Account",
        {},
        None,
        {},
        None,
    ),
    (
        "describe_sobject",
        {"sobject": "Account"},
        "GET",
        "/services/data/v68.0/sobjects/Account/describe",
        {},
        None,
        {},
        None,
    ),
    (
        "get_deleted_records",
        {"sobject": "Account", "start": "val", "end": "val"},
        "GET",
        "/services/data/v68.0/sobjects/Account/deleted",
        {"start": "val", "end": "val"},
        None,
        {},
        None,
    ),
    (
        "get_updated_records",
        {"sobject": "Account", "start": "val", "end": "val"},
        "GET",
        "/services/data/v68.0/sobjects/Account/updated",
        {"start": "val", "end": "val"},
        None,
        {},
        None,
    ),
    (
        "get_named_layout",
        {"sobject": "Account", "layout_name": "a b"},
        "GET",
        "/services/data/v68.0/sobjects/Account/describe/namedLayouts/a%20b",
        {},
        None,
        {},
        None,
    ),
    (
        "get_layouts",
        {"sobject": "Account"},
        "GET",
        "/services/data/v68.0/sobjects/Account/describe/layouts",
        {},
        None,
        {},
        None,
    ),
    (
        "get_layouts_for_record_type",
        {"sobject": "Account", "record_type_id": "001000000000001AAA"},
        "GET",
        "/services/data/v68.0/sobjects/Account/describe/layouts/001000000000001AAA",
        {},
        None,
        {},
        None,
    ),
    (
        "get_global_publisher_layouts",
        {},
        "GET",
        "/services/data/v68.0/sobjects/Global/describe/layouts",
        {},
        None,
        {},
        None,
    ),
    (
        "get_compact_layouts",
        {"sobject": "Account"},
        "GET",
        "/services/data/v68.0/sobjects/Account/describe/compactLayouts",
        {},
        None,
        {},
        None,
    ),
    (
        "get_compact_layouts_for_objects",
        {"objects": ["a", "b"]},
        "GET",
        "/services/data/v68.0/compactLayouts",
        {"q": "a,b"},
        None,
        {},
        None,
    ),
    (
        "get_approval_layouts",
        {"sobject": "Account"},
        "GET",
        "/services/data/v68.0/sobjects/Account/describe/approvalLayouts",
        {},
        None,
        {},
        None,
    ),
    (
        "get_approval_layout",
        {"sobject": "Account", "approval_process_name": "a b"},
        "GET",
        "/services/data/v68.0/sobjects/Account/describe/approvalLayouts/a%20b",
        {},
        None,
        {},
        None,
    ),
    (
        "get_platform_action",
        {},
        "GET",
        "/services/data/v68.0/sobjects/PlatformAction",
        {},
        None,
        {},
        None,
    ),
    (
        "create_record",
        {"sobject": "Account", "fields": {"k": "v"}},
        "POST",
        "/services/data/v68.0/sobjects/Account",
        {},
        {"k": "v"},
        {},
        None,
    ),
    (
        "get_record",
        {"sobject": "Account", "record_id": "001000000000001AAA", "fields": ["a", "b"]},
        "GET",
        "/services/data/v68.0/sobjects/Account/001000000000001AAA",
        {"fields": "a,b"},
        None,
        {},
        None,
    ),
    (
        "update_record",
        {"sobject": "Account", "record_id": "001000000000001AAA", "fields": {"k": "v"}},
        "PATCH",
        "/services/data/v68.0/sobjects/Account/001000000000001AAA",
        {},
        {"k": "v"},
        {},
        None,
    ),
    (
        "delete_record",
        {"sobject": "Account", "record_id": "001000000000001AAA"},
        "DELETE",
        "/services/data/v68.0/sobjects/Account/001000000000001AAA",
        {},
        None,
        {},
        None,
    ),
    (
        "get_record_by_external_id",
        {"sobject": "Account", "field_name": "a b", "field_value": "a b", "fields": ["a", "b"]},
        "GET",
        "/services/data/v68.0/sobjects/Account/a%20b/a%20b",
        {"fields": "a,b"},
        None,
        {},
        None,
    ),
    (
        "upsert_record_by_external_id",
        {"sobject": "Account", "field_name": "a b", "field_value": "a b", "fields": {"k": "v"}},
        "PATCH",
        "/services/data/v68.0/sobjects/Account/a%20b/a%20b",
        {},
        {"k": "v"},
        {},
        None,
    ),
    (
        "delete_record_by_external_id",
        {"sobject": "Account", "field_name": "a b", "field_value": "a b"},
        "DELETE",
        "/services/data/v68.0/sobjects/Account/a%20b/a%20b",
        {},
        None,
        {},
        None,
    ),
    (
        "get_blob_field",
        {"sobject": "Account", "record_id": "001000000000001AAA", "blob_field": "a b"},
        "GET",
        "/services/data/v68.0/sobjects/Account/001000000000001AAA/a%20b",
        {},
        None,
        {},
        None,
    ),
    (
        "get_rich_text_image",
        {
            "sobject": "Account",
            "record_id": "001000000000001AAA",
            "field_name": "a b",
            "content_reference_id": "a b",
        },
        "GET",
        "/services/data/v68.0/sobjects/Account/001000000000001AAA/richTextImageFields/a%20b/a%20b",
        {},
        None,
        {},
        None,
    ),
    (
        "get_related_records",
        {
            "sobject": "Account",
            "record_id": "001000000000001AAA",
            "relationship_name": "a b",
            "fields": ["a", "b"],
        },
        "GET",
        "/services/data/v68.0/sobjects/Account/001000000000001AAA/a%20b",
        {"fields": "a,b"},
        None,
        {},
        None,
    ),
    (
        "update_related_record",
        {
            "sobject": "Account",
            "record_id": "001000000000001AAA",
            "relationship_name": "a b",
            "fields": {"k": "v"},
        },
        "PATCH",
        "/services/data/v68.0/sobjects/Account/001000000000001AAA/a%20b",
        {},
        {"k": "v"},
        {},
        None,
    ),
    (
        "delete_related_record",
        {"sobject": "Account", "record_id": "001000000000001AAA", "relationship_name": "a b"},
        "DELETE",
        "/services/data/v68.0/sobjects/Account/001000000000001AAA/a%20b",
        {},
        None,
        {},
        None,
    ),
    (
        "create_records",
        {"records": [{"a": 1}], "all_or_none": True},
        "POST",
        "/services/data/v68.0/composite/sobjects",
        {},
        {"records": [{"a": 1}], "allOrNone": True},
        {},
        None,
    ),
    (
        "get_records",
        {"sobject": "Account", "ids": ["a", "b"], "fields": ["a", "b"]},
        "GET",
        "/services/data/v68.0/composite/sobjects/Account",
        {"ids": "a,b", "fields": "a,b"},
        None,
        {},
        None,
    ),
    (
        "get_records_with_body",
        {"sobject": "Account", "ids": ["a", "b"], "fields": ["a", "b"]},
        "POST",
        "/services/data/v68.0/composite/sobjects/Account",
        {},
        {"ids": ["a", "b"], "fields": ["a", "b"]},
        {},
        None,
    ),
    (
        "update_records",
        {"records": [{"a": 1}], "all_or_none": True},
        "PATCH",
        "/services/data/v68.0/composite/sobjects",
        {},
        {"records": [{"a": 1}], "allOrNone": True},
        {},
        None,
    ),
    (
        "upsert_records",
        {
            "sobject": "Account",
            "external_id_field": "a b",
            "records": [{"a": 1}],
            "all_or_none": True,
        },
        "PATCH",
        "/services/data/v68.0/composite/sobjects/Account/a%20b",
        {},
        {"records": [{"a": 1}], "allOrNone": True},
        {},
        None,
    ),
    (
        "delete_records",
        {"ids": ["a", "b"], "all_or_none": True},
        "DELETE",
        "/services/data/v68.0/composite/sobjects",
        {"ids": "a,b", "allOrNone": "true"},
        None,
        {},
        None,
    ),
    (
        "composite",
        {"composite_request": [{"a": 1}], "all_or_none": True, "collate_subrequests": True},
        "POST",
        "/services/data/v68.0/composite",
        {},
        {"composite_request": [{"a": 1}], "allOrNone": True, "collateSubrequests": True},
        {},
        None,
    ),
    ("get_composite_resources", {}, "GET", "/services/data/v68.0/composite", {}, None, {}, None),
    (
        "composite_graph",
        {"graphs": [{"a": 1}]},
        "POST",
        "/services/data/v68.0/composite/graph",
        {},
        {"graphs": [{"a": 1}]},
        {},
        None,
    ),
    (
        "composite_batch",
        {"batch_requests": [{"a": 1}], "halt_on_error": True},
        "POST",
        "/services/data/v68.0/composite/batch",
        {},
        {"batch_requests": [{"a": 1}], "haltOnError": True},
        {},
        None,
    ),
    (
        "create_record_tree",
        {"sobject": "Account", "records": [{"a": 1}]},
        "POST",
        "/services/data/v68.0/composite/tree/Account",
        {},
        {"records": [{"a": 1}]},
        {},
        None,
    ),
    ("query", {"q": "val"}, "GET", "/services/data/v68.0/query", {"q": "val"}, None, {}, None),
    (
        "query_more",
        {"locator": "01gAAA-2000"},
        "GET",
        "/services/data/v68.0/query/01gAAA-2000",
        {},
        None,
        {},
        None,
    ),
    (
        "query_all",
        {"q": "val"},
        "GET",
        "/services/data/v68.0/queryAll",
        {"q": "val"},
        None,
        {},
        None,
    ),
    (
        "query_all_more",
        {"locator": "01gAAA-2000"},
        "GET",
        "/services/data/v68.0/queryAll/01gAAA-2000",
        {},
        None,
        {},
        None,
    ),
    ("search", {"q": "val"}, "GET", "/services/data/v68.0/search", {"q": "val"}, None, {}, None),
    (
        "parameterized_search_get",
        {"q": "val", "parameters": {"k": "v"}},
        "GET",
        "/services/data/v68.0/parameterizedSearch",
        {"q": "val", "k": "v"},
        None,
        {},
        None,
    ),
    (
        "parameterized_search_post",
        {"search": {"k": "v"}},
        "POST",
        "/services/data/v68.0/parameterizedSearch",
        {},
        {"k": "v"},
        {},
        None,
    ),
    (
        "get_search_scope_order",
        {},
        "GET",
        "/services/data/v68.0/search/scopeOrder",
        {},
        None,
        {},
        None,
    ),
    (
        "get_search_result_layouts",
        {"objects": ["a", "b"]},
        "GET",
        "/services/data/v68.0/search/layout",
        {"q": "a,b"},
        None,
        {},
        None,
    ),
    (
        "search_suggestions",
        {"q": "val", "sobject": "val", "parameters": {"k": "v"}},
        "GET",
        "/services/data/v68.0/search/suggestions",
        {"q": "val", "sobject": "val", "k": "v"},
        None,
        {},
        None,
    ),
    (
        "search_title_matches",
        {"q": "val", "language": "val", "publish_status": "val", "parameters": {"k": "v"}},
        "GET",
        "/services/data/v68.0/search/suggestTitleMatches",
        {"q": "val", "language": "val", "publishStatus": "val", "k": "v"},
        None,
        {},
        None,
    ),
    (
        "search_suggested_queries",
        {"q": "val", "language": "val", "parameters": {"k": "v"}},
        "GET",
        "/services/data/v68.0/search/suggestSearchQueries",
        {"q": "val", "language": "val", "k": "v"},
        None,
        {},
        None,
    ),
    (
        "run_named_query",
        {"query_name": "My_Query", "parameters": {"k": "v"}},
        "GET",
        "/services/data/v68.0/named/query/My_Query",
        {"k": "v"},
        None,
        {},
        None,
    ),
    (
        "list_list_views",
        {"sobject": "Account", "parameters": {"k": "v"}},
        "GET",
        "/services/data/v68.0/sobjects/Account/listviews",
        {"k": "v"},
        None,
        {},
        None,
    ),
    (
        "get_list_view",
        {"sobject": "Account", "list_view_id": "001000000000001AAA"},
        "GET",
        "/services/data/v68.0/sobjects/Account/listviews/001000000000001AAA",
        {},
        None,
        {},
        None,
    ),
    (
        "describe_list_view",
        {"sobject": "Account", "list_view_id": "001000000000001AAA"},
        "GET",
        "/services/data/v68.0/sobjects/Account/listviews/001000000000001AAA/describe",
        {},
        None,
        {},
        None,
    ),
    (
        "get_list_view_results",
        {
            "sobject": "Account",
            "list_view_id": "001000000000001AAA",
            "limit": 5,
            "offset": 5,
            "parameters": {"k": "v"},
        },
        "GET",
        "/services/data/v68.0/sobjects/Account/listviews/001000000000001AAA/results",
        {"limit": "5", "offset": "5", "k": "v"},
        None,
        {},
        None,
    ),
    (
        "get_recent_list_views",
        {"sobject": "Account"},
        "GET",
        "/services/data/v68.0/sobjects/Account/listviews/recent",
        {},
        None,
        {},
        None,
    ),
    (
        "get_recent_items",
        {"limit": 5},
        "GET",
        "/services/data/v68.0/recent",
        {"limit": "5"},
        None,
        {},
        None,
    ),
    (
        "get_relevant_items",
        {"parameters": {"k": "v"}},
        "GET",
        "/services/data/v68.0/sobjects/relevantItems",
        {"k": "v"},
        None,
        {},
        None,
    ),
    ("list_quick_actions", {}, "GET", "/services/data/v68.0/quickActions", {}, None, {}, None),
    (
        "create_record_with_quick_action",
        {"action_name": "a b", "record": {"k": "v"}},
        "POST",
        "/services/data/v68.0/quickActions/a%20b",
        {},
        {"record": {"k": "v"}},
        {},
        None,
    ),
    (
        "list_sobject_quick_actions",
        {"sobject": "Account"},
        "GET",
        "/services/data/v68.0/sobjects/Account/quickActions",
        {},
        None,
        {},
        None,
    ),
    (
        "get_sobject_quick_action",
        {"sobject": "Account", "action_name": "a b"},
        "GET",
        "/services/data/v68.0/sobjects/Account/quickActions/a%20b",
        {},
        None,
        {},
        None,
    ),
    (
        "invoke_sobject_quick_action",
        {
            "sobject": "Account",
            "action_name": "a b",
            "record": {"k": "v"},
            "context_id": "001000000000001AAA",
        },
        "POST",
        "/services/data/v68.0/sobjects/Account/quickActions/a%20b",
        {},
        {"record": {"k": "v"}, "contextId": "001000000000001AAA"},
        {},
        None,
    ),
    (
        "describe_sobject_quick_action",
        {"sobject": "Account", "action_name": "a b"},
        "GET",
        "/services/data/v68.0/sobjects/Account/quickActions/a%20b/describe",
        {},
        None,
        {},
        None,
    ),
    (
        "get_quick_action_default_values",
        {"sobject": "Account", "action_name": "a b"},
        "GET",
        "/services/data/v68.0/sobjects/Account/quickActions/a%20b/defaultValues",
        {},
        None,
        {},
        None,
    ),
    (
        "get_quick_action_default_values_for_record",
        {"sobject": "Account", "action_name": "a b", "context_id": "001000000000001AAA"},
        "GET",
        "/services/data/v68.0/sobjects/Account/quickActions/a%20b/defaultValues/001000000000001AAA",
        {},
        None,
        {},
        None,
    ),
    (
        "list_process_approvals",
        {},
        "GET",
        "/services/data/v68.0/process/approvals",
        {},
        None,
        {},
        None,
    ),
    (
        "submit_process_approval",
        {"requests": [{"a": 1}]},
        "POST",
        "/services/data/v68.0/process/approvals",
        {},
        {"requests": [{"a": 1}]},
        {},
        None,
    ),
    ("list_process_rules", {}, "GET", "/services/data/v68.0/process/rules", {}, None, {}, None),
    (
        "trigger_process_rules",
        {"context_ids": ["a", "b"]},
        "POST",
        "/services/data/v68.0/process/rules",
        {},
        {"contextIds": ["a", "b"]},
        {},
        None,
    ),
    (
        "list_process_rules_for_sobject",
        {"sobject": "Account"},
        "GET",
        "/services/data/v68.0/process/rules/Account",
        {},
        None,
        {},
        None,
    ),
    (
        "get_process_rule",
        {"sobject": "Account", "rule_id": "001000000000001AAA"},
        "GET",
        "/services/data/v68.0/process/rules/Account/001000000000001AAA",
        {},
        None,
        {},
        None,
    ),
    (
        "trigger_process_rule",
        {"sobject": "Account", "rule_id": "001000000000001AAA", "context_id": "001000000000001AAA"},
        "POST",
        "/services/data/v68.0/process/rules/Account/001000000000001AAA",
        {},
        {"contextId": "001000000000001AAA"},
        {},
        None,
    ),
    ("list_invocable_actions", {}, "GET", "/services/data/v68.0/actions", {}, None, {}, None),
    (
        "list_standard_invocable_actions",
        {},
        "GET",
        "/services/data/v68.0/actions/standard",
        {},
        None,
        {},
        None,
    ),
    (
        "list_custom_invocable_actions",
        {},
        "GET",
        "/services/data/v68.0/actions/custom",
        {},
        None,
        {},
        None,
    ),
    (
        "deploy_data_kit_components",
        {"inputs": [{"a": 1}]},
        "POST",
        "/services/data/v68.0/actions/custom/flow/sfdatakit__DeployDataKitComponents",
        {},
        {"inputs": [{"a": 1}]},
        {},
        None,
    ),
    (
        "get_product_schedules",
        {"record_id": "001000000000001AAA"},
        "GET",
        "/services/data/v68.0/sobjects/OpportunityLineItem/001000000000001AAA/OpportunityLineItemSchedules",
        {},
        None,
        {},
        None,
    ),
    (
        "create_product_schedules",
        {"record_id": "001000000000001AAA", "schedule": {"k": "v"}},
        "PUT",
        "/services/data/v68.0/sobjects/OpportunityLineItem/001000000000001AAA/OpportunityLineItemSchedules",
        {},
        {"k": "v"},
        {},
        None,
    ),
    (
        "delete_product_schedules",
        {"record_id": "001000000000001AAA"},
        "DELETE",
        "/services/data/v68.0/sobjects/OpportunityLineItem/001000000000001AAA/OpportunityLineItemSchedules",
        {},
        None,
        {},
        None,
    ),
    ("get_support_resources", {}, "GET", "/services/data/v68.0/support", {}, None, {}, None),
    (
        "list_data_category_groups",
        {"parameters": {"k": "v"}},
        "GET",
        "/services/data/v68.0/support/dataCategoryGroups",
        {"k": "v"},
        None,
        {},
        None,
    ),
    (
        "get_data_category_detail",
        {"group": "a b", "category": "a b", "parameters": {"k": "v"}},
        "GET",
        "/services/data/v68.0/support/dataCategoryGroups/a%20b/dataCategories/a%20b",
        {"k": "v"},
        None,
        {},
        None,
    ),
    (
        "list_knowledge_articles",
        {"language": "val", "parameters": {"k": "v"}},
        "GET",
        "/services/data/v68.0/support/knowledgeArticles",
        {"k": "v"},
        None,
        {"Accept-Language": "val"},
        None,
    ),
    (
        "get_knowledge_article",
        {"article_id_or_url_name": "a b", "language": "val"},
        "GET",
        "/services/data/v68.0/support/knowledgeArticles/a%20b",
        {},
        None,
        {"Accept-Language": "val"},
        None,
    ),
    (
        "get_knowledge_language_settings",
        {},
        "GET",
        "/services/data/v68.0/knowledgeManagement/settings",
        {},
        None,
        {},
        None,
    ),
    (
        "get_suggested_articles",
        {"sobject": "Account", "language": "val", "parameters": {"k": "v"}},
        "GET",
        "/services/data/v68.0/sobjects/Account/suggestedArticles",
        {"language": "val", "k": "v"},
        None,
        {},
        None,
    ),
    (
        "get_suggested_articles_for_record",
        {
            "sobject": "Account",
            "record_id": "001000000000001AAA",
            "language": "val",
            "parameters": {"k": "v"},
        },
        "GET",
        "/services/data/v68.0/sobjects/Account/001000000000001AAA/suggestedArticles",
        {"language": "val", "k": "v"},
        None,
        {},
        None,
    ),
    (
        "get_embedded_service_config",
        {"config_name": "a b"},
        "GET",
        "/services/data/v68.0/support/embeddedservice/configuration/a%20b",
        {},
        None,
        {},
        None,
    ),
    (
        "get_event_schema_by_name",
        {"event_name": "Account", "payload_format": "val"},
        "GET",
        "/services/data/v68.0/sobjects/Account/eventSchema",
        {"payloadFormat": "val"},
        None,
        {},
        None,
    ),
    (
        "get_event_schema_by_id",
        {"schema_id": "a b", "payload_format": "val"},
        "GET",
        "/services/data/v68.0/event/eventSchema/a%20b",
        {"payloadFormat": "val"},
        None,
        {},
        None,
    ),
    (
        "compile_consent",
        {"consent_action": "a b", "ids": ["a", "b"], "parameters": {"k": "v"}},
        "GET",
        "/services/data/v68.0/consent/action/a%20b",
        {"ids": "a,b", "k": "v"},
        None,
        {},
        None,
    ),
    (
        "compile_multi_consent",
        {"actions": ["a", "b"], "ids": ["a", "b"], "parameters": {"k": "v"}},
        "GET",
        "/services/data/v68.0/consent/multiaction",
        {"actions": "a,b", "ids": "a,b", "k": "v"},
        None,
        {},
        None,
    ),
    (
        "write_consent",
        {"consent_action": "a b", "ids": ["a", "b"], "consent": {"k": "v"}},
        "PATCH",
        "/services/data/v68.0/consent/action/a%20b",
        {"ids": "a,b"},
        {"k": "v"},
        {},
        None,
    ),
    (
        "request_portability_data",
        {"data_subject_id": "001000000000001AAA", "policy_name": "val"},
        "POST",
        "/services/data/v68.0/consent/dsr/rtp/execute",
        {},
        {"dataSubjectId": "001000000000001AAA", "policyName": "val"},
        {},
        None,
    ),
    (
        "get_portability_status",
        {"policy_file_id": "val"},
        "GET",
        "/services/data/v68.0/consent/dsr/rtp/execute",
        {"policyFileId": "val"},
        None,
        {},
        None,
    ),
    ("get_scheduling_resources", {}, "GET", "/services/data/v68.0/scheduling", {}, None, {}, None),
    (
        "get_appointment_slots",
        {"request": {"k": "v"}},
        "POST",
        "/services/data/v68.0/scheduling/getAppointmentSlots",
        {},
        {"k": "v"},
        {},
        None,
    ),
    (
        "get_appointment_candidates",
        {"request": {"k": "v"}},
        "POST",
        "/services/data/v68.0/scheduling/getAppointmentCandidates",
        {},
        {"k": "v"},
        {},
        None,
    ),
    (
        "set_survey_field_translation",
        {"developer_name": "a b", "language": "a b", "value": "val"},
        "POST",
        "/services/data/v68.0/localizedvalue/record/a%20b/a%20b",
        {},
        {"value": "val"},
        {},
        None,
    ),
    (
        "set_survey_field_translations",
        {"request": {"k": "v"}},
        "POST",
        "/services/data/v68.0/localizedvalue/records/upsert",
        {},
        {"k": "v"},
        {},
        None,
    ),
    (
        "delete_survey_field_translation",
        {"developer_name": "a b", "language": "a b"},
        "DELETE",
        "/services/data/v68.0/localizedvalue/record/a%20b/a%20b",
        {},
        None,
        {},
        None,
    ),
    (
        "delete_survey_field_translations",
        {"request": {"k": "v"}},
        "POST",
        "/services/data/v68.0/localizedvalue/records/delete",
        {},
        {"k": "v"},
        {},
        None,
    ),
    (
        "get_survey_field_translation",
        {"developer_name": "a b", "language": "a b"},
        "GET",
        "/services/data/v68.0/localizedvalue/record/a%20b/a%20b",
        {},
        None,
        {},
        None,
    ),
    (
        "get_survey_field_translations",
        {"request": {"k": "v"}},
        "POST",
        "/services/data/v68.0/localizedvalue/records/get",
        {},
        {"k": "v"},
        {},
        None,
    ),
    (
        "get_user_password_status",
        {"user_id": "001000000000001AAA"},
        "GET",
        "/services/data/v68.0/sobjects/User/001000000000001AAA/password",
        {},
        None,
        {},
        None,
    ),
    (
        "set_user_password",
        {"user_id": "001000000000001AAA", "new_password": "S3cret!pw"},
        "POST",
        "/services/data/v68.0/sobjects/User/001000000000001AAA/password",
        {},
        {"NewPassword": "S3cret!pw"},
        {},
        None,
    ),
    (
        "reset_user_password",
        {"user_id": "001000000000001AAA"},
        "DELETE",
        "/services/data/v68.0/sobjects/User/001000000000001AAA/password",
        {},
        None,
        {},
        None,
    ),
    (
        "get_self_service_user_password_status",
        {"self_service_user_id": "001000000000001AAA"},
        "GET",
        "/services/data/v68.0/sobjects/SelfServiceUser/001000000000001AAA/password",
        {},
        None,
        {},
        None,
    ),
    (
        "set_self_service_user_password",
        {"self_service_user_id": "001000000000001AAA", "new_password": "S3cret!pw"},
        "POST",
        "/services/data/v68.0/sobjects/SelfServiceUser/001000000000001AAA/password",
        {},
        {"NewPassword": "S3cret!pw"},
        {},
        None,
    ),
    (
        "reset_self_service_user_password",
        {"self_service_user_id": "001000000000001AAA"},
        "DELETE",
        "/services/data/v68.0/sobjects/SelfServiceUser/001000000000001AAA/password",
        {},
        None,
        {},
        None,
    ),
    (
        "get_lightning_toggle_metrics",
        {},
        "GET",
        "/services/data/v68.0/sobjects/LightningToggleMetrics",
        {},
        None,
        {},
        None,
    ),
    (
        "get_lightning_usage_by_app_type",
        {},
        "GET",
        "/services/data/v68.0/sobjects/LightningUsageByAppTypeMetrics",
        {},
        None,
        {},
        None,
    ),
    (
        "get_lightning_usage_by_browser",
        {},
        "GET",
        "/services/data/v68.0/sobjects/LightningUsageByBrowserMetrics",
        {},
        None,
        {},
        None,
    ),
    (
        "get_lightning_usage_by_page",
        {},
        "GET",
        "/services/data/v68.0/sobjects/LightningUsageByPageMetrics",
        {},
        None,
        {},
        None,
    ),
    (
        "get_lightning_usage_by_flexipage",
        {},
        "GET",
        "/services/data/v68.0/sobjects/LightningUsageByFlexiPageMetrics",
        {},
        None,
        {},
        None,
    ),
    (
        "get_lightning_exit_by_page",
        {},
        "GET",
        "/services/data/v68.0/sobjects/LightningExitByPageMetrics",
        {},
        None,
        {},
        None,
    ),
    (
        "create_ingest_job",
        {
            "object": "val",
            "operation": "insert",
            "content_type": "CSV",
            "column_delimiter": "BACKQUOTE",
            "line_ending": "LF",
            "external_id_field_name": "val",
            "assignment_rule_id": "001000000000001AAA",
        },
        "POST",
        "/services/data/v68.0/jobs/ingest",
        {},
        {
            "object": "val",
            "operation": "insert",
            "contentType": "CSV",
            "columnDelimiter": "BACKQUOTE",
            "lineEnding": "LF",
            "externalIdFieldName": "val",
            "assignmentRuleId": "001000000000001AAA",
        },
        {},
        None,
    ),
    (
        "upload_ingest_job_data",
        {"job_id": "001000000000001AAA", "csv_data": "val"},
        "PUT",
        "/services/data/v68.0/jobs/ingest/001000000000001AAA/batches",
        {},
        None,
        {},
        "val",
    ),
    (
        "complete_ingest_job",
        {"job_id": "001000000000001AAA"},
        "PATCH",
        "/services/data/v68.0/jobs/ingest/001000000000001AAA",
        {},
        {"state": "UploadComplete"},
        {},
        None,
    ),
    (
        "abort_ingest_job",
        {"job_id": "001000000000001AAA"},
        "PATCH",
        "/services/data/v68.0/jobs/ingest/001000000000001AAA",
        {},
        {"state": "Aborted"},
        {},
        None,
    ),
    (
        "delete_ingest_job",
        {"job_id": "001000000000001AAA"},
        "DELETE",
        "/services/data/v68.0/jobs/ingest/001000000000001AAA",
        {},
        None,
        {},
        None,
    ),
    (
        "get_ingest_job",
        {"job_id": "001000000000001AAA"},
        "GET",
        "/services/data/v68.0/jobs/ingest/001000000000001AAA",
        {},
        None,
        {},
        None,
    ),
    (
        "list_ingest_jobs",
        {"is_pk_chunking_enabled": True, "job_type": "val", "query_locator": "val"},
        "GET",
        "/services/data/v68.0/jobs/ingest",
        {"isPkChunkingEnabled": "true", "jobType": "val", "queryLocator": "val"},
        None,
        {},
        None,
    ),
    (
        "get_ingest_successful_results",
        {"job_id": "001000000000001AAA"},
        "GET",
        "/services/data/v68.0/jobs/ingest/001000000000001AAA/successfulResults/",
        {},
        None,
        {},
        None,
    ),
    (
        "get_ingest_failed_results",
        {"job_id": "001000000000001AAA"},
        "GET",
        "/services/data/v68.0/jobs/ingest/001000000000001AAA/failedResults/",
        {},
        None,
        {},
        None,
    ),
    (
        "get_ingest_unprocessed_records",
        {"job_id": "001000000000001AAA"},
        "GET",
        "/services/data/v68.0/jobs/ingest/001000000000001AAA/unprocessedrecords/",
        {},
        None,
        {},
        None,
    ),
    (
        "create_query_job",
        {
            "operation": "query",
            "query": "val",
            "content_type": "CSV",
            "column_delimiter": "BACKQUOTE",
            "line_ending": "LF",
        },
        "POST",
        "/services/data/v68.0/jobs/query",
        {},
        {
            "operation": "query",
            "query": "val",
            "contentType": "CSV",
            "columnDelimiter": "BACKQUOTE",
            "lineEnding": "LF",
        },
        {},
        None,
    ),
    (
        "get_query_job",
        {"job_id": "001000000000001AAA"},
        "GET",
        "/services/data/v68.0/jobs/query/001000000000001AAA",
        {},
        None,
        {},
        None,
    ),
    (
        "abort_query_job",
        {"job_id": "001000000000001AAA"},
        "PATCH",
        "/services/data/v68.0/jobs/query/001000000000001AAA",
        {},
        {"state": "Aborted"},
        {},
        None,
    ),
    (
        "delete_query_job",
        {"job_id": "001000000000001AAA"},
        "DELETE",
        "/services/data/v68.0/jobs/query/001000000000001AAA",
        {},
        None,
        {},
        None,
    ),
    (
        "list_query_jobs",
        {"is_pk_chunking_enabled": True, "job_type": "val", "query_locator": "val"},
        "GET",
        "/services/data/v68.0/jobs/query",
        {"isPkChunkingEnabled": "true", "jobType": "val", "queryLocator": "val"},
        None,
        {},
        None,
    ),
    (
        "get_query_job_results",
        {"job_id": "001000000000001AAA", "locator": "val", "max_records": 5},
        "GET",
        "/services/data/v68.0/jobs/query/001000000000001AAA/results",
        {"locator": "val", "maxRecords": "5"},
        None,
        {},
        None,
    ),
    (
        "get_query_job_result_pages",
        {"job_id": "001000000000001AAA", "max_records": 5},
        "GET",
        "/services/data/v68.0/jobs/query/001000000000001AAA/resultPages",
        {"maxRecords": "5"},
        None,
        {},
        None,
    ),
]


class Secrets(SecretProvider):
    def get_secret(self, key: str) -> str:
        return {"salesforce_instance_url": BASE + "/"}[key]


class Wire:
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
        if self.status == 204:
            return httpx.Response(204)
        return httpx.Response(self.status, json=self.json, headers=self.headers)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    @property
    def path(self) -> str:
        return self.last.url.raw_path.split(b"?")[0].decode()

    @property
    def query(self) -> dict[str, str]:
        return dict(self.last.url.params)

    @property
    def body(self) -> Any:
        return json.loads(self.last.content) if self.last.content else None


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    w = Wire()
    client = httpx.AsyncClient(transport=httpx.MockTransport(w.handler))

    @contextlib.asynccontextmanager
    async def fake_client(**_: Any) -> AsyncIterator[httpx.AsyncClient]:
        yield client

    monkeypatch.setattr(logic, "_http_client", fake_client)
    return w


def make(**config: Any) -> SalesforceConnector:
    conn = SalesforceConnector(secret_provider=Secrets(), config=config or None)
    # The OAuth provider's real defaults: FHIR media types, which Salesforce refuses with a 415.
    conn.get_auth_headers = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "Authorization": "Bearer tok",
            "Content-Type": "application/fhir+json",
            "Accept": "application/fhir+json",
        }
    )
    return conn


# ---- the action table -------------------------------------------------------------------------


def test_the_connector_registers_every_action() -> None:
    names = set(SalesforceConnector.nw_action_metas())
    assert len(names) == 150
    assert {r[0] for r in ROWS} <= names
    old = {
        f"{op}_{kind}"
        for op in ("create", "read", "update", "delete")
        for kind in ("lead", "contact")
    }
    assert len(names - {r[0] for r in ROWS}) == 8 and old == names - {r[0] for r in ROWS}


@pytest.mark.parametrize("row", ROWS, ids=[r[0] for r in ROWS])
async def test_action_builds_the_request(wire: Wire, row: tuple[Any, ...]) -> None:
    name, inp, method, path, query, body, headers, csv = row
    result = await make().run({"action": name, **inp})
    assert result.success, result.message
    req = wire.last
    assert req.method == method
    assert wire.path == path
    assert wire.query == query
    assert req.headers["authorization"] == "Bearer tok"
    for key, value in headers.items():
        assert req.headers[key] == value
    assert req.headers["accept"] == ("text/csv" if name in CSV_RESULTS else "application/json")
    if csv is not None:
        assert req.headers["content-type"] == "text/csv"
    elif body is not None:
        assert req.headers["content-type"] == "application/json"
    else:
        assert "content-type" not in req.headers
    if csv is not None:
        assert req.content.decode() == csv and req.headers["content-type"] == "text/csv"
    else:
        assert wire.body == body
    assert req.url.host == "acme.my.salesforce.com"


# ---- configuration and credentials ------------------------------------------------------------


async def test_default_api_version_and_the_config_override(wire: Wire) -> None:
    await make().run({"action": "get_limits"})
    assert wire.path == "/services/data/v68.0/limits"
    await make(api_version="v66.0").run({"action": "get_limits"})
    assert wire.path == "/services/data/v66.0/limits"
    await make().run({"action": "list_api_versions"})
    assert wire.path == "/services/data"


@pytest.mark.parametrize("bad", ["68.0", "v68", "v68.0/../x", "latest", "v8.0"])
def test_a_bad_api_version_is_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="api_version"):
        make(api_version=bad)._get_api_version()


@pytest.mark.parametrize("field", ["instance_url", "base_url", "api_version", "url"])
def test_no_action_takes_a_host_or_a_version(field: str) -> None:
    with pytest.raises(ValidationError):
        GetLimitsInput.model_validate({field: "https://evil.example.com"})


async def test_the_existing_actions_use_the_configured_version(wire: Wire) -> None:
    result = await make().run({"action": "read_lead", "record_id": "00Q000000000001AAA"})
    assert result.success
    assert wire.path == "/services/data/v68.0/sobjects/Lead/00Q000000000001AAA"
    await make(api_version="v66.0").run({"action": "read_lead", "record_id": "00Q000000000001AAA"})
    assert wire.path == "/services/data/v66.0/sobjects/Lead/00Q000000000001AAA"


# ---- responses --------------------------------------------------------------------------------


async def test_json_list_empty_and_text_bodies(wire: Wire) -> None:
    wire.json = [{"version": "68.0"}]
    assert (await make().run({"action": "list_api_versions"})).data["body"] == [{"version": "68.0"}]
    wire.status = 204
    result = await make().run(
        {"action": "delete_record", "sobject": "Account", "record_id": "001000000000001AAA"}
    )
    assert (result.data["status_code"], result.data["body"]) == (204, None)
    wire.content, wire.status = b"Id,Name\n001,Acme\n", 200
    wire.headers = {"content-type": "text/csv"}
    result = await make().run(
        {"action": "get_ingest_failed_results", "job_id": "750000000000001AAA"}
    )
    assert result.data["body"] == "Id,Name\n001,Acme\n"


async def test_a_binary_answer_comes_back_as_base64(wire: Wire) -> None:
    wire.content = b"\x89PNG\r\n"
    wire.headers = {"content-type": "application/octet-stream"}
    result = await make().run(
        {
            "action": "get_blob_field",
            "sobject": "Attachment",
            "record_id": "00P000000000001AAA",
            "blob_field": "Body",
        }
    )
    assert result.data["body"] == "iVBORw0K"


async def test_invalid_json_falls_back_to_text(wire: Wire) -> None:
    wire.content = b"not json"
    wire.headers = {"content-type": "application/json"}
    assert (await make().run({"action": "get_limits"})).data["body"] == "not json"


# ---- errors -----------------------------------------------------------------------------------

BODY = [{"message": "Session expired or invalid", "errorCode": "INVALID_SESSION_ID"}]


@pytest.mark.parametrize(
    ("status", "action", "code", "category"),
    [
        (401, {"action": "get_limits"}, "SALESFORCE_AUTH_ERROR", "AUTH"),
        (429, {"action": "get_limits"}, "SALESFORCE_RATE_LIMITED", "RETRYABLE"),
        (503, {"action": "get_limits"}, "SALESFORCE_TRANSIENT_ERROR", "RETRYABLE"),
        (502, {"action": "get_limits"}, "SALESFORCE_TRANSIENT_ERROR", "RETRYABLE"),
        (
            504,
            {"action": "delete_record", "sobject": "Account", "record_id": "001000000000001AAA"},
            "SALESFORCE_TRANSIENT_ERROR",
            "RETRYABLE",
        ),
        (
            503,
            {"action": "create_record", "sobject": "Account", "fields": {"Name": "x"}},
            "SALESFORCE_API_ERROR",
            "BUSINESS",
        ),
        (500, {"action": "get_limits"}, "SALESFORCE_API_ERROR", "BUSINESS"),
        (400, {"action": "get_limits"}, "SALESFORCE_API_ERROR", "BUSINESS"),
        (403, {"action": "get_limits"}, "SALESFORCE_API_ERROR", "BUSINESS"),
        (404, {"action": "get_limits"}, "SALESFORCE_API_ERROR", "BUSINESS"),
    ],
)
async def test_status_decides_the_category(
    wire: Wire, status: int, action: dict[str, Any], code: str, category: str
) -> None:
    wire.status = status
    wire.json = BODY
    result = await make().run(action)
    assert (result.error_code, result.error_category) == (code, category)


async def test_the_error_carries_salesforces_own_explanation(wire: Wire) -> None:
    wire.status = 400
    wire.json = [{"message": "unexpected token: 'FROM'", "errorCode": "MALFORMED_QUERY"}]
    result = await make().run({"action": "query", "q": "SELECT FROM"})
    assert "MALFORMED_QUERY" in result.message and "400" in result.message


@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
async def test_transport_errors_are_retryable(wire: Wire, error: Exception) -> None:
    wire.error = error
    result = await make().run({"action": "get_limits"})
    assert result.error_category == "RETRYABLE"


# ---- input checks -----------------------------------------------------------------------------


@pytest.mark.parametrize("sobject", ["", "../x", "Account/describe", "a b", "9Account", "A" * 81])
def test_an_object_name_cannot_leave_its_path_segment(sobject: str) -> None:
    with pytest.raises(ValidationError):
        GetRecordInput.model_validate({"sobject": sobject, "record_id": "001000000000001AAA"})


@pytest.mark.parametrize("record_id", ["", "short", "001/../x", "00100000000000"])
def test_a_record_id_is_checked(record_id: str) -> None:
    with pytest.raises(ValidationError):
        GetRecordInput.model_validate({"sobject": "Account", "record_id": record_id})


@pytest.mark.parametrize(
    ("action", "bad"),
    [
        ("create_record", {"sobject": "Account"}),
        ("create_record", {"sobject": "Account", "fields": "Name"}),
        ("create_ingest_job", {"object": "Account", "operation": "merge"}),
        ("create_ingest_job", {"object": "Account", "operation": "insert", "line_ending": "CR"}),
        ("query", {}),
        ("query_more", {"locator": "01g/../x"}),
        ("create_records", {"records": [{"a": 1}], "all_or_none": "maybe"}),
        ("get_deleted_records", {"sobject": "Account", "start": "2026-01-01T00:00:00Z"}),
    ],
)
def test_bad_input_is_rejected(action: str, bad: dict[str, Any]) -> None:
    from node_wire_salesforce import schema

    cls = getattr(schema, "".join(p.capitalize() for p in action.split("_")) + "Input")
    with pytest.raises(ValidationError):
        cls.model_validate(bad)


def test_the_new_password_is_hidden_from_repr() -> None:
    inp = SetUserPasswordInput.model_validate(
        {"user_id": "005000000000001AAA", "new_password": "S3cret!pw"}
    )
    assert "S3cret" not in repr(inp) and "S3cret" not in str(
        inp.model_dump_json(exclude={"new_password"})
    )


def test_csv_text_and_a_record_body_are_just_fields() -> None:
    assert UploadIngestJobDataInput.model_validate(
        {"job_id": "750000000000001AAA", "csv_data": "a,b\n1,2\n"}
    )
    assert CreateRecordInput.model_validate({"sobject": "My__c", "fields": {"Name": "x"}})


# ---- logging ----------------------------------------------------------------------------------


async def test_secrets_values_and_bodies_are_not_logged(
    wire: Wire, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await make().run(
        {
            "action": "set_user_password",
            "user_id": "005000000000001AAA",
            "new_password": "S3cret!pw",
        }
    )
    await make().run(
        {"action": "query", "q": "SELECT Name FROM Account WHERE Email = 'private@example.com'"}
    )
    wire.status = 400
    await make().run(
        {"action": "create_record", "sobject": "Account", "fields": {"Name": "Private Co"}}
    )
    text = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    for hidden in ("S3cret", "private@example.com", "Private Co", "Bearer tok"):
        assert hidden not in text
    assert "/services/data/v68.0/query" in {r.__dict__.get("path") for r in caplog.records}


# ---- the lead and contact actions -------------------------------------------------------------

TRACE = "test-trace"
LEAD = "00Q123456789012"
CONTACT = "003123456789012"


async def test_create_contact(wire: Wire) -> None:
    wire.status, wire.json = 201, {"id": CONTACT, "success": True}
    result = await make().create_contact(
        CreateContactInput(LastName="Doe", FirstName="John", Email="john@example.com"),
        trace_id=TRACE,
    )
    assert (result.success, result.resource_id, result.resource_type) == (True, CONTACT, "Contact")
    assert result.data["id"] == CONTACT
    assert wire.last.method == "POST" and wire.path == "/services/data/v68.0/sobjects/Contact"
    assert wire.body == {"LastName": "Doe", "FirstName": "John", "Email": "john@example.com"}


def test_create_contact_checks_the_account_id() -> None:
    with pytest.raises(ValidationError, match="Invalid Salesforce AccountId format"):
        CreateContactInput(LastName="Doe", AccountId="short")


async def test_create_lead(wire: Wire) -> None:
    wire.status, wire.json = 201, {"id": LEAD, "success": True}
    result = await make().create_lead(
        CreateLeadInput(LastName="Smith", Company="Acme Corp", Email="smith@acme.com"),
        trace_id=TRACE,
    )
    assert (result.resource_id, result.resource_type) == (LEAD, "Lead")
    assert wire.body == {"LastName": "Smith", "Company": "Acme Corp", "Email": "smith@acme.com"}


@pytest.mark.parametrize(
    ("kind", "record_id", "read"),
    [("Lead", LEAD, ReadLeadInput), ("Contact", CONTACT, ReadContactInput)],
)
async def test_read(wire: Wire, kind: str, record_id: str, read: Any) -> None:
    wire.json = {"Id": record_id, "LastName": "Smith"}
    action = getattr(make(), f"read_{kind.lower()}")
    result = await action(read(record_id=record_id), trace_id=TRACE)
    assert (result.resource_id, result.data["LastName"]) == (record_id, "Smith")
    assert (
        wire.last.method == "GET"
        and wire.path == f"/services/data/v68.0/sobjects/{kind}/{record_id}"
    )


@pytest.mark.parametrize(
    ("kind", "record_id", "update", "fields"),
    [
        ("Lead", LEAD, UpdateLeadInput, {"Company": "New Acme"}),
        ("Contact", CONTACT, UpdateContactInput, {"FirstName": "Jane"}),
    ],
)
async def test_update_answers_204_with_empty_data(
    wire: Wire, kind: str, record_id: str, update: Any, fields: dict[str, str]
) -> None:
    wire.status = 204
    result = await getattr(make(), f"update_{kind.lower()}")(
        update(record_id=record_id, fields=fields), trace_id=TRACE
    )
    assert (result.success, result.resource_id, result.data) == (True, record_id, {})
    assert wire.last.method == "PATCH" and wire.body == fields


@pytest.mark.parametrize(
    ("kind", "record_id", "delete"),
    [("Lead", LEAD, DeleteLeadInput), ("Contact", CONTACT, DeleteContactInput)],
)
async def test_delete(wire: Wire, kind: str, record_id: str, delete: Any) -> None:
    wire.status = 204
    result = await getattr(make(), f"delete_{kind.lower()}")(
        delete(record_id=record_id), trace_id=TRACE
    )
    assert (result.success, result.resource_id) == (True, record_id)
    assert wire.last.method == "DELETE" and wire.body is None


async def test_a_non_json_answer_is_returned_as_text(wire: Wire) -> None:
    wire.content, wire.headers = b"not-json-body", {"content-type": "text/plain"}
    result = await make().read_contact(ReadContactInput(record_id=CONTACT), trace_id=TRACE)
    assert result.data == {"text": "not-json-body"}


async def test_a_lead_error_raises_for_the_runtime_to_classify(wire: Wire) -> None:
    wire.status = 400
    with pytest.raises(httpx.HTTPStatusError):
        await make().read_contact(ReadContactInput(record_id=CONTACT), trace_id=TRACE)


async def test_a_gateway_error_on_a_read_is_transient(wire: Wire) -> None:
    wire.status = 503
    with pytest.raises(SalesforceTransientError):
        await make().read_contact(ReadContactInput(record_id=CONTACT), trace_id=TRACE)
    with pytest.raises(SalesforceTransientError):
        await make().internal_execute(ReadContactInput(record_id=CONTACT), trace_id=TRACE)


async def test_a_gateway_error_on_a_write_is_not_retried(wire: Wire) -> None:
    # A write may already have run, so a retry could create the record twice.
    wire.status = 503
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await make().create_contact(CreateContactInput(LastName="Doe"), trace_id=TRACE)
    assert not isinstance(caught.value, SalesforceTransientError)


# ---- the shapes LLM callers send for the lead and contact actions -----------------------------


@pytest.mark.parametrize(
    ("normalize", "args", "fields"),
    [
        (
            normalize_salesforce_update_contact,
            {"contact_id": CONTACT, "first_name": "Jane", "email": "j@example.com"},
            {"FirstName": "Jane", "Email": "j@example.com"},
        ),
        (
            normalize_salesforce_update_contact,
            {"id": CONTACT, "fields": {"last_name": "Roe"}},
            {"LastName": "Roe"},
        ),
        (
            normalize_salesforce_update_lead,
            {"lead_id": LEAD, "company": "Acme", "first_name": "Al"},
            {"Company": "Acme", "FirstName": "Al"},
        ),
        (
            normalize_salesforce_update_lead,
            {"recordId": LEAD, "fields": "not a map", "Company": "Acme"},
            {"Company": "Acme"},
        ),
    ],
)
def test_update_arguments_are_coalesced_to_a_record_id_and_fields(
    normalize: Any, args: dict[str, Any], fields: dict[str, Any]
) -> None:
    normalize(args)
    assert args["record_id"] in (LEAD, CONTACT)
    assert args["fields"] == fields
    assert not {"contact_id", "lead_id", "id", "recordId"} & set(args)


@pytest.mark.parametrize(
    ("normalize", "key", "record_id"),
    [
        (normalize_salesforce_read_delete_contact, "contact_id", CONTACT),
        (normalize_salesforce_read_delete_contact, "recordId", CONTACT),
        (normalize_salesforce_read_delete_lead, "lead_id", LEAD),
        (normalize_salesforce_read_delete_lead, "id", LEAD),
    ],
)
def test_read_and_delete_arguments_are_coalesced_to_a_record_id(
    normalize: Any, key: str, record_id: str
) -> None:
    args = {key: record_id}
    normalize(args)
    assert args["record_id"] == record_id and key not in args


def test_the_models_accept_the_alias_shapes_and_check_the_id() -> None:
    assert ReadLeadInput.model_validate({"lead_id": LEAD}).record_id == LEAD
    assert DeleteLeadInput.model_validate({"id": LEAD}).record_id == LEAD
    assert ReadContactInput.model_validate({"contact_id": CONTACT}).record_id == CONTACT
    assert DeleteContactInput.model_validate({"recordId": CONTACT}).record_id == CONTACT
    assert UpdateLeadInput.model_validate({"lead_id": LEAD, "company": "Acme"}).fields == {
        "Company": "Acme"
    }
    assert UpdateContactInput.model_validate({"id": CONTACT, "email": "j@example.com"}).fields == {
        "Email": "j@example.com"
    }
    for model in (ReadLeadInput, DeleteLeadInput, ReadContactInput, DeleteContactInput):
        with pytest.raises(ValidationError):
            model.model_validate({})
        with pytest.raises(ValidationError, match="Invalid Salesforce record_id"):
            model.model_validate({"record_id": "short"})
        with pytest.raises(ValidationError):
            model.model_validate("not a dict")
    for model in (UpdateLeadInput, UpdateContactInput):
        with pytest.raises(ValidationError):
            model.model_validate({"fields": {"A": 1}})
        with pytest.raises(ValidationError):
            model.model_validate("not a dict")
        with pytest.raises(ValidationError, match="Invalid Salesforce record_id"):
            model.model_validate({"record_id": "short", "fields": {"A": 1}})
        with pytest.raises(ValidationError, match="at least one field"):
            model.model_validate({"record_id": LEAD})
    assert CreateContactInput(LastName="Doe", AccountId="001000000000001AAA")


async def test_a_create_is_sent_as_json_not_the_providers_fhir_type(wire: Wire) -> None:
    # Regression: the OAuth provider's Content-Type (application/fhir+json) made Salesforce answer 415.
    wire.status, wire.json = 201, {"id": "001000000000001AAA", "success": True}
    result = await make().run(
        {"action": "create_record", "sobject": "Account", "fields": {"Name": "x"}}
    )
    assert result.success
    assert wire.last.headers["content-type"] == "application/json"
    assert wire.last.headers["accept"] == "application/json"
    await make().create_lead(CreateLeadInput(LastName="Smith", Company="Acme"), trace_id=TRACE)
    assert wire.last.headers["content-type"] == "application/json"


# ---- path values cannot change the path -------------------------------------------------------


def _path_fields() -> list[tuple[str, str]]:
    out = []
    for row in ROWS:
        template = getattr(SalesforceConnector, row[0])._http_path
        out += [(row[0], name) for name in row[1] if "{" + name + "}" in template]
    return out


@pytest.mark.parametrize(("action", "field"), _path_fields(), ids=lambda v: str(v))
@pytest.mark.parametrize("bad", ["..", ".", "a\x00b"])
def test_a_path_value_cannot_be_a_dot_segment(action: str, field: str, bad: str) -> None:
    # httpx resolves "." and ".." in a URL, so such a value would send the call to another path.
    from node_wire_salesforce import schema

    row = next(r for r in ROWS if r[0] == action)
    cls = getattr(schema, "".join(p.capitalize() for p in action.split("_")) + "Input")
    with pytest.raises(ValidationError):
        cls.model_validate({"action": action, **{**row[1], field: bad}})


def test_a_dots_inside_a_value_are_fine_and_quoted() -> None:
    from node_wire_salesforce.schema import GetNamedLayoutInput

    assert GetNamedLayoutInput(sobject="Account", layout_name="a.b").layout_name == "a.b"
    assert GetNamedLayoutInput(sobject="Account", layout_name="...").layout_name == "..."


# ---- error messages carry only Salesforce's errorCode and message -----------------------------


async def test_only_the_error_code_and_message_reach_the_exception(wire: Wire) -> None:
    wire.status = 400
    wire.json = [
        {
            "message": "duplicate value found",
            "errorCode": "DUPLICATE_VALUE",
            "fields": ["Email"],
            "extra": "private@example.com",
        }
    ]
    result = await make().run({"action": "get_limits"})
    assert result.message == "Salesforce answered 400: DUPLICATE_VALUE: duplicate value found"
    assert "private@example.com" not in result.message and "Email" not in result.message


async def test_error_messages_are_cut_short(wire: Wire) -> None:
    wire.status = 400
    wire.json = [{"message": "x" * 500, "errorCode": f"E{i}"} for i in range(5)]
    result = await make().run({"action": "get_limits"})
    assert result.message.count("; ") == 2 and "E3" not in result.message
    assert result.message.count("x") == 600  # 3 errors, 200 characters each


@pytest.mark.parametrize(
    "body",
    [
        b"<html>secret page private@example.com</html>",
        b"[1, 2]",
        b'"private@example.com"',
        b'{"error": "private@example.com"}',
    ],
)
async def test_other_error_bodies_are_left_out(wire: Wire, body: bytes) -> None:
    wire.status, wire.content = 400, body
    result = await make().run({"action": "get_limits"})
    assert result.message == "Salesforce answered 400"


async def test_a_single_error_object_is_read_too(wire: Wire) -> None:
    wire.status = 404
    wire.json = {"message": "The requested resource does not exist", "errorCode": "NOT_FOUND"}
    result = await make().run({"action": "get_limits"})
    assert (
        result.message
        == "Salesforce answered 404: NOT_FOUND: The requested resource does not exist"
    )
