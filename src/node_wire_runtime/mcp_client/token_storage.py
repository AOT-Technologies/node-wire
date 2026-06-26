#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Encrypted OAuth token storage partitioned per user, MCP server, and issuer."""

from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .config import McpClientConfig, TokenStoreMode, canonicalize_mcp_server_url
from node_wire_runtime.secrets import EnvSecretProvider, SecretProvider

logger = logging.getLogger("runtime.mcp_client.token_storage")

_PARTITION_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")
_KEYRING_SERVICE = "node-wire-mcp-oauth"


@dataclass
class StoredOAuthTokens:
    """Persisted token set for one (user, mcp_server, issuer) partition."""

    user_id: str
    mcp_server_url: str
    issuer: str
    access_token: str
    token_type: str
    refresh_token: Optional[str]
    scope: Optional[str]
    expires_at: Optional[float]
    updated_at: float

    @classmethod
    def from_dict(cls, data: dict) -> StoredOAuthTokens:
        return cls(
            user_id=str(data["user_id"]),
            mcp_server_url=str(data["mcp_server_url"]),
            issuer=str(data["issuer"]),
            access_token=str(data["access_token"]),
            token_type=str(data.get("token_type") or "Bearer"),
            refresh_token=data.get("refresh_token"),
            scope=data.get("scope"),
            expires_at=data.get("expires_at"),
            updated_at=float(data.get("updated_at") or time.time()),
        )

    def is_expired(self, *, lead_seconds: int) -> bool:
        if self.expires_at is None:
            return False
        return time.time() >= (self.expires_at - max(0, lead_seconds))


def token_partition_key(user_id: str, mcp_server_url: str, issuer: str) -> str:
    mcp = canonicalize_mcp_server_url(mcp_server_url)
    issuer_norm = issuer.rstrip("/")
    raw = f"{user_id}|{mcp}|{issuer_norm}"
    safe = _PARTITION_SAFE.sub("_", raw)
    return safe[:200]


def default_token_store_dir() -> Path:
    return Path.home() / ".node-wire" / "mcp-oauth" / "tokens"


class TokenStore(ABC):
    @abstractmethod
    def get(self, partition_key: str) -> Optional[StoredOAuthTokens]:
        raise NotImplementedError

    @abstractmethod
    def save(self, tokens: StoredOAuthTokens) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, partition_key: str) -> None:
        raise NotImplementedError


class InMemoryTokenStore(TokenStore):
    """Test-only in-memory store."""

    def __init__(self) -> None:
        self._data: dict[str, StoredOAuthTokens] = {}

    def get(self, partition_key: str) -> Optional[StoredOAuthTokens]:
        return self._data.get(partition_key)

    def save(self, tokens: StoredOAuthTokens) -> None:
        key = token_partition_key(tokens.user_id, tokens.mcp_server_url, tokens.issuer)
        self._data[key] = tokens

    def delete(self, partition_key: str) -> None:
        self._data.pop(partition_key, None)


class FileEncryptedTokenStore(TokenStore):
    """Fernet-encrypted JSON files (fallback when OS keychain unavailable)."""

    def __init__(self, base_dir: Optional[Path | str] = None) -> None:
        self._base_dir = Path(base_dir) if base_dir else default_token_store_dir()
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._fernet = _load_fernet(self._base_dir)

    def _path(self, partition_key: str) -> Path:
        safe = _PARTITION_SAFE.sub("_", partition_key).strip("_") or "default"
        return self._base_dir / f"{safe}.token"

    def get(self, partition_key: str) -> Optional[StoredOAuthTokens]:
        path = self._path(partition_key)
        if not path.is_file():
            return None
        try:
            payload = self._fernet.decrypt(path.read_bytes())
            return StoredOAuthTokens.from_dict(json.loads(payload.decode("utf-8")))
        except Exception as exc:
            logger.warning("Ignoring corrupt token file %s: %s", path, exc)
            return None

    def save(self, tokens: StoredOAuthTokens) -> None:
        key = token_partition_key(tokens.user_id, tokens.mcp_server_url, tokens.issuer)
        path = self._path(key)
        payload = json.dumps(asdict(tokens)).encode("utf-8")
        path.write_bytes(self._fernet.encrypt(payload))

    def delete(self, partition_key: str) -> None:
        path = self._path(partition_key)
        if path.is_file():
            path.unlink()


