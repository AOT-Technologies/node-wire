# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Finish a stacklok-scaffolded project for node-wire mode.

Runs after stacklok's scaffold, renderers and patches. Adds the node-wire wheels and their
``[tool.uv.sources]``, the connector's ``connectors.yaml`` entry, runtime environment defaults,
config-tool registration, and a README section. Stacklok's Dockerfile base images are unchanged.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404  # fixed `uv lock` argv, no shell
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import yaml

from mcp_builder.generate.plan import ServerPlan
from nw_stacklok.wheels import WheelTarget

RUNTIME_ENV: Dict[str, str] = {
    "NW_MULTITENANCY_ENABLED": "true",
    # ToolHive authorizes callers; node-wire's per-caller scope policy is not used here.
    "NW_MCP_SCOPE_POLICY_DEFAULT": "allow",
    "NW_REST_LOAD_DOTENV": "false",
    "NW_CONFIG_PATH": "/app/config/connectors.yaml",
    "NW_TENANTS_PATH": "/app/tenants/tenants.yaml",
}


class TemplateChangedError(RuntimeError):
    """A patch to the stacklok-generated project found nothing to change."""


def _replace_once(text: str, old: str, new: str, *, where: str) -> str:
    if old not in text:
        raise TemplateChangedError(f"stacklok template changed: {old!r} not found in {where}")
    return text.replace(old, new, 1)


def _sub_once(text: str, pattern: str, repl: str, *, where: str) -> str:
    result, count = re.subn(pattern, repl, text, count=1, flags=re.MULTILINE)
    if not count:
        raise TemplateChangedError(f"stacklok template changed: /{pattern}/ not found in {where}")
    return result


class WheelsMissingError(FileNotFoundError):
    """No wheel matching the image (see :class:`~nw_stacklok.wheels.WheelTarget`) for a package."""


@dataclass(frozen=True)
class Distribution:
    name: str  # distribution name, e.g. node-wire-runtime
    dist_dir: Path


def distributions(node_wire_root: Path, connector_id: str) -> List[Distribution]:
    """The node-wire wheels a stacklok server installs, in dependency order."""
    packages = node_wire_root / "packages"
    return [
        Distribution("node-wire-runtime", packages / "runtime" / "dist"),
        Distribution("node-wire-bindings", packages / "bindings" / "dist"),
        Distribution("node-wire-toolhive", packages / "toolhive" / "dist"),
        Distribution(
            f"node-wire-{connector_id.replace('_', '-')}",
            packages / "connectors" / connector_id / "dist",
        ),
    ]


def finish_project(project_dir: Path, plan: ServerPlan, *, wheels: bool, lock: bool) -> None:
    assert plan.node_wire is not None
    node_wire_root = Path(plan.node_wire.node_wire_root)
    connector_id = plan.node_wire.connector_id

    sources: Dict[str, Dict[str, str]] = {}
    if wheels:
        sources = _copy_wheels(project_dir, node_wire_root, connector_id)
    _update_pyproject(project_dir / "pyproject.toml", node_wire_root, connector_id, sources)
    _write_config(project_dir, node_wire_root, connector_id)
    _update_dockerfile(project_dir / "Dockerfile", connector_id, wheels=wheels)
    _update_dockerignore(project_dir / ".dockerignore")
    _update_env_example(project_dir / ".env.example", connector_id)
    _patch_sources(project_dir / "src" / plan.module_name)
    _append_readme(project_dir / "README.md", plan, connector_id)
    if lock:
        _lock(project_dir)


def _lock(project_dir: Path) -> None:
    """``uv lock`` so the image builds with stacklok's ``uv sync --frozen``."""
    try:
        subprocess.run(["uv", "lock"], cwd=project_dir, check=True)  # nosec B603 B607
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"`uv lock` failed in {project_dir}: {exc}") from exc


