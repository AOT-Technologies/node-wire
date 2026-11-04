#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import logging
from typing import Literal
from pathlib import Path

import pytest
from pydantic import BaseModel

from node_wire_runtime import BaseConnector
from node_wire_runtime.base_connector import nw_action
from node_wire_runtime.errors import CATALOGUE, ErrorCode, ErrorCategory
from node_wire_runtime.exposure import ActionExposure, ExposureConfigError


class _ListIn(BaseModel):
    action: Literal["list_things"] = "list_things"


class _DeleteIn(BaseModel):
    action: Literal["delete_thing"] = "delete_thing"


class _CreateIn(BaseModel):
    action: Literal["create_thing"] = "create_thing"


class _Out(BaseModel):
    ok: bool = True


class _Demo(BaseConnector):
    connector_id = "demo"
    output_model = _Out

    @nw_action("list_things", http_method="GET", http_path="/things")
    async def list_things(self, params: _ListIn, *, trace_id: str) -> _Out:
        return _Out()

    @nw_action("delete_thing", http_method="DELETE", http_path="/things/{id}")
    async def delete_thing(self, params: _DeleteIn, *, trace_id: str) -> _Out:
        return _Out()

    @nw_action("create_thing", http_method="POST", http_path="/things")
    async def create_thing(self, params: _CreateIn, *, trace_id: str) -> _Out:
        return _Out()


class _Other(_Demo):
    connector_id = "other"


def _connector(cls=_Demo):
    return cls.__new__(cls)  # allows() and from_env() only read connector_id and the class


def _profile(root: Path, name: str, body: str, cid: str = "demo") -> None:
    (root / cid).mkdir(parents=True, exist_ok=True)
    (root / cid / f"{name}.yaml").write_text(body, encoding="utf-8")


@pytest.fixture()
def expose_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NW_EXPOSE_DIR", str(tmp_path))
    monkeypatch.delenv("NW_EXPOSE_STRICT_VERSION", raising=False)
    monkeypatch.delenv("NW_EXPOSE_PROFILE", raising=False)
    return tmp_path


def test_error_code_is_a_business_error():
    assert CATALOGUE[ErrorCode.ACTION_NOT_EXPOSED] is ErrorCategory.BUSINESS


def test_unset_profile_exposes_everything(expose_dir):
    exposure = ActionExposure.from_env([_connector()])
    assert exposure.allows("demo", "anything") and exposure.allows("nope", "x")
    assert exposure.summary() == {}


def test_only_listed_actions_are_allowed(expose_dir, monkeypatch):
    _profile(expose_dir, "ro", "profile: ro\nactions:\n  - list_things\n")
    monkeypatch.setenv("NW_EXPOSE_PROFILE", "ro")
    exposure = ActionExposure.from_env([_connector()])
    assert exposure.allows("demo", "list_things")
    assert not exposure.allows("demo", "delete_thing")
    assert not exposure.allows("unknown_connector", "list_things")


def test_summary_counts_by_http_method(expose_dir, monkeypatch):
    _profile(expose_dir, "p", "actions: [list_things, delete_thing]\n")
    monkeypatch.setenv("NW_EXPOSE_PROFILE", "p")
    count = ActionExposure.from_env([_connector()]).summary()["demo"]
    assert (count.exposed, count.total) == (2, 3)
    assert count.by_method == {"DELETE": 1, "GET": 1}


def test_connector_without_the_file_exposes_nothing(expose_dir, monkeypatch, caplog):
    _profile(expose_dir, "p", "actions: [list_things]\n")
    monkeypatch.setenv("NW_EXPOSE_PROFILE", "p")
    with caplog.at_level(logging.WARNING, logger="runtime.exposure"):
        exposure = ActionExposure.from_env([_connector(), _connector(_Other)])
    assert exposure.allows("demo", "list_things")
    assert not exposure.allows("other", "list_things")
    assert exposure.summary()["other"].exposed == 0
    assert "exposes nothing" in caplog.text


def test_unknown_profile_name_refuses_to_start(expose_dir, monkeypatch):
    monkeypatch.setenv("NW_EXPOSE_PROFILE", "missing")
    with pytest.raises(ExposureConfigError, match="no loaded connector"):
        ActionExposure.from_env([_connector()])


@pytest.mark.parametrize("name", ["../x", "a/b", "..", "a" + chr(92) + "b"])
def test_profile_must_be_a_name_not_a_path(expose_dir, monkeypatch, name):
    monkeypatch.setenv("NW_EXPOSE_PROFILE", name)
    with pytest.raises(ExposureConfigError, match="not a path"):
        ActionExposure.from_env([_connector()])


def test_unknown_action_is_rejected_with_a_suggestion(expose_dir, monkeypatch):
    _profile(expose_dir, "p", "actions: [list_thing]\n")
    monkeypatch.setenv("NW_EXPOSE_PROFILE", "p")
    with pytest.raises(ExposureConfigError, match="did you mean list_things"):
        ActionExposure.from_env([_connector()])


@pytest.mark.parametrize(
    "body",
    [
        "actions: [unclosed\n",
        "- just\n- a list\n",
        "actions: nope\n",
        "connector: other\nactions: []\n",
    ],
)
def test_invalid_files_refuse_to_start(expose_dir, monkeypatch, body):
    _profile(expose_dir, "p", body)
    monkeypatch.setenv("NW_EXPOSE_PROFILE", "p")
    with pytest.raises(ExposureConfigError):
        ActionExposure.from_env([_connector()])


def test_version_drift_warns_and_strict_refuses(expose_dir, monkeypatch, caplog):
    monkeypatch.setattr("node_wire_runtime.exposure._connector_version", lambda cls: "2.0.0")
    _profile(expose_dir, "p", "connector_version: 1.0.0\nactions: [list_things]\n")
    monkeypatch.setenv("NW_EXPOSE_PROFILE", "p")
    with caplog.at_level(logging.WARNING, logger="runtime.exposure"):
        assert ActionExposure.from_env([_connector()]).allows("demo", "list_things")
    assert "re-resolve" in caplog.text
    monkeypatch.setenv("NW_EXPOSE_STRICT_VERSION", "true")
    with pytest.raises(ExposureConfigError, match="re-resolve"):
        ActionExposure.from_env([_connector()])


def test_default_dir_is_expose_beside_the_config_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("NW_EXPOSE_DIR", raising=False)
    monkeypatch.setenv("NW_EXPOSE_PROFILE", "p")
    (tmp_path / "config").mkdir()
    _profile(tmp_path / "expose", "p", "actions: [list_things]\n")
    exposure = ActionExposure.from_env(
        [_connector()], config_path=str(tmp_path / "config" / "connectors.yaml")
    )
    assert exposure.allows("demo", "list_things")
