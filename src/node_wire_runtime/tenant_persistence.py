#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Multi-tenant config + secret overlay persistence.

Simplified: one YAML file rewritten on each mutation; gitignored by the repo.

Lives in the runtime (not a binding) because REST, gRPC, and MCP must all
observe the same persisted tenant/config dataset from the same file — this is
shared runtime *state* across transports, not connector business logic.

Connector-specific secret shapes are declared, not inferred: see
:func:`declare_secret_shape`. A connector that has not declared one cannot
persist tenant secrets once enforcement is on.

Rollout is staged via ``NW_SECRET_SHAPE_POLICY`` (:func:`secret_shape_policy`):

- ``warn`` (default) — undeclared connectors are logged and allowed through, so
  upgrading this package cannot break an existing deployment.
- ``enforce`` — undeclared connectors are refused.

Boot never applies the gate: :func:`load_tenants` restores already-persisted
secrets regardless, so a missing declaration can't become a startup failure.
Use :func:`undeclared_persisted_connectors` to find what is in that state.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Set, Tuple

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
    # Credentials are optional (logic.py falls back to env, then to an
    # unauthenticated relay), so these are format-checked but not required.
    "smtp": {
        "SMTP_USERNAME": "opaque_secret",
        "SMTP_PASSWORD": "opaque_secret",
    },
}


class SecretShapeNotDeclaredError(ValueError):
    """Raised when a connector persists tenant secrets without a declared shape.

    Subclasses ``ValueError`` so the REST binding's existing ``except ValueError``
    handlers surface it as a 400 rather than a 500.
    """


# Connectors that genuinely have no tenant secrets. Listing them is the explicit
# form of "nothing to validate", so it reads as a decision rather than as a row
# someone forgot to add.
_NO_TENANT_SECRETS: FrozenSet[str] = frozenset({"http_generic"})

# Every connector permitted to persist tenant secrets. Seeded from the tables
# above; out-of-tree and generated connectors register via declare_secret_shape().
# Absence is an error under the "enforce" policy — see
# require_declared_secret_shape().
_DECLARED_SECRET_SHAPES: Set[str] = (
    set(SHARED_ENV_BY_CONNECTOR)
    | set(REQUIRED_SECRETS_BY_CONNECTOR)
    | set(SECRET_FORMAT_BY_CONNECTOR)
    | set(_NO_TENANT_SECRETS)
)

SECRET_SHAPE_POLICY_ENV = "NW_SECRET_SHAPE_POLICY"
SECRET_SHAPE_POLICY_WARN = "warn"
SECRET_SHAPE_POLICY_ENFORCE = "enforce"


def secret_shape_policy() -> str:
    """Resolve the undeclared-connector policy: ``warn`` (default) or ``enforce``.

    Defaults to ``warn`` so upgrading this package cannot start rejecting secret
    writes for connectors that predate the declaration requirement — operators
    run warn first, read the logged connector ids, declare them, then set
    ``enforce``. An *invalid* value is treated as ``enforce``, matching
    ``mcp_scope_policy``'s unknown-mode-means-deny: a typo in a security knob
    must not silently disable it.

    Read per call, not cached, so it can be flipped without a restart.
    """
    raw = (os.getenv(SECRET_SHAPE_POLICY_ENV) or "").strip().lower()
    if not raw:
        return SECRET_SHAPE_POLICY_WARN
    if raw in (SECRET_SHAPE_POLICY_WARN, SECRET_SHAPE_POLICY_ENFORCE):
        return raw
    logger.warning(
        "Invalid %s=%r; falling back to %r",
        SECRET_SHAPE_POLICY_ENV,
        raw,
        SECRET_SHAPE_POLICY_ENFORCE,
    )
    return SECRET_SHAPE_POLICY_ENFORCE


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


def declare_secret_shape(
    connector_id: str,
    *,
    required: Iterable[str] = (),
    formats: Optional[Mapping[str, str]] = None,
    shared_env: Iterable[Tuple[str, str]] = (),
) -> None:
    """Declare a connector's tenant-secret shape. Call at import time.

    Out-of-tree and generated connectors must call this before they can persist
    tenant secrets. Passing only ``connector_id`` declares that the connector has
    no tenant secrets — an explicit statement, unlike the absence it replaces.

    The declaration **replaces** any previous one for this connector rather than
    merging into it, so dropping a key from a re-declaration actually drops its
    check and ``declare_secret_shape(cid)`` really does mean "no secrets".
    Repeating an identical call is a no-op; replacing a *different* existing
    shape is logged, because that is usually an accident (two packages claiming
    the same connector id) rather than an intent.

    A format kind with no registered validator raises here, so a typo fails at
    declaration time instead of silently skipping the check at write time.
    """
    cid = (connector_id or "").strip()
    if not cid:
        raise ValueError("connector_id is required to declare a secret shape")

    fmt_map = {str(k): str(v) for k, v in (formats or {}).items()}
    unknown = sorted({v for v in fmt_map.values() if v not in _FORMAT_VALIDATORS})
    if unknown:
        raise ValueError(
            f"unknown secret format kind(s) for {cid}: {', '.join(unknown)}. "
            f"Known kinds: {', '.join(sorted(_FORMAT_VALIDATORS))}"
        )

    required_keys = [str(k) for k in required]
    env_pairs = [(str(a), str(b)) for a, b in shared_env]

    # Mutating the three shared tables — take the module lock like every other
    # mutator here (save_tenants/load_tenants/upsert_tenant_secrets) so a
    # declaration can't interleave with a read-merge-validate-write sequence.
    # _lock is an RLock, so a declaration made from under it still works.
    with _lock:
        previous = (
            REQUIRED_SECRETS_BY_CONNECTOR.get(cid),
            SECRET_FORMAT_BY_CONNECTOR.get(cid),
            SHARED_ENV_BY_CONNECTOR.get(cid),
        )
        incoming = (
            required_keys or None,
            fmt_map or None,
            env_pairs or None,
        )
        if cid in _DECLARED_SECRET_SHAPES and previous != incoming:
            logger.warning(
                "Replacing an existing secret-shape declaration",
                extra={"connector_id": cid},
            )

        for table, value in (
            (REQUIRED_SECRETS_BY_CONNECTOR, required_keys),
            (SECRET_FORMAT_BY_CONNECTOR, fmt_map),
            (SHARED_ENV_BY_CONNECTOR, env_pairs),
        ):
            if value:
                table[cid] = value  # type: ignore[assignment]
            else:
                table.pop(cid, None)
        _DECLARED_SECRET_SHAPES.add(cid)


