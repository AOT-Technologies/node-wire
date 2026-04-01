from __future__ import annotations

"""
Node Wire - Layer B: System Adapters.

Each connector lives in its own subpackage:

    connector_name/
      schema.py
      logic.py
      registration.py  (optional — legacy connectors)

SDKConnector-based connectors self-register when their `logic` module is
imported. Legacy connectors may still use `registration.py` for ErrorMapper.
"""

from importlib import import_module
from pkgutil import iter_modules
from typing import List


def auto_register() -> List[str]:
    """
    Import connector subpackages so SDK connectors register and legacy mappings apply.

    Imports `logic` first (triggers SDKConnector.__init_subclass__), then
    `registration` when present.
    """
    imported: List[str] = []
    package_name = __name__

    for module_info in iter_modules(__path__, prefix=f"{package_name}."):
        if not module_info.ispkg:
            continue

        logic_module = f"{module_info.name}.logic"
        try:
            import_module(logic_module)
            imported.append(logic_module)
        except ModuleNotFoundError:
            pass

        registration_module = f"{module_info.name}.registration"
        try:
            import_module(registration_module)
            imported.append(registration_module)
        except ModuleNotFoundError:
            continue

    return imported


__all__ = ["auto_register"]
