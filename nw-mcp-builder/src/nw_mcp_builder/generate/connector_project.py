# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Emit a thin MCP host that runs a node-wire connector via wheels.

ponytail: Docker-parity packaging -- install runtime+connector ``.whl`` for
entry points / deps, and put a *minimal* node-wire ``src`` slice on PYTHONPATH
(``bindings`` + ``node_wire_runtime`` + ``node_wire_<connector_id>``). Cython
wheels alone can omit nested packages (e.g. ``node_wire_runtime.policies``);
other connectors are not vendored (``NW_ALLOWED_CONNECTORS`` pins one id).
"""

from __future__ import annotations

import logging
import re
import shutil
import tomllib
from pathlib import Path

from nw_mcp_builder.schema.models import MCPScope

logger = logging.getLogger(__name__)

# Cache / bytecode dirs omitted from the vendored tree. Also drop credential
# filenames so a checkout accident cannot be copied into the image context.
_VENDOR_IGNORE = shutil.ignore_patterns(
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".mypy_cache",
    ".ruff_cache",
    "*.egg-info",
    ".env",
    ".env.*",
    "tenants.yaml",
    "tenants.yaml.tmp",
    "playground_tenants.yaml",
    "*.pem",
    "*.key",
    "credentials.json",
    "service-account*.json",
)

# node-wire McpServer uses the mcp 1.x decorator API (@server.list_tools()),
# which was removed in mcp 2.0. Prefer the version locked in the monorepo.
_MCP_DEP_FALLBACK = "mcp>=1.6.0,<2"

# Digest-pinned base. Keep in sync with Dockerfile and docker/*/Dockerfile
# (enforced by tests/nw_mcp_builder/test_generated_dockerfile.py and
# .github/workflows/docker-policy.yml).
PYTHON_312_SLIM_IMAGE = (
    "python:3.12-slim@sha256:3d5ed973e45820f5ba5e46bd065bd88b3a504ff0724d85980dcd05eab361fcf4"
)


def server_name_to_module(name: str) -> str:
    """Convert DNS-label server name to Python module name."""
    return name.replace("-", "_") + "_mcp"


def connector_dist_package_name(connector_id: str) -> str:
    """Map connector_id (``google_drive``) to wheel name (``node-wire-google-drive``)."""
    return f"node-wire-{connector_id.replace('_', '-')}"


def write_connector_project(
    scope: MCPScope,
    node_wire_root: Path,
    output_dir: Path,
) -> Path:
    """Create ``out/<server>-mcp/`` wrapping node-wire ``McpServer``."""
    if scope.runtime is None or scope.runtime.type != "node_wire":
        raise ValueError("write_connector_project requires runtime.type=node_wire")

    connector_id = scope.runtime.connector_id
    if not re.fullmatch(r"[a-z][a-z0-9_]*", connector_id):
        raise ValueError(
            f"Invalid connector_id '{connector_id}': "
            "must be a lowercase Python identifier (e.g. salesforce)."
        )

    node_wire_root = node_wire_root.resolve()
    if not (node_wire_root / "pyproject.toml").is_file():
        raise FileNotFoundError(f"node-wire root missing pyproject.toml: {node_wire_root}")

    runtime_wheel, connector_wheel = _resolve_wheels(node_wire_root, connector_id)
    nw_src = node_wire_root / "src"
    if not (nw_src / "bindings").is_dir():
        raise FileNotFoundError(f"node-wire src/bindings package missing: {nw_src / 'bindings'}")

    server_name = scope.server.name
    project_name = f"{server_name}-mcp"
    project_dir = output_dir / project_name
    if project_dir.exists():
        raise FileExistsError(f"Output project already exists: {project_dir}")

    module_name = server_name_to_module(server_name)
    module_dir = project_dir / "src" / module_name
    module_dir.mkdir(parents=True)

    wheels_dir = project_dir / "wheels"
    wheels_dir.mkdir()
    runtime_dest = wheels_dir / runtime_wheel.name
    connector_dest = wheels_dir / connector_wheel.name
    shutil.copy2(runtime_wheel, runtime_dest)
    shutil.copy2(connector_wheel, connector_dest)

    vendor_src = project_dir / "vendor" / "node_wire_src"
    _vendor_minimal_node_wire_src(nw_src, vendor_src, connector_id)

    config_src = node_wire_root / "config" / "connectors.yaml"
    if not config_src.is_file():
        raise FileNotFoundError(f"node-wire connector config missing: {config_src}")
    config_dir = project_dir / "config"
    config_dir.mkdir()
    shutil.copy2(config_src, config_dir / "connectors.yaml")

    connector_pkg = connector_dist_package_name(connector_id)
    mcp_dep = resolve_mcp_dependency(node_wire_root)

    (project_dir / "pyproject.toml").write_text(
        _pyproject_toml(
            project_name=project_name,
            module_name=module_name,
            description=scope.server.description,
            connector_pkg=connector_pkg,
            runtime_wheel_name=runtime_dest.name,
            connector_wheel_name=connector_dest.name,
            mcp_dep=mcp_dep,
        ),
        encoding="utf-8",
    )
    (module_dir / "__init__.py").write_text(
        f'"""Thin MCP host for node-wire connector `{connector_id}`."""\n',
        encoding="utf-8",
    )
    (module_dir / "__main__.py").write_text(
        _main_py(connector_id=connector_id),
        encoding="utf-8",
    )
    (project_dir / "README.md").write_text(
        _readme(
            project_name=project_name,
            module_name=module_name,
            connector_id=connector_id,
            connector_pkg=connector_pkg,
            description=scope.server.description,
            runtime_wheel_name=runtime_dest.name,
            connector_wheel_name=connector_dest.name,
        ),
        encoding="utf-8",
    )
    (project_dir / "Dockerfile").write_text(
        _dockerfile(
            module_name=module_name,
            connector_id=connector_id,
            connector_pkg=connector_pkg,
            mcp_dep=mcp_dep,
        ),
        encoding="utf-8",
    )
    (project_dir / ".dockerignore").write_text(_dockerignore(), encoding="utf-8")

    logger.info(
        "Wrote connector host project dir=%s connector_id=%s",
        project_dir,
        connector_id,
    )
    return project_dir


def _vendor_minimal_node_wire_src(
    nw_src: Path,
    vendor_src: Path,
    connector_id: str,
) -> None:
    """Copy only packages required for a single-connector MCP host.

    Full ``src/`` is unnecessary: unused connectors (slack, fhir, …) are never
    loaded when ``NW_ALLOWED_CONNECTORS`` is pinned to ``connector_id``.
    """
    packages = (
        "bindings",
        "node_wire_runtime",
        f"node_wire_{connector_id}",
    )
    vendor_src.mkdir(parents=True, exist_ok=False)
    for name in packages:
        src = nw_src / name
        if not src.is_dir():
            raise FileNotFoundError(
                f"Required package missing under node-wire src: {src} "
                f"(needed for connector_id={connector_id!r})"
            )
        shutil.copytree(src, vendor_src / name, ignore=_VENDOR_IGNORE)
        logger.info("Vendored %s -> %s", src, vendor_src / name)


def resolve_mcp_dependency(node_wire_root: Path) -> str:
    """Return a pip requirement for ``mcp`` matching the monorepo lockfile.

    Reads ``uv.lock`` under ``node_wire_root`` and pins ``mcp==<locked>``.
    Falls back to an mcp 1.x upper-bound when the lockfile is missing
    (e.g. unit-test fixtures).
    """
    lock_path = node_wire_root / "uv.lock"
    if not lock_path.is_file():
        logger.warning(
            "node-wire uv.lock missing at %s; using fallback %s",
            lock_path,
            _MCP_DEP_FALLBACK,
        )
        return _MCP_DEP_FALLBACK

    try:
        data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning(
            "Failed to parse %s (%s); using fallback %s",
            lock_path,
            exc,
            _MCP_DEP_FALLBACK,
        )
        return _MCP_DEP_FALLBACK

    for pkg in data.get("package", []):
        if pkg.get("name") == "mcp" and pkg.get("version"):
            spec = f"mcp=={pkg['version']}"
            logger.info("Pinning generated host mcp dependency to %s", spec)
            return spec

    logger.warning(
        "mcp package not found in %s; using fallback %s",
        lock_path,
        _MCP_DEP_FALLBACK,
    )
    return _MCP_DEP_FALLBACK


def _resolve_wheels(node_wire_root: Path, connector_id: str) -> tuple[Path, Path]:
    runtime_dist = node_wire_root / "packages" / "runtime" / "dist"
    connector_dist = node_wire_root / "packages" / "connectors" / connector_id / "dist"
    runtime_wheels = sorted(
        runtime_dist.glob("*.whl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    connector_wheels = sorted(
        connector_dist.glob("*.whl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not runtime_wheels:
        raise FileNotFoundError(
            f"No node-wire-runtime wheel in {runtime_dist}. "
            "Build it (e.g. uvx --from build pyproject-build --wheel -o dist "
            "in packages/runtime)."
        )
    if not connector_wheels:
        raise FileNotFoundError(
            f"No node-wire-{connector_id.replace('_', '-')} wheel in "
            f"{connector_dist}. Build it in packages/connectors/{connector_id}."
        )
    return runtime_wheels[0], connector_wheels[0]


def _pyproject_toml(
    *,
    project_name: str,
    module_name: str,
    description: str,
    connector_pkg: str,
    runtime_wheel_name: str,
    connector_wheel_name: str,
    mcp_dep: str,
) -> str:
    return f'''\
[project]
name = "{project_name}"
version = "0.1.0"
description = "{_escape_toml(description)}"
requires-python = ">=3.11"
dependencies = [
    "node-wire-runtime",
    "{connector_pkg}",
    "{mcp_dep}",
    "httpx[http2]>=0.27.0,<0.28.0",
    "python-dotenv>=1.0.0",
]

[project.scripts]
{module_name} = "{module_name}.__main__:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/{module_name}"]

[tool.uv.sources]
node-wire-runtime = {{ path = "wheels/{runtime_wheel_name}" }}
{connector_pkg} = {{ path = "wheels/{connector_wheel_name}" }}
'''


def _main_py(*, connector_id: str) -> str:
    return f'''\
# ponytail: thin host -- runtime+connector from wheels; bindings vendored
"""Entry point: run node-wire McpServer for connector `{connector_id}`."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _project_root() -> Path | None:
    here = Path(__file__).resolve().parent
    for root in (here, *here.parents):
        if (root / "vendor" / "node_wire_src" / "bindings").is_dir():
            return root
        if (root / "pyproject.toml").is_file() and (root / "config").is_dir():
            return root
    return None


