from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from connectors.fhir_epic.logic import FhirEpicConnector
from connectors.fhir_cerner.logic import FhirCernerConnector
from connectors.http_generic.logic import HttpGenericConnector
from connectors.http_generic.schema import HttpRequestInput, HttpResponseOutput
from connectors.google_drive.logic import GoogleDriveConnector
from connectors.google_drive.schema import (
    GoogleDriveOperationInput,
    GoogleDriveOperationOutput,
)
from connectors.smtp.logic import SmtpConnector
from connectors.smtp.schema import SmtpSendInput, SmtpSendOutput
from connectors.stripe.logic import StripeChargeConnector
from connectors.stripe.schema import ChargeInput, ChargeOutput
from runtime import BaseConnector, SecretProvider

logger = logging.getLogger("bindings.factory")

# Resolve default config relative to platform root so it works from any cwd.
_PLATFORM_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CONFIG_PATH = _PLATFORM_ROOT / "config" / "connectors.yaml"


@dataclass
class ConnectorConfig:
    id: str
    enabled: bool
    exposed_via: List[str]
    raw: Dict[str, Any]


class EnvSecretProvider(SecretProvider):
    """
    Simple SecretProvider implementation backed by environment variables.

    Keys are looked up directly from os.environ for the POC.
    """

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
        # Return empty string instead of raising RuntimeError for zero-config/local testing.
        return ""


class ConnectorFactory:
    """
    Factory responsible for:
    - Loading connector configuration from config/connectors.yaml
    - Instantiating connector adapters
    - Enforcing exposed_via rules per protocol
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        if config_path is not None:
            self._config_path = str(config_path)
        elif _DEFAULT_CONFIG_PATH.is_file():
            self._config_path = str(_DEFAULT_CONFIG_PATH)
        else:
            # Fallback when run from platform dir (e.g. package installed from wheel)
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

    def _instantiate(self, connector_id: str) -> Any:
        if connector_id == "http_generic":
            return HttpGenericConnector(HttpRequestInput, HttpResponseOutput, secret_provider=self._secret_provider)
        if connector_id == "smtp":
            return SmtpConnector(SmtpSendInput, SmtpSendOutput, secret_provider=self._secret_provider)
        if connector_id == "stripe":
            return StripeChargeConnector(ChargeInput, ChargeOutput, secret_provider=self._secret_provider)
        if connector_id == "google_drive":
            return GoogleDriveConnector(
                GoogleDriveOperationInput,
                GoogleDriveOperationOutput,
                secret_provider=self._secret_provider,
            )
        if connector_id == "fhir_epic":
            return FhirEpicConnector(secret_provider=self._secret_provider)
        if connector_id == "fhir_cerner":
            return FhirCernerConnector(secret_provider=self._secret_provider)

        raise ValueError(f"Unknown connector id {connector_id!r}")

    def get_for_protocol(
        self, connector_id: str, protocol: str, action: Optional[str] = None
    ) -> Optional[BaseConnector[Any, Any]]:
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
                "get_for_protocol resolved connector (action from URL is merged into payload by REST)",
                extra={"connector_id": connector_id, "protocol": protocol, "action": action},
            )

        return connector  # type: ignore[return-value]

    def list_for_protocol(self, protocol: str) -> List[BaseConnector[Any, Any]]:
        result: List[BaseConnector[Any, Any]] = []
        for connector_id, connector in self._connectors.items():
            if protocol in self._configs[connector_id].exposed_via:
                result.append(connector)  # type: ignore[arg-type]
        return result
