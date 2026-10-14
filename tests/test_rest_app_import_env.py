#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Guardrails for REST app import during pytest collection.

Requires ``conftest`` env + ``connectors_for_tests.yaml`` so enabled connectors match
``NW_ALLOWED_CONNECTORS`` (optional connectors like slack/salesforce stay disabled).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_pytest_env_disables_rest_dotenv() -> None:
    import os

    assert os.environ.get("NW_REST_LOAD_DOTENV", "").lower() in ("false", "0", "no")


def test_pytest_uses_test_connector_config_fixture() -> None:
    import os

    path = os.environ.get("NW_CONFIG_PATH", "")
    assert path
    assert path.endswith("connectors_for_tests.yaml")
    assert Path(path).is_file()


def test_rest_app_module_imports_without_runtime_error() -> None:
    """``bindings.rest_api.app`` builds routes at import time; must not raise."""
    import bindings.rest_api.app as rest_app

    assert rest_app.app is not None
    assert len(rest_app.app.routes) > 0


_REPO = Path(__file__).resolve().parent.parent
_PLAYGROUND_ENV = (
    "NW_REST_PLAYGROUND_ENABLED",
    "NW_REST_PLAYGROUND_ALLOW_WITH_AUTH",
    "NW_REST_API_KEY",
    "NW_REST_JWT_SECRET",
    "NW_REST_AUTH_DISABLED",
)


def _import_app(**overrides: str) -> tuple[set[str], str]:
    """Import the REST app in a fresh process; return its route paths and stderr.

    The in-process app is already imported (and conftest opts the playground in), so mounting
    has to be observed in a subprocess.
    """
    env = {k: v for k, v in os.environ.items() if k not in _PLAYGROUND_ENV}
    env.update(overrides)
    env["PYTHONPATH"] = os.pathsep.join(
        str(p) for p in (_REPO / "src", _REPO, _REPO / "src" / "bindings" / "grpc_server")
    )
    code = (
        "import json, bindings.rest_api.app as a; "
        "print(json.dumps(sorted(getattr(r, 'path', '') for r in a.app.routes)))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return set(json.loads(done.stdout.strip().splitlines()[-1])), done.stderr


def _playground_routes(paths: set[str]) -> set[str]:
    return {p for p in paths if p.startswith(("/playground", "/scenarios"))}


def test_playground_is_off_when_the_env_var_is_unset() -> None:
    paths, _ = _import_app()
    assert _playground_routes(paths) == set()


def test_playground_mounts_only_when_explicitly_enabled() -> None:
    paths, _ = _import_app(NW_REST_PLAYGROUND_ENABLED="true")
    assert "/scenarios/stripe-charge" in paths
    assert any(p.startswith("/playground") for p in paths)


@pytest.mark.parametrize("secret_env", ["NW_REST_API_KEY", "NW_REST_JWT_SECRET"])
def test_playground_is_refused_when_auth_is_configured(secret_env: str) -> None:
    paths, stderr = _import_app(NW_REST_PLAYGROUND_ENABLED="true", **{secret_env: "x" * 32})
    assert _playground_routes(paths) == set()
    assert "playground NOT mounted" in stderr


def test_playground_mounts_with_auth_only_with_the_second_flag() -> None:
    paths, _ = _import_app(
        NW_REST_PLAYGROUND_ENABLED="true",
        NW_REST_API_KEY="x" * 32,
        NW_REST_PLAYGROUND_ALLOW_WITH_AUTH="true",
    )
    assert "/scenarios/stripe-charge" in paths
