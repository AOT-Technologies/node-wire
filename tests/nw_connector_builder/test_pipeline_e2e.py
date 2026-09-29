# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from nw_connector_builder.pipeline import BuildError, UsageError, run_build

FIXTURES = Path(__file__).parent / "fixtures"


def _mini_root(tmp_path: Path) -> Path:
    root = tmp_path / "node-wire"
    (root / "src").mkdir(parents=True)
    (root / "packages" / "connectors").mkdir(parents=True)
    (root / "config").mkdir(parents=True)
    (root / "nw-mcp-builder").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='node-wire'\n", encoding="utf-8")
    (root / "config" / "connectors.yaml").write_text("connectors: {}\n", encoding="utf-8")
    (root / "sample.env").write_text("NW_ALLOWED_CONNECTORS=\n", encoding="utf-8")
    return root


def test_e2e_no_mcp_promote(tmp_path: Path) -> None:
    root = _mini_root(tmp_path)
    code = run_build(
        spec=str(FIXTURES / "demo_pets.openapi.yaml"),
        connector_id="demo_pets",
        node_wire_root=root,
        no_mcp=True,
        wire=True,
        force=False,
        report_path=tmp_path / "abort.json",
    )
    assert code == 0
    assert (root / "src" / "node_wire_demo_pets" / "logic.py").is_file()
    assert (root / "packages" / "connectors" / "demo_pets" / "pyproject.toml").is_file()
    assert (root / "packages" / "connectors" / "demo_pets" / "report.json").is_file()
    yaml_text = (root / "config" / "connectors.yaml").read_text()
    assert "demo_pets" in yaml_text
    env_text = (root / "sample.env").read_text()
    assert "demo_pets" in env_text
    assert "DEMO_PETS_API_KEY" in env_text


def test_force_required_on_second_build(tmp_path: Path) -> None:
    root = _mini_root(tmp_path)
    run_build(
        spec=str(FIXTURES / "demo_pets.openapi.yaml"),
        connector_id="demo_pets",
        node_wire_root=root,
        no_mcp=True,
    )
    with pytest.raises(UsageError, match="--force"):
        run_build(
            spec=str(FIXTURES / "demo_pets.openapi.yaml"),
            connector_id="demo_pets",
            node_wire_root=root,
            no_mcp=True,
            force=False,
        )
    code = run_build(
        spec=str(FIXTURES / "demo_pets.openapi.yaml"),
        connector_id="demo_pets",
        node_wire_root=root,
        no_mcp=True,
        force=True,
    )
    assert code == 0


def test_remote_ref_leaves_repo_untouched(tmp_path: Path) -> None:
    root = _mini_root(tmp_path)
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "openapi: 3.0.3\ninfo: {title: t, version: '1'}\n"
        "servers: [{url: 'https://api.example.com'}]\n"
        "paths:\n  /x:\n    get:\n      responses:\n"
        "        '200':\n          description: ok\n"
        "          content:\n            application/json:\n"
        "              schema:\n                $ref: https://evil.example/schema.json\n",
        encoding="utf-8",
    )
    with pytest.raises(BuildError):
        run_build(
            spec=str(bad),
            connector_id="evil_api",
            node_wire_root=root,
            no_mcp=True,
            report_path=tmp_path / "report.json",
        )
    assert not (root / "src" / "node_wire_evil_api").exists()


def test_generated_connector_secrets_are_storable_under_enforce(tmp_path: Path) -> None:
    """A Slack-shaped spec must end up storable through the config store.

    Hand-written connectors are hard-coded in
    ``node_wire_runtime.tenant_persistence``; generated ones declare themselves.
    The runtime half runs in a subprocess so importing the promoted package does
    not register a connector class in this test session's global registry.
    """
    root = _mini_root(tmp_path)
    code = run_build(
        spec=str(FIXTURES / "slack_web.openapi.yaml"),
        connector_id="slack_web",
        node_wire_root=root,
        no_mcp=True,
        wire=True,
    )
    assert code == 0

    probe = tmp_path / "probe.py"
    probe.write_text(
        textwrap.dedent(
            """\
            import importlib
            from node_wire_runtime import tenant_persistence as tp

            # Undeclared: the store must refuse it under the enforce policy.
            try:
                tp.upsert_tenant_secrets(
                    "acme", "slack_web", {"SLACK_WEB_ACCESS_TOKEN": "xoxb-1"},
                    config_name="cfg", auto_shared_env=False, require_varying=False,
                )
            except ValueError:
                print("REFUSED_BEFORE_IMPORT")

            # Importing the generated connector runs its declare_secret_shape call.
            importlib.import_module("node_wire_slack_web.logic")
            keys = tp.upsert_tenant_secrets(
                "acme", "slack_web", {"SLACK_WEB_ACCESS_TOKEN": "xoxb-1"},
                config_name="cfg", auto_shared_env=False, require_varying=False,
            )
            print("STORED", keys)
            """
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(root / "src"), *sys.path]),
        "NW_SECRET_SHAPE_POLICY": "enforce",
        "NW_TENANTS_PATH": str(tmp_path / "tenants.yaml"),
    }
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(probe)], capture_output=True, text=True, env=env, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert "REFUSED_BEFORE_IMPORT" in proc.stdout, proc.stdout
    assert "STORED ['SLACK_WEB_ACCESS_TOKEN']" in proc.stdout, proc.stdout


