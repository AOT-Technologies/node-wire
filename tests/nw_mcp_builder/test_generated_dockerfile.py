#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Generated MCP Dockerfiles must not bake secrets and must follow image policy."""

from __future__ import annotations

from pathlib import Path

from nw_mcp_builder.generate.connector_project import (
    PYTHON_312_SLIM_IMAGE,
    _dockerignore,
    _dockerfile,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _sample_dockerfile() -> str:
    return _dockerfile(
        module_name="smtp_nw_mcp",
        connector_id="smtp",
        connector_pkg="node-wire-smtp",
        mcp_dep="mcp>=1.6.0,<2",
    )


def test_generated_dockerfile_is_digest_pinned_and_non_root() -> None:
    text = _sample_dockerfile()
    assert f"FROM {PYTHON_312_SLIM_IMAGE}" in text
    assert "USER app" in text
    assert "USER root" not in text
    assert "HEALTHCHECK" in text
    assert '"mcp>=1.6.0,<2"' in text


def test_generated_dockerfile_does_not_copy_or_bake_secrets() -> None:
    text = _sample_dockerfile()
    assert "COPY .env" not in text
    assert "COPY config/ ." not in text
    assert "COPY config/connectors.yaml" in text
    assert "README" not in text
    assert "pyproject.toml" not in text
    assert "pip install -e" not in text
    assert "NW_MCP_AUTH_DISABLED" not in text
    assert "NW_MCP_SCOPE_POLICY_DEFAULT" not in text
    assert "NW_REST_LOAD_DOTENV=false" in text
    assert "NW_MCP_CONTAINER=true" in text
    lowered = text.lower()
    for needle in ("password=", "secret=", "token=", "api_key="):
        assert needle not in lowered


def test_generated_dockerfile_application_tree_is_not_writable() -> None:
    text = _sample_dockerfile()
    assert "chmod -R a-w /app /nw_src" in text
    assert "--home /nonexistent" in text
    assert "rm -rf /wheels" in text


def test_generated_dockerignore_is_whitelist_and_excludes_secrets() -> None:
    text = _dockerignore()
    non_comments = [
        line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")
    ]
    assert non_comments[0] == "*"
    assert "!config/connectors.yaml" in text
    assert "**/.env" in text
    assert "**/.env.*" in text
    assert "**/tenants.yaml" in text
    assert "**/credentials.json" in text
    assert "!wheels/" in text
    assert "!src/" in text


def test_repo_dockerfiles_share_generated_base_digest() -> None:
    files = [_REPO_ROOT / "Dockerfile", *sorted((_REPO_ROOT / "docker").glob("*/Dockerfile"))]
    assert files, "expected checked-in Dockerfiles"
    for path in files:
        contents = path.read_text(encoding="utf-8")
        assert PYTHON_312_SLIM_IMAGE in contents, (
            f"{path.relative_to(_REPO_ROOT)} must use {PYTHON_312_SLIM_IMAGE}"
        )
