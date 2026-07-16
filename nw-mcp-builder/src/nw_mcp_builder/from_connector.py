# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Automate node-wire connector → thin MCP host (wheels + fixture + generate).

Steps:
  1. Resolve connector package under node-wire ``packages/connectors/<id>``
  2. Build ``node-wire-runtime`` + ``node-wire-<connector>`` wheels into dist/
  3. Ensure a connector-mode scope fixture exists (create/update from logic.py)
  4. Run ``run_connector_pipeline`` → ``out/<server>-mcp/``
  5. Write ``.env.example`` with secret env names from connectors.yaml / sample.env
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import textwrap
from pathlib import Path

import yaml

from nw_mcp_builder.generate.connector_project import connector_dist_package_name
from nw_mcp_builder.pipeline import run_connector_pipeline

logger = logging.getLogger(__name__)

# connector_id → env var prefix(es) for sample.env scraping
_ENV_PREFIXES: dict[str, tuple[str, ...]] = {
    "salesforce": ("SALESFORCE_",),
    "fhir_epic": ("EPIC_",),
    "fhir_cerner": ("CERNER_",),
    "google_drive": ("GOOGLE_DRIVE_", "GDRIVE_"),
    "slack": ("SLACK_",),
    "stripe": ("STRIPE_",),
    "smtp": ("SMTP_",),
    "http_generic": ("HTTP_GENERIC_",),
}

_ACTION_RE = re.compile(
    r"""@(?:nw_action|sdk_action)\(\s*["']([^"']+)["']""",
    re.MULTILINE,
)
_SPEC_ASSIGN_RE = re.compile(
    r"""\[[\"']([^\"']+)[\"']\]\s*=\s*SdkActionSpec\s*\(""",
    re.MULTILINE,
)


def run_from_connector(
    connector_id: str,
    *,
    node_wire_root: Path,
    package_root: Path,
    output_dir: Path | None = None,
    fixtures_dir: Path | None = None,
    skip_build_wheels: bool = False,
    force_fixture: bool = False,
    force_output: bool = False,
    python: str | None = None,
) -> Path:
    """Build wheels, ensure fixture, generate host under ``output_dir``.

    Returns:
        Path to the generated project directory.
    """
    import shutil

    connector_id = connector_id.strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", connector_id):
        raise ValueError(
            f"Invalid connector_id '{connector_id}': "
            "must be a lowercase Python identifier (e.g. google_drive)."
        )

    node_wire_root = node_wire_root.resolve()
    package_root = package_root.resolve()
    output_dir = (output_dir or (package_root / "out")).resolve()
    fixtures_dir = (fixtures_dir or (package_root / "fixtures")).resolve()

    _validate_node_wire_layout(node_wire_root, connector_id)

    if not skip_build_wheels:
        build_connector_wheels(node_wire_root, connector_id, python=python)
    else:
        _require_wheels(node_wire_root, connector_id)

    fixture_path = ensure_connector_fixture(
        connector_id,
        node_wire_root=node_wire_root,
        fixtures_dir=fixtures_dir,
        force=force_fixture,
    )
    logger.info("Using scope fixture %s", fixture_path)

    # Predict project dir name (server.name = {id with _→-}-nw)
    server_name = connector_id.replace("_", "-") + "-nw"
    project_dir_pre = output_dir / f"{server_name}-mcp"
    if project_dir_pre.exists():
        if force_output:
            shutil.rmtree(project_dir_pre)
        else:
            raise FileExistsError(
                f"Output project already exists: {project_dir_pre}. "
                "Pass --force-output to replace it."
            )

    project_dir = run_connector_pipeline(fixture_path, node_wire_root, output_dir)
    env_example = write_env_example(project_dir, connector_id, node_wire_root)
    logger.info("Wrote %s", env_example)
    return project_dir


def _validate_node_wire_layout(node_wire_root: Path, connector_id: str) -> None:
    if not (node_wire_root / "pyproject.toml").is_file():
        raise FileNotFoundError(f"Not a node-wire root: {node_wire_root}")
    pkg = node_wire_root / "packages" / "connectors" / connector_id
    if not (pkg / "pyproject.toml").is_file():
        raise FileNotFoundError(
            f"Connector package missing: {pkg}. "
            f"Expected packages/connectors/{connector_id}/pyproject.toml"
        )
    logic = node_wire_root / "src" / f"node_wire_{connector_id}" / "logic.py"
    if not logic.is_file():
        raise FileNotFoundError(f"Connector logic.py missing: {logic}")


