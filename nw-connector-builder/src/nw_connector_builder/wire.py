# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""``--wire``: comment-preserving connectors.yaml + sample.env edits."""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class WireError(Exception):
    pass


def wire_connectors_yaml(
    path: Path,
    connector_id: str,
    *,
    base_url: str,
    auth_block: dict[str, Any],
    extra_auth_blocks: dict[str, dict[str, Any]] | None = None,
) -> None:
    try:
        from ruamel.yaml import YAML
    except ImportError as exc:
        raise WireError("ruamel.yaml is required for --wire") from exc

    yaml = YAML()
    yaml.preserve_quotes = True
    if not path.is_file():
        raise WireError(f"connectors.yaml not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.load(f)

    if data is None:
        data = {}
    if "connectors" not in data or data["connectors"] is None:
        data["connectors"] = {}

    block: dict[str, Any] = {
        "enabled": True,
        "exposed_via": ["rest", "grpc", "mcp"],
        "base_url": base_url,
    }
    if auth_block:
        block["auth"] = auth_block
    if extra_auth_blocks:
        # Named schemes beyond the connector default (multi-scheme specs).
        block["auth_schemes"] = extra_auth_blocks
    data["connectors"][connector_id] = block

    # Atomic write
    fd, tmp_name = tempfile.mkstemp(prefix="connectors.", suffix=".yaml", dir=str(path.parent))
    os_close = True
    try:
        import os

        os.close(fd)
        os_close = False
        tmp = Path(tmp_name)
        with tmp.open("w", encoding="utf-8") as f:
            yaml.dump(data, f)
        tmp.replace(path)
    finally:
        if os_close:
            import os

            try:
                os.close(fd)
            except Exception:  # noqa: BLE001
                pass


def wire_sample_env(
    path: Path,
    connector_id: str,
    *,
    secret_keys: list[str],
    secret_defaults: dict[str, str] | None = None,
    host_supplied_keys: frozenset[str] = frozenset(),
) -> None:
    if not path.is_file():
        # Create minimal file
        path.write_text("", encoding="utf-8")

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    # NW_ALLOWED_CONNECTORS
    allow_re = re.compile(r"^(NW_ALLOWED_CONNECTORS=)(.*)$")
    found_allow = False
    new_lines: list[str] = []
    for line in lines:
        m = allow_re.match(line)
        if m:
            found_allow = True
            prefix, val = m.group(1), m.group(2)
            parts = [p.strip() for p in val.split(",") if p.strip()]
            if connector_id not in parts:
                parts.append(connector_id)
            new_lines.append(prefix + ",".join(parts))
        else:
            new_lines.append(line)
    if not found_allow:
        new_lines.append(f"NW_ALLOWED_CONNECTORS={connector_id}")

    existing_keys = set()
    key_re = re.compile(r"^([A-Z0-9_]+)=")
    for line in new_lines:
        m = key_re.match(line)
        if m:
            existing_keys.add(m.group(1))

    defaults = secret_defaults or {}
    for key in secret_keys:
        if key and key not in existing_keys:
            if key in host_supplied_keys:
                new_lines.append(
                    f"# {key}: host-supplied credential — Node Wire will not obtain, "
                    "refresh, or detect the expiry of this token. See auth.notes in "
                    f"the {connector_id} build report."
                )
            new_lines.append(f"{key}={defaults.get(key, '')}")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def apply_wire(
    node_wire_root: Path,
    connector_id: str,
    *,
    base_url: str,
    auth_block: dict[str, Any],
    secret_keys: list[str],
    secret_defaults: dict[str, str] | None = None,
    host_supplied: bool = False,
    extra_auth_plans: dict[str, Any] | None = None,
) -> None:
    """``extra_auth_plans``: scheme_name -> plan with ``.yaml_block`` / secrets / ``.tier``.

    Empty/``None`` for single-scheme connectors (the common case).
    """
    extra_auth_plans = extra_auth_plans or {}

    extra_auth_blocks = {name: plan.yaml_block for name, plan in extra_auth_plans.items()}

    all_secret_keys = list(secret_keys)
    all_secret_defaults = dict(secret_defaults or {})
    host_supplied_keys: set[str] = set(secret_keys) if host_supplied else set()
    for plan in extra_auth_plans.values():
        all_secret_keys.extend(plan.secret_keys)
        all_secret_defaults.update(plan.secret_defaults)
        if plan.tier == "host_supplied":
            host_supplied_keys.update(plan.secret_keys)

    yaml_path = node_wire_root / "config" / "connectors.yaml"
    env_path = node_wire_root / "sample.env"
    wire_connectors_yaml(
        yaml_path,
        connector_id,
        base_url=base_url,
        auth_block=auth_block,
        extra_auth_blocks=extra_auth_blocks,
    )
    wire_sample_env(
        env_path,
        connector_id,
        secret_keys=[k for k in all_secret_keys if k],
        secret_defaults=all_secret_defaults,
        host_supplied_keys=frozenset(k for k in host_supplied_keys if k),
    )
    logger.info("--wire updated %s and %s", yaml_path, env_path)
