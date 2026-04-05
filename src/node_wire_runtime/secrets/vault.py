from __future__ import annotations

from node_wire_runtime.secrets.base import (
    SecretNotFoundError,
    SecretProvider,
    SecretProviderError,
)

try:
    import hvac
except ImportError as _e:
    raise ImportError(
        "hvac is required for HashiCorpVaultProvider. "
        "Install it with: pip install 'node-wire-runtime[vault]'"
    ) from _e


class HashiCorpVaultProvider(SecretProvider):
    """Reads a KV-v2 secret from HashiCorp Vault.

    Fetches the secret at init time and caches it in memory.
    Raises SecretProviderError on Vault connectivity / auth failures.
    """

    def __init__(
        self,
        secret_path: str,
        *,
        url: str = "http://127.0.0.1:8200",
        token: str | None = None,
        mount_point: str = "secret",
    ) -> None:
        try:
            client = hvac.Client(url=url, token=token)
            if not client.is_authenticated():
                raise SecretProviderError("Vault client is not authenticated")
            response = client.secrets.kv.v2.read_secret_version(
                path=secret_path, mount_point=mount_point
            )
            self._data: dict = response["data"]["data"]
        except SecretProviderError:
            raise
        except hvac.exceptions.InvalidPath:
            raise SecretNotFoundError(secret_path)
        except hvac.exceptions.VaultError as exc:
            raise SecretProviderError(f"Vault error for path {secret_path!r}: {exc}") from exc

    def get_secret(self, key: str) -> str:
        try:
            return self._data[key]
        except KeyError:
            raise SecretNotFoundError(key)
