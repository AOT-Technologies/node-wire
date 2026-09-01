#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
node_wire_runtime.connector_registry
=====================================

Entry-point–based connector auto-registration.

Installed connector packages declare themselves via the ``node_wire.connectors``
entry point group in their ``pyproject.toml``:

    [project.entry-points."node_wire.connectors"]
    fhir_epic = "node_wire_fhir_epic.logic"

Calling :func:`auto_register` loads each allowed connector package's ``logic`` module,
which triggers ``BaseConnector.__init_subclass__`` — this both registers the connector
class and, via its declarative ``error_map`` class attribute, registers the connector's
exception mappings with ``ErrorMapper`` under that connector's own ``connector_id``.
There is no separate registration step: a connector's ``logic`` module is the sole
source of its runtime registration.

Allowlist (recommended for production):

* ``NW_ALLOWED_CONNECTORS`` — comma-separated entry point **names** (e.g. ``fhir_epic,http_generic``).
  If unset or empty, no entry points are loaded (secure default).

* ``NW_CONNECTOR_MODULE_PREFIX`` — if set (default ``node_wire_``), entry points whose
  target module does not start with this prefix are skipped with a warning.

"""

from __future__ import annotations

import importlib
import logging
import os
from importlib.metadata import EntryPoint, entry_points
from typing import List

logger = logging.getLogger("node_wire_runtime.connector_registry")


def _parse_allowed_names() -> set[str]:
    """Return allowed entry point names. Defaults to empty set (nothing allowed)."""
    raw = os.environ.get("NW_ALLOWED_CONNECTORS")
    if raw is None or not str(raw).strip():
        return set()
    return {x.strip() for x in str(raw).split(",") if x.strip()}


def _module_prefix() -> str | None:
    """Prefix that logic module names must start with; None disables the check."""
    raw = os.environ.get("NW_CONNECTOR_MODULE_PREFIX")
    if raw is None:
        return "node_wire_"
    s = str(raw).strip()
    return s if s else None


def _logic_module_dotted_path(ep: EntryPoint) -> str:
    """Dotted import path for the entry point target (e.g. ``node_wire_fhir_epic.logic``)."""
    val = ep.value.strip()
    if ":" in val:
        return val.split(":", 1)[0].strip()
    return val


def _should_skip_ep(ep: EntryPoint, allowed: set[str], prefix: str | None) -> bool:
    if ep.name not in allowed:
        logger.warning(
            "Skipping connector entry point %r (not in NW_ALLOWED_CONNECTORS)",
            ep.name,
        )
        return True
    logic_mod = _logic_module_dotted_path(ep)
    if prefix and not logic_mod.startswith(prefix):
        logger.warning(
            "Skipping connector entry point %r: module %r does not start with NW_CONNECTOR_MODULE_PREFIX %r",
            ep.name,
            logic_mod,
            prefix,
        )
        return True
    return False


def auto_register() -> List[str]:
    """Load connector packages declared under ``node_wire.connectors``.

    For each entry point, loads the ``logic`` module — this triggers
    ``BaseConnector.__init_subclass__``, which populates the connector registry
    exposed via ``get_connector_registry()`` and registers the connector's
    ``error_map`` with ``ErrorMapper`` under its own ``connector_id``.

    If an allowed connector is not discovered via entry points, attempts to fallback
    to importing the logic module directly.

    Returns the list of loaded module name strings (useful for testing / logging).
    """
    loaded: List[str] = []
    allowed = _parse_allowed_names()
    prefix = _module_prefix()

    discovered_names = set()

    for ep in entry_points(group="node_wire.connectors"):
        if _should_skip_ep(ep, allowed, prefix):
            continue

        logic_mod = _logic_module_dotted_path(ep)
        importlib.import_module(logic_mod)
        loaded.append(logic_mod)
        logger.debug("Registered connector: %s (%s)", ep.name, ep.value)

        discovered_names.add(ep.name)

    # Fallback for allowlisted names not discovered via entry points
    for name in allowed:
        if name not in discovered_names:
            pkg_prefix = prefix if prefix is not None else "node_wire_"
            logic_mod = f"{pkg_prefix}{name}.logic"

            try:
                importlib.import_module(logic_mod)
                loaded.append(logic_mod)
                logger.debug("Registered connector via fallback: %s (%s)", name, logic_mod)
            except ModuleNotFoundError as exc:
                if exc.name == logic_mod or exc.name == f"{pkg_prefix}{name}":
                    logger.debug("Fallback connector module not found: %s", logic_mod)
                    continue
                else:
                    logger.error(
                        "Import error inside fallback %s (missing dep: %s): %s",
                        logic_mod,
                        exc.name,
                        exc,
                    )
                    raise
            except Exception as exc:
                logger.error("Unexpected error loading fallback %s: %s", logic_mod, exc)
                raise

    return loaded
