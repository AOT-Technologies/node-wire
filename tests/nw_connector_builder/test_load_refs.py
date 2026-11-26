# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Same-document ``$ref`` resolution matches prance, without re-copying shared schemas."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from prance.util.resolver import RefResolver

from nw_connector_builder.load import load_raw_document, resolve_and_validate
from nw_connector_builder.refs import resolve_local_refs

FIXTURES = Path(__file__).parent / "fixtures"


def _shared_ref_spec(depth: int) -> dict[str, Any]:
    """Each schema references the previous two: prance's work doubles per level."""
    schemas: dict[str, Any] = {"S0": {"type": "object", "properties": {"v": {"type": "string"}}}}
    schemas["S1"] = {"type": "object", "properties": {"a": {"$ref": "#/components/schemas/S0"}}}
    for i in range(2, depth):
        schemas[f"S{i}"] = {
            "type": "object",
            "properties": {
                "a": {"$ref": f"#/components/schemas/S{i - 1}"},
                "b": {"$ref": f"#/components/schemas/S{i - 2}"},
            },
        }
    top = f"#/components/schemas/S{depth - 1}"
    return {
        "openapi": "3.0.3",
        "info": {"title": "deep", "version": "1"},
        "paths": {
            "/x": {
                "get": {
                    "parameters": [{"$ref": "#/components/parameters/Q"}],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {"application/json": {"schema": {"$ref": top}}},
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": schemas,
            "parameters": {"Q": {"name": "q", "in": "query", "schema": {"type": "string"}}},
        },
    }


def _prance(doc: dict[str, Any], base: str) -> dict[str, Any]:
    resolver = RefResolver(doc, url=base)
    resolver.resolve_references()
    return resolver.specs


@pytest.mark.parametrize("name", ["demo_pets.openapi.yaml", "slack_web.openapi.yaml"])
def test_matches_prance_on_fixtures(name: str) -> None:
    doc, origin, _ = load_raw_document(str(FIXTURES / name))
    assert resolve_local_refs(doc) == _prance(doc, str(Path(origin).resolve()))


def test_matches_prance_on_deep_shared_refs(tmp_path: Path) -> None:
    doc = _shared_ref_spec(12)
    resolved = resolve_local_refs(doc)
    assert resolved == _prance(doc, str(tmp_path / "spec.json"))
    # Resolved once and shared, not copied per occurrence.
    s11 = resolved["components"]["schemas"]["S11"]["properties"]
    assert s11["a"]["properties"]["a"] is s11["b"]


def test_deep_shared_refs_resolve_without_blowup() -> None:
    """At depth 60 prance would copy ~10^12 nodes; shared resolution and repair stay linear."""
    from nw_connector_builder.normalize_v2 import sanitize_draft4_schemas

    doc = _shared_ref_spec(60)
    resolved = resolve_local_refs(doc)
    assert resolved["paths"]["/x"]["get"]["parameters"][0]["name"] == "q"
    assert sanitize_draft4_schemas(resolved) == {}


def test_operation_level_refs_get_fresh_containers() -> None:
    """derive annotates operation dicts in place, so only schemas are shared."""
    doc = _shared_ref_spec(3)
    doc["paths"]["/y"] = {"get": doc["paths"]["/x"]["get"]}
    doc["paths"]["/x"]["get"] = {"$ref": "#/paths/~1y/get"}
    resolved = resolve_local_refs(doc)
    assert resolved["paths"]["/x"]["get"] == resolved["paths"]["/y"]["get"]
    assert resolved["paths"]["/x"]["get"] is not resolved["paths"]["/y"]["get"]


def test_recursive_schema_is_cut_without_prance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A $ref cycle is cut into a permissive object by the built-in resolver."""
    doc = _shared_ref_spec(3)
    doc["components"]["schemas"]["Node"] = {
        "type": "object",
        "properties": {"next": {"$ref": "#/components/schemas/Node"}},
    }
    with pytest.raises(ValueError, match="Recursion"):
        resolve_local_refs(copy.deepcopy(doc))

    def _no_prance(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("recursive specs should not fall back to prance")

    monkeypatch.setattr("prance.util.resolver.RefResolver.__init__", _no_prance)
    resolved = resolve_and_validate(doc, from_url=False, base_url=str(tmp_path / "s.json"))
    nested = resolved["components"]["schemas"]["Node"]["properties"]["next"]
    cut = nested["properties"]["next"]  # inlined one level before cutting, as prance does
    assert cut["x-recursive-ref"].endswith("#/components/schemas/Node")
    assert cut["additionalProperties"] is True


def _graph_like_spec() -> dict[str, Any]:
    """Self, mutual and three-way cycles, reached from paths and from each other."""

    def obj(**props: Any) -> dict[str, Any]:
        return {"type": "object", "properties": props}

    def ref(name: str) -> dict[str, Any]:
        return {"$ref": f"#/components/schemas/{name}"}

    schemas = {
        "Message": obj(id={"type": "string"}, replies={"type": "array", "items": ref("Message")}),
        "Team": obj(channels={"type": "array", "items": ref("Channel")}, owner=ref("User")),
        "Channel": obj(team=ref("Team"), messages={"type": "array", "items": ref("Message")}),
        "User": obj(manager=ref("User"), teams={"type": "array", "items": ref("Team")}),
        "A": obj(b=ref("B")),
        "B": obj(c=ref("C"), user=ref("User")),
        "C": obj(a=ref("A")),
    }
    paths = {
        f"/{name.lower()}": {
            "get": {
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {"application/json": {"schema": ref(name)}},
                    }
                }
            }
        }
        for name in ("Channel", "C", "User", "Message", "B")
    }
    return {
        "openapi": "3.0.3",
        "info": {"title": "cycles", "version": "1"},
        "paths": paths,
        "components": {"schemas": schemas},
    }


def test_cycle_cuts_match_prance_by_value(tmp_path: Path) -> None:
    base = str(tmp_path / "s.json")

    def placeholder(target: str) -> dict[str, Any]:
        return {"type": "object", "additionalProperties": True, "x-recursive-ref": target}

    doc = _graph_like_spec()
    resolver = RefResolver(
        copy.deepcopy(doc),
        url=base,
        recursion_limit_handler=lambda _limit, url, _recursions=(): placeholder(url.geturl()),
    )
    resolver.resolve_references()
    doc_url = Path(base).as_uri()
    ours = resolve_local_refs(doc, on_cycle=lambda r: placeholder(doc_url + r))
    assert ours == resolver.specs


@pytest.mark.parametrize(
    "node",
    [
        {"$ref": "other.yaml#/components/schemas/X"},  # another file
        {"$ref": "#/components/schemas/S0", "description": "sibling"},  # sibling keys
    ],
)
def test_falls_back_to_prance_for_other_shapes(node: dict[str, Any]) -> None:
    doc = _shared_ref_spec(3)
    doc["components"]["schemas"]["Odd"] = node
    assert resolve_local_refs(doc) is None


def test_valid_bundled_document_skips_inlined_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _inlined_validation(_resolved: dict[str, Any]) -> None:
        raise AssertionError("inlined validation should not run for a valid bundled document")

    monkeypatch.setattr(
        "nw_connector_builder.load._validate_dropping_invalid_parts", _inlined_validation
    )
    resolved = resolve_and_validate(
        _shared_ref_spec(8), from_url=False, base_url=str(tmp_path / "spec.json")
    )
    assert "$ref" not in str(resolved)


def test_invalid_bundled_document_still_drops_invalid_operation(tmp_path: Path) -> None:
    from nw_connector_builder.load import INVALID_OPERATIONS_KEY

    doc = _shared_ref_spec(4)
    doc["paths"]["/bad"] = {"get": {"responses": {"200": {"description": 1}}}}
    resolved = resolve_and_validate(doc, from_url=False, base_url=str(tmp_path / "spec.json"))
    assert "/bad" not in resolved["paths"] or "get" not in resolved["paths"]["/bad"]
    assert [op["path"] for op in resolved[INVALID_OPERATIONS_KEY]] == ["/bad"]
