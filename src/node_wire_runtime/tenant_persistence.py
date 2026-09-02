#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Multi-tenant config + secret overlay persistence.

Simplified: one YAML file rewritten on each mutation; gitignored by the repo.

Lives in the runtime (not a binding) because REST, gRPC, and MCP must all
observe the same persisted tenant/config dataset from the same file — this is
shared runtime *state* across transports, not connector business logic (see
docs/adr/0002-connector-specific-logic-stays-in-the-connector.md, which covers
the latter and explicitly carves this module out as the exception).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Tuple

import yaml

from node_wire_runtime.config_store import ConfigNotFoundError, ConnectorConfigStore
from node_wire_runtime.secrets import OverlaySecretProvider, tenant_scoped_secret_key

logger = logging.getLogger("runtime.tenant_persistence")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_TENANTS_PATH = _REPO_ROOT / "config" / "tenants.yaml"
_LEGACY_TENANTS_PATH = _REPO_ROOT / "config" / "playground_tenants.yaml"

# (process env name, logical secret key used by connector / auth refs)
SHARED_ENV_BY_CONNECTOR: Dict[str, List[Tuple[str, str]]] = {
    "fhir_epic": [
        ("EPIC_FHIR_BASE_URL", "epic_fhir_base_url"),
        ("EPIC_TOKEN_URL", "EPIC_TOKEN_URL"),
    ],
    "fhir_cerner": [
        ("CERNER_FHIR_BASE_URL", "cerner_fhir_base_url"),
        ("CERNER_TOKEN_URL", "CERNER_TOKEN_URL"),
        ("CERNER_TOKEN_URL", "cerner_token_url"),
    ],
    "salesforce": [
        ("SALESFORCE_TOKEN_URL", "SALESFORCE_TOKEN_URL"),
        ("SALESFORCE_INSTANCE_URL", "salesforce_instance_url"),
    ],
}

# When a secrets map is provided, these logical keys must be present and non-empty.
REQUIRED_SECRETS_BY_CONNECTOR: Dict[str, List[str]] = {
    "google_drive": ["GOOGLE_DRIVE_SA_JSON"],
    "fhir_epic": ["EPIC_CLIENT_ID", "EPIC_PRIVATE_KEY", "EPIC_KID"],
    "fhir_cerner": ["CERNER_CLIENT_ID", "CERNER_PRIVATE_KEY", "CERNER_KID"],
    "slack": ["SLACK_BOT_TOKEN"],
    "stripe": ["stripe_api_key"],
    "salesforce": [
        "SALESFORCE_CLIENT_ID",
        "SALESFORCE_CLIENT_SECRET",
        "SALESFORCE_REFRESH_TOKEN",
    ],
}

# Format kind per logical secret key (validated only for newly supplied values).
SECRET_FORMAT_BY_CONNECTOR: Dict[str, Dict[str, str]] = {
    "google_drive": {"GOOGLE_DRIVE_SA_JSON": "google_sa_json"},
    "fhir_epic": {
        "EPIC_CLIENT_ID": "opaque_secret",
        "EPIC_PRIVATE_KEY": "pem_private_key",
        "EPIC_KID": "jwt_kid",
    },
    "fhir_cerner": {
        "CERNER_CLIENT_ID": "opaque_secret",
        "CERNER_PRIVATE_KEY": "pem_private_key",
        "CERNER_KID": "jwt_kid",
        "CERNER_SCOPES": "scopes_space_separated",
    },
    "slack": {"SLACK_BOT_TOKEN": "slack_bot_token"},
    "stripe": {"stripe_api_key": "stripe_secret_key"},
    "salesforce": {
        "SALESFORCE_CLIENT_ID": "opaque_secret",
        "SALESFORCE_CLIENT_SECRET": "opaque_secret",
        "SALESFORCE_REFRESH_TOKEN": "opaque_secret",
    },
}

_PEM_PRIVATE_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]+?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
_JWT_KID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")
_SLACK_BOT_RE = re.compile(r"^xoxb-[A-Za-z0-9\-]+$")
_STRIPE_SECRET_RE = re.compile(r"^sk_(?:test|live)_[A-Za-z0-9]+$")


def _normalize_pem(value: str) -> str:
    return value.replace("\\n", "\n").strip()


