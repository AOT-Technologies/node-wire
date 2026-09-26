#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Advertised tool schemas must not carry Node Wire's wire-routing metadata.

``nw_in`` / ``nw_wire_name`` / ``nw_style`` / ``nw_explode`` / ``nw_media_type``
exist so ``split_params_by_location`` can place a value on the wire. Pydantic
copies them into the public JSON schema, where no caller can act on them — over a
third of a generated connector's advertised schema was this metadata.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from bindings.mcp_server.server import mcp_llm_safe_input_schema
from node_wire_runtime.rest import split_params_by_location

_EXTRA = {
    "nw_in": "query",
    "nw_wire_name": "user-id",
    "nw_style": "form",
    "nw_explode": True,
    "nw_media_type": None,
}


class _Input(BaseModel):
    action: Literal["ping"] = "ping"
    user_id: str = Field(..., alias="user-id", json_schema_extra=_EXTRA)
    body: dict[str, Any] | None = Field(
        None,
        json_schema_extra={
            "nw_in": "body",
            "nw_wire_name": "body",
            "nw_media_type": "application/json",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )


def _advertised() -> dict[str, Any]:
    return mcp_llm_safe_input_schema(_Input.model_json_schema())


def test_internal_keys_are_stripped_at_every_depth() -> None:
    props = _advertised()["properties"]
    assert not [k for k in props["user-id"] if k.startswith("nw_")]
    assert not [k for k in props["body"] if k.startswith("nw_")]


def test_declared_body_fields_survive_the_strip() -> None:
    """Only the nw_* keys go; anything else a connector advertises stays."""
    body = _advertised()["properties"]["body"]
    assert body["properties"] == {"text": {"type": "string"}}
    assert body["required"] == ["text"]
    assert body["type"] == "object"


def test_stripping_does_not_mutate_the_source_schema() -> None:
    original = _Input.model_json_schema()
    mcp_llm_safe_input_schema(original)
    assert original["properties"]["user-id"]["nw_in"] == "query"


def test_runtime_routing_still_reads_the_metadata() -> None:
    """The strip is advertised-copy only — the model fields keep their metadata."""
    path, query, header, body, media = split_params_by_location(
        _Input.model_validate({"user-id": "U1", "body": {"text": "hi"}})
    )
    assert query == {"user-id": ("U1", "form", True)}
    assert body == {"text": "hi"}
    assert media == "application/json"
    assert path == {} and header == {}