def _running_in_container() -> bool:
    flag = os.environ.get("NW_MCP_CONTAINER", "").strip().lower()
    if flag in {{"1", "true", "yes"}}:
        return True
    return Path("/.dockerenv").is_file()


def _load_env() -> None:
    # Never let vendored MCP/REST merge cwd .env over process env.
    os.environ["NW_REST_LOAD_DOTENV"] = "false"
    root = _project_root()
    if root is None:
        raise SystemExit(
            "auth error: cannot locate generated MCP project root "
            "(expected vendor/node_wire_src/bindings or config/ + pyproject.toml)."
        )
    # Images: secrets come from the orchestrator (docker -e / --env-file /
    # ToolHive). Do not read a filesystem .env — that file must not be in the
    # image, and a bind-mount would expose it in the container filesystem.
    if _running_in_container():
        return
    env_path = root / ".env"
    if env_path.is_file():
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)


def _ensure_bindings_on_path() -> None:
    root = _project_root()
    if root is not None:
        nw_src = root / "vendor" / "node_wire_src"
        if (nw_src / "bindings").is_dir():
            nw_src_str = str(nw_src)
            if nw_src_str not in sys.path:
                sys.path.insert(0, nw_src_str)


def main() -> None:
    _load_env()
    os.environ["NW_ALLOWED_CONNECTORS"] = "{connector_id}"
    # Local Inspector convenience only. Container images do not disable auth
    # or open the scope policy — set those at runtime if you really need them.
    if not _running_in_container():
        os.environ.setdefault("NW_MCP_AUTH_DISABLED", "true")
        os.environ.setdefault("NW_MCP_SCOPE_POLICY_DEFAULT", "allow")
    root = _project_root()
    if root is not None:
        cfg = root / "config" / "connectors.yaml"
        if cfg.is_file():
            os.environ.setdefault("NW_CONFIG_PATH", str(cfg))
    _ensure_bindings_on_path()

    from bindings.mcp_server.server import McpServer

    transport = os.getenv("NW_MCP_TRANSPORT", "streamable-http")
    McpServer(
        server_name="nw-{connector_id}",
        connector_ids=["{connector_id}"],
    ).run(transport=transport)