def test_generated_connector_reports_ok_false_as_a_failure(tmp_path: Path) -> None:
    """A 2xx body carrying ``ok: false`` must not read as a successful call.

    Runs against the promoted package in a subprocess: this is the whole chain —
    derive detects the required boolean flag, codegen passes it to
    ``execute_rest``, and the connector's ``error_map`` turns the raised
    ``RestEnvelopeError`` into a BUSINESS failure.
    """
    root = _mini_root(tmp_path)
    assert (
        run_build(
            spec=str(FIXTURES / "slack_web.openapi.yaml"),
            connector_id="slack_web",
            node_wire_root=root,
            no_mcp=True,
        )
        == 0
    )

    probe = tmp_path / "envelope_probe.py"
    probe.write_text(
        textwrap.dedent(
            """\
            import asyncio, json
            from typing import Any
            from unittest.mock import AsyncMock

            import node_wire_runtime.rest as rest
            from node_wire_slack_web.logic import SlackWebConnector
            from node_wire_runtime.auth import NoAuthProvider
            from node_wire_runtime.secrets import SecretProvider

            PAYLOAD = {"ok": False, "error": "invalid_auth"}

            class _Response:
                status_code = 200
                content = json.dumps(PAYLOAD).encode()
                headers = {"content-type": "application/json"}
                def json(self): return PAYLOAD
                def raise_for_status(self): return None

            class _Client:
                def __init__(self, *a: Any, **k: Any): ...
                async def __aenter__(self): return self
                async def __aexit__(self, *a: Any): return None
                async def request(self, **k: Any): return _Response()

            class _NoSecrets(SecretProvider):
                def get_secret(self, key): raise KeyError(key)

            rest.httpx.AsyncClient = _Client
            rest.assert_safe_destination = AsyncMock(return_value=None)

            conn = SlackWebConnector(
                secret_provider=_NoSecrets(), auth_provider=NoAuthProvider()
            )
            resp = asyncio.run(
                conn.run(
                    {
                        "action": "chat_post_message",
                        "channel": "C1",
                        "text": "hi",
                    }
                )
            )
            print("SUCCESS", resp.success, "CODE", resp.error_code)
            """
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(root / "src"), *sys.path])}
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(probe)], capture_output=True, text=True, env=env, check=False
    )
    assert proc.returncode == 0, proc.stderr
    assert "SUCCESS False CODE API_ENVELOPE_ERROR" in proc.stdout, proc.stdout + proc.stderr


def test_build_refuses_a_hand_written_connector_id_before_staging(tmp_path: Path) -> None:
    """The gate imports generated code in-process, so the id check must precede it.

    Importing a generated ``node_wire_slack`` would run its ``declare_secret_shape``
    and replace the hand-written connector's tenant-secret contract in this
    process — a build that never promotes must not get that far.
    """
    root = _mini_root(tmp_path)
    hand_written = root / "src" / "node_wire_slack"
    hand_written.mkdir(parents=True)
    (hand_written / "logic.py").write_text("class SlackConnector: ...\n", encoding="utf-8")

    from node_wire_runtime import tenant_persistence as tp

    with pytest.raises(UsageError, match="not written by nw-connector-builder"):
        run_build(
            spec=str(FIXTURES / "slack_web.openapi.yaml"),
            connector_id="slack",
            node_wire_root=root,
            no_mcp=True,
            force=True,
        )

    assert (hand_written / "logic.py").read_text() == "class SlackConnector: ...\n"
    # The hand-written declaration is intact: nothing was imported.
    assert tp.REQUIRED_SECRETS_BY_CONNECTOR.get("slack") == ["SLACK_BOT_TOKEN"]
