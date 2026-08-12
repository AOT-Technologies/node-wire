# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for import smoke + pytest gate."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from nw_connector_builder.gate import GateResult, import_smoke, run_gate, run_pytest_gate

_LOGIC_OK = '''\
from __future__ import annotations

from typing import ClassVar, Type

from pydantic import BaseModel

from node_wire_runtime import RestConnector, RestResponseOutput, nw_action


class PingInput(BaseModel):
    pass


class Demo(RestConnector):
    connector_id = "gate_demo"
    output_model: ClassVar[Type[BaseModel]] = RestResponseOutput
    _nw_abstract_base = False

    @nw_action("ping")
    async def ping(self, params: PingInput, *, trace_id: str) -> RestResponseOutput:
        return RestResponseOutput()
'''

_LOGIC_ZERO = '''\
from __future__ import annotations

from node_wire_runtime import RestConnector


class Demo(RestConnector):
    connector_id = "gate_demo"
    _nw_abstract_base = True
    _action_registry = {}
'''


def _write_minimal_connector(staging: Path, *, logic: str) -> None:
    pkg = staging / "src" / "node_wire_gate_demo"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "logic.py").write_text(logic, encoding="utf-8")
    tests = staging / "packages" / "connectors" / "gate_demo" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_models.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")


def test_import_smoke_ok(tmp_path: Path) -> None:
    _write_minimal_connector(tmp_path, logic=_LOGIC_OK)
    ok, msg = import_smoke(tmp_path, "gate_demo")
    assert ok is True
    assert "Import OK" in msg
    assert "1 actions" in msg


def test_import_smoke_zero_actions(tmp_path: Path) -> None:
    _write_minimal_connector(tmp_path, logic=_LOGIC_ZERO)
    ok, msg = import_smoke(tmp_path, "gate_demo")
    assert ok is False
    assert "zero" in msg.lower()


def test_import_smoke_missing_module(tmp_path: Path) -> None:
    ok, msg = import_smoke(tmp_path, "missing_conn")
    assert ok is False
    assert "Import smoke failed" in msg


def test_run_pytest_gate_success(tmp_path: Path) -> None:
    _write_minimal_connector(tmp_path, logic=_LOGIC_OK)
    mock_proc = MagicMock(returncode=0, stdout="1 passed\n", stderr="")
    with patch("nw_connector_builder.gate.subprocess.run", return_value=mock_proc) as run:
        ok, out = run_pytest_gate(tmp_path, "gate_demo")
    assert ok is True
    assert "1 passed" in out
    assert str(run.call_args.kwargs["cwd"]).endswith("gate_demo")


def test_run_pytest_gate_failure(tmp_path: Path) -> None:
    _write_minimal_connector(tmp_path, logic=_LOGIC_OK)
    mock_proc = MagicMock(returncode=1, stdout="", stderr="FAILED\n")
    with patch("nw_connector_builder.gate.subprocess.run", return_value=mock_proc):
        ok, out = run_pytest_gate(tmp_path, "gate_demo")
    assert ok is False
    assert "FAILED" in out


def test_run_gate_short_circuits_on_import_failure(tmp_path: Path) -> None:
    result = run_gate(tmp_path, "missing_conn")
    assert isinstance(result, GateResult)
    assert result.ok is False
    assert result.import_ok is False
    assert result.pytest_ok is False


def test_run_gate_reports_pytest_failure(tmp_path: Path) -> None:
    _write_minimal_connector(tmp_path, logic=_LOGIC_OK)
    mock_proc = MagicMock(returncode=1, stdout="boom\n", stderr="")
    with patch("nw_connector_builder.gate.subprocess.run", return_value=mock_proc):
        result = run_gate(tmp_path, "gate_demo")
    assert result.ok is False
    assert result.import_ok is True
    assert result.pytest_ok is False
    assert result.message == "pytest gate failed"
    assert "boom" in result.pytest_output


def test_run_gate_success(tmp_path: Path) -> None:
    _write_minimal_connector(tmp_path, logic=_LOGIC_OK)
    mock_proc = MagicMock(returncode=0, stdout="ok\n", stderr="")
    with patch("nw_connector_builder.gate.subprocess.run", return_value=mock_proc):
        result = run_gate(tmp_path, "gate_demo")
    assert result.ok is True
    assert result.import_ok is True
    assert result.pytest_ok is True
    assert result.message == "clean build"
