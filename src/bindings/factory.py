#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from node_wire_runtime import BaseConnector, SecretProvider, get_connector_registry
from node_wire_runtime.config_store import (
    DEFAULT_TENANT,
    ConfigRecord,
    ConnectorConfigStore,
)
from node_wire_runtime.policy import PolicyHook, TenantConfigHook
from node_wire_runtime.policies.mcp_scope_policy import (
    DEFAULT_SCOPE_MODE_DENY,
    ScopePolicyHook,
    load_scope_map_from_env,
    load_scope_policy_default_from_env,
)
from node_wire_runtime.secrets import (
    ChainedSecretProvider,
    EnvSecretProvider,
    TenantSecretProvider,
)

logger = logging.getLogger("bindings.factory")

_PLATFORM_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CONFIG_PATH = _PLATFORM_ROOT / "config" / "connectors.yaml"

_GOOGLE_DRIVE_AUTH_PROVIDER_ENV = "GOOGLE_DRIVE_AUTH_PROVIDER"
_GOOGLE_DRIVE_AUTH_PROVIDERS = frozenset({"service_account", "upstream_bearer"})


def _resolve_google_drive_auth(auth_cfg: dict[str, Any]) -> dict[str, Any]:
    """Apply GOOGLE_DRIVE_AUTH_PROVIDER env override when set (wins over connectors.yaml)."""
    override = os.environ.get(_GOOGLE_DRIVE_AUTH_PROVIDER_ENV, "").strip()
    if not override:
        return auth_cfg
    if override not in _GOOGLE_DRIVE_AUTH_PROVIDERS:
        raise ValueError(
            f"{_GOOGLE_DRIVE_AUTH_PROVIDER_ENV} must be one of "
            f"{sorted(_GOOGLE_DRIVE_AUTH_PROVIDERS)!r}, got {override!r}"
        )
    merged = dict(auth_cfg)
    merged["provider"] = override
    return merged


