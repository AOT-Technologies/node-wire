# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for --wire connectors.yaml + sample.env edits."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from nw_connector_builder.wire import WireError, apply_wire, wire_connectors_yaml, wire_sample_env


def test_wire_connectors_yaml_upserts(tmp_path: Path) -> None:
    path = tmp_path / "connectors.yaml"
    path.write_text("connectors:\n  other:\n    enabled: false\n", encoding="utf-8")
    auth = {"provider": "static_token", "secret_key": "PET_STORE_API_KEY"}
    base_url = "https://api.example.com"
    wire_connectors_yaml(
        path,
        "pet_store",
        base_url=base_url,
        auth_block=auth,
    )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["connectors"]["other"]["enabled"] is False
    block = data["connectors"]["pet_store"]
    assert block["base_url"] == base_url
    assert block["auth"]["provider"] == "static_token"


def test_wire_connectors_yaml_anonymous_omits_auth(tmp_path: Path) -> None:
    path = tmp_path / "connectors.yaml"
    path.write_text("connectors: {}\n", encoding="utf-8")
    base_url = "https://x.example"
    wire_connectors_yaml(path, "anon", base_url=base_url, auth_block={})
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    block = data["connectors"]["anon"]
    assert block["base_url"] == base_url
    assert "auth" not in block


def test_wire_connectors_yaml_missing_file(tmp_path: Path) -> None:
    with pytest.raises(WireError, match="not found"):
        wire_connectors_yaml(
            tmp_path / "missing.yaml",
            "x",
            base_url="https://example.invalid",
            auth_block={},
        )


def test_wire_sample_env_appends_allowlist_and_secrets(tmp_path: Path) -> None:
    path = tmp_path / "sample.env"
    path.write_text("NW_ALLOWED_CONNECTORS=slack\nFOO=1\n", encoding="utf-8")
    wire_sample_env(path, "pet_store", secret_keys=["PET_STORE_API_KEY", "FOO"])
    text = path.read_text(encoding="utf-8")
    assert "NW_ALLOWED_CONNECTORS=slack,pet_store" in text
    assert "PET_STORE_API_KEY=" in text
    # Existing FOO must not be duplicated
    assert text.count("FOO=") == 1


def test_wire_sample_env_creates_file_and_allowlist(tmp_path: Path) -> None:
    path = tmp_path / "sample.env"
    wire_sample_env(path, "pet_store", secret_keys=["PET_STORE_TOKEN"])
    text = path.read_text(encoding="utf-8")
    assert "NW_ALLOWED_CONNECTORS=pet_store" in text
    assert "PET_STORE_TOKEN=" in text


def test_apply_wire(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    yaml_path = tmp_path / "config" / "connectors.yaml"
    yaml_path.write_text("connectors: {}\n", encoding="utf-8")
    base_url = "https://api.example.com/v1"
    apply_wire(
        tmp_path,
        "pet_store",
        base_url=base_url,
        auth_block={"provider": "static_token", "secret_key": "PET_STORE_API_KEY"},
        secret_key="PET_STORE_API_KEY",
    )
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert data["connectors"]["pet_store"]["base_url"] == base_url
    env = (tmp_path / "sample.env").read_text(encoding="utf-8")
    assert "NW_ALLOWED_CONNECTORS=pet_store" in env
    assert "PET_STORE_API_KEY=" in env
