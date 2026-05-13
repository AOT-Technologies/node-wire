"""Guardrails for REST app import during pytest collection.

Requires ``conftest`` env + ``connectors_for_tests.yaml`` so enabled connectors match
``NW_ALLOWED_CONNECTORS`` (optional connectors like slack/salesforce stay disabled).
"""

from __future__ import annotations

from pathlib import Path


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
