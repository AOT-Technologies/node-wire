# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Emit a thin MCP host that runs a node-wire connector via wheels only.

Installs three Cython wheels into the image (``node-wire-runtime``,
``node-wire-bindings``, ``node-wire-<connector>``). No vendored source tree —
packages come from site-packages after ``pip install``.
"""

from __future__ import annotations

import logging
import re
import shutil
import tomllib
from pathlib import Path

from nw_mcp_builder.schema.models import MCPScope

logger = logging.getLogger(__name__)

# node-wire McpServer uses the mcp 1.x decorator API (@server.list_tools()),
# which was removed in mcp 2.0. Prefer the version locked in the monorepo.
_MCP_DEP_FALLBACK = "mcp>=1.6.0,<2"

# Digest-pinned base. Keep in sync with Dockerfile and docker/*/Dockerfile
# (enforced by tests/nw_mcp_builder/test_generated_dockerfile.py and
# .github/workflows/docker-policy.yml).
PYTHON_313_SLIM_IMAGE = (
    "python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b"
)

BINDINGS_DIST_PACKAGE = "node-wire-bindings"


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
    *,
    tool_mode: str = "list",
) -> Path:
    """Create ``out/<server>-mcp/`` wrapping node-wire ``McpServer``.

    ``tool_mode`` becomes the host's default ``NW_MCP_TOOL_MODE`` (``list`` or
    ``search``), in ``main.py`` and the Dockerfile; still overridable at runtime.
    """
    if tool_mode not in ("list", "search"):
        raise ValueError(f"Unknown tool mode {tool_mode!r}: expected 'list' or 'search'")
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

    runtime_wheel, bindings_wheel, connector_wheel = _resolve_wheels(node_wire_root, connector_id)

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
    bindings_dest = wheels_dir / bindings_wheel.name
    connector_dest = wheels_dir / connector_wheel.name
    shutil.copy2(runtime_wheel, runtime_dest)
    shutil.copy2(bindings_wheel, bindings_dest)
    shutil.copy2(connector_wheel, connector_dest)

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
            bindings_wheel_name=bindings_dest.name,
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
        _main_py(connector_id=connector_id, tool_mode=tool_mode),
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
            bindings_wheel_name=bindings_dest.name,
            connector_wheel_name=connector_dest.name,
            tool_mode=tool_mode,
        ),
        encoding="utf-8",
    )
    (project_dir / "Dockerfile").write_text(
        _dockerfile(
            module_name=module_name,
            connector_id=connector_id,
            connector_pkg=connector_pkg,
            mcp_dep=mcp_dep,
            tool_mode=tool_mode,
        ),
        encoding="utf-8",
    )
    (project_dir / ".dockerignore").write_text(_dockerignore(), encoding="utf-8")

    logger.info(
        "Wrote connector host project dir=%s connector_id=%s (wheels-only)",
        project_dir,
        connector_id,
    )
    return project_dir


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


# The Python and libc of PYTHON_313_SLIM_IMAGE: the wheels copied into the project must install
# there. dist/ also holds wheels for other targets (gen-stacklok's Alpine musllinux, macOS, cp312).
_IMAGE_PYTHON_TAGS = frozenset({"cp313", "py3", "py313"})
_IMAGE_ABI3_MAX_MINOR = 13


def _installs_in_image(wheel: Path) -> bool:
    """True when ``wheel``'s tags fit CPython 3.13 on glibc Linux (or any platform)."""
    parts = wheel.name[: -len(".whl")].split("-")
    if len(parts) < 5:
        return False
    python_tags, abi_tags, platform_tags = (tag.split(".") for tag in parts[-3:])
    python_ok = any(tag in _IMAGE_PYTHON_TAGS for tag in python_tags) or (
        "abi3" in abi_tags
        and any(
            tag.startswith("cp3") and tag[3:].isdigit() and int(tag[3:]) <= _IMAGE_ABI3_MAX_MINOR
            for tag in python_tags
        )
    )
    platform_ok = any(
        tag == "any" or tag.startswith(("linux_", "manylinux")) for tag in platform_tags
    )
    return python_ok and platform_ok


