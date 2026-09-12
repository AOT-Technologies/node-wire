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
    assert text.count(f"FROM {PYTHON_312_SLIM_IMAGE}") == 2  # deps + runtime
    assert f"FROM {PYTHON_312_SLIM_IMAGE} AS deps" in text
    assert "USER app" in text
    assert "USER root" not in text
    assert "HEALTHCHECK" in text
    assert '"mcp>=1.6.0,<2"' in text
    assert "# syntax=docker/dockerfile:1" in text


def test_generated_dockerfile_does_not_copy_or_bake_secrets() -> None:
    text = _sample_dockerfile()
    assert "COPY .env" not in text
    assert "COPY config/ ." not in text
    assert "COPY --chmod=0755 config/connectors.yaml" in text
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


def test_generated_dockerfile_is_multistage_with_cached_install() -> None:
    text = _sample_dockerfile()
    assert "AS deps" in text
    assert "COPY --from=deps /usr/local /usr/local" in text
    assert "RUN --mount=type=cache,target=/root/.cache/pip" in text
    # Wheels only in deps stage — never a final-image layer.
    assert "COPY wheels/ /wheels/" in text
    assert text.index("AS deps") < text.index("COPY wheels/")
    assert text.index("COPY --from=deps") < text.index("COPY --chmod=0755 config/connectors.yaml")
    assert "rm -rf /wheels" not in text  # multi-stage replaces delete-in-place


def test_generated_dockerfile_application_tree_is_not_writable() -> None:
    text = _sample_dockerfile()
    assert "chmod -R a-w /app /nw_src" in text
    assert "--home /nonexistent" in text
    # Vendored tree + PYTHONPATH preserved for Cython wheel nested-package gaps.
    assert "PYTHONPATH=/nw_src:/app/src" in text
    assert "COPY --chmod=0755 vendor/node_wire_src/ /nw_src/" in text


def test_generated_dockerfile_normalizes_copied_permissions() -> None:
    """Regression: ``config/connectors.yaml`` is mode 600 on disk (repo convention
    for a file that must never contain secrets). Docker's plain ``COPY`` preserves
    the source file's mode, and the final ``chmod -R a-w`` only *removes* the
    write bit — a 600 source file becomes 400 (root-only), so ``USER app``
    (uid 1000, non-root) got ``PermissionError`` reading it at startup. Every
    COPY that lands under the later ``chmod -R a-w /app /nw_src`` must set an
    explicit, world-readable ``--chmod`` so the result is independent of the
    host file's permissions.
    """
    text = _sample_dockerfile()
    assert "COPY --chmod=0755 vendor/node_wire_src/ /nw_src/" in text
    assert "COPY --chmod=0755 config/connectors.yaml /app/config/connectors.yaml" in text
    assert "COPY --chmod=0755 src/ /app/src/" in text


def test_generated_dockerfile_install_before_app_sources() -> None:
    """App COPY layers must follow the deps install copy so source edits cache."""
    text = _sample_dockerfile()
    install_at = text.index("COPY --from=deps /usr/local /usr/local")
    assert text.index("COPY --chmod=0755 config/connectors.yaml") > install_at
    assert text.index("COPY --chmod=0755 vendor/node_wire_src/") > install_at
    assert text.index("COPY --chmod=0755 src/") > install_at


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
