# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for --wire connectors.yaml + sample.env edits."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from nw_connector_builder.derive.auth import ConnectorAuthPlan
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


def test_wire_sample_env_prefills_secret_defaults(tmp_path: Path) -> None:
    path = tmp_path / "sample.env"
    wire_sample_env(
        path,
        "microsoft_teams",
        secret_keys=["MICROSOFT_TEAMS_TOKEN_URL", "MICROSOFT_TEAMS_CLIENT_ID"],
        secret_defaults={"MICROSOFT_TEAMS_TOKEN_URL": "https://idp.example.com/token"},
    )
    text = path.read_text(encoding="utf-8")
    assert "MICROSOFT_TEAMS_TOKEN_URL=https://idp.example.com/token" in text
    # No default supplied for this one -> blank placeholder, same as before.
    assert "MICROSOFT_TEAMS_CLIENT_ID=" in text


def test_apply_wire_multiple_secret_keys_with_defaults(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    yaml_path = tmp_path / "config" / "connectors.yaml"
    yaml_path.write_text("connectors: {}\n", encoding="utf-8")
    apply_wire(
        tmp_path,
        "microsoft_teams",
        base_url="https://graph.microsoft.com/v1.0",
        auth_block={
            "provider": "oauth2",
            "grant_method": "refresh_token",
            "token_url_secret": "MICROSOFT_TEAMS_TOKEN_URL",
            "client_id_secret": "MICROSOFT_TEAMS_CLIENT_ID",
            "client_secret_secret": "MICROSOFT_TEAMS_CLIENT_SECRET",
            "refresh_token_secret": "MICROSOFT_TEAMS_REFRESH_TOKEN",
        },
        secret_keys=[
            "MICROSOFT_TEAMS_TOKEN_URL",
            "MICROSOFT_TEAMS_CLIENT_ID",
            "MICROSOFT_TEAMS_CLIENT_SECRET",
            "MICROSOFT_TEAMS_REFRESH_TOKEN",
        ],
        secret_defaults={
            "MICROSOFT_TEAMS_TOKEN_URL": "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        },
    )
    env = (tmp_path / "sample.env").read_text(encoding="utf-8")
    assert (
        "MICROSOFT_TEAMS_TOKEN_URL=https://login.microsoftonline.com/common/oauth2/v2.0/token"
        in env
    )
    assert "MICROSOFT_TEAMS_CLIENT_ID=" in env
    assert "MICROSOFT_TEAMS_CLIENT_SECRET=" in env
    assert "MICROSOFT_TEAMS_REFRESH_TOKEN=" in env


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
        secret_keys=["PET_STORE_API_KEY"],
    )
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert data["connectors"]["pet_store"]["base_url"] == base_url
    env = (tmp_path / "sample.env").read_text(encoding="utf-8")
    assert "NW_ALLOWED_CONNECTORS=pet_store" in env
    assert "PET_STORE_API_KEY=" in env


def test_apply_wire_with_extra_auth_plans_writes_auth_schemes_block(tmp_path: Path) -> None:
    """Multi-scheme wire: default `auth` plus named `auth_schemes` and secrets."""
    (tmp_path / "config").mkdir()
    yaml_path = tmp_path / "config" / "connectors.yaml"
    yaml_path.write_text("connectors: {}\n", encoding="utf-8")
    extra_plan = ConnectorAuthPlan(
        scheme_name="petstore_auth",
        scheme={"type": "oauth2", "flows": {"implicit": {}}},
        provider="static_token",
        secret_key="PET_STORE_ACCESS_TOKEN",
        yaml_block={
            "provider": "static_token",
            "secret_key": "PET_STORE_ACCESS_TOKEN",
            "header_name": "Authorization",
            "prefix": "Bearer",
            "host_supplied": True,
        },
        notes=["HOST-SUPPLIED CREDENTIAL: ..."],
        secret_keys=["PET_STORE_ACCESS_TOKEN"],
        tier="host_supplied",
    )
    apply_wire(
        tmp_path,
        "pet_store",
        base_url="https://petstore.swagger.io/v2",
        auth_block={"provider": "static_token", "secret_key": "PET_STORE_API_KEY", "header_name": "api_key"},
        secret_keys=["PET_STORE_API_KEY"],
        extra_auth_plans={"petstore_auth": extra_plan},
    )
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    block = data["connectors"]["pet_store"]
    assert block["auth"]["secret_key"] == "PET_STORE_API_KEY"
    assert block["auth_schemes"]["petstore_auth"]["secret_key"] == "PET_STORE_ACCESS_TOKEN"
    assert block["auth_schemes"]["petstore_auth"]["host_supplied"] is True

    env = (tmp_path / "sample.env").read_text(encoding="utf-8")
    assert "PET_STORE_API_KEY=" in env
    assert "PET_STORE_ACCESS_TOKEN=" in env
    # The default scheme's own secret must NOT be marked host-supplied (it's
    # self_managed) — only the extra scheme's secret gets the warning comment.
    lines = env.splitlines()
    default_idx = lines.index("PET_STORE_API_KEY=")
    extra_idx = lines.index("PET_STORE_ACCESS_TOKEN=")
    assert not lines[default_idx - 1].startswith("#")
    assert lines[extra_idx - 1].startswith("# PET_STORE_ACCESS_TOKEN: host-supplied")


def test_apply_wire_without_extra_auth_plans_omits_auth_schemes_block(tmp_path: Path) -> None:
    """Single-scheme connectors omit the `auth_schemes` key entirely."""
    (tmp_path / "config").mkdir()
    yaml_path = tmp_path / "config" / "connectors.yaml"
    yaml_path.write_text("connectors: {}\n", encoding="utf-8")
    apply_wire(
        tmp_path,
        "stripe",
        base_url="https://api.stripe.com/v1",
        auth_block={"provider": "static_token", "secret_key": "STRIPE_API_KEY"},
        secret_keys=["STRIPE_API_KEY"],
    )
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert "auth_schemes" not in data["connectors"]["stripe"]
