from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from runtime import BaseConnector, SecretProvider
from runtime.base_connector import _CONNECTOR_REGISTRY

logger = logging.getLogger("bindings.factory")

_PLATFORM_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CONFIG_PATH = _PLATFORM_ROOT / "config" / "connectors.yaml"


@dataclass
class ConnectorConfig:
    id: str
    enabled: bool
    exposed_via: List[str]
    raw: Dict[str, Any]


class EnvSecretProvider(SecretProvider):
    """SecretProvider backed by environment variables."""

    def __init__(self) -> None:
        import os

        self._env = os.environ

    def get_secret(self, key: str) -> str:
        val = self._env.get(key)
        if val is not None:
            return val.strip(" '\"")
        val = self._env.get(key.upper())
        if val is not None:
            return val.strip(" '\"")
        return ""


class ConnectorFactory:
    """
    Loads config/connectors.yaml and instantiates connectors from the SDK registry.
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        if config_path is not None:
            self._config_path = str(config_path)
        elif _DEFAULT_CONFIG_PATH.is_file():
            self._config_path = str(_DEFAULT_CONFIG_PATH)
        else:
            cwd_config = Path.cwd() / "config" / "connectors.yaml"
            self._config_path = str(cwd_config)
        self._secret_provider: SecretProvider = EnvSecretProvider()
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

            self._connectors[connector_id] = self._instantiate(connector_id)

    def _instantiate(self, connector_id: str) -> BaseConnector:
        sdk_cls = _CONNECTOR_REGISTRY.get(connector_id)
        if sdk_cls is not None:
            return sdk_cls(secret_provider=self._secret_provider)

        raise ValueError(f"Unknown connector id {connector_id!r}")

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
