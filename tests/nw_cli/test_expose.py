# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""``nw expose``: selectors, new, resolve --check, search."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pytest
import yaml
from pydantic import BaseModel
from typer.testing import CliRunner

from node_wire_runtime import BaseConnector
from node_wire_runtime.base_connector import nw_action
from node_wire_runtime.exposure import ActionExposure
from nw_cli.cli import app
from nw_cli.expose import ExposeError, expand, match

runner = CliRunner()


class _In(BaseModel):
    action: Literal["x"] = "x"


class _Out(BaseModel):
    ok: bool = True


class _Demo(BaseConnector):
    connector_id = "demo"
    output_model = _Out

    @nw_action("list_things", http_method="GET", http_path="/things", tags=("things",))
    async def list_things(self, params: _In, *, trace_id: str) -> _Out:
        return _Out()

    @nw_action("get_thing", http_method="GET", http_path="/things/{id}")
    async def get_thing(self, params: _In, *, trace_id: str) -> _Out:
        return _Out()

    @nw_action("delete_thing", http_method="DELETE", http_path="/things/{id}")
    async def delete_thing(self, params: _In, *, trace_id: str) -> _Out:
        return _Out()

    @nw_action("create_user", http_method="POST", http_path="/users")
    async def create_user(self, params: _In, *, trace_id: str) -> _Out:
        return _Out()

    @nw_action("list_thing_parts", http_method="GET", http_path="/things/{id}/parts/{part}")
    async def list_thing_parts(self, params: _In, *, trace_id: str) -> _Out:
        return _Out()


class _Plain(BaseConnector):
    """A hand-written connector: no HTTP method, path or tags on its actions."""

    connector_id = "plain"
    output_model = _Out

    @nw_action("ping")
    async def ping(self, params: _In, *, trace_id: str) -> _Out:
        return _Out()


METAS = _Demo.nw_action_metas()


@pytest.fixture
def root(tmp_path: Path):
    with (
        patch("nw_cli.expose.resolve_node_wire_root", return_value=tmp_path),
        patch("nw_cli.expose.load_connector_class", return_value=_Demo),
    ):
        yield tmp_path


def _run(*args: str):
    result = runner.invoke(app, ["expose", *args])
    return result


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ("method:get", {"list_things", "get_thing", "list_thing_parts"}),
        ("tag:things", {"list_things"}),
        ("*_thing", {"get_thing", "delete_thing"}),
        ("name:create_*", {"create_user"}),
        # * stays within one segment; ** crosses them; a trailing /** also matches the base.
        ("path:/things*", {"list_things"}),
        ("path:/things/*", {"get_thing", "delete_thing"}),
        ("path:/things/**", {"list_things", "get_thing", "delete_thing", "list_thing_parts"}),
        ("path:/things/*/parts/*", {"list_thing_parts"}),
        ("path:/**/parts/*", {"list_thing_parts"}),
        ("path:/things/{id}", {"get_thing", "delete_thing"}),
    ],
)
def test_selectors(selector: str, expected: set[str]) -> None:
    assert match(METAS, selector) == expected


def test_exclude_wins_and_empty_select_is_an_error() -> None:
    assert expand(METAS, ["path:/things/**"], ["method:DELETE", "list_thing_*"]) == [
        "get_thing",
        "list_things",
    ]
    with pytest.raises(ExposeError, match="matches no action"):
        expand(METAS, ["nope_*"], [])
    with pytest.raises(ExposeError, match="Bad selector"):
        expand(METAS, ["colour:red"], [])


def test_an_exclude_that_matches_nothing_is_an_error() -> None:
    """A mistyped exclude would otherwise expose what it was meant to hide."""
    with pytest.raises(ExposeError, match="Exclude 'delete_thnig' matches no action"):
        expand(METAS, ["path:/things/**"], ["delete_thnig"])


def test_search_is_not_a_stored_selector() -> None:
    """A ranking can shift when the connector changes, so it never decides a profile."""
    with pytest.raises(ExposeError, match="Bad selector"):
        match(METAS, "search:users")