def _copy_wheels(
    project_dir: Path, node_wire_root: Path, connector_id: str
) -> Dict[str, Dict[str, str]]:
    wheel_target = WheelTarget.from_dockerfile(project_dir / "Dockerfile")
    target = project_dir / "wheels"
    target.mkdir(exist_ok=True)
    found: Dict[str, Dict[str, str]] = {}
    missing: List[str] = []
    for dist in distributions(node_wire_root, connector_id):
        wheels = wheel_target.wheels_by_arch(dist.dist_dir)
        if not wheels:
            missing.append(f"{dist.name} (in {dist.dist_dir})")
            continue
        found[dist.name] = {}
        for arch, wheel in wheels.items():
            shutil.copy2(wheel, target / wheel.name)
            found[dist.name][arch] = f"wheels/{wheel.name}"
    if missing:
        raise WheelsMissingError(
            f"No {wheel_target.description} wheel for: "
            + ", ".join(missing)
            + ". nw gen-stacklok builds them (or: CIBW_BUILD='"
            + wheel_target.cibw_build
            + "' scripts/build-packages.sh --cibw-linux <packages>)."
        )
    common = set.intersection(*(set(arches) for arches in found.values()))
    if not common:
        raise WheelsMissingError(
            "The node-wire wheels share no architecture: "
            + ", ".join(f"{name}: {sorted(arches)}" for name, arches in found.items())
        )
    return {
        name: {a: p for a, p in arches.items() if a in common} for name, arches in found.items()
    }


def _update_pyproject(
    path: Path,
    node_wire_root: Path,
    connector_id: str,
    sources: Dict[str, Dict[str, str]],
) -> None:
    text = path.read_text(encoding="utf-8")
    # Stacklok adds httpx>=0.28 for its generated client; node-wire's runtime pins httpx<0.28
    # and there is no httpx client in node-wire mode.
    text = _sub_once(text, r'^[ \t]*"httpx>=0\.28[^"]*",?[ \t]*\n', "", where=path.name)
    names = [d.name for d in distributions(node_wire_root, connector_id)]
    deps = "".join(f'\n    "{name}",' for name in names)
    text = _sub_once(text, r"^(dependencies\s*=\s*\[)", rf"\g<1>{deps}", where=path.name)
    if sources:
        arches = sorted(next(iter(sources.values())))
        lines = ["", "[tool.uv]", "environments = ["]
        lines += [f"    \"sys_platform == 'linux' and platform_machine == '{a}'\"," for a in arches]
        lines += ["]", "", "[tool.uv.sources]"]
        for name, by_arch in sources.items():
            entries = ", ".join(
                f'{{ path = "{p}", marker = "platform_machine == \'{a}\'" }}'
                for a, p in sorted(by_arch.items())
            )
            lines.append(f"{name} = [{entries}]")
        text = text.rstrip("\n") + "\n" + "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")


def _connector_entry(node_wire_root: Path, connector_id: str) -> Dict[str, object]:
    config = node_wire_root / "config" / "connectors.yaml"
    raw = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    entry = (raw.get("connectors") or {}).get(connector_id)
    if not isinstance(entry, dict):
        raise FileNotFoundError(f"{config} has no entry for connector {connector_id!r}")
    entry = dict(entry)
    entry["enabled"] = True
    exposed = list(entry.get("exposed_via") or [])
    if "mcp" not in exposed:
        exposed.append("mcp")
    entry["exposed_via"] = exposed
    return entry


def _write_config(project_dir: Path, node_wire_root: Path, connector_id: str) -> None:
    entry = _connector_entry(node_wire_root, connector_id)
    config_dir = project_dir / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "connectors.yaml").write_text(
        yaml.safe_dump({"connectors": {connector_id: entry}}, sort_keys=False), encoding="utf-8"
    )
    doc = {
        "name": "default",
        "default": True,
        "base_url": entry.get("base_url", "https://api.example.com"),
        "auth": entry.get("auth", {"provider": "none"}),
    }
    if entry.get("auth_schemes"):
        doc["auth_schemes"] = entry["auth_schemes"]
    example = {"tenants": {"example-tenant": {connector_id: [doc]}}}
    (config_dir / "tenants.example.yaml").write_text(
        "# One entry per tenant; the ToolHive proxy for that tenant sets X-Tenant-ID to its key.\n"
        "# Mount the real file at NW_TENANTS_PATH (default /app/tenants/tenants.yaml).\n"
        "# Credentials never go here: ToolHive forwards them per request.\n"
        + yaml.safe_dump(example, sort_keys=False),
        encoding="utf-8",
    )


def _update_dockerfile(path: Path, connector_id: str, *, wheels: bool) -> None:
    text = path.read_text(encoding="utf-8")
    if wheels:
        text = _replace_once(
            text,
            "COPY pyproject.toml uv.lock* README.md ./\n",
            "COPY wheels/ ./wheels/\nCOPY pyproject.toml uv.lock* README.md ./\n",
            where=path.name,
        )
    env = {
        **RUNTIME_ENV,
        "NW_ALLOWED_CONNECTORS": connector_id,
        "NW_UPSTREAM_BEARER_CONNECTORS": connector_id,
    }
    env_lines = " \\\n    ".join(f"{k}={v}" for k, v in env.items())
    text = _replace_once(
        text,
        "COPY --from=builder /app/src /app/src\n",
        "COPY --from=builder /app/src /app/src\nCOPY config/ /app/config/\n\n"
        f"# node-wire runtime (tenant configs are mounted at NW_TENANTS_PATH)\nENV {env_lines}\n",
        where=path.name,
    )
    path.write_text(text, encoding="utf-8")


