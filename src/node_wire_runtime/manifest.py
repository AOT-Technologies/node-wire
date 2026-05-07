from __future__ import annotations

import copy
from typing import Any, Dict, List, Type

from pydantic import BaseModel

from node_wire_runtime import BaseConnector
from node_wire_runtime.models import ErrorCategory

# Bump when published input/output schema shape policy changes (MCP clients cache tools/list).
MCP_MANIFEST_CONTRACT_VERSION = "2"


def _schema_for(model: Type[BaseModel], *, strict: bool = True) -> Dict[str, Any]:
    schema = copy.deepcopy(model.model_json_schema())
    # Remove `action` from `required`: it is always auto-injected from the tool
    # name by invoke_tool (run_args.setdefault("action", action)), so LLMs must
    # not be required to pass it.  Keeping it as an optional property is fine.
    if "required" in schema:
        schema["required"] = [f for f in schema["required"] if f != "action"]
        if not schema["required"]:
            del schema["required"]
    # Only remove additionalProperties:false for alias-tolerant actions so that
    # common LLM aliases (e.g. mimeType → mime_type) are not rejected by the
    # MCP SDK's JSON-Schema validation layer before our normalization runs.
    # Strict actions retain additionalProperties:false for proper contract enforcement.
    if not strict:
        schema.pop("additionalProperties", None)
    return schema


def _strip_action_field_from_json_schema(schema: Dict[str, Any]) -> None:
    """
    Remove ``action`` from published input schemas for MCP/REST tool contracts.

    The binding injects ``action`` from the tool name or URL path; exposing it in
    ``inputSchema`` invites redundant or legacy values (e.g. ``upload``).
    Mutates ``schema`` in place (recurses into ``$defs``).
    """
    props = schema.get("properties")
    if isinstance(props, dict) and "action" in props:
        del props["action"]
    defs = schema.get("$defs")
    if isinstance(defs, dict):
        for sub in defs.values():
            if isinstance(sub, dict):
                _strip_action_field_from_json_schema(sub)
    # oneOf / anyOf branches
    for key in ("oneOf", "anyOf", "allOf"):
        branch = schema.get(key)
        if isinstance(branch, list):
            for item in branch:
                if isinstance(item, dict):
                    _strip_action_field_from_json_schema(item)


def _error_category_json_schema() -> Dict[str, Any]:
    """Inline enum from runtime ErrorCategory (single source of truth, no drift)."""
    return {
        "type": "string",
        "enum": [e.value for e in ErrorCategory],
    }


def _connector_response_schema(output_model: Type[BaseModel]) -> Dict[str, Any]:
    """
    Build the ConnectorResponse envelope schema with `data` typed to the
    specific output_model for this action.

    Built by hand (not from ConnectorResponse.model_json_schema()) to avoid
    $defs/$ref pollution from ErrorCategory and to keep the schema self-contained.
    """
    output_schema = _schema_for(output_model)
    return {
        "type": "object",
        "title": "ConnectorResponse",
        "properties": {
            "success": {"type": "boolean"},
            "data": {"anyOf": [output_schema, {"type": "null"}]},
            "error_code": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "error_category": {
                "anyOf": [
                    _error_category_json_schema(),
                    {"type": "null"},
                ]
            },
            "message": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "trace_id": {"type": "string"},
            "details": {},
        },
        "required": ["success", "trace_id"],
    }


def build_manifest(
    connectors: List[BaseConnector],
    *,
    strip_input_action: bool = True,
) -> List[Dict[str, Any]]:
    """
    One manifest entry per SDK @sdk_action (specific input/output schemas).

    :param strip_input_action: When True (default), omit ``action`` from the
        published ``input_schema`` properties. Bindings inject ``action`` from
        the MCP tool name or REST path; keeping it out of ``inputSchema`` avoids
        redundant/legacy client payloads.
    """
    manifest: List[Dict[str, Any]] = []
    for connector in connectors:
        cid = connector.connector_id
        for action_name, meta in type(connector).sdk_action_metas().items():
            input_schema = _schema_for(meta.input_model, strict=not meta.alias_tolerant)
            if strip_input_action:
                _strip_action_field_from_json_schema(input_schema)
            manifest.append(
                {
                    "connector_id": cid,
                    "action": action_name,
                    "input_schema": input_schema,
                    "output_schema": _connector_response_schema(meta.output_model),
                }
            )
    return manifest
