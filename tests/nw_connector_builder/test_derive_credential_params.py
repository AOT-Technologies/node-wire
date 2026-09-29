# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Spec parameters that carry the credential are not exposed as call arguments.

Slack's spec declares a ``token`` header parameter on every operation. Generated
verbatim, the MCP tool asks its caller for a Slack token — so a model refuses to
act until a human pastes a secret into a tool call, while the connector already
holds that credential and attaches it itself.
"""

from __future__ import annotations

from typing import Any

from nw_connector_builder.derive.operations import derive_operations


def _doc(params: list[dict[str, Any]], *, anonymous: bool = False) -> dict[str, Any]:
    op: dict[str, Any] = {
        "operationId": "ping",
        "parameters": params,
        "responses": {"200": {"description": "ok"}},
    }
    if anonymous:
        op["security"] = []
    return {
        "openapi": "3.0.3",
        "info": {"title": "t", "version": "1"},
        "servers": [{"url": "https://api.example.com"}],
        "security": [{"apiKeyAuth": []}],
        "paths": {"/ping": {"get": op}},
        "components": {
            "securitySchemes": {
                "apiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-Api-Key"}
            }
        },
    }


def _fields(doc: dict[str, Any]) -> list[str]:
    result = derive_operations(doc, connector_id="acme")
    return [p.wire_name for p in result.actions[0].params]


def test_token_header_is_not_exposed_as_an_argument() -> None:
    doc = _doc(
        [
            {"name": "token", "in": "header", "required": True, "schema": {"type": "string"}},
            {"name": "channel", "in": "query", "schema": {"type": "string"}},
        ]
    )
    assert _fields(doc) == ["channel"]


def test_credential_query_parameters_are_dropped_too() -> None:
    doc = _doc(
        [
            {"name": "api_key", "in": "query", "schema": {"type": "string"}},
            {"name": "limit", "in": "query", "schema": {"type": "integer"}},
        ]
    )
    assert _fields(doc) == ["limit"]


def test_drop_is_reported() -> None:
    doc = _doc([{"name": "token", "in": "header", "schema": {"type": "string"}}])
    result = derive_operations(doc, connector_id="acme")
    assert any("duplicate the connector credential" in n and "token" in n for n in result.notes)


def test_lookalike_names_are_kept() -> None:
    """Matching is exact: pagination cursors are not credentials."""
    doc = _doc(
        [
            {"name": "page_token", "in": "query", "schema": {"type": "string"}},
            {"name": "next_token", "in": "query", "schema": {"type": "string"}},
            {"name": "cursor", "in": "query", "schema": {"type": "string"}},
            {"name": "token_id", "in": "query", "schema": {"type": "string"}},
        ]
    )
    assert _fields(doc) == ["page_token", "next_token", "cursor", "token_id"]


def test_path_parameters_are_never_dropped() -> None:
    """A path segment is part of the URL, not a credential the provider can inject."""
    doc = {
        "openapi": "3.0.3",
        "info": {"title": "t", "version": "1"},
        "servers": [{"url": "https://api.example.com"}],
        "security": [{"apiKeyAuth": []}],
        "paths": {
            "/t/{token}": {
                "get": {
                    "operationId": "getT",
                    "parameters": [
                        {
                            "name": "token",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
        "components": {
            "securitySchemes": {
                "apiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-Api-Key"}
            }
        },
    }
    assert _fields(doc) == ["token"]


def test_anonymous_actions_keep_their_credential_parameter() -> None:
    """With no AuthProvider on the call, nothing else would supply it."""
    doc = _doc([{"name": "token", "in": "header", "schema": {"type": "string"}}], anonymous=True)
    assert _fields(doc) == ["token"]
