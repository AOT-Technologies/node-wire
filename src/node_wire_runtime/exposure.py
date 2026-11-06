#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
Action exposure profiles: which connector actions one deployment exposes.

``NW_EXPOSE_PROFILE`` names a profile. For each loaded connector the runtime reads
``<NW_EXPOSE_DIR>/<connector_id>/<name>.yaml`` and enforces only its exact ``actions:`` list.
Selectors (``select:``) are for the ``nw expose`` CLI and are never read here.
With no profile set, everything is exposed, as before.
"""

from __future__ import annotations

import difflib
import logging
import os
from collections import Counter
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, Optional

import yaml

logger = logging.getLogger("runtime.exposure")


class ExposureConfigError(ValueError):
    """The profile is missing, unreadable, invalid, or names an action that does not exist."""


@dataclass(frozen=True)
class ExposureCount:
    exposed: int
    total: int
    by_method: Dict[str, int]


def _suggest(name: str, known: Iterable[str]) -> str:
    close = difflib.get_close_matches(name, list(known), n=3)
    return f" (did you mean {', '.join(close)}?)" if close else ""


def _connector_version(cls: type) -> Optional[str]:
    """Installed version of the distribution that ships ``cls``; ``None`` if unknown."""
    top = cls.__module__.split(".")[0]
    try:
        return metadata.version(metadata.packages_distributions()[top][0])
    except Exception:  # noqa: BLE001 - version is advisory, never fail startup over it
        return None


def _default_dir(config_path: Optional[str]) -> Path:
    """``expose/`` beside the ``config/`` folder that holds connectors.yaml."""
    explicit = os.getenv("NW_EXPOSE_DIR", "").strip()
    if explicit:
        return Path(explicit)
    config = (
        config_path or os.getenv("NW_CONFIG_PATH") or str(Path.cwd() / "config" / "connectors.yaml")
    )
    return Path(config).resolve().parent.parent / "expose"


class ActionExposure:
    """Answers one question per call: is this ``(connector, action)`` exposed?"""

    def __init__(
        self,
        allowed: Optional[Dict[str, FrozenSet[str]]] = None,
        counts: Optional[Dict[str, ExposureCount]] = None,
        profile: Optional[str] = None,
    ) -> None:
        # ``allowed`` None means unrestricted. When set it is the complete list: a connector
        # missing from it exposes nothing (fail closed).
        self._allowed = allowed
        self._counts = counts or {}
        self.profile = profile

    @classmethod
    def unrestricted(cls) -> "ActionExposure":
        return cls()

    @classmethod
    def from_env(
        cls, connectors: Iterable[Any], *, config_path: Optional[str] = None
    ) -> "ActionExposure":
        """Load ``NW_EXPOSE_PROFILE`` for the loaded connectors.

        Unset: everything exposed. Raises :class:`ExposureConfigError` for an unknown
        profile, an invalid file, or an action the connector does not have.
        """
        name = os.getenv("NW_EXPOSE_PROFILE", "").strip()
        if not name:
            return cls.unrestricted()
        if "/" in name or "\\" in name or name in (".", ".."):
            raise ExposureConfigError(
                f"NW_EXPOSE_PROFILE must be a profile name, not a path: {name!r}"
            )
        root = _default_dir(config_path)
        strict = os.getenv("NW_EXPOSE_STRICT_VERSION", "").strip().lower() in ("1", "true", "yes")

        allowed: Dict[str, FrozenSet[str]] = {}
        counts: Dict[str, ExposureCount] = {}
        found = False
        for connector in connectors:
            cid = connector.connector_id
            cls_ = type(connector)
            metas = cls_.nw_action_metas()
            file = root / cid / f"{name}.yaml"
            if not file.is_file():
                # f-string, not %s args: the log filter redacts any string arg over 100 chars.
                logger.warning(f"exposure: no profile {name!r} for {cid} ({file}): exposes nothing")
                allowed[cid] = frozenset()
                counts[cid] = ExposureCount(0, len(metas), {})
                continue
            found = True
            doc = cls._read_doc(file)
            actions = cls._actions(doc, file, cid, metas)
            cls._check_version(doc, file, cid, cls_, strict)
            allowed[cid] = actions
            by_method = Counter(metas[a].http_method or "?" for a in actions)
            counts[cid] = ExposureCount(len(actions), len(metas), dict(sorted(by_method.items())))
        if not found:
            raise ExposureConfigError(
                f"NW_EXPOSE_PROFILE={name!r}: no loaded connector has {root}/<connector_id>/{name}.yaml"
            )
        return cls(allowed, counts, name)

    @staticmethod
    def _read_doc(file: Path) -> Dict[str, Any]:
        try:
            doc = yaml.safe_load(file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ExposureConfigError(f"Cannot read exposure profile {file}: {exc}") from exc
        if not isinstance(doc, dict):
            raise ExposureConfigError(f"{file}: expected a mapping at the top level")
        return doc

    @staticmethod
    def _actions(
        doc: Dict[str, Any], file: Path, cid: str, metas: Dict[str, Any]
    ) -> FrozenSet[str]:
        declared = doc.get("connector")
        if declared is not None and declared != cid:
            raise ExposureConfigError(f"{file}: connector is {declared!r}, expected {cid!r}")
        actions = doc.get("actions")
        if not isinstance(actions, list) or not all(isinstance(a, str) for a in actions):
            raise ExposureConfigError(f"{file}: 'actions' must be a list of action names")
        unknown = [a for a in actions if a not in metas]
        if unknown:
            hints = "; ".join(f"{a}{_suggest(a, metas)}" for a in unknown)
            raise ExposureConfigError(f"{file}: unknown action(s) for {cid}: {hints}")
        return frozenset(actions)

    @staticmethod
    def _check_version(doc: Dict[str, Any], file: Path, cid: str, cls_: type, strict: bool) -> None:
        declared = doc.get("connector_version")
        loaded = _connector_version(cls_)
        if declared is None or loaded is None or str(declared) == loaded:
            return
        message = f"{file}: written for {cid} {declared}, loaded {loaded}; re-resolve with nw expose resolve"
        if strict:
            raise ExposureConfigError(message)
        logger.warning(f"exposure: {message}")

    def allows(self, connector_id: str, action: str) -> bool:
        if self._allowed is None:
            return True
        return action in self._allowed.get(connector_id, frozenset())

    def summary(self) -> Dict[str, ExposureCount]:
        """Per connector: exposed / total and counts by HTTP method, for the startup log."""
        return dict(self._counts)
