"""Tests for EnvSecretProvider and factory secret wiring."""
from __future__ import annotations

import sys
import types

import pytest

from bindings.factory import _build_secret_provider
from node_wire_runtime.secrets.base import EnvSecretProvider, SecretNotFoundError
from node_wire_runtime.secrets.chained import ChainedSecretProvider


def test_env_secret_provider_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_ENV_SECRET_LEGACY_EMPTY", raising=False)
    monkeypatch.delenv("MISSING_TEST_KEY_X", raising=False)
    p = EnvSecretProvider(legacy_empty_on_missing=False)
    with pytest.raises(SecretNotFoundError):
        p.get_secret("MISSING_TEST_KEY_X")


def test_env_secret_provider_legacy_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_TEST_KEY_X", raising=False)
    p = EnvSecretProvider(legacy_empty_on_missing=True)
    assert p.get_secret("MISSING_TEST_KEY_X") == ""


def test_build_secret_provider_default_env() -> None:
    p = _build_secret_provider()
    assert isinstance(p, EnvSecretProvider)


def test_build_secret_provider_aws_env_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chained AWS+env without importing real boto3 (fake ``secrets.aws`` module)."""
    monkeypatch.setenv("NW_SECRET_BACKEND", "aws_env")
    monkeypatch.setenv("NW_AWS_SECRETS_MANAGER_SECRET_ID", "test-secret")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("CHAIN_TEST_KEY", "from-env")

    class FakeAws:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def get_secret(self, key: str) -> str:
            raise SecretNotFoundError(key)

    fake_mod = types.ModuleType("node_wire_runtime.secrets.aws")
    fake_mod.AwsSecretsManagerProvider = FakeAws  # type: ignore[attr-defined]
    old = sys.modules.get("node_wire_runtime.secrets.aws")
    sys.modules["node_wire_runtime.secrets.aws"] = fake_mod
    try:
        out = _build_secret_provider()
    finally:
        if old is not None:
            sys.modules["node_wire_runtime.secrets.aws"] = old
        else:
            sys.modules.pop("node_wire_runtime.secrets.aws", None)

    assert isinstance(out, ChainedSecretProvider)
    assert out.get_secret("CHAIN_TEST_KEY") == "from-env"


def test_aws_env_requires_secret_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_SECRET_BACKEND", "aws_env")
    monkeypatch.delenv("NW_AWS_SECRETS_MANAGER_SECRET_ID", raising=False)
    with pytest.raises(ValueError, match="NW_AWS_SECRETS_MANAGER_SECRET_ID"):
        _build_secret_provider()
