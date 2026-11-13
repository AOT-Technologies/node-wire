# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Load a node-wire connector class from its source tree.

Shared by ``from_connector`` and ``tool_listing``; kept in its own module so
neither has to import the other.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


# logic.py path → (source stamp, class). One ``from-openapi`` run asks for the same
# connector several times (tool listing size, action discovery); importing it re-creates
# every pydantic model in its schema module, so reuse the class while the source is unchanged.
_loaded: dict[str, tuple[tuple[tuple[str, int, int], ...], type]] = {}


def _source_stamp(package_dir: Path) -> tuple[tuple[str, int, int], ...]:
    stamps = []
    for path in sorted(package_dir.rglob("*.py")):
        stat = path.stat()
        stamps.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(stamps)


def load_connector_class(logic_py: Path) -> type:
    """Import ``node_wire_<id>.logic`` from its own ``src`` tree and return the connector class.

    The ``src`` directory is put at the front of ``sys.path`` only for the import
    (see :func:`nw_mcp_builder.from_connector.discover_actions` for why that is safe for
    ``node_wire_runtime``). Re-imported only when the package's ``.py`` files changed.
    """
    key = str(logic_py.resolve())
    stamp = _source_stamp(logic_py.parent)
    cached = _loaded.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    cls = _import_connector_class(logic_py)
    _loaded[key] = (stamp, cls)
    return cls


def _import_connector_class(logic_py: Path) -> type:
    package_dir = logic_py.parent
    connector_id = package_dir.name.removeprefix("node_wire_")
    src_root = str(package_dir.parent)
    mod_name = f"node_wire_{connector_id}"

    for key in list(sys.modules):
        if key == mod_name or key.startswith(mod_name + "."):
            del sys.modules[key]

    old_path = list(sys.path)
    try:
        sys.path.insert(0, src_root)
        logic = importlib.import_module(f"{mod_name}.logic")
    finally:
        sys.path[:] = old_path

    from node_wire_runtime import BaseConnector

    cls = None
    for attr in dir(logic):
        obj = getattr(logic, attr)
        if isinstance(obj, type) and issubclass(obj, BaseConnector) and obj is not BaseConnector:
            if getattr(obj, "connector_id", None) == connector_id:
                cls = obj
                break
    if cls is None:
        raise ValueError(
            f"No BaseConnector subclass with connector_id={connector_id!r} found under "
            f"{package_dir}"
        )
    return cls