@pytest.mark.parametrize("selector", ["path:/x", "method:GET", "tag:x"])
def test_metadata_selectors_on_a_connector_without_metadata_say_so(selector: str) -> None:
    with pytest.raises(ExposeError, match="needs action"):
        match(_Plain.nw_action_metas(), selector)
    assert match(_Plain.nw_action_metas(), "ping") == {"ping"}


def test_new_writes_a_profile_the_runtime_accepts(root: Path, monkeypatch) -> None:
    result = _run("new", "demo", "ro", "-s", "method:GET")
    assert result.exit_code == 0, result.output
    doc = yaml.safe_load((root / "expose" / "demo" / "ro.yaml").read_text(encoding="utf-8"))
    assert doc["actions"] == ["get_thing", "list_thing_parts", "list_things"]
    assert doc["select"] == ["method:GET"]
    monkeypatch.setenv("NW_EXPOSE_DIR", str(root / "expose"))
    monkeypatch.setenv("NW_EXPOSE_PROFILE", "ro")
    exposure = ActionExposure.from_env([_Demo.__new__(_Demo)])
    assert exposure.allows("demo", "get_thing") and not exposure.allows("demo", "delete_thing")


def test_new_refuses_to_overwrite_and_needs_a_selector(root: Path) -> None:
    assert _run("new", "demo", "ro", "-s", "method:GET").exit_code == 0
    assert _run("new", "demo", "ro", "-s", "method:GET").exit_code != 0
    assert _run("new", "demo", "other").exit_code != 0  # no terminal, no --select


def test_resolve_check_detects_a_stale_list_and_resolve_fixes_it(root: Path) -> None:
    assert _run("new", "demo", "ro", "-s", "method:GET").exit_code == 0
    file = root / "expose" / "demo" / "ro.yaml"
    assert _run("resolve", "--check").exit_code == 0
    doc = yaml.safe_load(file.read_text(encoding="utf-8"))
    doc["actions"] = ["get_thing"]  # the connector gained list_things since
    file.write_text(yaml.safe_dump(doc), encoding="utf-8")
    stale = _run("resolve", "--check")
    assert stale.exit_code == 1 and "ro.yaml" in stale.output
    assert yaml.safe_load(file.read_text(encoding="utf-8"))["actions"] == ["get_thing"]
    assert _run("resolve").exit_code == 0
    assert yaml.safe_load(file.read_text(encoding="utf-8"))["actions"] == [
        "get_thing",
        "list_thing_parts",
        "list_things",
    ]
    assert _run("resolve", "--check").exit_code == 0


def test_search_lists_ranked_actions(root: Path) -> None:
    result = _run("search", "demo", "users")
    assert result.exit_code == 0 and "create_user  POST /users" in result.output


def test_resolve_check_with_no_profiles_passes_but_a_named_one_must_exist(root: Path) -> None:
    assert _run("resolve", "--check").exit_code == 0
    assert _run("resolve", "demo", "ro").exit_code != 0


def test_a_bad_selector_is_reported_as_a_user_error_not_a_bug(root: Path) -> None:
    result = _run("new", "demo", "ro", "-s", "method:GET", "-x", "delete_thnig")
    assert result.exit_code != 0
    assert "matches no action" in result.output
    assert "bug in nw" not in result.output and "unexpected" not in result.output


@pytest.mark.parametrize(
    ("body", "message"),
    [("actions: [unclosed\n", "Cannot parse exposure profile"), ("- foo\n", "expected a mapping")],
)
def test_a_malformed_profile_is_reported_as_a_user_error_not_a_bug(
    root: Path, body: str, message: str
) -> None:
    file = root / "expose" / "demo" / "ro.yaml"
    file.parent.mkdir(parents=True)
    file.write_text(body, encoding="utf-8")
    result = _run("resolve", "--check")
    assert result.exit_code != 0
    assert message in result.output and "ro.yaml" in result.output
    assert "bug in nw" not in result.output and "unexpected" not in result.output
