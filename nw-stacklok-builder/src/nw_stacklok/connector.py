# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Read a generated node-wire connector: its build report and its action input models."""

from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class ConnectorNotFoundError(FileNotFoundError):
    """The node-wire connector the scope names has not been generated."""


@dataclass(frozen=True)
class ActionField:
    """One input field of a connector action, as the runtime binds it."""

    field_name: str
    wire_name: str
    location: str  # path | query | header | body_property | body
    required: bool


@dataclass(frozen=True)
class ConnectorInfo:
    connector_id: str
    # (METHOD, /path) → action name, from nw-connector-builder's report.json.
    endpoints: Dict[Tuple[str, str], str]
    # (METHOD, /path) → reason, for operations the connector builder soft-dropped.
    skipped: Dict[Tuple[str, str], str]
    fields: Dict[str, List[ActionField]]


def read_connector(node_wire_root: Path, connector_id: str) -> ConnectorInfo:
    """Endpoint map from ``report.json`` plus field bindings from the connector's models."""
    report_file = node_wire_root / "packages" / "connectors" / connector_id / "report.json"
    if not report_file.is_file():
        raise ConnectorNotFoundError(
            f"No build report for node-wire connector {connector_id!r} at {report_file}; "
            "generate the connector first (nw gen-stacklok builds it)."
        )
    report = json.loads(report_file.read_text(encoding="utf-8"))
    endpoints = {
        (str(a["method"]).upper(), str(a["path"])): str(a["name"])
        for a in report.get("generated_actions") or []
    }
    skipped = {
        (str(d["method"]).upper(), str(d["path"])): str(d.get("reason") or "")
        for d in report.get("skipped") or []
    }
    cls = load_connector_class(node_wire_root, connector_id)
    metas = cls.nw_action_metas()
    fields = {name: _fields(meta.input_model) for name, meta in metas.items()}
    return ConnectorInfo(connector_id, endpoints, skipped, fields)


def _fields(model: Any) -> List[ActionField]:
    out: List[ActionField] = []
    for name, info in model.model_fields.items():
        if name == "action":
            continue
        extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
        location = str(extra.get("nw_in") or "")
        if not location:
            continue
        out.append(
            ActionField(
                field_name=name,
                wire_name=str(extra.get("nw_wire_name") or info.alias or name),
                location=location,
                required=info.is_required(),
            )
        )
    return out


def load_connector_class(node_wire_root: Path, connector_id: str) -> Any:
    """Import ``node_wire_<id>.logic`` from ``<root>/src`` and return its connector class."""
    from node_wire_runtime import BaseConnector

    src_root = node_wire_root / "src"
    mod_name = f"node_wire_{connector_id}"
    if not (src_root / mod_name / "logic.py").is_file():
        raise ConnectorNotFoundError(
            f"Connector source missing: {src_root / mod_name / 'logic.py'}"
        )
    for key in list(sys.modules):
        if key == mod_name or key.startswith(mod_name + "."):
            del sys.modules[key]
    old_path = list(sys.path)
    try:
        sys.path.insert(0, str(src_root))
        logic = importlib.import_module(f"{mod_name}.logic")
    finally:
        sys.path[:] = old_path
    found: Optional[type] = None
    for attr in dir(logic):
        obj = getattr(logic, attr)
        if (
            isinstance(obj, type)
            and issubclass(obj, BaseConnector)
            and getattr(obj, "connector_id", None) == connector_id
        ):
            found = obj
            break
    if found is None:
        raise ConnectorNotFoundError(f"No connector class with id {connector_id!r} in {mod_name}")
    return found