def build_connector_wheels(
    node_wire_root: Path,
    connector_id: str,
    *,
    python: str | None = None,
) -> tuple[Path, Path]:
    """Build runtime + connector wheels into each package's ``dist/``."""
    runtime_pkg = node_wire_root / "packages" / "runtime"
    connector_pkg = node_wire_root / "packages" / "connectors" / connector_id
    runtime_whl = _build_wheel(runtime_pkg, python=python)
    connector_whl = _build_wheel(connector_pkg, python=python)
    return runtime_whl, connector_whl


def _build_wheel(package_dir: Path, *, python: str | None = None) -> Path:
    dist = package_dir / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    cmd = [
        "uvx",
        "--from",
        "build",
        "pyproject-build",
        "--wheel",
        "-o",
        "dist",
    ]
    env = os.environ.copy()
    if python:
        env["UV_PYTHON"] = python
    logger.info("Building wheel in %s", package_dir)
    try:
        subprocess.run(
            cmd,
            cwd=package_dir,
            check=True,
            env=env,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        py = python or "python"
        subprocess.run(
            [py, "-m", "pip", "install", "build", "cython", "wheel", "setuptools", "-q"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [py, "-m", "build", "--wheel", "--outdir", "dist"],
            cwd=package_dir,
            check=True,
            env=env,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"Wheel build failed in {package_dir}:\n{detail}") from exc

    wheels = sorted(dist.glob("*.whl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not wheels:
        raise FileNotFoundError(f"No .whl produced in {dist}")
    logger.info("Built %s", wheels[0].name)
    return wheels[0]


def _require_wheels(node_wire_root: Path, connector_id: str) -> None:
    runtime_dist = node_wire_root / "packages" / "runtime" / "dist"
    connector_dist = node_wire_root / "packages" / "connectors" / connector_id / "dist"
    if not list(runtime_dist.glob("*.whl")):
        raise FileNotFoundError(
            f"Missing runtime wheel in {runtime_dist} (omit --skip-build-wheels)"
        )
    if not list(connector_dist.glob("*.whl")):
        raise FileNotFoundError(
            f"Missing connector wheel in {connector_dist} (omit --skip-build-wheels)"
        )


def discover_actions(logic_py: Path) -> list[str]:
    """Parse action names from logic.py and sibling modules.

    Covers ``@nw_action`` / ``@sdk_action`` and
    ``SPECS["files.create"] = SdkActionSpec(...)`` (Google Drive-style).
    """
    package_dir = logic_py.parent
    actions: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        name = name.strip()
        # Skip docstring/comment placeholders like @nw_action("...")
        if not name or name == "..." or not re.fullmatch(r"[a-z][a-z0-9_.]*", name):
            return
        if name not in seen:
            seen.add(name)
            actions.append(name)

    for py_path in sorted(package_dir.glob("*.py")):
        text = py_path.read_text(encoding="utf-8")
        for match in _ACTION_RE.finditer(text):
            _add(match.group(1))
        for match in _SPEC_ASSIGN_RE.finditer(text):
            _add(match.group(1))

    if not actions:
        raise ValueError(
            f"No actions found under {package_dir} "
            "(@nw_action/@sdk_action or SdkActionSpec assignments)"
        )
    return actions


def action_to_tool_name(action: str) -> str:
    """Map NW action (``files.create``) to scope tool_name (``files_create``)."""
    name = action.replace(".", "_").replace("-", "_").lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError(f"Cannot map action '{action}' to a valid tool_name")
    if len(name) > 40:
        name = name[:40]
    return name


def ensure_connector_fixture(
    connector_id: str,
    *,
    node_wire_root: Path,
    fixtures_dir: Path,
    force: bool = False,
) -> Path:
    """Write ``fixtures/<connector_id>_nw.yaml`` if missing (or force)."""
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    path = fixtures_dir / f"{connector_id}_nw.yaml"
    if path.is_file() and not force:
        logger.info("Fixture already exists (use --force-fixture to regenerate): %s", path)
        return path

    logic = node_wire_root / "src" / f"node_wire_{connector_id}" / "logic.py"
    actions = discover_actions(logic)
    server_name = connector_id.replace("_", "-") + "-nw"
    if len(server_name) > 63:
        server_name = server_name[:63].rstrip("-")

    pkg_name = connector_dist_package_name(connector_id)
    tools = []
    for action in actions:
        tool_name = action_to_tool_name(action)
        tools.append(
            {
                "tool_name": tool_name,
                "endpoint": f"POST /connectors/{connector_id}/{action}",
                "description": (
                    f"Execute node-wire {connector_id}.{action} "
                    f"(see src/node_wire_{connector_id}/logic.py + schema)."
                ),
                "response_kind": "json",
                "hints": [
                    f"MCP tool at runtime: {connector_id}.{action}",
                    f"Packaged wheel: {pkg_name}",
                ],
            }
        )

    doc = {
        "version": "1",
        "server": {
            "name": server_name,
            "description": (
                f"{connector_id} MCP via node-wire connector "
                f"({pkg_name}; auth/OTel inside node-wire)"
            ),
        },
        "runtime": {
            "type": "node_wire",
            "connector_id": connector_id,
        },
        "spec": {
            "source": f"node-wire/src/node_wire_{connector_id}",
            "format": "openapi3",
            "base_url": "https://unused.invalid",
            "total_endpoints": len(tools),
            "scoped_endpoints": len(tools),
        },
        "workflows": [
            f"MCP tools for node-wire connector `{connector_id}` (actions from logic.py)",
            "Auth and telemetry are handled by the node-wire connector runtime",
        ],
        "groups": [
            {
                "name": "connector",
                "description": (
                    f"Actions from node_wire_{connector_id}.logic (dispatched via McpServer)"
                ),
                "tools": tools,
            }
        ],
        "auth": {
            "type": "none",
            "notes": (
                f"Auth is handled inside node-wire for `{connector_id}`. "
                "Set connector secrets from node-wire sample.env / "
                "config/connectors.yaml (see generated .env.example)."
            ),
        },
    }

    # ponytail: prepend REUSE header so regenerated fixtures stay license-compliant
    # REUSE-IgnoreStart
    header = (
        "##\n"
        "## SPDX-FileCopyrightText: 2026 AOT Technologies\n"
        "## SPDX-License-Identifier: Apache-2.0\n"
        "##\n"
    )
    # REUSE-IgnoreEnd
    body = yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, allow_unicode=True)
    path.write_text(header + body, encoding="utf-8")
    logger.info("Wrote fixture %s (%d tools)", path, len(tools))
    return path


def discover_secret_env_names(connector_id: str, node_wire_root: Path) -> list[str]:
    """Collect env var names for connector secrets from YAML + sample.env."""
    names: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        name = name.strip()
        if name and name not in seen and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            seen.add(name)
            names.append(name)

    connectors_yaml = node_wire_root / "config" / "connectors.yaml"
    if connectors_yaml.is_file():
        raw = yaml.safe_load(connectors_yaml.read_text(encoding="utf-8")) or {}
        cfg = (raw.get("connectors") or {}).get(connector_id) or {}
        for _key, val in cfg.items():
            if isinstance(val, str):
                for m in re.finditer(r"\$\{([A-Z0-9_]+)(?::[^}]*)?\}", val):
                    _add(m.group(1))
        auth = cfg.get("auth") or {}
        if isinstance(auth, dict):
            for key, val in auth.items():
                if key.endswith("_secret") or key in {
                    "secret_key",
                    "sa_json_secret",
                    "username_secret",
                    "password_secret",
                }:
                    if isinstance(val, str):
                        _add(val)

    sample = node_wire_root / "sample.env"
    prefixes = _ENV_PREFIXES.get(connector_id, (connector_id.upper() + "_",))
    if sample.is_file():
        for line in sample.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key = line.split("=", 1)[0].strip()
            if any(key.startswith(p) for p in prefixes):
                _add(key)

    return names


def write_env_example(project_dir: Path, connector_id: str, node_wire_root: Path) -> Path:
    """Write ``.env.example`` next to the generated host (does not overwrite `.env`)."""
    secrets = discover_secret_env_names(connector_id, node_wire_root)
    lines = [
        f"# Example env for {connector_id} MCP host (copy to .env and fill in).",
        "# Generated by: nw-mcp-builder",
        "NW_MCP_AUTH_DISABLED=true",
        "NW_MCP_SCOPE_POLICY_DEFAULT=allow",
        f"NW_ALLOWED_CONNECTORS={connector_id}",
        "",
    ]
    if secrets:
        lines.append(f"# Connector secrets ({connector_id})")
        for name in secrets:
            lines.append(f"{name}=")
    else:
        lines.append(
            f"# No secrets auto-detected; see node-wire sample.env "
            f"and config/connectors.yaml for `{connector_id}`."
        )
    lines.append("")
    path = project_dir / ".env.example"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def format_success_message(project_dir: Path, connector_id: str) -> str:
    module = _guess_module(project_dir)
    return textwrap.dedent(
        f"""\
        Generated connector MCP host for `{connector_id}`:

          {project_dir}

        Next:
          cd {project_dir}
          # copy .env.example to .env and fill secrets
          uv sync --python 3.14
          uv run python -m {module}
        """
    )


def _guess_module(project_dir: Path) -> str:
    src = project_dir / "src"
    if src.is_dir():
        for child in src.iterdir():
            if child.is_dir() and (child / "__main__.py").is_file():
                return child.name
    return project_dir.name.replace("-", "_")
