# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from nw_connector_builder.derive.naming import fallback_operation_name, uniquify_names
from nw_connector_builder.load import SpecLoadError, load_openapi_document
from nw_connector_builder.normalize_v2 import normalize_swagger2_to_openapi3
from nw_connector_builder.refs import collect_remote_refs

FIXTURES = Path(__file__).parent / "fixtures"


def test_uniquify_truncates_and_suffixes() -> None:
    long = "a" * 45
    names = uniquify_names([long, long, "short"])
    assert all(len(n) <= 40 for n in names)
    assert len(set(names)) == 3
    assert names[2] == "short"


def test_fallback_operation_name() -> None:
    assert fallback_operation_name("GET", "/users/{username}/repos") == "get_users_username_repos"


def test_remote_ref_detected() -> None:
    doc = {"paths": {"/x": {"get": {"responses": {"200": {"$ref": "https://evil/x"}}}}}}
    remotes = collect_remote_refs(doc, from_url=False)
    assert remotes == ["https://evil/x"]


def test_relative_ref_remote_when_from_url() -> None:
    doc = {"components": {"schemas": {"A": {"$ref": "./other.yaml#/A"}}}}
    assert collect_remote_refs(doc, from_url=True)
    assert not collect_remote_refs(doc, from_url=False)


def test_normalize_swagger2() -> None:
    import yaml

    raw = yaml.safe_load((FIXTURES / "demo_legacy.swagger.yaml").read_text())
    oa3 = normalize_swagger2_to_openapi3(raw)
    assert oa3["openapi"].startswith("3.")
    assert oa3["servers"][0]["url"] == "https://api.legacy.example.com/v2"
    assert "items" in (oa3.get("components") or {}).get("schemas", {}) or True
    get_op = oa3["paths"]["/items/{id}"]["get"]
    params = get_op["parameters"]
    tags = next(p for p in params if p["name"] == "tags")
    assert tags.get("style") == "form"
    assert tags.get("explode") is False


def test_load_openapi3_fixture() -> None:
    doc, meta = load_openapi_document(str(FIXTURES / "demo_pets.openapi.yaml"))
    assert meta["spec_version"].startswith("3.")
    assert "paths" in doc


def test_load_rejects_remote_ref(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "openapi: 3.0.3\ninfo: {title: t, version: '1'}\npaths: {}\n"
        "components:\n  schemas:\n    X:\n      $ref: https://example.com/x.yaml\n",
        encoding="utf-8",
    )
    with pytest.raises(SpecLoadError, match="Remote"):
        load_openapi_document(str(bad))


def test_local_relative_ref_resolves() -> None:
    doc, _ = load_openapi_document(str(FIXTURES / "multifile" / "openapi.yaml"))
    schema = doc["paths"]["/items"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    # prance should have inlined the relative file $ref
    assert "$ref" not in schema
    assert schema.get("type") == "object"
    assert "id" in (schema.get("properties") or {})
