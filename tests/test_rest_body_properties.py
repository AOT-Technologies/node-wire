#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""``nw_in="body_property"`` fields are reassembled into the request body.

Generated connectors expose each property of an object request body as its own
argument. The runtime rebuilds the body from those fields by wire name; the
whole-body ``nw_in="body"`` field used by hand-written connectors is unchanged.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, Field

from node_wire_runtime.rest import _encode_request_body, split_params_by_location


def _prop(wire: str, media: str = "application/json") -> dict[str, Any]:
    return {"nw_in": "body_property", "nw_wire_name": wire, "nw_media_type": media}


class _PostMessage(BaseModel):
    action: str = "chat_post_message"
    channel: str = Field(..., json_schema_extra=_prop("channel"))
    text: str | None = Field(None, json_schema_extra=_prop("text"))
    from_: str | None = Field(None, alias="from", json_schema_extra=_prop("from"))
    limit: int | None = Field(
        None,
        json_schema_extra={"nw_in": "query", "nw_wire_name": "limit"},
    )


def test_body_is_built_from_properties_by_wire_name() -> None:
    params = _PostMessage.model_validate({"channel": "C1", "text": "hi", "from": "bot", "limit": 5})
    path, query, header, body, media = split_params_by_location(params)
    assert body == {"channel": "C1", "text": "hi", "from": "bot"}
    assert media == "application/json"
    assert query == {"limit": (5, None, None)}


def test_omitted_optional_properties_are_not_sent() -> None:
    _, _, _, body, _ = split_params_by_location(_PostMessage(channel="C1"))
    assert body == {"channel": "C1"}


def test_form_properties_are_form_encoded() -> None:
    class _Form(BaseModel):
        channel: str = Field(
            ..., json_schema_extra=_prop("channel", "application/x-www-form-urlencoded")
        )
        unfurl: bool | None = Field(
            None, json_schema_extra=_prop("unfurl", "application/x-www-form-urlencoded")
        )

    _, _, _, body, media = split_params_by_location(_Form(channel="C1", unfurl=False))
    assert _encode_request_body(body, media) == {"data": {"channel": "C1", "unfurl": "false"}}


def test_no_properties_set_still_sends_an_empty_object() -> None:
    """The operation declares a body; an empty object is valid where a missing one may not be."""

    class _AllOptional(BaseModel):
        text: str | None = Field(None, json_schema_extra=_prop("text"))

    _, _, _, body, media = split_params_by_location(_AllOptional())
    assert body == {}
    assert media == "application/json"


def test_whole_body_and_body_properties_cannot_be_mixed() -> None:
    class _Mixed(BaseModel):
        body: dict[str, Any] | None = Field(
            None, json_schema_extra={"nw_in": "body", "nw_media_type": "application/json"}
        )
        text: str | None = Field(None, json_schema_extra=_prop("text"))

    with pytest.raises(ValueError, match="body_property"):
        split_params_by_location(_Mixed(body={"a": 1}, text="hi"))


def test_properties_must_agree_on_media_type() -> None:
    class _Split(BaseModel):
        a: str = Field(..., json_schema_extra=_prop("a", "application/json"))
        b: str = Field(..., json_schema_extra=_prop("b", "application/x-www-form-urlencoded"))

    with pytest.raises(ValueError, match="media type"):
        split_params_by_location(_Split(a="1", b="2"))
