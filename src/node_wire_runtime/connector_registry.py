"""
node_wire_runtime.connector_registry
=====================================

Entry-point–based connector auto-registration.

Installed connector packages declare themselves via the ``node_wire.connectors``
entry point group in their ``pyproject.toml``:

    [project.entry-points."node_wire.connectors"]
    fhir_epic = "node_wire_fhir_epic.logic"

Calling :func:`auto_register` loads each allowed connector package's ``logic`` module
(triggering ``BaseConnector.__init_subclass__`` registration) and its optional
``registration`` module (for ``ErrorMapper`` side effects).

Allowlist (recommended for production):

* ``NW_ALLOWED_CONNECTORS`` — comma-separated entry point **names** (e.g. ``fhir_epic,http_generic``).
  If unset or empty, all discovered entry points are loaded (development default).

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


def _parse_allowed_names() -> set[str] | None:
    """Return allowed entry point names, or None if no allowlist (load all)."""
    raw = os.environ.get("NW_ALLOWED_CONNECTORS")
    if raw is None or not str(raw).strip():
        return None
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


def _parent_package_for_logic_module(logic_module: str) -> str:
    """``node_wire_fhir_epic.logic`` -> ``node_wire_fhir_epic``."""
    return logic_module.rsplit(".", 1)[0]


def _should_skip_ep(ep: EntryPoint, allowed: set[str] | None, prefix: str | None) -> bool:
    if allowed is not None and ep.name not in allowed:
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

    For each entry point:
    1. Load the ``logic`` module — triggers ``BaseConnector.__init_subclass__``,
       which populates ``_CONNECTOR_REGISTRY``.
    2. Attempt to load a sibling ``registration`` module (optional) for
       ``ErrorMapper`` registrations and other import-time side effects.

    Returns the list of loaded module name strings (useful for testing / logging).
    """
    loaded: List[str] = []
    allowed = _parse_allowed_names()
    prefix = _module_prefix()

    for ep in entry_points(group="node_wire.connectors"):
        if _should_skip_ep(ep, allowed, prefix):
            continue

        logic_mod = _logic_module_dotted_path(ep)
        importlib.import_module(logic_mod)
        loaded.append(logic_mod)
        logger.debug("Registered connector: %s (%s)", ep.name, ep.value)

        pkg = _parent_package_for_logic_module(logic_mod)
        reg_name = f"{pkg}.registration"
        try:
            importlib.import_module(reg_name)
            loaded.append(reg_name)
            logger.debug("Loaded registration module: %s", reg_name)
        except ModuleNotFoundError as exc:
            if exc.name == reg_name:
                pass
            else:
                logger.error(
                    "Import error inside %s (missing dep: %s): %s",
                    reg_name,
                    exc.name,
                    exc,
                )
                raise
        except Exception as exc:
            logger.error("Unexpected error loading %s: %s", reg_name, exc)
            raise

    return loaded
