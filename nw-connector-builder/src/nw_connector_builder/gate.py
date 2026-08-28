# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Import smoke + pytest clean-build gate."""

from __future__ import annotations

import importlib
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class GateResult:
    ok: bool
    import_ok: bool
    pytest_ok: bool
    message: str
    pytest_output: str = ""


def import_smoke(staging: Path, connector_id: str) -> tuple[bool, str]:
    src = str(staging / "src")
    # Ensure a clean import
    mod_name = f"node_wire_{connector_id}"
    for key in list(sys.modules):
        if key == mod_name or key.startswith(mod_name + "."):
            del sys.modules[key]

    old_path = list(sys.path)
    try:
        sys.path.insert(0, src)
        logic = importlib.import_module(f"{mod_name}.logic")
        from node_wire_runtime.rest import RestConnector

        # Find connector class
        cls = None
        for attr in dir(logic):
            obj = getattr(logic, attr)
            if (
                isinstance(obj, type)
                and issubclass(obj, RestConnector)
                and obj is not RestConnector
            ):
                if getattr(obj, "connector_id", None) == connector_id:
                    cls = obj
                    break
        if cls is None:
            return False, "No RestConnector subclass with matching connector_id found"
        metas = getattr(cls, "nw_action_metas", None)
        if callable(metas):
            actions = metas()
        else:
            actions = getattr(cls, "_action_registry", {}) or {}
        if not actions:
            return False, "Connector exposes zero @nw_action methods"
        return True, f"Import OK ({len(actions)} actions)"
    except Exception as exc:  # noqa: BLE001
        return False, f"Import smoke failed: {exc}"
    finally:
        sys.path[:] = old_path


def run_pytest_gate(staging: Path, connector_id: str) -> tuple[bool, str]:
    pkg_tests = staging / "packages" / "connectors" / connector_id / "tests"
    src = staging / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(pkg_tests), "-q", "--tb=short"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(staging / "packages" / "connectors" / connector_id),
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, output


def run_gate(staging: Path, connector_id: str) -> GateResult:
    import_ok, import_msg = import_smoke(staging, connector_id)
    if not import_ok:
        return GateResult(False, False, False, import_msg)
    pytest_ok, pytest_out = run_pytest_gate(staging, connector_id)
    if not pytest_ok:
        return GateResult(
            False,
            True,
            False,
            "pytest gate failed",
            pytest_output=pytest_out,
        )
    return GateResult(True, True, True, "clean build", pytest_output=pytest_out)