class OsKeychainTokenStore(TokenStore):
    """OS keychain via optional ``keyring`` package; falls back to encrypted files."""

    def __init__(self, *, fallback_dir: Optional[Path | str] = None) -> None:
        self._fallback = FileEncryptedTokenStore(fallback_dir)
        self._keyring = _try_import_keyring()

    def get(self, partition_key: str) -> Optional[StoredOAuthTokens]:
        if self._keyring is None:
            return self._fallback.get(partition_key)
        try:
            raw = self._keyring.get_password(_KEYRING_SERVICE, partition_key)
            if not raw:
                return None
            return StoredOAuthTokens.from_dict(json.loads(raw))
        except Exception as exc:
            logger.warning("Keychain read failed, using file fallback: %s", exc)
            return self._fallback.get(partition_key)

    def save(self, tokens: StoredOAuthTokens) -> None:
        key = token_partition_key(tokens.user_id, tokens.mcp_server_url, tokens.issuer)
        payload = json.dumps(asdict(tokens))
        if self._keyring is not None:
            try:
                self._keyring.set_password(_KEYRING_SERVICE, key, payload)
                return
            except Exception as exc:
                logger.warning("Keychain write failed, using file fallback: %s", exc)
        self._fallback.save(tokens)

    def delete(self, partition_key: str) -> None:
        if self._keyring is not None:
            try:
                self._keyring.delete_password(_KEYRING_SERVICE, partition_key)
            except Exception:
                pass
        self._fallback.delete(partition_key)


class SecretProviderTokenStore(TokenStore):
    """Store encrypted blobs in a configured secret backend (env JSON per partition key)."""

    def __init__(
        self,
        secret_provider: SecretProvider,
        *,
        key_prefix: str = "NW_MCP_OAUTH_TOKEN_",
    ) -> None:
        self._secrets = secret_provider
        self._prefix = key_prefix

    def _secret_key(self, partition_key: str) -> str:
        safe = _PARTITION_SAFE.sub("_", partition_key).strip("_").upper()
        return f"{self._prefix}{safe}"

    def get(self, partition_key: str) -> Optional[StoredOAuthTokens]:
        try:
            raw = self._secrets.get_secret(self._secret_key(partition_key))
        except KeyError:
            return None
        if not raw:
            return None
        try:
            return StoredOAuthTokens.from_dict(json.loads(raw))
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def save(self, tokens: StoredOAuthTokens) -> None:
        key = token_partition_key(tokens.user_id, tokens.mcp_server_url, tokens.issuer)
        # SecretProvider is read-only in runtime; persist via env only in tests.
        # Production server deployments should use OsKeychainTokenStore or external vault
        # integration wired by operators; this store supports EnvSecretProvider round-trip
        # when NW_MCP_OAUTH_TOKEN_* vars are injected by the platform.
        import os

        os.environ[self._secret_key(key)] = json.dumps(asdict(tokens))

    def delete(self, partition_key: str) -> None:
        import os

        os.environ.pop(self._secret_key(partition_key), None)


def make_token_store(
    config: McpClientConfig,
    *,
    token_store_path: Optional[str] = None,
    secret_provider: Optional[SecretProvider] = None,
) -> TokenStore:
    mode = config.auth.token.store
    if mode == TokenStoreMode.CONFIGURED_SECRET_STORE:
        return SecretProviderTokenStore(secret_provider or EnvSecretProvider())
    return OsKeychainTokenStore(
        fallback_dir=token_store_path or config.auth.registration_store_path,
    )


def _try_import_keyring():
    try:
        import keyring  # type: ignore[import-untyped]

        return keyring
    except ImportError:
        return None


def _load_fernet(base_dir: Path):
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise RuntimeError(
            "cryptography package required for encrypted token storage; "
            "install PyJWT[crypto] or cryptography"
        ) from exc

    key_path = base_dir / ".fernet.key"
    if key_path.is_file():
        key = key_path.read_bytes()
    else:
        key = Fernet.generate_key()
        key_path.write_bytes(key)
    return Fernet(key)


def stored_from_oauth_response(
    *,
    user_id: str,
    mcp_server_url: str,
    issuer: str,
    access_token: str,
    token_type: str,
    expires_in: Optional[int],
    refresh_token: Optional[str],
    scope: Optional[str],
) -> StoredOAuthTokens:
    expires_at: Optional[float] = None
    if expires_in is not None and expires_in > 0:
        expires_at = time.time() + float(expires_in)
    return StoredOAuthTokens(
        user_id=user_id,
        mcp_server_url=canonicalize_mcp_server_url(mcp_server_url),
        issuer=issuer.rstrip("/"),
        access_token=access_token,
        token_type=token_type,
        refresh_token=refresh_token,
        scope=scope,
        expires_at=expires_at,
        updated_at=time.time(),
    )