if __name__ == "__main__":
    main()
'''


def _readme(
    *,
    project_name: str,
    module_name: str,
    connector_id: str,
    connector_pkg: str,
    description: str,
    runtime_wheel_name: str,
    connector_wheel_name: str,
) -> str:
    return f"""\
# {project_name}

{description}

Thin host generated by **nw-mcp-builder** (node-wire Docker packaging):

- Wheels: `wheels/{runtime_wheel_name}`, `wheels/{connector_wheel_name}`
- PYTHONPATH: vendored `vendor/node_wire_src` (`bindings`, `node_wire_runtime`, `node_wire_{connector_id}` only)
- Auth/OTel live in the node-wire connector, not this host

```text
McpServer(connector_ids=["{connector_id}"])
```

## Setup

```bash
cd {project_name}
cp .env.example .env   # optional locally — process env / secrets win if already set
# Fill connector secrets (see node-wire sample.env). Monorepo/cwd .env is ignored.
uv sync
```

## Run

```bash
uv run python -m {module_name}
NW_MCP_TRANSPORT=stdio uv run python -m {module_name}
```

Process env (ToolHive secrets, Docker `-e` / `--env-file`) is preferred. A project `.env` is **local-only**: it fills unset keys (`override=False`) and is never copied into the Docker image. Monorepo/cwd `.env` is never loaded.

| Variable | Default | Meaning |
|----------|---------|---------|
| `NW_MCP_TRANSPORT` | `streamable-http` | `stdio` or `streamable-http` |
| `NW_MCP_PORT` | `8081` | HTTP port |
| `NW_ALLOWED_CONNECTORS` | `{connector_id}` | Connector allowlist |
| `NW_MCP_AUTH_DISABLED` | `true` locally; **unset in images** | Local/Inspector only — do not bake into Docker |

## Docker

Secrets stay **out of the image**. `.dockerignore` is a whitelist (wheels, vendor src, `config/connectors.yaml`, host `src/` only). `.env`, `.env.example`, and tenant YAML never enter the build context. Inject credentials at **run** time:

```bash
docker build -t {module_name} .
docker run --rm --env-file .env -p 8081:8081 {module_name}
# ToolHive / K8s: pass secrets as process env, not a file baked into layers
```

`--env-file` sets process environment; it does not COPY the file into the image. Do not `COPY` or bind-mount `.env` into the container filesystem.

The image is digest-pinned, runs as non-root `USER app` with a read-only application tree, and does not disable MCP auth unless you set `NW_MCP_AUTH_DISABLED` at runtime.

Installs `{connector_pkg}` + `node-wire-runtime` from `./wheels`.
"""


def _dockerignore() -> str:
    return """\
# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0
#
# Whitelist: only paths the Dockerfile COPY's may enter the build context.
# Secrets, env files, git, and tenant YAML stay on the host.

*
!wheels/
!wheels/**
!vendor/node_wire_src/
!vendor/node_wire_src/**
!config/
!config/connectors.yaml
!src/
!src/**

**/.env
**/.env.*
**/tenants.yaml
**/tenants.yaml.tmp
**/playground_tenants.yaml
**/__pycache__
**/*.pyc
**/*.pyo
**/*.pem
**/*.key
**/credentials.json
**/.git
**/.DS_Store
"""


def _dockerfile(
    *,
    module_name: str,
    connector_id: str,
    connector_pkg: str,
    mcp_dep: str,
) -> str:
    return f'''\
