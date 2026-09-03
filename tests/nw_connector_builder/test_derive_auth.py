# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from nw_connector_builder.derive.auth import (
    build_auth_plan,
    choose_connector_scheme,
    evaluate_operation_security,
)
from nw_connector_builder.derive.operations import derive_operations
from nw_connector_builder.load import load_openapi_document

FIXTURES = Path(__file__).parent / "fixtures"


def test_auth_anonymous_vs_optional() -> None:
    schemes = {
        "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
    }
    fp = "apiKey:header:X-API-Key"
    assert evaluate_operation_security([], None, schemes, fp).mode == "anonymous"
    assert evaluate_operation_security([{}], None, schemes, fp).mode == "optional"


def test_auth_and_multi_and_oauth_skip() -> None:
    schemes = {
        "A": {"type": "apiKey", "in": "header", "name": "X"},
        "O": {"type": "oauth2", "flows": {}},
    }
    fp = "apiKey:header:X"
    d = evaluate_operation_security([{"A": [], "O": []}], None, schemes, fp)
    assert d.mode == "and_multi"
    d2 = evaluate_operation_security([{"O": []}], None, schemes, fp)
    assert d2.mode == "unsupported"


def test_derive_demo_pets() -> None:
    doc, _ = load_openapi_document(str(FIXTURES / "demo_pets.openapi.yaml"))
    result = derive_operations(doc, connector_id="demo_pets")
    names = {a.name for a in result.actions}
    assert "get_pet" in names
    assert "create_pet" in names
    assert "health_check" in names
    health = next(a for a in result.actions if a.name == "health_check")
    assert health.auth is False
    assert result.auth_plan.provider == "static_token"
    assert result.default_base_url == "https://api.example.com/v1"


@pytest.mark.parametrize(
    ("schemes", "chosen", "provider", "secret_key"),
    [
        (
            {"K": {"type": "apiKey", "in": "header", "name": "X-API-Key"}},
            "K",
            "static_token",
            "PET_STORE_API_KEY",
        ),
        (
            {"K": {"type": "apiKey", "in": "query", "name": "api_key"}},
            "K",
            "apikey_query",
            "PET_STORE_API_KEY",
        ),
        (
            {"B": {"type": "http", "scheme": "bearer"}},
            "B",
            "static_token",
            "PET_STORE_TOKEN",
        ),
        (
            {"B": {"type": "http", "scheme": "basic"}},
            "B",
            "static_token",
            "PET_STORE_BASIC_AUTH",
        ),
    ],
)
def test_build_auth_plan_supported_schemes(
    schemes: dict,
    chosen: str,
    provider: str,
    secret_key: str,
) -> None:
    plan = build_auth_plan("pet_store", schemes, chosen)
    assert plan.provider == provider
    assert plan.secret_key == secret_key
    assert plan.scheme_name == chosen
    assert plan.yaml_block["provider"] == provider
    assert plan.yaml_block["secret_key"] == secret_key


def test_build_auth_plan_anonymous_when_unmapped() -> None:
    plan = build_auth_plan("pet_store", {}, None)
    assert plan.provider == "none"
    assert plan.secret_key == ""
    assert plan.yaml_block == {}
    assert any("anonymous" in n.lower() for n in plan.notes)


def test_choose_connector_scheme_from_document_security() -> None:
    schemes = {
        "ApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
        "Bearer": {"type": "http", "scheme": "bearer"},
    }
    doc = {"security": [{"ApiKey": []}], "paths": {}}
    assert choose_connector_scheme(doc, schemes) == "ApiKey"


def test_choose_connector_scheme_majority_vote() -> None:
    schemes = {
        "A": {"type": "apiKey", "in": "header", "name": "X"},
        "B": {"type": "http", "scheme": "bearer"},
    }
    doc = {
        "paths": {
            "/a": {"get": {"security": [{"A": []}]}},
            "/b": {"get": {"security": [{"B": []}]}},
            "/c": {"post": {"security": [{"B": []}]}},
            "/d": {"get": {"security": [{"B": []}]}},
        }
    }
    assert choose_connector_scheme(doc, schemes) == "B"
