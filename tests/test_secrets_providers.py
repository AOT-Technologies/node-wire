#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for the cloud secret providers (AWS, Azure, GCP, Vault).

The cloud SDKs are not installed in the test environment, so each fixture
injects stub modules into ``sys.modules`` before importing the provider
module, and removes the provider module afterwards so other tests see a
clean import state.
"""

from __future__ import annotations

import importlib
import json
import sys
import types
from types import SimpleNamespace

import pytest

from node_wire_runtime.secrets.base import (
    EnvSecretProvider,
    SecretNotFoundError,
    SecretProvider,
    SecretProviderError,
)
from node_wire_runtime.secrets.chained import ChainedSecretProvider


# ---------------------------------------------------------------------------
# AWS
# ---------------------------------------------------------------------------


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch):
    """Import node_wire_runtime.secrets.aws against stub boto3/botocore."""
    state = SimpleNamespace(
        secret_string="{}",
        error=None,
        service=None,
        region=None,
        requested_secret_id=None,
    )

    class BotoCoreError(Exception):
        pass

    class ClientError(Exception):
        def __init__(self, error_response: dict, operation_name: str) -> None:
            super().__init__(error_response, operation_name)
            self.response = error_response
            self.operation_name = operation_name

    exc_mod = types.ModuleType("botocore.exceptions")
    exc_mod.BotoCoreError = BotoCoreError
    exc_mod.ClientError = ClientError
    botocore_mod = types.ModuleType("botocore")
    botocore_mod.exceptions = exc_mod

    class FakeClient:
        def get_secret_value(self, SecretId: str) -> dict:
            state.requested_secret_id = SecretId
            if state.error is not None:
                raise state.error
            return {"SecretString": state.secret_string}

    boto3_mod = types.ModuleType("boto3")

    def client(service: str, region_name: str | None = None) -> FakeClient:
        state.service = service
        state.region = region_name
        return FakeClient()

    boto3_mod.client = client

    monkeypatch.setitem(sys.modules, "boto3", boto3_mod)
    monkeypatch.setitem(sys.modules, "botocore", botocore_mod)
    monkeypatch.setitem(sys.modules, "botocore.exceptions", exc_mod)
    sys.modules.pop("node_wire_runtime.secrets.aws", None)
    mod = importlib.import_module("node_wire_runtime.secrets.aws")
    yield mod, state, exc_mod
    sys.modules.pop("node_wire_runtime.secrets.aws", None)


def test_aws_success_and_missing_key(aws) -> None:
    mod, state, _exc = aws
    state.secret_string = json.dumps({"epic_client_id": "abc123"})

    provider = mod.AwsSecretsManagerProvider("my-bundle", region="eu-west-1")

    assert state.service == "secretsmanager"
    assert state.region == "eu-west-1"
    assert state.requested_secret_id == "my-bundle"
    assert provider.get_secret("epic_client_id") == "abc123"
    with pytest.raises(SecretNotFoundError):
        provider.get_secret("nope")


def test_aws_resource_not_found_maps_to_secret_not_found(aws) -> None:
    mod, state, exc = aws
    state.error = exc.ClientError(
        {"Error": {"Code": "ResourceNotFoundException"}}, "GetSecretValue"
    )
    with pytest.raises(SecretNotFoundError):
        mod.AwsSecretsManagerProvider("missing-bundle")


def test_aws_other_client_error_maps_to_provider_error(aws) -> None:
    mod, state, exc = aws
    state.error = exc.ClientError({"Error": {"Code": "AccessDeniedException"}}, "GetSecretValue")
    with pytest.raises(SecretProviderError, match="AccessDeniedException"):
        mod.AwsSecretsManagerProvider("forbidden-bundle")


def test_aws_botocore_error_maps_to_provider_error(aws) -> None:
    mod, state, exc = aws
    state.error = exc.BotoCoreError("connection refused")
    with pytest.raises(SecretProviderError, match="AWS connection error"):
        mod.AwsSecretsManagerProvider("unreachable-bundle")


def test_aws_import_error_has_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """With boto3 absent, constructing the provider fails with an actionable hint.

    SDK imports are lazy (inside the constructor) so Cython-compiled modules and
    test stubs both resolve via ``sys.modules`` on each construction.
    """
    monkeypatch.delitem(sys.modules, "boto3", raising=False)
    monkeypatch.delitem(sys.modules, "botocore", raising=False)
    monkeypatch.delitem(sys.modules, "botocore.exceptions", raising=False)
    # Ensure a fresh module object is not required; construction re-imports boto3.
    mod = importlib.import_module("node_wire_runtime.secrets.aws")
    with pytest.raises(ImportError, match=r"node-wire-runtime\[aws\]"):
        mod.AwsSecretsManagerProvider("any-bundle")


# ---------------------------------------------------------------------------
# Azure
# ---------------------------------------------------------------------------


@pytest.fixture
def azure(monkeypatch: pytest.MonkeyPatch):
    """Import node_wire_runtime.secrets.azure against stub azure-* SDKs."""
    state = SimpleNamespace(
        secrets={},  # azure-name -> value
        error=None,
        credential_error=None,
        vault_url=None,
        requested_names=[],
    )

    class ResourceNotFoundError(Exception):
        pass

    class HttpResponseError(Exception):
        pass

    class DefaultAzureCredential:
        def __init__(self) -> None:
            if state.credential_error is not None:
                raise state.credential_error

    class SecretClient:
        def __init__(self, vault_url: str, credential: object) -> None:
            state.vault_url = vault_url

        def get_secret(self, name: str) -> SimpleNamespace:
            state.requested_names.append(name)
            if state.error is not None:
                raise state.error
            if name not in state.secrets:
                raise ResourceNotFoundError(name)
            return SimpleNamespace(value=state.secrets[name])

    identity_mod = types.ModuleType("azure.identity")
    identity_mod.DefaultAzureCredential = DefaultAzureCredential
    kv_secrets_mod = types.ModuleType("azure.keyvault.secrets")
    kv_secrets_mod.SecretClient = SecretClient
    core_exc_mod = types.ModuleType("azure.core.exceptions")
    core_exc_mod.ResourceNotFoundError = ResourceNotFoundError
    core_exc_mod.HttpResponseError = HttpResponseError

    azure_mod = types.ModuleType("azure")
    kv_mod = types.ModuleType("azure.keyvault")
    core_mod = types.ModuleType("azure.core")

    monkeypatch.setitem(sys.modules, "azure", azure_mod)
    monkeypatch.setitem(sys.modules, "azure.identity", identity_mod)
    monkeypatch.setitem(sys.modules, "azure.keyvault", kv_mod)
    monkeypatch.setitem(sys.modules, "azure.keyvault.secrets", kv_secrets_mod)
    monkeypatch.setitem(sys.modules, "azure.core", core_mod)
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", core_exc_mod)
    sys.modules.pop("node_wire_runtime.secrets.azure", None)
    mod = importlib.import_module("node_wire_runtime.secrets.azure")
    yield mod, state, core_exc_mod
    sys.modules.pop("node_wire_runtime.secrets.azure", None)


def test_azure_success_maps_underscores_to_hyphens(azure) -> None:
    mod, state, _exc = azure
    state.secrets["epic-client-id"] = "azure-value"

    provider = mod.AzureKeyVaultProvider("https://kv.example.vault.azure.net")

    assert state.vault_url == "https://kv.example.vault.azure.net"
    assert provider.get_secret("epic_client_id") == "azure-value"
    assert state.requested_names == ["epic-client-id"]


def test_azure_missing_secret_maps_to_secret_not_found(azure) -> None:
    mod, _state, _exc = azure
    provider = mod.AzureKeyVaultProvider("https://kv.example.vault.azure.net")
    with pytest.raises(SecretNotFoundError):
        provider.get_secret("absent_key")


def test_azure_none_value_maps_to_secret_not_found(azure) -> None:
    mod, state, _exc = azure
    state.secrets["empty-key"] = None
    provider = mod.AzureKeyVaultProvider("https://kv.example.vault.azure.net")
    with pytest.raises(SecretNotFoundError):
        provider.get_secret("empty_key")


def test_azure_http_error_maps_to_provider_error(azure) -> None:
    mod, state, exc = azure
    provider = mod.AzureKeyVaultProvider("https://kv.example.vault.azure.net")
    state.error = exc.HttpResponseError("503 upstream unavailable")
    with pytest.raises(SecretProviderError, match="Azure Key Vault HTTP error"):
        provider.get_secret("any_key")


def test_azure_init_failure_maps_to_provider_error(azure) -> None:
    mod, state, _exc = azure
    state.credential_error = RuntimeError("no credential chain available")
    with pytest.raises(SecretProviderError, match="Failed to initialise"):
        mod.AzureKeyVaultProvider("https://kv.example.vault.azure.net")


# ---------------------------------------------------------------------------
# GCP
# ---------------------------------------------------------------------------


@pytest.fixture
def gcp(monkeypatch: pytest.MonkeyPatch):
    """Import node_wire_runtime.secrets.gcp against stub google SDK modules."""
    state = SimpleNamespace(payload=b"{}", error=None, requested_name=None)

    class GoogleAPICallError(Exception):
        pass

    class NotFound(GoogleAPICallError):
        pass

    class SecretManagerServiceClient:
        def access_secret_version(self, request: dict) -> SimpleNamespace:
            state.requested_name = request["name"]
            if state.error is not None:
                raise state.error
            return SimpleNamespace(payload=SimpleNamespace(data=state.payload))

    sm_mod = types.ModuleType("google.cloud.secretmanager")
    sm_mod.SecretManagerServiceClient = SecretManagerServiceClient
    api_exc_mod = types.ModuleType("google.api_core.exceptions")
    api_exc_mod.NotFound = NotFound
    api_exc_mod.GoogleAPICallError = GoogleAPICallError
    api_core_mod = types.ModuleType("google.api_core")
    api_core_mod.exceptions = api_exc_mod

    # "google" and "google.cloud" may exist as real namespace packages; only
    # provide them if absent so we never clobber installed google packages.
    if "google" not in sys.modules:
        monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    if "google.cloud" not in sys.modules:
        monkeypatch.setitem(sys.modules, "google.cloud", types.ModuleType("google.cloud"))
    monkeypatch.setitem(sys.modules, "google.cloud.secretmanager", sm_mod)
    monkeypatch.setitem(sys.modules, "google.api_core", api_core_mod)
    monkeypatch.setitem(sys.modules, "google.api_core.exceptions", api_exc_mod)
    sys.modules.pop("node_wire_runtime.secrets.gcp", None)
    mod = importlib.import_module("node_wire_runtime.secrets.gcp")
    yield mod, state, api_exc_mod
    sys.modules.pop("node_wire_runtime.secrets.gcp", None)


def test_gcp_success_and_missing_key(gcp) -> None:
    mod, state, _exc = gcp
    state.payload = json.dumps({"db_password": "gcp-value"}).encode("utf-8")

    provider = mod.GcpSecretManagerProvider("proj-1", "bundle", version="7")

    assert state.requested_name == "projects/proj-1/secrets/bundle/versions/7"
    assert provider.get_secret("db_password") == "gcp-value"
    with pytest.raises(SecretNotFoundError):
        provider.get_secret("nope")


def test_gcp_default_version_is_latest(gcp) -> None:
    mod, state, _exc = gcp
    mod.GcpSecretManagerProvider("proj-1", "bundle")
    assert state.requested_name == "projects/proj-1/secrets/bundle/versions/latest"


def test_gcp_not_found_maps_to_secret_not_found(gcp) -> None:
    mod, state, exc = gcp
    state.error = exc.NotFound("no such secret")
    with pytest.raises(SecretNotFoundError):
        mod.GcpSecretManagerProvider("proj-1", "missing")


def test_gcp_api_error_maps_to_provider_error(gcp) -> None:
    mod, state, exc = gcp
    state.error = exc.GoogleAPICallError("permission denied")
    with pytest.raises(SecretProviderError, match="GCP Secret Manager error"):
        mod.GcpSecretManagerProvider("proj-1", "forbidden")


# ---------------------------------------------------------------------------
# HashiCorp Vault
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch):
    """Import node_wire_runtime.secrets.vault against a stub hvac module."""
    state = SimpleNamespace(
        data={},
        error=None,
        authenticated=True,
        url=None,
        token=None,
        path=None,
        mount_point=None,
    )

    class VaultError(Exception):
        pass

    class InvalidPath(VaultError):
        pass

    class _KvV2:
        def read_secret_version(self, path: str, mount_point: str = "secret") -> dict:
            state.path = path
            state.mount_point = mount_point
            if state.error is not None:
                raise state.error
            return {"data": {"data": state.data}}

    class Client:
        def __init__(self, url: str, token: str | None = None) -> None:
            state.url = url
            state.token = token
            self.secrets = SimpleNamespace(kv=SimpleNamespace(v2=_KvV2()))

        def is_authenticated(self) -> bool:
            return state.authenticated

    hvac_mod = types.ModuleType("hvac")
    hvac_mod.Client = Client
    exc_mod = types.ModuleType("hvac.exceptions")
    exc_mod.VaultError = VaultError
    exc_mod.InvalidPath = InvalidPath
    hvac_mod.exceptions = exc_mod

    monkeypatch.setitem(sys.modules, "hvac", hvac_mod)
    monkeypatch.setitem(sys.modules, "hvac.exceptions", exc_mod)
    sys.modules.pop("node_wire_runtime.secrets.vault", None)
    mod = importlib.import_module("node_wire_runtime.secrets.vault")
    yield mod, state, exc_mod
    sys.modules.pop("node_wire_runtime.secrets.vault", None)


def test_vault_success_and_missing_key(vault) -> None:
    mod, state, _exc = vault
    state.data = {"api_token": "vault-value"}

    provider = mod.HashiCorpVaultProvider(
        "apps/node-wire", url="https://vault.internal:8200", token="t-1", mount_point="kv"
    )

    assert state.url == "https://vault.internal:8200"
    assert state.token == "t-1"
    assert state.path == "apps/node-wire"
    assert state.mount_point == "kv"
    assert provider.get_secret("api_token") == "vault-value"
    with pytest.raises(SecretNotFoundError):
        provider.get_secret("nope")


def test_vault_unauthenticated_maps_to_provider_error(vault) -> None:
    mod, state, _exc = vault
    state.authenticated = False
    with pytest.raises(SecretProviderError, match="not authenticated"):
        mod.HashiCorpVaultProvider("apps/node-wire")


def test_vault_invalid_path_maps_to_secret_not_found(vault) -> None:
    mod, state, exc = vault
    state.error = exc.InvalidPath("no secret at path")
    with pytest.raises(SecretNotFoundError):
        mod.HashiCorpVaultProvider("apps/missing")


def test_vault_error_maps_to_provider_error(vault) -> None:
    mod, state, exc = vault
    state.error = exc.VaultError("sealed")
    with pytest.raises(SecretProviderError, match="Vault error"):
        mod.HashiCorpVaultProvider("apps/broken")


# ---------------------------------------------------------------------------
# base.py gaps
# ---------------------------------------------------------------------------


def test_abstract_get_secret_body_raises_not_implemented() -> None:
    class PassThrough(SecretProvider):
        def get_secret(self, key: str) -> str:
            return super().get_secret(key)  # type: ignore[safe-super]

    with pytest.raises(NotImplementedError):
        PassThrough().get_secret("anything")


def test_env_provider_uppercase_fallback_strips_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("nw_test_upper_key", raising=False)
    monkeypatch.setenv("NW_TEST_UPPER_KEY", " 'quoted-value' ")
    p = EnvSecretProvider(legacy_empty_on_missing=False)
    assert p.get_secret("nw_test_upper_key") == "quoted-value"


# ---------------------------------------------------------------------------
# chained.py gaps
# ---------------------------------------------------------------------------


class _StaticProvider(SecretProvider):
    def __init__(self, data: dict[str, str]) -> None:
        self._data = data

    def get_secret(self, key: str) -> str:
        try:
            return self._data[key]
        except KeyError:
            raise SecretNotFoundError(key)


class _BrokenProvider(SecretProvider):
    def get_secret(self, key: str) -> str:
        raise SecretProviderError("IAM is on fire")


def test_chained_requires_at_least_one_provider() -> None:
    with pytest.raises(ValueError, match="at least one provider"):
        ChainedSecretProvider()


def test_chained_falls_through_on_not_found() -> None:
    chain = ChainedSecretProvider(_StaticProvider({}), _StaticProvider({"k": "v2"}))
    assert chain.get_secret("k") == "v2"


def test_chained_propagates_provider_error_immediately() -> None:
    fallback = _StaticProvider({"k": "should-never-be-reached"})
    chain = ChainedSecretProvider(_BrokenProvider(), fallback)
    with pytest.raises(SecretProviderError, match="IAM is on fire"):
        chain.get_secret("k")


def test_chained_raises_not_found_when_all_providers_miss() -> None:
    chain = ChainedSecretProvider(_StaticProvider({}), _StaticProvider({}))
    with pytest.raises(SecretNotFoundError, match="not found in any of 2 provider"):
        chain.get_secret("missing")
