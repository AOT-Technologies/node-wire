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

    for key in secret_keys:
        if key and key not in existing_keys:
            new_lines.append(f"{key}=")

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def apply_wire(
    node_wire_root: Path,
    connector_id: str,
    *,
    base_url: str,
    auth_block: dict[str, Any],
    secret_key: str,
) -> None:
    yaml_path = node_wire_root / "config" / "connectors.yaml"
    env_path = node_wire_root / "sample.env"
    wire_connectors_yaml(yaml_path, connector_id, base_url=base_url, auth_block=auth_block)
    keys = [secret_key] if secret_key else []
    wire_sample_env(env_path, connector_id, secret_keys=keys)
    logger.info("--wire updated %s and %s", yaml_path, env_path)
