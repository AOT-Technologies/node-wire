from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from node_wire_runtime import BaseConnector, SecretProvider
from node_wire_runtime.base_connector import _CONNECTOR_REGISTRY
from node_wire_runtime.policy import PolicyHook
from node_wire_runtime.policies.mcp_scope_policy import (
    ScopePolicyHook,
    load_scope_map_from_env,
)
from node_wire_runtime.secrets import ChainedSecretProvider, EnvSecretProvider

logger = logging.getLogger("bindings.factory")

_PLATFORM_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CONFIG_PATH = _PLATFORM_ROOT / "config" / "connectors.yaml"


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
    if not action_scope_map:
        logger.info("Policy hook disabled (no action scope map)")
        return None
    logger.info(
        "Policy hook enabled",
        extra={"scope_map_entries": len(action_scope_map)},
    )
    return ScopePolicyHook(action_scope_map)


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
        self._policy_hook: PolicyHook | None = _build_policy_hook()
        self._connectors: Dict[str, Any] = {}
        self._configs: Dict[str, ConnectorConfig] = {}

    def load(self) -> None:
        logger.info("Loading connector configuration", extra={"config_path": self._config_path})
        path = Path(self._config_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"Connector config not found: {self._config_path} (resolved: {path.resolve()})"
            )
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        connectors_cfg: Dict[str, Any] = raw.get("connectors", {})

        for connector_id, cfg in connectors_cfg.items():
            enabled = bool(cfg.get("enabled", False))
            exposed_via = list(cfg.get("exposed_via", []))

            self._configs[connector_id] = ConnectorConfig(
                id=connector_id,
                enabled=enabled,
                exposed_via=exposed_via,
                raw=cfg,
            )

            if not enabled:
                logger.info(
                    "Connector disabled via configuration",
                    extra={"connector_id": connector_id},
                )
                continue

            instance = self._instantiate(connector_id)
            if instance is not None:
                self._connectors[connector_id] = instance

    def _build_auth_provider(self, connector_id: str, cfg: dict) -> Any:
        """Construct the appropriate AuthProvider from the connector's YAML ``auth:`` block.

        Falls back to :class:`NoAuthProvider` when the block is absent.
        """
        from node_wire_runtime.auth import (
            NoAuthProvider,
            OAuth2AuthProvider,
            ServiceAccountAuthProvider,
            StaticTokenAuthProvider,
        )

        auth_cfg = cfg.get("auth") or {}
        provider_type = auth_cfg.get("provider", "none")

        if provider_type in ("none", ""):
            return NoAuthProvider()

        if provider_type == "static_token":
            return StaticTokenAuthProvider(
                secret_provider=self._secret_provider,
                secret_key=auth_cfg["secret_key"],
                header_name=auth_cfg.get("header_name", "Authorization"),
                prefix=auth_cfg.get("prefix", "Bearer"),
                encoding=auth_cfg.get("encoding"),
            )

        if provider_type == "oauth2":
            return OAuth2AuthProvider(
                secret_provider=self._secret_provider,
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
                secret_provider=self._secret_provider,
                sa_json_secret=auth_cfg["sa_json_secret"],
                scopes=auth_cfg.get("scopes"),
            )

        if provider_type == "static_credentials":
            # SMTP-style: returns (username, password) tuple via get_client_credentials().
            # We use a lightweight wrapper around StaticTokenAuthProvider pair.
            username_secret = auth_cfg.get("username_secret", "SMTP_USERNAME")
            password_secret = auth_cfg.get("password_secret", "SMTP_PASSWORD")
            from node_wire_runtime.auth.base import AuthProvider

            sp = self._secret_provider

            class _SmtpCredentialsProvider(AuthProvider):  # type: ignore[misc]
                async def get_headers(self) -> dict:
                    return {}

                async def get_client_credentials(self):  # type: ignore[override]
                    return (sp.get_secret(username_secret), sp.get_secret(password_secret))

            return _SmtpCredentialsProvider()

        logger.warning(
            "Unknown auth provider type %r for connector %r — defaulting to NoAuthProvider",
            provider_type,
            connector_id,
        )
        return NoAuthProvider()

    def _instantiate(self, connector_id: str) -> "BaseConnector | None":
        connector_cls = _CONNECTOR_REGISTRY.get(connector_id)
        if connector_cls is not None:
            cfg = self._configs[connector_id]
            auth_provider = self._build_auth_provider(connector_id, cfg.raw)
            return connector_cls(
                secret_provider=self._secret_provider,
                auth_provider=auth_provider,
                policy_hook=self._policy_hook,
            )

        logger.warning(
            "Connector %r is enabled in config but not registered (filtered by NW_ALLOWED_CONNECTORS or not installed) — skipping",
            connector_id,
        )
        return None

    def get_for_protocol(
        self, connector_id: str, protocol: str, action: Optional[str] = None
    ) -> Optional[BaseConnector]:
        cfg = self._configs.get(connector_id)
        if cfg is None:
            logger.warning(
                "Requested connector is not configured",
                extra={"connector_id": connector_id, "protocol": protocol},
            )
            return None

        if not cfg.enabled:
            logger.warning(
                "Requested connector is disabled",
                extra={"connector_id": connector_id, "protocol": protocol},
            )
            return None

        if protocol not in cfg.exposed_via:
            logger.warning(
                "Connector is not exposed via requested protocol",
                extra={"connector_id": connector_id, "protocol": protocol},
            )
            return None

        connector = self._connectors.get(connector_id)
        if connector is None:
            return None

        if action:
            logger.debug(
                "get_for_protocol resolved connector",
                extra={"connector_id": connector_id, "protocol": protocol, "action": action},
            )

        return connector  # type: ignore[return-value]

    def list_for_protocol(self, protocol: str) -> List[BaseConnector]:
        result: List[BaseConnector] = []
        for connector_id, connector in self._connectors.items():
            if protocol in self._configs[connector_id].exposed_via:
                result.append(connector)  # type: ignore[arg-type]
        return result
