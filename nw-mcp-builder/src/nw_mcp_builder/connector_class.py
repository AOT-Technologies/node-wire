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


def load_connector_class(logic_py: Path) -> type:
    """Import ``node_wire_<id>.logic`` from its own ``src`` tree and return the connector class.

    The ``src`` directory is put at the front of ``sys.path`` only for the import
    (see :func:`nw_mcp_builder.from_connector.discover_actions` for why that is safe for
    ``node_wire_runtime``).
    """
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