def _validate_pem_private_key(value: str) -> None:
    pem = _normalize_pem(value)
    if not _PEM_PRIVATE_RE.search(pem):
        if re.search(r"-----BEGIN (?:RSA )?PUBLIC KEY-----", pem):
            raise ValueError("expected a PEM private key, not a public key")
        if "BEGIN CERTIFICATE" in pem:
            raise ValueError("expected a PEM private key, not a certificate")
        raise ValueError("expected PEM private key (BEGIN/END PRIVATE KEY block)")


def _validate_jwt_kid(value: str) -> None:
    if not _JWT_KID_RE.match(value.strip()):
        raise ValueError("expected kid as 1–128 chars of A–Z, a–z, 0–9, '.', '_', or '-'")


def _validate_google_sa_json(value: str) -> None:
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"expected JSON service account: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object for service account")
    if data.get("type") != "service_account":
        raise ValueError('expected JSON with "type": "service_account"')


def _validate_slack_bot_token(value: str) -> None:
    if not _SLACK_BOT_RE.match(value.strip()):
        raise ValueError("expected Slack bot token starting with xoxb-")


def _validate_stripe_secret_key(value: str) -> None:
    if not _STRIPE_SECRET_RE.match(value.strip()):
        raise ValueError("expected Stripe secret key (sk_test_… or sk_live_…)")


def _validate_opaque_secret(value: str) -> None:
    v = value.strip()
    if not v:
        raise ValueError("expected a non-empty value")
    if "\n" in v or "\r" in v:
        raise ValueError("must be a single-line value")


def _validate_scopes_space_separated(value: str) -> None:
    parts = value.split()
    if not parts:
        raise ValueError("expected one or more scopes separated by spaces")
    if any(not p for p in parts):
        raise ValueError("scopes must be non-empty tokens separated by spaces")


_FORMAT_VALIDATORS: Dict[str, Callable[[str], None]] = {
    "pem_private_key": _validate_pem_private_key,
    "jwt_kid": _validate_jwt_kid,
    "google_sa_json": _validate_google_sa_json,
    "slack_bot_token": _validate_slack_bot_token,
    "stripe_secret_key": _validate_stripe_secret_key,
    "opaque_secret": _validate_opaque_secret,
    "scopes_space_separated": _validate_scopes_space_separated,
}


def validate_required_secrets(connector_id: str, logical_secrets: Mapping[str, str]) -> None:
    """Raise ValueError when required varying keys are missing from an effective secrets map."""
    required = REQUIRED_SECRETS_BY_CONNECTOR.get(connector_id) or []
    missing = [
        key
        for key in required
        if key not in logical_secrets or not str(logical_secrets.get(key) or "").strip()
    ]
    if missing:
        raise ValueError(f"missing required secrets for {connector_id}: {', '.join(missing)}")


def validate_secret_formats(connector_id: str, logical_secrets: Mapping[str, str]) -> None:
    """Raise ValueError when supplied secret values fail format checks for this connector."""
    formats = SECRET_FORMAT_BY_CONNECTOR.get(connector_id) or {}
    errors: List[str] = []
    for key, value in logical_secrets.items():
        fmt = formats.get(str(key))
        if not fmt:
            continue
        raw = str(value).strip()
        if not raw:
            continue
        validator = _FORMAT_VALIDATORS.get(fmt)
        if not validator:
            continue
        try:
            validator(raw)
        except ValueError as exc:
            errors.append(f"{key}: {exc}")
    if errors:
        raise ValueError("; ".join(errors))


_lock = threading.RLock()
# Faithful nested secrets: tenant → connector → config_name → logical_key → value.
_nested_secrets_mirror: Dict[str, Dict[str, Dict[str, Dict[str, str]]]] = {}


def existing_logical_secrets(tenant_id: str, connector_id: str, config_name: str) -> Dict[str, str]:
    with _lock:
        return dict(
            ((_nested_secrets_mirror.get(tenant_id) or {}).get(connector_id) or {}).get(config_name)
            or {}
        )