def _newest_installable_wheel(dist: Path, *, package: str, build_command: str) -> Path:
    wheels = sorted(dist.glob("*.whl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not wheels:
        raise FileNotFoundError(f"No {package} wheel in {dist}. Build it: `{build_command}`.")
    installable = [wheel for wheel in wheels if _installs_in_image(wheel)]
    if not installable:
        found = ", ".join(wheel.name for wheel in wheels)
        raise FileNotFoundError(
            f"No {package} wheel in {dist} installs on the MCP host image "
            f"(CPython 3.13, glibc Linux); found: {found}. Build one: `{build_command}`."
        )
    return installable[0]


def _resolve_wheels(node_wire_root: Path, connector_id: str) -> tuple[Path, Path, Path]:
    packages = node_wire_root / "packages"
    connector_package = f"node-wire-{connector_id.replace('_', '-')}"
    return (
        _newest_installable_wheel(
            packages / "runtime" / "dist",
            package="node-wire-runtime",
            build_command="nw gen-whl --runtime",
        ),
        _newest_installable_wheel(
            packages / "bindings" / "dist",
            package=BINDINGS_DIST_PACKAGE,
            build_command="nw gen-whl --bindings",
        ),
        _newest_installable_wheel(
            packages / "connectors" / connector_id / "dist",
            package=connector_package,
            build_command=f"nw gen-whl --connector-id {connector_id}",
        ),
    )


def _pyproject_toml(
    *,
    project_name: str,
    module_name: str,
    description: str,
    connector_pkg: str,
    runtime_wheel_name: str,
    bindings_wheel_name: str,
    connector_wheel_name: str,
    mcp_dep: str,
) -> str:
    return f'''\
[project]
name = "{project_name}"
version = "0.1.0"
description = "{_escape_toml(description)}"
requires-python = ">=3.13"
dependencies = [
    "node-wire-runtime",
    "{BINDINGS_DIST_PACKAGE}",
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
{BINDINGS_DIST_PACKAGE} = {{ path = "wheels/{bindings_wheel_name}" }}
{connector_pkg} = {{ path = "wheels/{connector_wheel_name}" }}
'''


def _main_py(*, connector_id: str, tool_mode: str = "list") -> str:
    return f'''\
# ponytail: thin host -- runtime + bindings + connector from wheels
"""Entry point: run node-wire McpServer for connector `{connector_id}`."""

from __future__ import annotations

import os
from pathlib import Path


def _project_root() -> Path | None:
    here = Path(__file__).resolve().parent
    for root in (here, *here.parents):
        if (root / "pyproject.toml").is_file() and (root / "config").is_dir():
            return root
        if (root / "wheels").is_dir() and (root / "config").is_dir():
            return root
    return None


def _running_in_container() -> bool:
    flag = os.environ.get("NW_MCP_CONTAINER", "").strip().lower()
    if flag in {{"1", "true", "yes"}}:
        return True
    return Path("/.dockerenv").is_file()


def _load_env() -> None:
    # Never let process cwd .env override orchestrator-injected secrets.
    os.environ["NW_REST_LOAD_DOTENV"] = "false"
    # Images: secrets come from the orchestrator (docker -e / --env-file /
    # ToolHive). Do not read a filesystem .env — that file must not be in the
    # image. Inside a container, NW_CONFIG_PATH is already set via Dockerfile ENV.
    if _running_in_container():
        return
    root = _project_root()
    if root is None:
        raise SystemExit(
            "auth error: cannot locate generated MCP project root "
            "(expected config/ + pyproject.toml or wheels/)."
        )
    env_path = root / ".env"
    if env_path.is_file():
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)


def main() -> None:
    _load_env()
    from node_wire_runtime.host_logging import configure_host_logging

    # Prints the runtime's trace_id / error_code / audit_event fields; OTLP export
    # only when OTEL_EXPORTER_OTLP_ENDPOINT is set.
    configure_host_logging("nw-{connector_id}")
    os.environ["NW_ALLOWED_CONNECTORS"] = "{connector_id}"
    # Local Inspector convenience only. Container images do not disable auth
    # or open the scope policy — set those at runtime if you really need them.
    if not _running_in_container():
        os.environ.setdefault("NW_MCP_AUTH_DISABLED", "true")
        os.environ.setdefault("NW_MCP_SCOPE_POLICY_DEFAULT", "allow")
    # Chosen at generation time (nw-mcp-builder); override with NW_MCP_TOOL_MODE.
    os.environ.setdefault("NW_MCP_TOOL_MODE", "{tool_mode}")
    root = _project_root()
    if root is not None:
        cfg = root / "config" / "connectors.yaml"
        if cfg.is_file():
            os.environ.setdefault("NW_CONFIG_PATH", str(cfg))

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
    bindings_wheel_name: str,
    connector_wheel_name: str,
    tool_mode: str,
) -> str:
    return f"""\
# {project_name}

{description}

Thin host generated by **nw-mcp-builder** (node-wire Docker packaging):

- Wheels: `wheels/{runtime_wheel_name}`, `wheels/{bindings_wheel_name}`, `wheels/{connector_wheel_name}`
- Imports come from installed wheels (no vendored `src/` on PYTHONPATH)
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
| `NW_MCP_TOOL_MODE` | `{tool_mode}` (chosen at generation) | `list` — every tool in `tools/list`; `search` — only `nw_search_tools` + `nw_call_tool`, schemas on demand |
| `NW_MCP_PORT` | `8081` | HTTP port |
| `NW_ALLOWED_CONNECTORS` | `{connector_id}` | Connector allowlist |
| `NW_MCP_AUTH_DISABLED` | `true` locally; **unset in images** | Local/Inspector only — do not bake into Docker |
| `NW_MCP_SCOPE_POLICY_DEFAULT` | `allow` locally; **unset (= `deny`) in images** | With no `NW_MCP_ACTION_SCOPE_MAP_JSON`, `deny` silently returns zero tools from `tools/list` — this is separate from `NW_MCP_AUTH_DISABLED` and must be set explicitly too, e.g. for local ToolHive testing |

## Docker

Secrets stay **out of the image**. `.dockerignore` is a whitelist (wheels, `config/connectors.yaml`, host `src/` only). `.env`, `.env.example`, and tenant YAML never enter the build context. Inject credentials at **run** time:

```bash
DOCKER_BUILDKIT=1 docker build -t {module_name} .
docker run --rm --env-file .env -p 8081:8081 {module_name}
# ToolHive / K8s: pass secrets as process env, not a file baked into layers
```

`--env-file` sets process environment; it does not COPY the file into the image. Do not `COPY` or bind-mount `.env` into the container filesystem.

The image is multi-stage and digest-pinned: wheels install in a `deps` stage (BuildKit pip cache), then only `/usr/local` is copied into the runtime stage. App sources (`src/`, config) are copied **after** that so source edits do not bust the install layer. Runs as non-root `USER app` with a read-only application tree, and does not disable MCP auth or open the scope policy unless you set `NW_MCP_AUTH_DISABLED` / `NW_MCP_SCOPE_POLICY_DEFAULT` at runtime. Both default fail-closed in images (unlike local `uv run`, which sets both for Inspector convenience) — a container started without them accepts connections but `tools/list` comes back empty.

Installs `{connector_pkg}` + `node-wire-runtime` + `{BINDINGS_DIST_PACKAGE}` from `./wheels`.
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
    tool_mode: str = "list",
) -> str:
    return f'''\
# syntax=docker/dockerfile:1
##
## SPDX-FileCopyrightText: 2026 AOT Technologies
## SPDX-License-Identifier: Apache-2.0
##
# Multi-stage MCP host image (wheels-only):
# - deps: install runtime + bindings + connector wheels + PyPI deps
#   (BuildKit pip cache); wheels never enter the final image layers.
# - runtime: copy /usr/local from deps, then thin host src + config last
#   so source edits do not invalidate the expensive install layer.

FROM {PYTHON_313_SLIM_IMAGE} AS deps

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \\
    PYTHONDONTWRITEBYTECODE=1

COPY wheels/ /wheels/
# --no-compile, not --no-cache-dir: the local wheels install offline via
# --find-links, but the PyPI tree behind them (opentelemetry-*, traceloop-sdk,
# cryptography via PyJWT[crypto], pydantic, mcp) is a real download. Every
# `gen-all` produces new wheel files, which invalidates this layer, so that
# download repeats on each loop. --no-cache-dir told pip to neither read nor
# write a cache, leaving the mount above permanently empty and doing nothing.
# --no-compile then avoids writing .pyc files the next two lines only delete.
# The find still earns its place: it strips the base image's own stdlib
# __pycache__, which shrinks the /usr/local copied into the final stage.
# Brace the __pycache__ cleanup so `|| true` cannot mask a failed pip install
# (shell associates `&&`/`||` left-to-right; bare `pip && find || true && …`
# succeeds even when pip fails).
RUN --mount=type=cache,target=/root/.cache/pip \\
    pip install --no-compile --find-links=/wheels \\
        node-wire-runtime {BINDINGS_DIST_PACKAGE} {connector_pkg} "{mcp_dep}" "httpx[http2]>=0.27.0,<0.28.0" \\
    && {{ find /usr/local -type d -name '__pycache__' -exec rm -rf {{}} + 2>/dev/null || true; }} \\
    && find /usr/local -type f \\( -name '*.pyc' -o -name '*.pyo' \\) -delete

# --- runtime stage ---
FROM {PYTHON_313_SLIM_IMAGE}

LABEL org.opencontainers.image.title="{module_name}" \\
      org.opencontainers.image.description="Node Wire — {connector_id} MCP server (nw-mcp-builder)" \\
      org.opencontainers.image.source="https://github.com/AOT-Technologies/node-wire"

# No secrets in ENV. NW_MCP_CONTAINER disables filesystem .env loading.
ENV PYTHONPATH=/app/src \\
    PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    PIP_DISABLE_PIP_VERSION_CHECK=1 \\
    NW_ALLOWED_CONNECTORS={connector_id} \\
    NW_MCP_TRANSPORT=streamable-http \\
    NW_CONFIG_PATH=/app/config/connectors.yaml \\
    NW_REST_LOAD_DOTENV=false \\
    NW_MCP_TOOL_MODE={tool_mode} \\
    NW_MCP_CONTAINER=true

WORKDIR /app

# Before the COPY below: this depends on nothing from the deps stage, so
# keeping it above the 154MB copy lets it stay cached when wheels change.
RUN groupadd --system --gid 1000 app \\
    && useradd --system --uid 1000 --gid app --home /nonexistent --no-create-home --shell /usr/sbin/nologin app

# Installed packages only — no /wheels in the final image.
COPY --from=deps /usr/local /usr/local

# --chmod normalizes to a known-good, world-readable mode regardless of the
# host file's permissions (e.g. config/connectors.yaml is 600 on disk) —
# the final `chmod -R a-w` below only ever *removes* the write bit, so a
# source file with no group/other bits would stay unreadable by `USER app`.
# Use 0755 (not 0644) even for the YAML file: Docker applies --chmod to
# parent dirs it creates for the destination path, and a 0644 directory has
# no execute bit → ``USER app`` cannot traverse ``/app/config``.
COPY --chmod=0755 config/connectors.yaml /app/config/connectors.yaml
COPY --chmod=0755 src/ /app/src/

RUN chown -R root:root /app \\
    && chmod -R a-w /app

USER app

EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD \\
    python -c "import {module_name}" || exit 1

CMD ["python", "-m", "{module_name}"]
'''


def _escape_toml(value: str) -> str:
    collapsed = " ".join(value.split())
    return collapsed.replace("\\", "\\\\").replace('"', '\\"')