def _update_dockerignore(path: Path) -> None:
    text = path.read_text(encoding="utf-8").rstrip("\n")
    # Allow-list: a tenants file saved under another name (tenants.prod.yaml) stays out too.
    text += (
        "\n\n# node-wire: only connectors.yaml goes into the image; tenant configs are mounted\n"
        "# at runtime, never baked in\nconfig/*\n!config/connectors.yaml\n"
    )
    path.write_text(text, encoding="utf-8")


def _update_env_example(path: Path, connector_id: str) -> None:
    env = {
        **RUNTIME_ENV,
        "NW_CONFIG_PATH": "config/connectors.yaml",
        "NW_TENANTS_PATH": "config/tenants.yaml",
        "NW_ALLOWED_CONNECTORS": connector_id,
        "NW_UPSTREAM_BEARER_CONNECTORS": connector_id,
    }
    text = path.read_text(encoding="utf-8").rstrip("\n")
    text += "\n\n# node-wire runtime\n" + "".join(f"{k}={v}\n" for k, v in env.items())
    path.write_text(text, encoding="utf-8")


def _patch_sources(module_dir: Path) -> None:
    mcp_builder = module_dir / "api" / "mcp_builder.py"
    text = mcp_builder.read_text(encoding="utf-8")
    text = _replace_once(
        text,
        "from mcp.server.fastmcp import FastMCP\n",
        "from mcp.server.fastmcp import FastMCP\nfrom node_wire_toolhive import register_config_tools\n",
        where=mcp_builder.name,
    )
    text = _sub_once(
        text,
        r"^([ \t]*)return mcp$",
        r"\1register_config_tools(mcp, tools._client.node_wire)\n\n\1return mcp",
        where=mcp_builder.name,
    )
    mcp_builder.write_text(text, encoding="utf-8")

    # node-wire reads its settings from the process environment; the template's Settings
    # model would reject NW_* keys in .env, so ignore them there and load .env into the
    # environment for local runs (containers get ENV / orchestrator values instead).
    settings = module_dir / "settings.py"
    text = settings.read_text(encoding="utf-8")
    text = _sub_once(
        text,
        r'^([ \t]*)env_file_encoding="utf-8",\n',
        r'\g<0>\1extra="ignore",\n',
        where=settings.name,
    )
    settings.write_text(text, encoding="utf-8")

    main = module_dir / "__main__.py"
    text = main.read_text(encoding="utf-8")
    text = _replace_once(
        text,
        'if __name__ == "__main__":\n',
        'if __name__ == "__main__":\n'
        "    from dotenv import load_dotenv\n\n"
        "    load_dotenv(override=False)\n",
        where=main.name,
    )
    main.write_text(text, encoding="utf-8")


def _append_readme(path: Path, plan: ServerPlan, connector_id: str) -> None:
    text = path.read_text(encoding="utf-8").rstrip("\n")
    text += f"""

## node-wire runtime

This server was generated by `nw gen-stacklok`. Every tool runs through the node-wire
connector `{connector_id}` (validation, retries, circuit breaker, error taxonomy, telemetry)
instead of calling the API directly.

- **Auth** follows ToolHive: the credential ToolHive forwards as `Authorization: Bearer` is
  relayed into the connector's own auth placement on every call
  (`NW_UPSTREAM_BEARER_CONNECTORS={connector_id}` opts in).
- **Tenants**: each ToolHive tenant proxy (`deploy/tenant-proxy.yaml`) sets `X-Tenant-ID`.
  Tenant configs (base URL, auth placement, named configs) come from the file at
  `NW_TENANTS_PATH`; see `config/tenants.example.yaml`. `nw_list_configs` and
  `nw_select_config` choose a named config for the session.
- **Wheels** in `wheels/` are cp313 musllinux builds for the image's Alpine base; `uv lock`
  is limited to those Linux architectures (`[tool.uv] environments`). Build the image with
  `docker build .`; the wheels do not install on macOS or Windows hosts.
"""
    path.write_text(text + "\n", encoding="utf-8")
