#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel

from node_wire_runtime import BaseConnector, nw_action
from node_wire_runtime.policies.mcp_scope_policy import (
    DEFAULT_SCOPE_MODE_DENY,
    ScopePolicyHook,
    _warn_implicit_scope_default,
    check_scope_policy_config,
    load_scope_policy_default_from_env,
)


class _Input(BaseModel):
    action: Literal["read_patient"] = "read_patient"
    resource_id: str


class _Output(BaseModel):
    ok: bool


class _PolicyTestConnector(BaseConnector):
    # Do not shadow production ``fhir_epic`` in the global registry.
    connector_id = "policy_transport_test"
    output_model = _Output

    @nw_action("read_patient")
    async def read_patient(self, params: _Input, *, trace_id: str) -> _Output:
        return _Output(ok=True)


def _connector_with_scope_map() -> _PolicyTestConnector:
    return _PolicyTestConnector(
        policy_hook=ScopePolicyHook({"policy_transport_test.read_patient": "mcp:fhir.read_patient"})
    )


def test_scope_policy_denies_when_identity_missing() -> None:
    connector = _connector_with_scope_map()
    response = asyncio.run(connector.run({"action": "read_patient", "resource_id": "x"}))

    assert response.success is False
    assert response.error_code == "POLICY_DENIED"
    assert response.message == "Missing required scope: mcp:fhir.read_patient"


def test_scope_policy_denies_when_identity_present_without_required_scope() -> None:
    connector = _connector_with_scope_map()
    response = asyncio.run(
        connector.run(
            {"action": "read_patient", "resource_id": "x"},
            principal="alice",
            tenant_id="tenant-1",
            scopes=("mcp:other.scope",),
        )
    )

    assert response.success is False
    assert response.error_code == "POLICY_DENIED"
    assert response.message == "Missing required scope: mcp:fhir.read_patient"


def test_scope_policy_default_deny_uses_conventional_scope() -> None:
    hook = ScopePolicyHook({}, default_mode=DEFAULT_SCOPE_MODE_DENY)
    connector = _PolicyTestConnector(policy_hook=hook)
    response = asyncio.run(
        connector.run(
            {"action": "read_patient", "resource_id": "x"},
            principal="alice",
            tenant_id="tenant-1",
            scopes=("mcp:policy_transport_test.read_patient",),
        )
    )
    assert response.success is True


def test_scope_policy_default_deny_without_fallback_scope() -> None:
    hook = ScopePolicyHook({}, default_mode=DEFAULT_SCOPE_MODE_DENY)
    connector = _PolicyTestConnector(policy_hook=hook)
    response = asyncio.run(
        connector.run(
            {"action": "read_patient", "resource_id": "x"},
            principal="alice",
            tenant_id="tenant-1",
            scopes=("mcp:wrong",),
        )
    )
    assert response.success is False
    assert "Missing required scope" in (response.message or "")


# --------------------------------------------------------------------------- #
# NW_MCP_SCOPE_POLICY_DEFAULT: exactly allow or deny, and a bad value fails startup
# --------------------------------------------------------------------------- #

_VAR = "NW_MCP_SCOPE_POLICY_DEFAULT"
# The issue's list (each used to read as ``allow``), plus the two undocumented aliases.
_BAD_VALUES = [
    "denied",
    "strict",
    "true",
    "1",
    "deny,",
    "Deny-All",
    "false",
    "default-deny",
    "closed",
]
_REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("value", _BAD_VALUES)
def test_an_invalid_default_mode_is_rejected_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(_VAR, value)

    for check in (load_scope_policy_default_from_env, check_scope_policy_config):
        with pytest.raises(ValueError, match=_VAR) as exc:
            check()
        assert "must be 'allow' or 'deny'" in str(exc.value)
        assert repr(value) in str(exc.value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("allow", "allow"),
        ("Allow", "allow"),
        ("deny", "deny"),
        ("DENY", "deny"),
        (" Deny ", "deny"),
        ("", "deny"),
        ("   ", "deny"),
        (None, "deny"),  # unset
    ],
)
def test_valid_and_unset_default_modes_behave_as_before(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: str
) -> None:
    if value is None:
        monkeypatch.delenv(_VAR, raising=False)
    else:
        monkeypatch.setenv(_VAR, value)

    assert load_scope_policy_default_from_env() == expected


def test_an_unset_default_still_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv(_VAR, raising=False)
    _warn_implicit_scope_default.cache_clear()  # the warning is once per process

    with caplog.at_level("WARNING", logger="runtime.policy.scope"):
        assert load_scope_policy_default_from_env() == "deny"

    assert "is unset; using code default 'deny'" in caplog.text


def test_startup_logs_the_mode_but_not_the_raw_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(_VAR, "  ALLOW ")

    with caplog.at_level("INFO", logger="runtime.policy.scope"):
        check_scope_policy_config()

    assert "Scope policy default: allow" in caplog.text
    assert "ALLOW" not in caplog.text


@pytest.mark.parametrize("value", _BAD_VALUES)
def test_grpc_fails_at_startup_before_opening_a_port(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    from bindings.grpc_server.server import serve

    monkeypatch.setenv(_VAR, value)

    with pytest.raises(ValueError, match=_VAR):
        serve()


@pytest.mark.parametrize("value", _BAD_VALUES)
def test_mcp_fails_at_startup_on_both_transports(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    from bindings.mcp_server.server import McpServer

    server = McpServer(connector_ids=["http_generic"])  # built under a valid value
    monkeypatch.setenv(_VAR, value)

    with pytest.raises(ValueError, match=_VAR):
        server.run_stdio()
    with pytest.raises(ValueError, match=_VAR):
        server.run_streamable_http()


def test_mcp_server_cannot_even_be_built_with_an_invalid_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bindings.mcp_server.server import McpServer

    monkeypatch.setenv(_VAR, "denied")

    with pytest.raises(ValueError, match=_VAR):
        McpServer(connector_ids=["http_generic"])


@pytest.mark.parametrize(("value", "starts"), [("denied", False), ("deny", True)])
def test_rest_fails_at_import_not_at_the_first_request(value: str, starts: bool) -> None:
    """The REST app builds its factory on the first request, so it needs its own startup check.

    Observed in a subprocess: the in-process app is already imported.
    """
    env = {**os.environ, _VAR: value}
    env["PYTHONPATH"] = os.pathsep.join(
        str(p) for p in (_REPO / "src", _REPO, _REPO / "src" / "bindings" / "grpc_server")
    )
    done = subprocess.run(
        [sys.executable, "-c", "import bindings.rest_api.app"],
        env=env,
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert (done.returncode == 0) is starts, done.stderr
    if not starts:
        assert f"{_VAR} must be 'allow' or 'deny'" in done.stderr