def tenants_path(*, for_write: bool = False) -> Path:
    """Resolve the tenants YAML path.

    ``NW_TENANTS_PATH`` wins; otherwise write to ``config/tenants.yaml``.
    Reads may fall back to legacy ``config/playground_tenants.yaml`` if present.
    """
    override = (
        os.environ.get("NW_TENANTS_PATH", "").strip()
        or os.environ.get("NW_PLAYGROUND_TENANTS_PATH", "").strip()
    )
    if override:
        return Path(override)
    if for_write or DEFAULT_TENANTS_PATH.is_file() or not _LEGACY_TENANTS_PATH.is_file():
        return DEFAULT_TENANTS_PATH
    return _LEGACY_TENANTS_PATH


def _export_secrets_mirror() -> Dict[str, Any]:
    return {
        t: {c: {cfg: dict(kv) for cfg, kv in configs.items()} for c, configs in cons.items()}
        for t, cons in _nested_secrets_mirror.items()
    }


def save_tenants(store: ConnectorConfigStore) -> None:
    path = tenants_path(for_write=True)
    with _lock:
        payload = {
            "tenants": store.export_all(),
            "secrets": _export_secrets_mirror(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".yaml.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                payload, f, default_flow_style=False, allow_unicode=True, sort_keys=False
            )
        tmp.replace(path)
    logger.info("Wrote tenants file", extra={"path": str(path)})


def upsert_tenant_secrets(
    tenant_id: str,
    connector_id: str,
    logical_secrets: Mapping[str, str],
    *,
    config_name: str,
    auto_shared_env: bool = True,
    require_varying: bool = True,
) -> List[str]:
    """Merge logical secrets for one named config. Returns logical keys set for that config.

    Empty / omitted keys keep existing values for this config only (partial update).
    New configs must supply required secrets — sibling configs are not shared.
    """
    name = (config_name or "").strip()
    if not name:
        raise ValueError("config_name is required for tenant secrets")

    overlay = OverlaySecretProvider.instance()
    merged: Dict[str, str] = {
        str(k).strip(): str(v)
        for k, v in logical_secrets.items()
        if k is not None and str(k).strip() and v is not None and str(v).strip()
    }

    # Everything below reads and mutates the shared `_nested_secrets_mirror` and
    # writes to the shared overlay; hold the module lock for the whole
    # read-merge-validate-write sequence like the sibling mutators
    # (save_tenants/load_tenants/clear_*_secrets) do, so a concurrent upsert,
    # save, or reload can't interleave with this one (M-1, 2026-09-01 review).
    with _lock:
        existing = existing_logical_secrets(tenant_id, connector_id, name)
        if auto_shared_env:
            for env_key, logical_key in SHARED_ENV_BY_CONNECTOR.get(connector_id, []):
                if logical_key in merged and merged[logical_key].strip():
                    continue
                if logical_key in existing:
                    continue
                host_val = os.environ.get(env_key)
                if host_val is None:
                    host_val = os.environ.get(env_key.lower())
                if host_val is not None and str(host_val).strip():
                    merged[logical_key] = str(host_val).strip()

        if require_varying:
            effective = {**existing, **merged}
            validate_required_secrets(connector_id, effective)

        if merged:
            validate_secret_formats(connector_id, merged)

        flat: Dict[str, str] = {}
        for logical_key, value in merged.items():
            scoped = tenant_scoped_secret_key(
                tenant_id, connector_id, logical_key, config_name=name
            )
            flat[scoped] = value
            _nested_secrets_mirror.setdefault(tenant_id, {}).setdefault(
                connector_id, {}
            ).setdefault(name, {})[logical_key] = value

        if flat:
            overlay.set_many(flat)
        return sorted(
            (_nested_secrets_mirror.get(tenant_id) or {}).get(connector_id, {}).get(name, {}).keys()
        )


def _is_legacy_connector_secrets(kv: Mapping[str, Any]) -> bool:
    """True when secrets are flat logical→string (pre per-config nesting)."""
    if not kv:
        return False
    return all(not isinstance(v, dict) for v in kv.values())


def load_tenants(store: ConnectorConfigStore) -> None:
    path = tenants_path()
    if not path.is_file():
        return
    with _lock:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            logger.warning("Ignoring invalid tenants file", extra={"path": str(path)})
            return

        tenants = raw.get("tenants") or {}
        if isinstance(tenants, dict):
            for tenant_id, connectors in tenants.items():
                if not isinstance(connectors, dict):
                    continue
                for connector_id, docs in connectors.items():
                    if not isinstance(docs, list):
                        continue
                    for doc in docs:
                        if not isinstance(doc, dict) or not doc.get("name"):
                            continue
                        name = str(doc["name"])
                        try:
                            store.create(tenant_id, connector_id, doc)
                        except Exception:
                            try:
                                store.update(tenant_id, connector_id, name, doc)
                            except ConfigNotFoundError:
                                logger.warning(
                                    "Could not load tenant config",
                                    extra={
                                        "tenant_id": tenant_id,
                                        "connector_id": connector_id,
                                        "name": name,
                                    },
                                )

        global _nested_secrets_mirror
        _nested_secrets_mirror = {}
        flat: Dict[str, str] = {}
        secrets = raw.get("secrets") or {}
        if isinstance(secrets, dict):
            for tenant_id, connectors in secrets.items():
                if not isinstance(connectors, dict):
                    continue
                for connector_id, kv in connectors.items():
                    if not isinstance(kv, dict):
                        continue
                    if _is_legacy_connector_secrets(kv):
                        # Recommended: no auto-migrate — re-enter per-config credentials.
                        logger.warning(
                            "Skipping legacy connector-scoped secrets; "
                            "re-save credentials per named config",
                            extra={
                                "tenant_id": tenant_id,
                                "connector_id": connector_id,
                            },
                        )
                        continue
                    for config_name, logical_map in kv.items():
                        if not isinstance(logical_map, dict):
                            continue
                        cfg = str(config_name)
                        for logical, value in logical_map.items():
                            scoped = tenant_scoped_secret_key(
                                tenant_id,
                                connector_id,
                                str(logical),
                                config_name=cfg,
                            )
                            flat[scoped] = str(value)
                            _nested_secrets_mirror.setdefault(tenant_id, {}).setdefault(
                                connector_id, {}
                            ).setdefault(cfg, {})[str(logical)] = str(value)
        OverlaySecretProvider.instance().replace_all(flat)
        logger.info(
            "Loaded tenants file",
            extra={
                "path": str(path),
                "tenants": len(tenants) if isinstance(tenants, dict) else 0,
            },
        )


def list_secret_logical_keys(tenant_id: str, connector_id: str, config_name: str) -> List[str]:
    name = (config_name or "").strip()
    if not name:
        return []
    with _lock:
        return sorted(
            (_nested_secrets_mirror.get(tenant_id) or {}).get(connector_id, {}).get(name, {}).keys()
        )


def clear_config_secrets(tenant_id: str, connector_id: str, config_name: str) -> None:
    """Drop overlay + mirror secrets for one named config."""
    name = (config_name or "").strip()
    if not name:
        return
    with _lock:
        cons = _nested_secrets_mirror.get(tenant_id) or {}
        configs = cons.get(connector_id) or {}
        logical_map = configs.pop(name, {})
        if connector_id in cons and not cons[connector_id]:
            del cons[connector_id]
        if tenant_id in _nested_secrets_mirror and not _nested_secrets_mirror[tenant_id]:
            del _nested_secrets_mirror[tenant_id]

        overlay = OverlaySecretProvider.instance()
        data = overlay.export()
        keys_to_drop = set(logical_map.keys()) | set(
            overlay.logical_keys_for(tenant_id, connector_id, config_name=name)
        )
        for logical_key in keys_to_drop:
            data.pop(
                tenant_scoped_secret_key(tenant_id, connector_id, logical_key, config_name=name),
                None,
            )
        overlay.replace_all(data)


def clear_tenant_connector_secrets(tenant_id: str, connector_id: str) -> None:
    """Drop overlay + mirror secrets for all configs under one tenant/connector."""
    with _lock:
        configs = (_nested_secrets_mirror.get(tenant_id) or {}).pop(connector_id, {})
        if tenant_id in _nested_secrets_mirror and not _nested_secrets_mirror[tenant_id]:
            del _nested_secrets_mirror[tenant_id]

        overlay = OverlaySecretProvider.instance()
        data = overlay.export()
        for config_name, logical_map in configs.items():
            for logical_key in set(logical_map.keys()) | set(
                overlay.logical_keys_for(tenant_id, connector_id, config_name=config_name)
            ):
                data.pop(
                    tenant_scoped_secret_key(
                        tenant_id,
                        connector_id,
                        logical_key,
                        config_name=config_name,
                    ),
                    None,
                )
        overlay.replace_all(data)
