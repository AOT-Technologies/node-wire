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
        ("method:get", {"list_things", "get_thing"}),
        ("path:/things*", {"list_things", "get_thing", "delete_thing"}),
        ("tag:things", {"list_things"}),
        ("*_thing", {"get_thing", "delete_thing"}),
        ("name:create_*", {"create_user"}),
        ("search:users", {"create_user"}),
    ],
)
def test_selectors(selector: str, expected: set[str]) -> None:
    assert match(METAS, selector) == expected


def test_exclude_wins_and_empty_select_is_an_error() -> None:
    assert expand(METAS, ["path:/things*"], ["method:DELETE"]) == ["get_thing", "list_things"]
    with pytest.raises(ExposeError, match="matches no action"):
        expand(METAS, ["nope_*"], [])
    with pytest.raises(ExposeError, match="Bad selector"):
        expand(METAS, ["colour:red"], [])


def test_new_writes_a_profile_the_runtime_accepts(root: Path, monkeypatch) -> None:
    result = _run("new", "demo", "ro", "-s", "method:GET")
    assert result.exit_code == 0, result.output
    doc = yaml.safe_load((root / "expose" / "demo" / "ro.yaml").read_text(encoding="utf-8"))
    assert doc["actions"] == ["get_thing", "list_things"]
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
        "list_things",
    ]
    assert _run("resolve", "--check").exit_code == 0


def test_search_lists_ranked_actions(root: Path) -> None:
    result = _run("search", "demo", "users")
    assert result.exit_code == 0 and "create_user  POST /users" in result.output


def test_resolve_check_with_no_profiles_passes_but_a_named_one_must_exist(root: Path) -> None:
    assert _run("resolve", "--check").exit_code == 0
    assert _run("resolve", "demo", "ro").exit_code != 0