def require_declared_secret_shape(connector_id: str) -> None:
    """Refuse an undeclared connector under the ``enforce`` policy; warn under ``warn``.

    Fail-closed in spirit, matching ``base_connector.resolve_auth_provider`` and
    ``identity.py``: an unrecognised connector_id must not be handed the empty
    shape that used to mean "store anything, validate nothing". The staged
    default (see :func:`secret_shape_policy`) is what makes that safe to adopt.

    Membership is read without the lock: ``set`` lookup is atomic under the GIL
    and this sits on the write path, so locking every read would add contention
    for no benefit.
    """
    if connector_id in _DECLARED_SECRET_SHAPES:
        return

    policy = secret_shape_policy()
    # The operator gets the actionable detail; the caller does not. Echoing the
    # registry back over HTTP would let any tenant enumerate every connector
    # deployed on the host by POSTing guesses at /v1/connectors/<id>/secrets.
    logger.warning(
        "Tenant secrets submitted for a connector with no declared secret shape; "
        "declare_secret_shape() is required before these secrets can be validated "
        "(policy=%s, declared_count=%d)",
        policy,
        len(_DECLARED_SECRET_SHAPES),
        extra={"connector_id": connector_id, "secret_shape_policy": policy},
    )
    if policy == SECRET_SHAPE_POLICY_WARN:
        return
    # Deliberately does not distinguish "no such connector" from "exists but
    # undeclared" — that difference is itself deployment information.
    raise SecretShapeNotDeclaredError(
        f"connector {connector_id!r} is unknown or not configured to accept tenant secrets"
    )


def _undeclared_in_mirror() -> List[str]:
    """Connector ids present in the loaded secrets mirror with no declared shape."""
    seen: Set[str] = set()
    for connectors in _nested_secrets_mirror.values():
        if isinstance(connectors, dict):
            seen.update(str(cid) for cid in connectors)
    return sorted(seen - _DECLARED_SECRET_SHAPES)


def undeclared_persisted_connectors() -> List[str]:
    """Connector ids with persisted tenant secrets but no declared secret shape.

    Operational counterpart to the count logged at load time: these connectors
    keep serving already-stored secrets unvalidated, and under the ``enforce``
    policy any *new* write for them is refused. Declare them, or clear their
    secrets.
    """
    with _lock:
        return _undeclared_in_mirror()


def validate_required_secrets(connector_id: str, logical_secrets: Mapping[str, str]) -> None:
    """Raise ValueError when required varying keys are missing from an effective secrets map."""
    require_declared_secret_shape(connector_id)
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
    require_declared_secret_shape(connector_id)
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

    # Gate here, not only inside the validators: they run conditionally
    # (`if require_varying` / `if merged`), so an undeclared connector could
    # otherwise reach the write below with both checks skipped.
    require_declared_secret_shape(connector_id)

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
                        # Note: tenant_id/connector_id are deliberately omitted from
                        # `extra` here — both are derived from the same traversal as
                        # the secret values themselves, so CodeQL's clear-text-logging
                        # check flags them as tainted even though they're plain ids.
                        logger.warning(
                            "Skipping legacy connector-scoped secrets; "
                            "re-save credentials per named config"
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
        # Boot deliberately does not run the declaration gate: refusing to load
        # already-persisted secrets would turn a missing declaration into a
        # startup failure. But the gap must not be invisible, so surface a count
        # and let the operator get the ids from undeclared_persisted_connectors()
        # — the ids themselves come from the same traversal as the secret values,
        # which CodeQL's clear-text-logging check treats as tainted (see above).
        stale = _undeclared_in_mirror()
        if stale:
            logger.warning(
                "Loaded persisted secrets for %d connector(s) with no declared secret "
                "shape; these were stored before the declaration requirement and are "
                "served unvalidated. Call undeclared_persisted_connectors() for the ids",
                len(stale),
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
