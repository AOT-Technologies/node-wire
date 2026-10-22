# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""A public spec builds as published: ``--auth``, ``--header``, and tolerance for its flaws."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
import yaml

from nw_connector_builder.options import (
    apply_auth_override,
    header_env_vars,
    parse_auth,
    parse_headers,
)
from nw_connector_builder.pipeline import UsageError, run_build

FIXTURES = Path(__file__).parent / "fixtures"
UNRULY = FIXTURES / "unruly_api.openapi.yaml"


def _mini_root(tmp_path: Path) -> Path:
    root = tmp_path / "node-wire"
    (root / "src").mkdir(parents=True)
    (root / "packages" / "connectors").mkdir(parents=True)
    (root / "config").mkdir(parents=True)
    (root / "nw-mcp-builder").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='node-wire'\n", encoding="utf-8")
    (root / "config" / "connectors.yaml").write_text("connectors: {}\n", encoding="utf-8")
    (root / "sample.env").write_text("NW_ALLOWED_CONNECTORS=\n", encoding="utf-8")
    return root


def _build(tmp_path: Path, **options: object) -> Path:
    root = _mini_root(tmp_path)
    code = run_build(
        spec=str(UNRULY),
        connector_id="unruly",
        node_wire_root=root,
        no_mcp=True,
        wire=True,
        report_path=tmp_path / "abort.json",
        **options,
    )
    assert code == 0
    return root


def _report(root: Path) -> dict:
    return json.loads((root / "packages" / "connectors" / "unruly" / "report.json").read_text())


def _entry(root: Path) -> dict:
    return yaml.safe_load((root / "config" / "connectors.yaml").read_text())["connectors"]["unruly"]


def _runtime_floor(root: Path) -> str:
    pyproject = (root / "packages" / "connectors" / "unruly" / "pyproject.toml").read_text()
    deps = tomllib.loads(pyproject)["project"]["dependencies"]
    (runtime,) = [d for d in deps if d.startswith("node-wire-runtime")]
    return runtime


def test_spec_flaws_cost_only_the_broken_operation(tmp_path: Path) -> None:
    root = _build(tmp_path)
    report = _report(root)
    assert report["summary"]["total_operations"] == 4
    assert report["summary"]["generated"] == 3
    (skipped,) = report["skipped"]
    assert skipped["path"] == "/broken"
    assert skipped["reason"].startswith("invalid OpenAPI:")
    assert "non-unique" in skipped["reason"]
    notes = " ".join(report["notes"])
    assert "not used as tests: render_raw" in notes
    schema = (root / "src" / "node_wire_unruly" / "schema.py").read_text()
    # The body field keeps its wire name but must not alias onto the discriminator.
    assert "action_param:" in schema
    assert "alias='action'" not in schema and 'alias="action"' not in schema


def test_without_auth_the_connector_is_anonymous(tmp_path: Path) -> None:
    root = _build(tmp_path)
    assert _entry(root).get("auth", {"provider": "none"})["provider"] == "none"
    assert _runtime_floor(root) == "node-wire-runtime>=1.1.0"


def test_auth_declares_the_scheme_the_spec_leaves_out(tmp_path: Path) -> None:
    root = _build(tmp_path, auth="bearer")
    assert _entry(root)["auth"] == {"provider": "static_token", "secret_key": "UNRULY_TOKEN"}
    assert "UNRULY_TOKEN=" in (root / "sample.env").read_text()
    assert any("--auth bearer" in n for n in _report(root)["notes"])


def test_auth_header_and_query_forms(tmp_path: Path) -> None:
    header = _entry(_build(tmp_path / "h", auth="header:X-Api-Key"))["auth"]
    assert header["provider"] == "static_token" and header["header_name"] == "X-Api-Key"
    query = _entry(_build(tmp_path / "q", auth="query:key"))["auth"]
    assert query["provider"] == "apikey_query" and query["name"] == "key"


def test_auth_is_refused_when_the_spec_declares_auth(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="only for specs that declare no auth"):
        run_build(
            spec=str(FIXTURES / "demo_pets.openapi.yaml"),
            connector_id="demo_pets",
            node_wire_root=_mini_root(tmp_path),
            no_mcp=True,
            auth="bearer",
        )


def test_headers_are_wired_documented_and_pin_the_runtime(tmp_path: Path) -> None:
    root = _build(tmp_path, headers=["Client-Id=${UNRULY_CLIENT_ID}", "X-Api-Version=2"])
    assert _entry(root)["headers"] == {
        "Client-Id": "${UNRULY_CLIENT_ID}",
        "X-Api-Version": "2",
    }
    env = (root / "sample.env").read_text()
    assert "# UNRULY_CLIENT_ID: sent as the Client-Id header" in env
    assert "UNRULY_CLIENT_ID=" in env
    assert _report(root)["headers"]["Client-Id"] == "${UNRULY_CLIENT_ID}"
    readme = (root / "src" / "node_wire_unruly" / "README.md").read_text()
    assert "### Fixed headers" in readme
    assert _runtime_floor(root) == "node-wire-runtime>=1.1.0"


@pytest.mark.parametrize("bad", ["Client-Id", "=x", "Authorization=Bearer x", "Bad Name=x"])
def test_bad_headers_are_usage_errors(tmp_path: Path, bad: str) -> None:
    with pytest.raises(UsageError):
        run_build(
            spec=str(UNRULY),
            connector_id="unruly",
            node_wire_root=_mini_root(tmp_path),
            no_mcp=True,
            headers=[bad],
        )


def test_option_parsing() -> None:
    assert parse_auth("Bearer") == {"type": "http", "scheme": "bearer"}
    assert parse_auth("header:Api-Key") == {"type": "apiKey", "in": "header", "name": "Api-Key"}
    for bad in ("basic", "header:", "cookie:sid", "query:bad name"):
        with pytest.raises(ValueError, match="bearer, header:<name> or query:<name>"):
            parse_auth(bad)
    with pytest.raises(ValueError, match="more than once"):
        parse_headers(["Client-Id=a", "client-id=b"])
    assert header_env_vars({"A": "${X}-${Y:d}", "B": "${X}"}) == {"X": "A", "Y": "A"}


def test_auth_override_detects_operation_level_security() -> None:
    doc = {"paths": {"/x": {"get": {"security": [{"key": []}]}}}}
    with pytest.raises(ValueError):
        apply_auth_override(doc, "bearer")
    open_doc: dict = {"paths": {"/x": {"get": {}}}}
    apply_auth_override(open_doc, "bearer")
    assert open_doc["security"] == [{"nw_auth": []}]