def _resolve_env_vars(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: _resolve_env_vars(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_resolve_env_vars(item) for item in data]
    elif isinstance(data, str):

        def replacer(match: Any) -> str:
            var_name = match.group(1)
            default = match.group(3)
            if var_name in os.environ:
                return os.environ[var_name]
            elif default is not None:
                return default
            return match.group(0)

        return re.sub(r"\$\{([A-Za-z0-9_]+)(:(.*?))?\}", replacer, data)
    return data


def _resolve_config_path(explicit: str | Path | None) -> str:
    """Resolve connector config path with NW_CONFIG_PATH env var support.

    Priority order (first match wins):
    1. Explicit argument passed to ConnectorFactory()
    2. NW_CONFIG_PATH environment variable
    3. <repo-root>/config/connectors.yaml  (existing default — no breakage)
    4. <cwd>/config/connectors.yaml        (existing fallback — no breakage)
    """
    if explicit is not None:
        return str(explicit)
    env_path = os.getenv("NW_CONFIG_PATH")
    if env_path:
        return env_path
    if _DEFAULT_CONFIG_PATH.is_file():
        return str(_DEFAULT_CONFIG_PATH)
    return str(Path.cwd() / "config" / "connectors.yaml")


def _build_secret_provider() -> SecretProvider:
    """Compose secret providers from ``NW_SECRET_BACKEND`` (default: ``env``).

    - ``env`` — :class:`EnvSecretProvider` only (fail-closed unless ``NW_ENV_SECRET_LEGACY_EMPTY``).
    - ``aws_env`` — :class:`ChainedSecretProvider`(
        :class:`~node_wire_runtime.secrets.aws.AwsSecretsManagerProvider`,
        :class:`EnvSecretProvider`) for JSON bundle in AWS SM then env fallback.

    Environment for ``aws_env``:
        ``NW_AWS_SECRETS_MANAGER_SECRET_ID`` — Secrets Manager secret id or ARN
        ``AWS_REGION`` — optional, default ``us-east-1``
    """
    mode = os.environ.get("NW_SECRET_BACKEND", "env").strip().lower()
    if mode in ("", "env"):
        return EnvSecretProvider()
    if mode == "aws_env":
        secret_id = os.environ.get("NW_AWS_SECRETS_MANAGER_SECRET_ID")
        if not secret_id:
            raise ValueError(
                "NW_SECRET_BACKEND=aws_env requires NW_AWS_SECRETS_MANAGER_SECRET_ID to be set"
            )
        from node_wire_runtime.secrets.aws import AwsSecretsManagerProvider

        region = os.environ.get("AWS_REGION", "us-east-1")
        return ChainedSecretProvider(
            AwsSecretsManagerProvider(secret_name=secret_id, region=region),
            EnvSecretProvider(),
        )
    raise ValueError(f"Unknown NW_SECRET_BACKEND {mode!r}. Supported: env, aws_env.")


def _build_policy_hook() -> PolicyHook | None:
    action_scope_map = load_scope_map_from_env()
    default_mode = load_scope_policy_default_from_env()
    strict_mode = os.environ.get("NW_MCP_SCOPE_POLICY_STRICT", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    logger.info(
        "Evaluated MCP scope policy configuration",
        extra={
            "scope_map_entries": len(action_scope_map),
            "default_mode": default_mode,
            "strict_mode": strict_mode,
        },
    )
    if not action_scope_map and default_mode != DEFAULT_SCOPE_MODE_DENY:
        msg = (
            "MCP scope policy is effectively disabled "
            "(NW_MCP_ACTION_SCOPE_MAP_JSON empty and NW_MCP_SCOPE_POLICY_DEFAULT=allow). "
            "Set NW_MCP_SCOPE_POLICY_DEFAULT=deny for production."
        )
        if strict_mode:
            raise ValueError(msg + " Strict mode is enabled via NW_MCP_SCOPE_POLICY_STRICT=true.")
        logger.warning(msg)
        logger.info("Policy hook disabled (no action scope map; default is allow)")
        return None
    logger.info(
        "Policy hook enabled",
        extra={
            "scope_map_entries": len(action_scope_map),
            "default_mode": default_mode,
        },
    )
    return ScopePolicyHook(action_scope_map, default_mode=default_mode)


@dataclass
class ConnectorConfig:
    id: str
    enabled: bool
    exposed_via: List[str]
    raw: Dict[str, Any]


class ConnectorFactory:
    """
    Loads connectors.yaml and instantiates connectors from the connector registry.
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        self._config_path = _resolve_config_path(config_path)
        self._secret_provider: SecretProvider = _build_secret_provider()
        # Runtime config store + per-(tenant, connector, config_name) invoke cache.
        self._store = ConnectorConfigStore()
        self._store.attach_factory(self)
        # Scope policy takes precedence when configured; otherwise fall back to the
        # config-existence hook (defense in depth for configured = entitled).
        self._policy_hook: PolicyHook | None = _build_policy_hook() or TenantConfigHook(
            self._store
        )
        # YAML-derived metadata (enabled, exposed_via, raw) for enumeration,
        # protocol gating, and the MCP upstream-passthrough check.
        self._configs: Dict[str, ConnectorConfig] = {}
        self._instances: "OrderedDict[Tuple[str, str, str], BaseConnector]" = OrderedDict()
        self._locks: Dict[Tuple[str, str, str], asyncio.Lock] = {}
        self._locks_guard = threading.Lock()
        self._max_instances = int(os.environ.get("NW_FACTORY_MAX_INSTANCES", "512"))
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def store(self) -> ConnectorConfigStore:
        """The runtime connector config store backing this factory."""
        return self._store

    def load(self) -> None:
        """Load connector metadata and bootstrap the store from ``connectors.yaml``.

        The YAML remains the single-tenant bootstrap: it is translated once into the
        store under the ``__default__`` tenant (gated by ``NW_CONFIG_BOOTSTRAP_YAML``,
        default on), so existing deployments keep working with zero changes.
        """
        logger.info("Loading connector configuration", extra={"config_path": self._config_path})
        path = Path(self._config_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"Connector config not found: {self._config_path} (resolved: {path.resolve()})"
            )
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        raw = _resolve_env_vars(raw)

        connectors_cfg: Dict[str, Any] = raw.get("connectors", {})
        bootstrap_enabled = os.environ.get("NW_CONFIG_BOOTSTRAP_YAML", "true").strip().lower() not in (
            "0",
            "false",
            "no",
        )
        bootstrap_payload: Dict[str, Any] = {DEFAULT_TENANT: {}}

        for connector_id, cfg in connectors_cfg.items():
            enabled = bool(cfg.get("enabled", False))
            exposed_via = list(cfg.get("exposed_via", []))
            cfg_raw: Dict[str, Any] = dict(cfg)
            if connector_id == "google_drive":
                cfg_raw["auth"] = _resolve_google_drive_auth(cfg_raw.get("auth") or {})

            self._configs[connector_id] = ConnectorConfig(
                id=connector_id,
                enabled=enabled,
                exposed_via=exposed_via,
                raw=cfg_raw,
            )

            if not enabled:
                logger.info(
                    "Connector disabled via configuration",
                    extra={"connector_id": connector_id},
                )
                continue

            if connector_id not in get_connector_registry():
                logger.warning(
                    "Connector enabled in configuration but not registered; skipping instantiation",
                    extra={
                        "connector_id": connector_id,
                        "reason": "Filtered by NW_ALLOWED_CONNECTORS or not installed",
                    },
                )
                continue

            if bootstrap_enabled:
                doc: Dict[str, Any] = {
                    "name": "default",
                    "default": True,
                    "config": {
                        k: v
                        for k, v in cfg_raw.items()
                        if k not in ("enabled", "exposed_via", "auth")
                    },
                    "auth": cfg_raw.get("auth", {}),
                    "exposed_via": exposed_via,
                }
                bootstrap_payload[DEFAULT_TENANT][connector_id] = [doc]

        if bootstrap_enabled:
            self._store.init(bootstrap_payload)

    def _build_auth_provider(
        self, connector_id: str, cfg: dict, *, secret_provider: SecretProvider | None = None
    ) -> Any:
        """Construct the appropriate AuthProvider from the connector's ``auth:`` block.

        ``secret_provider`` defaults to the factory's shared provider (used for
        enumeration instances); the invoke path passes a tenant-scoped provider.
        Falls back to :class:`NoAuthProvider` when the block is absent.
        """
        from node_wire_runtime.auth import (
            NoAuthProvider,
            OAuth2AuthProvider,
            ServiceAccountAuthProvider,
            StaticTokenAuthProvider,
        )

        sp = secret_provider if secret_provider is not None else self._secret_provider

        auth_cfg = cfg.get("auth") or {}
        if connector_id == "google_drive":
            auth_cfg = _resolve_google_drive_auth(auth_cfg)
        provider_type = auth_cfg.get("provider", "none")

        if provider_type in ("none", ""):
            return NoAuthProvider()

        if provider_type == "static_token":
            return StaticTokenAuthProvider(
                secret_provider=sp,
                secret_key=auth_cfg["secret_key"],
                header_name=auth_cfg.get("header_name", "Authorization"),
                prefix=auth_cfg.get("prefix", "Bearer"),
                encoding=auth_cfg.get("encoding"),
            )

        if provider_type == "oauth2":
            return OAuth2AuthProvider(
                secret_provider=sp,
                grant_method=auth_cfg.get("grant_method", "private_key_jwt"),
                token_url_secret=auth_cfg["token_url_secret"],
                client_id_secret=auth_cfg["client_id_secret"],
                algorithm=auth_cfg.get("algorithm", "RS384"),
                private_key_secret=auth_cfg.get("private_key_secret"),
                kid_secret=auth_cfg.get("kid_secret"),
                client_secret_secret=auth_cfg.get("client_secret_secret"),
                refresh_token_secret=auth_cfg.get("refresh_token_secret"),
                scopes=auth_cfg.get("scopes"),
                scopes_secret=auth_cfg.get("scopes_secret"),
                extra_content_type_headers=auth_cfg.get("extra_headers"),
                buffer_secs=int(auth_cfg.get("buffer_secs", 60)),
                jwt_ttl_secs=int(auth_cfg.get("jwt_ttl_secs", 300)),
            )

        if provider_type == "service_account":
            return ServiceAccountAuthProvider(
                secret_provider=sp,
                sa_json_secret=auth_cfg["sa_json_secret"],
                scopes=auth_cfg.get("scopes"),
            )

        if provider_type == "upstream_bearer":
            from node_wire_runtime.auth.base import AuthProvider, get_upstream_bearer

            class _UpstreamBearerProvider(AuthProvider):  # type: ignore[misc]
                per_request_credentials = True

                async def get_headers(self) -> dict:
                    token = get_upstream_bearer()
                    if not token:
                        raise RuntimeError("Upstream bearer token required")
                    return {"Authorization": f"Bearer {token}"}

                async def get_client_credentials(self):  # type: ignore[override]
                    from google.oauth2.credentials import Credentials  # type: ignore[import]

                    token = get_upstream_bearer()
                    if not token:
                        return None
                    return Credentials(token=token)

            return _UpstreamBearerProvider()

        if provider_type == "static_credentials":
            # SMTP-style: returns (username, password) tuple via get_client_credentials().
            # We use a lightweight wrapper around StaticTokenAuthProvider pair.
            username_secret = auth_cfg.get("username_secret", "SMTP_USERNAME")
            password_secret = auth_cfg.get("password_secret", "SMTP_PASSWORD")
            from node_wire_runtime.auth.base import AuthProvider

            creds_sp = sp

            class _SmtpCredentialsProvider(AuthProvider):  # type: ignore[misc]
                async def get_headers(self) -> dict:
                    return {}

                async def get_client_credentials(self):  # type: ignore[override]
                    return (
                        creds_sp.get_secret(username_secret),
                        creds_sp.get_secret(password_secret),
                    )

            return _SmtpCredentialsProvider()

        logger.warning(
            "Unknown auth provider type %r for connector %r — defaulting to NoAuthProvider",
            provider_type,
            connector_id,
        )
        return NoAuthProvider()

    def _instantiate(self, record: ConfigRecord) -> BaseConnector:
        """Build an invoke instance from a resolved config record. I/O-free:
        secrets and tokens resolve lazily on first :meth:`BaseConnector.run`."""
        connector_cls = get_connector_registry().get(record.connector_id)
        if connector_cls is None:
            raise RuntimeError(
                f"Connector {record.connector_id!r} has a config but is not registered "
                "(filtered by NW_ALLOWED_CONNECTORS or not installed)"
            )

        # Simplified: the __default__ tenant keeps the plain (legacy) secret names
        # so existing single-tenant env vars (e.g. EPIC_FHIR_BASE_URL) keep working.
        # Named tenants are scoped to NW_{TENANT}_{CONNECTOR}_{KEY}.
        if record.tenant_id == DEFAULT_TENANT:
            scoped: SecretProvider = self._secret_provider
        else:
            scoped = TenantSecretProvider(
                self._secret_provider, record.tenant_id, record.connector_id
            )

        auth_provider = self._build_auth_provider(
            record.connector_id, record.raw, secret_provider=scoped
        )
        inst = connector_cls(
            secret_provider=scoped,
            auth_provider=auth_provider,
            config=record.raw.get("config", {}),
            policy_hook=self._policy_hook,
        )
        inst._config_name = record.name
        return inst

    async def get(
        self,
        connector_id: str,
        *,
        tenant_id: str = DEFAULT_TENANT,
        config_name: Optional[str] = None,
        action: Optional[str] = None,
    ) -> BaseConnector:
        """Resolve (and cache) the connector instance for a tenant/config.

        Fail-closed: raises :class:`ConfigNotFoundError` when the scope has no
        config or ``config_name`` is unknown (bindings map both to 403). Also
        callable directly by the embedding application (no binding required).
        """
        self._loop = asyncio.get_running_loop()

        # Existence IS entitlement; resolve to the concrete default name first so
        # default and explicit callers share one instance.
        record = self._store.resolve(tenant_id, connector_id, config_name)
        key = (tenant_id, connector_id, record.name)

        inst = self._instances.get(key)
        if inst is not None:
            self._instances.move_to_end(key)
            return inst

        lock = self._lock_for(key)
        async with lock:
            inst = self._instances.get(key)
            if inst is not None:
                self._instances.move_to_end(key)
                return inst
            inst = self._instantiate(record)
            self._instances[key] = inst
            self._evict_if_needed()
            return inst

    def is_exposed(self, connector_id: str, protocol: str) -> bool:
        """Whether ``connector_id`` may be reached over ``protocol``.

        YAML connectors honour their ``exposed_via`` list; connectors pushed only
        via the runtime API (no YAML metadata) are exposed on all protocols.
        """
        cfg = self._configs.get(connector_id)
        if cfg is None:
            return True
        return protocol in cfg.exposed_via

    def _lock_for(self, key: Tuple[str, str, str]) -> asyncio.Lock:
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock  # locks are never deleted
            return lock

    def _evict_if_needed(self) -> None:
        while len(self._instances) > self._max_instances:
            _, old = self._instances.popitem(last=False)
            self._schedule_aclose(old)

    def invalidate_configs(
        self, tenant_id: str, connector_id: str, names: List[str]
    ) -> None:
        """Drop cached instances for the given config names and schedule teardown.

        Called synchronously by the store on every mutating write; may run on a
        non-loop thread (store writes are plain sync Python)."""
        for name in names:
            old = self._instances.pop((tenant_id, connector_id, name), None)
            if old is not None:
                self._schedule_aclose(old)

    def _schedule_aclose(self, inst: BaseConnector) -> None:
        aclose = getattr(inst, "aclose", None)
        if aclose is None:
            return  # connectors without async cleanup: nothing to do

        async def _safe_aclose() -> None:
            try:
                await aclose()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error during connector aclose: %s", exc)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            loop.create_task(_safe_aclose())
        elif self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(lambda: self._loop.create_task(_safe_aclose()))
        # else: no running loop known; the instance is dropped without aclose.

    def _default_instance(self, connector_id: str) -> Optional[BaseConnector]:
        """Return (and cache) the ``__default__`` instance for a connector.

        Shares :attr:`_instances` with the async :meth:`get`, so enumeration and
        the default-tenant invoke path resolve the SAME object. Sync (no lock):
        only invoked at import/enumeration and by the default-tenant fast path.
        """
        try:
            record = self._store.resolve(DEFAULT_TENANT, connector_id, None)
        except ConfigNotFoundError:
            return None
        key = (DEFAULT_TENANT, connector_id, record.name)
        inst = self._instances.get(key)
        if inst is None:
            inst = self._instantiate(record)
            self._instances[key] = inst
        else:
            self._instances.move_to_end(key)
        return inst

    def get_for_protocol(
        self, connector_id: str, protocol: str, action: Optional[str] = None
    ) -> Optional[BaseConnector]:
        """Sync accessor for the default-tenant instance (playground/enumeration).

        Returns the SAME instance the async :meth:`get` returns for ``__default__``.
        Header-based tenancy still goes through :meth:`get`.
        """
        cfg = self._configs.get(connector_id)
        if cfg is None or not cfg.enabled:
            return None
        if protocol not in cfg.exposed_via:
            return None
        return self._default_instance(connector_id)

    def list_for_protocol(self, protocol: str) -> List[BaseConnector]:
        result: List[BaseConnector] = []
        for connector_id, cfg in self._configs.items():
            if not cfg.enabled or protocol not in cfg.exposed_via:
                continue
            if connector_id not in get_connector_registry():
                continue
            inst = self._default_instance(connector_id)
            if inst is not None:
                result.append(inst)
        return result
