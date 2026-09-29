"""End-to-end orchestration for ``mcp-builder generate``.

Pipeline stage: orchestration (the top of the generate subcommand).

This is the only place that combines filesystem I/O (scaffolding, writing
files, patching templates) with the pure ``generate`` package. The
``generate/`` subpackage stays side-effect-free; this module owns the I/O
glue. The CLI layer (``mcp_builder.cli``) is kept to arg parsing and
exit-code handling.

Entry point for: ``mcp-builder generate`` and the e2e / integration tests
that drive a full server generation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from mcp_builder.generate.patches import patch_app_builder, patch_mcp_builder
from mcp_builder.generate.plan import ServerPlan, build_server_plan
from mcp_builder.generate.renderers.client import render_client_module
from mcp_builder.generate.renderers.manifests import render_manifests
from mcp_builder.generate.renderers.tools import render_tools_module
from mcp_builder.generate.scaffold import scaffold_project
from mcp_builder.schema.models import load_scope
from mcp_builder.spec import load_openapi_spec

# node-wire: optional node-wire runtime mode (nw-stacklok-builder/UPSTREAM.md).
from nw_stacklok import hooks as node_wire_hooks
from nw_stacklok.hooks import NodeWireOptions

logger = logging.getLogger(__name__)


def run_pipeline(
    scope_yaml: Path,
    openapi_spec: Path,
    template_dir: Path,
    output_dir: Path,
    node_wire: NodeWireOptions | None = None,
) -> Path:
    """Run the full MCP server generation pipeline.

    Pipeline stage: orchestration (this is the top-level function).

    Steps:
        1. Load and validate the mcp-scope.yaml and OpenAPI spec.
        2. Build the typed ServerPlan from scope + spec.
        3. Scaffold the project from the template directory.
        4. Render and write generated source files (client, tools).
        5. Patch scaffolded server wiring files.
        6. Write deployment manifests to deploy/.

    Args:
        scope_yaml: Path to mcp-scope.yaml.
        openapi_spec: Path to the OpenAPI spec (YAML or JSON).
        template_dir: Path to the mcp-template-py checkout.
        output_dir: Directory where the generated project will be created.
        node_wire: node-wire mode options; required when the scope has a
            ``runtime: node_wire`` block (tools then run on that connector).

    Returns:
        Path to the generated project directory.
    """
    logger.info("Loading scope from %s", scope_yaml)
    scope = load_scope(scope_yaml)

    logger.info("Loading OpenAPI spec from %s", openapi_spec)
    spec = load_openapi_spec(openapi_spec)

    logger.info("Building server plan for '%s'", scope.server.name)
    plan = build_server_plan(scope, spec)
    plan = node_wire_hooks.attach(plan, scope, node_wire)

    logger.info("Scaffolding project into %s", output_dir)
    project_dir = scaffold_project(plan, template_dir, output_dir)
    module_dir = project_dir / "src" / plan.module_name

    # Render and write generated source files
    _write_file(
        module_dir / "client.py",
        node_wire_hooks.client_module(plan) if plan.node_wire else render_client_module(plan),
    )
    _write_file(module_dir / "api" / "tools.py", render_tools_module(plan))

    # The template ships a sample api/models.py (HelloRequest/HelloResponse)
    # that the generated tools.py doesn't import — generated tool methods take
    # flattened Annotated args so FastMCP can expose a per-param input schema.
    # Leaving the file behind ships dead code to generated projects.
    (module_dir / "api" / "models.py").unlink(missing_ok=True)

    # Patch scaffolded wiring files
    _patch_file(module_dir / "api" / "mcp_builder.py", patch_mcp_builder, plan)
    _patch_file(module_dir / "api" / "app_builder.py", patch_app_builder, plan)
    if plan.node_wire is not None and node_wire is not None:
        node_wire_hooks.finish(project_dir, plan, node_wire)

    # Write deployment manifests
    deploy_dir = project_dir / "deploy"
    deploy_dir.mkdir(exist_ok=True)
    manifests = node_wire_hooks.manifests(plan) if plan.node_wire else render_manifests(plan)
    for filename, content in manifests.items():
        _write_file(deploy_dir / filename, content)

    logger.info("Generated project at %s", project_dir)
    return project_dir


def _write_file(path: Path, content: str) -> None:
    """Write content to a file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.debug("Wrote %s", path)


def _patch_file(
    path: Path,
    patch_fn: Callable[[str, ServerPlan], str],
    plan: ServerPlan,
) -> None:
    """Read a file, apply a patch function, and write the result back."""
    source = path.read_text(encoding="utf-8")
    patched = patch_fn(source, plan)
    path.write_text(patched, encoding="utf-8")
    logger.debug("Patched %s", path)
