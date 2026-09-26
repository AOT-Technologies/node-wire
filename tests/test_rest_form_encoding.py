#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Form-encoded bodies must carry JSON-shaped values, not Python reprs.

``str()`` on a JSON value is wrong for three types that reach this layer
routinely — booleans, nested objects/arrays, and ``None``. Slack rejects a
``blocks`` field encoded as a Python repr with ``invalid_arguments``.
"""

from __future__ import annotations

from typing import Any

import pytest

from node_wire_runtime.rest import _encode_request_body

FORM = "application/x-www-form-urlencoded"
MULTIPART = "multipart/form-data"


def _form(body: dict[str, Any]) -> dict[str, str]:
    return _encode_request_body(body, FORM)["data"]


def test_booleans_render_as_json_literals() -> None:
    assert _form({"unfurl_links": False, "as_user": True}) == {
        "unfurl_links": "false",
        "as_user": "true",
    }


def test_nested_values_render_as_json_not_python_repr() -> None:
    out = _form({"blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}]})
    assert out["blocks"] == '[{"type":"section","text":{"type":"mrkdwn","text":"hi"}}]'
    assert "'" not in out["blocks"]


def test_none_drops_the_field_instead_of_sending_the_string_none() -> None:
    assert _form({"channel": "C1", "thread_ts": None}) == {"channel": "C1"}


def test_scalars_are_unchanged() -> None:
    assert _form({"channel": "C1", "count": 3, "ratio": 1.5}) == {
        "channel": "C1",
        "count": "3",
        "ratio": "1.5",
    }


def test_multipart_form_fields_use_the_same_rendering() -> None:
    out = _encode_request_body({"ok": True, "meta": {"a": 1}, "skip": None}, MULTIPART)
    assert out["data"] == {"ok": "true", "meta": '{"a":1}'}


def test_non_object_form_body_still_rejected() -> None:
    with pytest.raises(ValueError, match="form-urlencoded body must be an object"):
        _encode_request_body(["not", "an", "object"], FORM)
