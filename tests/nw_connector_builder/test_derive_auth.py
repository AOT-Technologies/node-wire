# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from nw_connector_builder.derive.auth import evaluate_operation_security
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