##
## SPDX-FileCopyrightText: 2026 AOT Technologies
## SPDX-License-Identifier: Apache-2.0
##
FROM {PYTHON_312_SLIM_IMAGE}

LABEL org.opencontainers.image.title="{module_name}" \\
      org.opencontainers.image.description="Node Wire — {connector_id} MCP server (nw-mcp-builder)" \\
      org.opencontainers.image.source="https://github.com/AOT-Technologies/node-wire"

# No secrets in ENV. NW_MCP_CONTAINER disables filesystem .env loading.
ENV PYTHONPATH=/nw_src:/app/src \\
    PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    PIP_DISABLE_PIP_VERSION_CHECK=1 \\
    NW_ALLOWED_CONNECTORS={connector_id} \\
    NW_MCP_TRANSPORT=streamable-http \\
    NW_CONFIG_PATH=/app/config/connectors.yaml \\
    NW_REST_LOAD_DOTENV=false \\
    NW_MCP_CONTAINER=true

WORKDIR /app

COPY wheels/ /wheels/
COPY vendor/node_wire_src/ /nw_src/
COPY config/connectors.yaml /app/config/connectors.yaml
COPY src/ /app/src/

RUN pip install --no-cache-dir --find-links=/wheels \\
    node-wire-runtime {connector_pkg} "{mcp_dep}" "httpx[http2]>=0.27.0,<0.28.0" \\
    && rm -rf /wheels /root/.cache/pip \\
    && groupadd --system --gid 1000 app \\
    && useradd --system --uid 1000 --gid app --home /nonexistent --no-create-home --shell /usr/sbin/nologin app \\
    && chown -R root:root /app /nw_src \\
    && chmod -R a-w /app /nw_src

USER app

EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD \\
    python -c "import {module_name}" || exit 1

CMD ["python", "-m", "{module_name}"]
'''


def _escape_toml(value: str) -> str:
    collapsed = " ".join(value.split())
    return collapsed.replace("\\", "\\\\").replace('"', '\\"')
