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


def test_uniquify_cuts_at_a_word_boundary() -> None:
    assert uniquify_names(["alpha_beta_gamma"], max_len=12) == ["alpha_beta"]


def test_uniquify_never_leaves_a_trailing_underscore() -> None:
    (name,) = uniquify_names(["admin_conversations_restrict_access_add_group"], max_len=40)
    assert name == "admin_conversations_restrict_access_add"


def test_uniquify_cuts_a_single_long_word_hard() -> None:
    assert uniquify_names(["a" * 45], max_len=40) == ["a" * 40]


def test_uniquify_suffix_keeps_the_word_boundary() -> None:
    names = uniquify_names(["alpha_beta_gamma", "alpha_beta_delta"], max_len=12)
    assert names == ["alpha_beta", "alpha_beta_1"]


def test_derived_action_names_use_the_mcp_tool_name_budget() -> None:
    """MCP tool names are `<connector_id>_<action>`, at most 64 characters."""
    from nw_connector_builder.derive.operations import derive_operations

    long_id = "admin_conversations_restrictAccess_removeGroup"  # 48 chars as snake_case
    doc = {
        "openapi": "3.0.3",
        "info": {"title": "t", "version": "1"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/x": {"get": {"operationId": long_id, "responses": {"204": {"description": "ok"}}}}
        },
    }
    (short_cid,) = [a.name for a in derive_operations(doc, connector_id="slack_web").actions]
    assert short_cid == "admin_conversations_restrict_access_remove_group"
    long_cid = "a_very_long_connector_identifier"
    (name,) = [a.name for a in derive_operations(doc, connector_id=long_cid).actions]
    assert len(f"{long_cid}_{name}") <= 64
    assert not name.endswith("_")


def test_yaml_specs_load_as_json_data(tmp_path: Path) -> None:
    """YAML 1.1 reads ``yes``/``no`` as booleans and dates as datetimes; JSON has neither.

    GitHub's spec has an emoji example keyed ``yes`` and ``no`` beside string keys, which
    made the content hash fail to sort the keys.
    """
    spec = tmp_path / "spec.yaml"
    spec.write_text(
        "openapi: 3.0.3\n"
        "info: {title: t, version: '1'}\n"
        "servers: [{url: 'https://api.example.com'}]\n"
        "paths:\n"
        "  /emojis:\n"
        "    get:\n"
        "      operationId: emojis_get\n"
        "      responses:\n"
        "        200:\n"
        "          description: ok\n"
        "          content:\n"
        "            application/json:\n"
        "              schema: {type: object, additionalProperties: {type: string}}\n"
        "              example: {yes: a, no: b, on: c, '+1': d, released: 2020-07-15}\n",
        encoding="utf-8",
    )
    doc, _meta = load_openapi_document(str(spec))
    response = doc["paths"]["/emojis"]["get"]["responses"]["200"]
    example = response["content"]["application/json"]["example"]
    assert example == {"yes": "a", "no": "b", "on": "c", "+1": "d", "released": "2020-07-15"}


def test_normalize_leaves_input_untouched_and_unshared() -> None:
    """Helpers work on shallow copies; the result must still own every container."""
    import copy

    shared = {"name": "ids", "in": "query", "type": "array", "items": {"type": "string"},
              "collectionFormat": "csv"}  # fmt: skip
    doc = {
        "swagger": "2.0",
        "info": {"title": "t", "version": "1"},
        "definitions": {"A": {"type": "object"}},
        "paths": {
            "/x": {
                "parameters": [shared],
                "get": {
                    "responses": {
                        "200": {
                            "description": "ok",
                            "schema": {"$ref": "#/definitions/A"},
                            "headers": {"H": {"type": "string"}},
                            "examples": {"application/json": {"a": 1}},
                        }
                    }
                },
                "delete": {"responses": {"204": {"description": "gone"}}},
            }
        },
    }
    before = copy.deepcopy(doc)
    out = normalize_swagger2_to_openapi3(doc)
    assert doc == before

    def containers(node: object, acc: set[int]) -> set[int]:
        if isinstance(node, (dict, list)):
            acc.add(id(node))
            for v in node.values() if isinstance(node, dict) else node:
                containers(v, acc)
        return acc

    assert not containers(out, set()) & containers(doc, set())
    get, delete = out["paths"]["/x"]["get"], out["paths"]["/x"]["delete"]
    assert get["parameters"] == delete["parameters"]
    assert get["parameters"][0] is not delete["parameters"][0]
    assert get["parameters"][0]["style"] == "form"
    ok = get["responses"]["200"]
    assert ok["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/A"}
    assert ok["content"]["application/json"]["example"] == {"a": 1}


def test_parse_spec_text_json_matches_yaml_loader() -> None:
    """The json fast path gives the same document the YAML spec loader does."""
    import json

    import yaml

    from nw_connector_builder.load import _SpecLoader, _string_keys, parse_spec_text

    for name in ("demo_pets.openapi.yaml", "slack_web.openapi.yaml"):
        text = (FIXTURES / name).read_text(encoding="utf-8")
        doc = _string_keys(yaml.load(text, Loader=_SpecLoader))  # nosec B506 - safe loader
        assert parse_spec_text(text) == doc
        assert parse_spec_text(json.dumps(doc)) == doc
    # Not strict JSON, but valid YAML flow style: still parsed.
    assert parse_spec_text("{a: 1, 'b': [x]}") == {"a": 1, "b": ["x"]}


def test_inlined_global_refs_share_no_containers() -> None:
    """Each ``#/parameters`` / ``#/responses`` occurrence gets its own containers.

    Later steps edit inlined parameters in place, so two operations must never share
    one dict, and the root tables must stay untouched.
    """
    import copy

    from nw_connector_builder.normalize_v2 import _inline_global_refs

    limit = {"name": "limit", "in": "query", "type": "integer", "enum": [1, 2]}
    not_found = {"description": "nf", "schema": {"type": "object", "properties": {"a": {}}}}
    op = {
        "parameters": [{"$ref": "#/parameters/Limit"}],
        "responses": {"404": {"$ref": "#/responses/NotFound"}},
    }
    doc = {
        "paths": {"/a": {"get": copy.deepcopy(op)}, "/b": {"get": copy.deepcopy(op)}},
        "parameters": {"Limit": limit},
        "responses": {"NotFound": not_found},
    }
    before = copy.deepcopy(doc)

    paths = _inline_global_refs(doc)

    a, b = paths["/a"]["get"], paths["/b"]["get"]
    assert a["parameters"][0] == limit and a["responses"]["404"] == not_found
    a["parameters"][0]["enum"].append(3)
    a["responses"]["404"]["schema"]["properties"]["x"] = {}
    assert b["parameters"][0]["enum"] == [1, 2]
    assert "x" not in b["responses"]["404"]["schema"]["properties"]
    assert doc == before
