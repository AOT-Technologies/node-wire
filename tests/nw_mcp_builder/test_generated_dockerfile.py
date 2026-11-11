#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Generated MCP Dockerfiles must not bake secrets and must follow image policy."""

from __future__ import annotations

from pathlib import Path

from nw_mcp_builder.generate.connector_project import (
    BINDINGS_DIST_PACKAGE,
    PYTHON_313_SLIM_IMAGE,
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
    assert text.count(f"FROM {PYTHON_313_SLIM_IMAGE}") == 2  # deps + runtime
    assert f"FROM {PYTHON_313_SLIM_IMAGE} AS deps" in text
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


def test_generated_dockerfile_is_wheels_only_multistage() -> None:
    text = _sample_dockerfile()
    assert "AS deps" in text
    assert "COPY --from=deps /usr/local /usr/local" in text
    assert "RUN --mount=type=cache,target=/root/.cache/pip" in text
    assert "COPY wheels/ /wheels/" in text
    assert BINDINGS_DIST_PACKAGE in text
    assert "vendor" not in text
    assert "/nw_src" not in text
    assert "PYTHONPATH=/app/src" in text
    assert text.index("COPY --from=deps") < text.index("COPY --chmod=0755 config/connectors.yaml")


def test_generated_dockerfile_pip_failure_is_not_masked_by_pycache_cleanup() -> None:
    """Regression: bare `pip && find || true && …` succeeds when pip fails
    because `&&`/`||` associate left-to-right. The __pycache__ cleanup must be
    braced so `|| true` applies only to find."""
    text = _sample_dockerfile()
    assert "pip install --no-compile --find-links=/wheels" in text
    # Old (buggy) form must not appear.
    assert (
        "&& find /usr/local -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true \\"
        not in text
    )
    # Braced form: pip failure short-circuits before cleanup.
    assert (
        "&& { find /usr/local -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true; } \\"
        in text
    )


def test_generated_dockerfile_installs_local_wheels_by_path() -> None:
    """Regression: installing by name let pip pick a same-version PyPI release
    of node-wire-runtime over the freshly built wheel in /wheels."""
    text = _sample_dockerfile()
    assert "/wheels/*.whl" in text
    assert "node-wire-runtime node-wire-bindings" not in text
    
    
def test_generated_dockerfile_ships_bytecode() -> None:
    """The app user cannot write __pycache__, so .pyc must be built into the image;
    deleting them made every container start recompile its imports."""
    text = _sample_dockerfile()
    assert "-name '*.pyc'" not in text
    assert (
        "&& { python -m compileall -qq -j 0 --invalidation-mode checked-hash \\\n"
        "        /usr/local/lib/python3.13/site-packages || true; }"
    ) in text
    assert text.index("find /usr/local -type d -name '__pycache__'") < text.index(
        "python -m compileall -qq -j 0"
    )
    host_compile = "RUN python -m compileall -qq --invalidation-mode checked-hash /app/src"
    assert host_compile in text
    assert text.index("COPY --chmod=0755 src/ /app/src/") < text.index(host_compile)
    assert text.index(host_compile) < text.index("chmod -R a-w /app")


def test_generated_dockerfile_application_tree_is_not_writable() -> None:
    text = _sample_dockerfile()
    assert "chmod -R a-w /app" in text
    assert "--home /nonexistent" in text


def test_generated_dockerfile_normalizes_copied_permissions() -> None:
    text = _sample_dockerfile()
    assert "COPY --chmod=0755 config/connectors.yaml /app/config/connectors.yaml" in text
    assert "COPY --chmod=0755 src/ /app/src/" in text


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
    assert "vendor" not in text


def test_repo_dockerfiles_share_generated_base_digest() -> None:
    files = [_REPO_ROOT / "Dockerfile", *sorted((_REPO_ROOT / "docker").glob("*/Dockerfile"))]
    assert files, "expected checked-in Dockerfiles"
    for path in files:
        contents = path.read_text(encoding="utf-8")
        assert PYTHON_313_SLIM_IMAGE in contents, (
            f"{path.relative_to(_REPO_ROOT)} must use {PYTHON_313_SLIM_IMAGE}"
        )


def test_exposure_profiles_are_copied_only_when_the_project_has_them(tmp_path: Path) -> None:
    from nw_mcp_builder.generate.connector_project import copy_profiles, profiles_in_sync

    kwargs = dict(
        module_name="smtp_nw_mcp", connector_id="smtp", connector_pkg="node-wire-smtp", mcp_dep="m"
    )
    assert "expose/" not in _dockerfile(**kwargs)
    assert "COPY --chmod=0755 expose/ /app/expose/" in _dockerfile(**kwargs, with_profiles=True)
    assert "!expose/**" in _dockerignore()

    root, project = tmp_path / "root", tmp_path / "project"
    project.mkdir()
    assert copy_profiles(root, "smtp", project) is False
    (root / "expose" / "smtp").mkdir(parents=True)
    (root / "expose" / "smtp" / "ro.yaml").write_text("actions: []\n")
    assert not profiles_in_sync(root, "smtp", project)
    assert copy_profiles(root, "smtp", project) is True
    assert profiles_in_sync(root, "smtp", project)
    (root / "expose" / "smtp" / "ro.yaml").write_text("actions: [a]\n")
    assert not profiles_in_sync(root, "smtp", project)
