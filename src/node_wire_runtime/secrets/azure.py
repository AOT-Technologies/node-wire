#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from node_wire_runtime.secrets.base import (
    SecretNotFoundError,
    SecretProvider,
    SecretProviderError,
)


def _import_azure():
    """Import Azure SDKs lazily so stubs / Cython reloads resolve via ``sys.modules``."""
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        from azure.core.exceptions import ResourceNotFoundError, HttpResponseError
    except ImportError as exc:
        raise ImportError(
            "azure-keyvault-secrets and azure-identity are required for AzureKeyVaultProvider. "
            "Install with: pip install 'node-wire-runtime[azure]'"
        ) from exc
    return DefaultAzureCredential, SecretClient, ResourceNotFoundError, HttpResponseError


class AzureKeyVaultProvider(SecretProvider):
    """Reads individual secrets from Azure Key Vault on demand.

    Uses DefaultAzureCredential — works with managed identities, environment
    credentials, and interactive logins without changing application code.
    """

    def __init__(self, vault_url: str) -> None:
        DefaultAzureCredential, SecretClient, _, _ = _import_azure()
        try:
            credential = DefaultAzureCredential()
            self._client = SecretClient(vault_url=vault_url, credential=credential)
        except Exception as exc:
            raise SecretProviderError(
                f"Failed to initialise Azure Key Vault client for {vault_url!r}: {exc}"
            ) from exc

    def get_secret(self, key: str) -> str:
        # Azure KV names use hyphens; map underscores for convention compatibility.
        _, _, ResourceNotFoundError, HttpResponseError = _import_azure()
        azure_name = key.replace("_", "-")
        try:
            secret = self._client.get_secret(azure_name)
            if secret.value is None:
                raise SecretNotFoundError(key)
            return secret.value
        except ResourceNotFoundError:
            raise SecretNotFoundError(key)
        except HttpResponseError as exc:
            raise SecretProviderError(
                f"Azure Key Vault HTTP error for secret {key!r}: {exc}"
            ) from exc
