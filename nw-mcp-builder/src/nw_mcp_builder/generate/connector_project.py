"""Emit a thin MCP host that runs a node-wire connector via wheels.

ponytail: Docker-parity packaging -- install runtime+connector ``.whl`` for
entry points / deps, and put full node-wire ``src/`` on PYTHONPATH (bindings +
runtime/connector source). Cython wheels alone can omit nested packages
(e.g. ``node_wire_runtime.policies``).
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from nw_mcp_builder.schema.models import MCPScope

logger = logging.getLogger(__name__)


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
        raise FileNotFoundError(
            f"node-wire root missing pyproject.toml: {node_wire_root}"
        )

    runtime_wheel, connector_wheel = _resolve_wheels(node_wire_root, connector_id)
    nw_src = node_wire_root / "src"
    if not (nw_src / "bindings").is_dir():
        raise FileNotFoundError(
            f"node-wire src/bindings package missing: {nw_src / 'bindings'}"
        )

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
    shutil.copytree(
        nw_src,
        vendor_src,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            "*.pyc",
            "*.pyo",
            ".mypy_cache",
            ".ruff_cache",
            "*.egg-info",
        ),
    )

    config_src = node_wire_root / "config" / "connectors.yaml"
    if not config_src.is_file():
        raise FileNotFoundError(f"node-wire connector config missing: {config_src}")
    config_dir = project_dir / "config"
    config_dir.mkdir()
    shutil.copy2(config_src, config_dir / "connectors.yaml")

    connector_pkg = connector_dist_package_name(connector_id)

    (project_dir / "pyproject.toml").write_text(
        _pyproject_toml(
            project_name=project_name,
            module_name=module_name,
            description=scope.server.description,
            connector_pkg=connector_pkg,
            runtime_wheel_name=runtime_dest.name,
            connector_wheel_name=connector_dest.name,
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
        ),
        encoding="utf-8",
    )

    logger.info(
        "Wrote connector host project dir=%s connector_id=%s",
        project_dir,
        connector_id,
    )
    return project_dir


def _resolve_wheels(node_wire_root: Path, connector_id: str) -> tuple[Path, Path]:
    runtime_dist = node_wire_root / "packages" / "runtime" / "dist"
    connector_dist = (
        node_wire_root / "packages" / "connectors" / connector_id / "dist"
    )
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
    "mcp>=1.6.0",
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

from dotenv import load_dotenv


def _project_root() -> Path | None:
    here = Path(__file__).resolve().parent
    for root in (here, *here.parents):
        if (root / "vendor" / "node_wire_src" / "bindings").is_dir():
            return root
        if (root / ".env").is_file() and (root / "pyproject.toml").is_file():
            return root
    return None


def _load_env() -> None:
    root = _project_root()
    if root is not None:
        load_dotenv(root / ".env")
    load_dotenv()


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
- PYTHONPATH: vendored `vendor/node_wire_src`
- Auth/OTel live in the node-wire connector, not this host

```text
McpServer(connector_ids=["{connector_id}"])
```

## Setup

```bash
cd {project_name}
# Put connector secrets in .env (see .env.example / node-wire sample.env)
uv sync
```

## Run

```bash
uv run python -m {module_name}
NW_MCP_TRANSPORT=stdio uv run python -m {module_name}
```

| Variable | Default | Meaning |
|----------|---------|---------|
| `NW_MCP_TRANSPORT` | `streamable-http` | `stdio` or `streamable-http` |
| `NW_MCP_PORT` | `8081` | HTTP port |
| `NW_ALLOWED_CONNECTORS` | `{connector_id}` | Connector allowlist |
| `NW_MCP_AUTH_DISABLED` | `true` | Local/Inspector |

## Docker

```bash
docker build -t {module_name} .
```

Installs `{connector_pkg}` + `node-wire-runtime` from `./wheels`.
"""


def _dockerfile(
    *,
    module_name: str,
    connector_id: str,
    connector_pkg: str,
) -> str:
    return f'''\
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \\
    curl ca-certificates \\
    && rm -rf /var/lib/apt/lists/*

COPY wheels/ /wheels/
COPY vendor/node_wire_src/ /nw_src/
COPY config/ ./config/
COPY pyproject.toml README.md ./
COPY src/ ./src/

ENV PYTHONPATH=/nw_src \\
    NW_ALLOWED_CONNECTORS={connector_id} \\
    NW_MCP_TRANSPORT=streamable-http \\
    NW_CONFIG_PATH=/app/config/connectors.yaml

RUN pip install --no-cache-dir --find-links=/wheels \\
    node-wire-runtime {connector_pkg} "mcp>=1.6.0" "httpx[http2]>=0.27.0,<0.28.0" \\
    && pip install --no-cache-dir -e /app \\
    && rm -rf /wheels

RUN groupadd --system --gid 1000 app \\
    && useradd --system --uid 1000 --gid app --home /app app \\
    && chown -R app:app /app

USER app

EXPOSE 8081

CMD ["python", "-m", "{module_name}"]
'''


def _escape_toml(value: str) -> str:
    collapsed = " ".join(value.split())
    return collapsed.replace("\\", "\\\\").replace('"', '\\"')
