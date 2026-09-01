#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Tenant store persistence + per-config secrets overlay."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from bindings.factory import ConnectorFactory
from bindings.rest_api.app import app, get_factory
from bindings.rest_api.tenant_store import (
    load_tenants,
    save_tenants,
    upsert_tenant_secrets,
)
from node_wire_runtime.secrets import OverlaySecretProvider, tenant_scoped_secret_key


def _factory() -> ConnectorFactory:
    return ConnectorFactory()


def test_overlay_resolves_before_env(monkeypatch: pytest.MonkeyPatch):
    overlay = OverlaySecretProvider.instance()
    key = tenant_scoped_secret_key(
        "acme", "google_drive", "GOOGLE_DRIVE_SA_JSON", config_name="test"
    )
    monkeypatch.setenv(key, "from-env")
    overlay.set_secret(key, "from-overlay")
    assert overlay.get_secret(key) == "from-overlay"


def test_drive_then_epic_same_tenant_config_name(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    monkeypatch.setenv("EPIC_FHIR_BASE_URL", "https://fhir.example/r4")
    monkeypatch.setenv("EPIC_TOKEN_URL", "https://auth.example/token")
    factory = _factory()
    app.dependency_overrides[get_factory] = lambda: factory
    headers = {"X-Tenant-ID": "acme"}
    try:
        client = TestClient(app)

        drive = client.post(
            "/v1/connectors/google_drive/configs",
            json={
                "name": "test",
                "default": True,
                "config": {},
                "auth": {
                    "provider": "service_account",
                    "sa_json_secret": "GOOGLE_DRIVE_SA_JSON",
                    "scopes": ["https://www.googleapis.com/auth/drive"],
                },
                "secrets": {"GOOGLE_DRIVE_SA_JSON": '{"type":"service_account"}'},
            },
            headers=headers,
        )
        assert drive.status_code == 201, drive.text

        epic = client.post(
            "/v1/connectors/fhir_epic/configs",
            json={
                "name": "test",
                "default": True,
                "config": {},
                "auth": {
                    "provider": "oauth2",
                    "grant_method": "private_key_jwt",
                    "token_url_secret": "EPIC_TOKEN_URL",
                    "client_id_secret": "EPIC_CLIENT_ID",
                    "private_key_secret": "EPIC_PRIVATE_KEY",
                    "kid_secret": "EPIC_KID",
                    "algorithm": "RS384",
                },
                "secrets": {
                    "EPIC_CLIENT_ID": "cid",
                    "EPIC_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
                    "EPIC_KID": "kid-1",
                },
            },
            headers=headers,
        )
        assert epic.status_code == 201, epic.text

        drive_list = client.get("/v1/connectors/google_drive/configs", headers=headers)
        epic_list = client.get("/v1/connectors/fhir_epic/configs", headers=headers)
        assert any(c["name"] == "test" for c in drive_list.json())
        assert any(c["name"] == "test" for c in epic_list.json())

        assert factory.store.has_config("acme", "google_drive")
        assert factory.store.has_config("acme", "fhir_epic")

        tenants = client.get("/v1/tenants")
        assert tenants.status_code == 200
        assert "acme" in tenants.json()["tenants"]

        keys = client.get(
            "/v1/connectors/fhir_epic/secrets?config_name=test",
            headers=headers,
        )
        assert keys.status_code == 200
        key_list = keys.json()["keys"]
        assert "EPIC_CLIENT_ID" in key_list
        assert "EPIC_TOKEN_URL" in key_list
        assert "epic_fhir_base_url" in key_list
        assert "cid" not in str(keys.json())

        assert not factory.store.has_config("acme", "slack")
    finally:
        app.dependency_overrides.clear()


def test_tenants_persist_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    path = tmp_path / "roundtrip.yaml"
    monkeypatch.setenv("NW_TENANTS_PATH", str(path))
    monkeypatch.setenv("EPIC_TOKEN_URL", "https://token.example")

    store_factory = _factory()
    upsert_tenant_secrets(
        "acme",
        "google_drive",
        {"GOOGLE_DRIVE_SA_JSON": '{"type":"service_account","client_email":"a@b.c"}'},
        config_name="test",
    )
    store_factory.store.create(
        "acme",
        "google_drive",
        {
            "name": "test",
            "default": True,
            "config": {},
            "auth": {"provider": "service_account", "sa_json_secret": "GOOGLE_DRIVE_SA_JSON"},
        },
    )
    save_tenants(store_factory.store)
    assert path.is_file()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "acme" in raw["tenants"]
    assert raw["secrets"]["acme"]["google_drive"]["test"]["GOOGLE_DRIVE_SA_JSON"] == (
        '{"type":"service_account","client_email":"a@b.c"}'
    )

    OverlaySecretProvider.instance().clear()
    fresh = _factory()
    load_tenants(fresh.store)
    assert fresh.store.has_config("acme", "google_drive")
    scoped = tenant_scoped_secret_key(
        "acme", "google_drive", "GOOGLE_DRIVE_SA_JSON", config_name="test"
    )
    assert OverlaySecretProvider.instance().get_secret(scoped) == (
        '{"type":"service_account","client_email":"a@b.c"}'
    )


def test_new_config_without_secrets_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    factory = _factory()
    app.dependency_overrides[get_factory] = lambda: factory
    headers = {"X-Tenant-ID": "acme"}
    try:
        client = TestClient(app)
        # Seed one config with secrets
        first = client.post(
            "/v1/connectors/google_drive/configs",
            json={
                "name": "test",
                "default": True,
                "config": {},
                "auth": {
                    "provider": "service_account",
                    "sa_json_secret": "GOOGLE_DRIVE_SA_JSON",
                },
                "secrets": {"GOOGLE_DRIVE_SA_JSON": '{"type":"service_account"}'},
            },
            headers=headers,
        )
        assert first.status_code == 201, first.text

        # Sibling config cannot reuse those secrets when none are supplied
        second = client.post(
            "/v1/connectors/google_drive/configs",
            json={
                "name": "test new 1",
                "default": False,
                "config": {},
                "auth": {
                    "provider": "service_account",
                    "sa_json_secret": "GOOGLE_DRIVE_SA_JSON",
                },
                "secrets": {},
            },
            headers=headers,
        )
        assert second.status_code == 400
        assert "GOOGLE_DRIVE_SA_JSON" in second.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_per_config_secrets_are_isolated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    factory = _factory()
    app.dependency_overrides[get_factory] = lambda: factory
    headers = {"X-Tenant-ID": "acme"}
    sa_a = '{"type":"service_account","client_email":"a@x"}'
    sa_b = '{"type":"service_account","client_email":"b@x"}'
    try:
        client = TestClient(app)
        assert (
            client.post(
                "/v1/connectors/google_drive/configs",
                json={
                    "name": "test",
                    "default": True,
                    "config": {},
                    "auth": {
                        "provider": "service_account",
                        "sa_json_secret": "GOOGLE_DRIVE_SA_JSON",
                    },
                    "secrets": {"GOOGLE_DRIVE_SA_JSON": sa_a},
                },
                headers=headers,
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/v1/connectors/google_drive/configs",
                json={
                    "name": "test new 1",
                    "default": False,
                    "config": {},
                    "auth": {
                        "provider": "service_account",
                        "sa_json_secret": "GOOGLE_DRIVE_SA_JSON",
                    },
                    "secrets": {"GOOGLE_DRIVE_SA_JSON": sa_b},
                },
                headers=headers,
            ).status_code
            == 201
        )

        key_a = tenant_scoped_secret_key(
            "acme", "google_drive", "GOOGLE_DRIVE_SA_JSON", config_name="test"
        )
        key_b = tenant_scoped_secret_key(
            "acme", "google_drive", "GOOGLE_DRIVE_SA_JSON", config_name="test new 1"
        )
        overlay = OverlaySecretProvider.instance()
        assert overlay.get_secret(key_a) == sa_a
        assert overlay.get_secret(key_b) == sa_b
        assert key_a != key_b
    finally:
        app.dependency_overrides.clear()


def test_secrets_put_rejects_missing_required(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    factory = _factory()
    app.dependency_overrides[get_factory] = lambda: factory
    try:
        client = TestClient(app)
        resp = client.put(
            "/v1/connectors/fhir_epic/secrets",
            json={"config_name": "demo", "secrets": {"EPIC_CLIENT_ID": "only-id"}},
            headers={"X-Tenant-ID": "acme"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 400
    assert "EPIC_PRIVATE_KEY" in resp.json()["detail"]


def test_secrets_put_rejects_bad_format(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    factory = _factory()
    app.dependency_overrides[get_factory] = lambda: factory
    try:
        client = TestClient(app)
        resp = client.put(
            "/v1/connectors/slack/secrets",
            json={"config_name": "demo", "secrets": {"SLACK_BOT_TOKEN": "not-a-bot-token"}},
            headers={"X-Tenant-ID": "acme"},
        )
        assert resp.status_code == 400
        assert "xoxb-" in resp.json()["detail"]

        pem_resp = client.put(
            "/v1/connectors/fhir_epic/secrets",
            json={
                "config_name": "demo",
                "secrets": {
                    "EPIC_CLIENT_ID": "cid-1",
                    "EPIC_PRIVATE_KEY": "not-a-pem",
                    "EPIC_KID": "kid-1",
                },
            },
            headers={"X-Tenant-ID": "acme"},
        )
        assert pem_resp.status_code == 400
        assert "PEM" in pem_resp.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_secrets_partial_update_keeps_existing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    monkeypatch.setenv("EPIC_TOKEN_URL", "https://token.example")
    monkeypatch.setenv("EPIC_FHIR_BASE_URL", "https://fhir.example")
    factory = _factory()
    app.dependency_overrides[get_factory] = lambda: factory
    headers = {"X-Tenant-ID": "acme"}
    pem = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    try:
        client = TestClient(app)
        first = client.put(
            "/v1/connectors/fhir_epic/secrets",
            json={
                "config_name": "demo",
                "secrets": {
                    "EPIC_CLIENT_ID": "cid-1",
                    "EPIC_PRIVATE_KEY": pem,
                    "EPIC_KID": "kid-1",
                },
            },
            headers=headers,
        )
        assert first.status_code == 200
        partial = client.put(
            "/v1/connectors/fhir_epic/secrets",
            json={"config_name": "demo", "secrets": {"EPIC_KID": "kid-2"}},
            headers=headers,
        )
        assert partial.status_code == 200
        keys = client.get(
            "/v1/connectors/fhir_epic/secrets?config_name=demo",
            headers=headers,
        )
        assert "EPIC_CLIENT_ID" in keys.json()["keys"]
        assert "EPIC_KID" in keys.json()["keys"]
        scoped = tenant_scoped_secret_key(
            "acme", "fhir_epic", "EPIC_CLIENT_ID", config_name="demo"
        )
        assert OverlaySecretProvider.instance().get_secret(scoped) == "cid-1"
        scoped_kid = tenant_scoped_secret_key(
            "acme", "fhir_epic", "EPIC_KID", config_name="demo"
        )
        assert OverlaySecretProvider.instance().get_secret(scoped_kid) == "kid-2"
    finally:
        app.dependency_overrides.clear()


def test_delete_config_clears_only_that_config_secrets(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    factory = _factory()
    app.dependency_overrides[get_factory] = lambda: factory
    headers = {"X-Tenant-ID": "acme"}
    try:
        client = TestClient(app)
        for name, token in (("keep", "xoxb-keep"), ("drop", "xoxb-drop")):
            created = client.post(
                "/v1/connectors/slack/configs",
                json={
                    "name": name,
                    "default": name == "keep",
                    "config": {},
                    "auth": {"provider": "static_token", "secret_key": "SLACK_BOT_TOKEN"},
                    "secrets": {"SLACK_BOT_TOKEN": token},
                },
                headers=headers,
            )
            assert created.status_code == 201, created.text

        deleted = client.delete("/v1/connectors/slack/configs/drop", headers=headers)
        assert deleted.status_code == 200

        keep_keys = client.get(
            "/v1/connectors/slack/secrets?config_name=keep",
            headers=headers,
        )
        assert "SLACK_BOT_TOKEN" in keep_keys.json()["keys"]
        drop_keys = client.get(
            "/v1/connectors/slack/secrets?config_name=drop",
            headers=headers,
        )
        assert drop_keys.json()["keys"] == []

        keep_scoped = tenant_scoped_secret_key(
            "acme", "slack", "SLACK_BOT_TOKEN", config_name="keep"
        )
        drop_scoped = tenant_scoped_secret_key(
            "acme", "slack", "SLACK_BOT_TOKEN", config_name="drop"
        )
        assert OverlaySecretProvider.instance().get_secret(keep_scoped) == "xoxb-keep"
        with pytest.raises(Exception):
            OverlaySecretProvider.instance().get_secret(drop_scoped)
    finally:
        app.dependency_overrides.clear()
