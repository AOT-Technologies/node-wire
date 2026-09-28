"""Post-scaffold patches to the generated project.

Pipeline stage: patching (source text + ServerPlan -> patched source text).

The scaffold step copies the template and renames the module, but leaves
placeholder tool registrations (``mcp.add_tool(tools.hello)``) and a
placeholder FastMCP server name. This module rewrites those placeholders
with the actual generated tools and server identity, in place.

Runs after ``scaffold.scaffold_project`` and after the pure renderers have
written their files; nothing in this module runs during pure rendering.
"""

from __future__ import annotations

import re

import structlog

from mcp_builder.generate.plan import ServerPlan

logger = structlog.get_logger()


def patch_mcp_builder(source: str, plan: ServerPlan) -> str:
    """Patch the scaffolded mcp_builder.py to wire generated tools.

    Pipeline stage: patching (post-scaffold source -> patched source).

    Performs four targeted replacements on the post-scaffold source:
    1. Adds ``APIClient`` import before the ``Settings`` import.
    2. Fixes the FastMCP server name from the template placeholder.
    3. Replaces ``Tools()`` with ``Tools(APIClient())`` to inject the client.
    4. Replaces the template tool registration with one per generated tool.

    Args:
        source: The scaffolded mcp_builder.py content (already has the
                correct module name from the scaffold step).
        plan: The server plan containing tool definitions.

    Returns:
        Patched source text.
    """
    logger.info("patching mcp_builder.py", tool_count=len(plan.tools))

    # 1. Add APIClient import before Settings import.
    # Source: mcp-template-py src/mcp_template_py/api/mcp_builder.py (copied at scaffold.py:46)
    settings_import = f"from {plan.module_name}.settings import Settings"
    client_import = f"from {plan.module_name}.client import APIClient\n"
    if settings_import not in source:
        logger.warning(
            "patch 1/4: settings import not found, APIClient import may be missing",
            expected=settings_import,
        )
    source = source.replace(settings_import, client_import + settings_import)
    logger.debug("patch 1/4: added APIClient import")

    # 2. Fix FastMCP server name from template placeholder.
    # Source: mcp-template-py src/mcp_template_py/api/mcp_builder.py (copied at scaffold.py:46)
    if not re.search(r'FastMCP\("[^"]*"', source):
        logger.warning("patch 2/4: FastMCP() call not found in source")
    source = re.sub(r'FastMCP\("[^"]*"', f'FastMCP("{plan.server_name}"', source)
    logger.debug("patch 2/4: set FastMCP server name", server_name=plan.server_name)

    # 3. Inject client into Tools constructor.
    # Source: mcp-template-py src/mcp_template_py/api/mcp_builder.py (copied at scaffold.py:46)
    if "tools = Tools()" not in source:
        logger.warning("patch 3/4: 'Tools()' not found, client injection may fail")
    source = source.replace("tools = Tools()", "tools = Tools(APIClient())")
    logger.debug("patch 3/4: injected APIClient into Tools constructor")

    # 4. Replace template tool registrations with generated ones.
    # Source: mcp-template-py src/mcp_template_py/api/mcp_builder.py (copied at scaffold.py:46)
    source = re.sub(r"^ *mcp\.add_tool\(tools\.\w+\)\n", "", source, flags=re.MULTILINE)
    registrations = "".join(
        f"        mcp.add_tool(tools.{t.tool_name})\n" for t in plan.tools
    )
    if "        return mcp" not in source:
        logger.warning("patch 4/4: 'return mcp' not found, tool registration may fail")
    source = source.replace(
        "        return mcp", registrations + "\n        return mcp"
    )
    logger.debug(
        "patch 4/4: registered tools",
        tools=[t.tool_name for t in plan.tools],
    )

    logger.debug("patching complete", chars=len(source))
    return source


def patch_app_builder(source: str, plan: ServerPlan) -> str:
    """Patch the scaffolded app_builder.py.

    Pipeline stage: patching (post-scaffold source -> patched source).

    Currently a no-op. The scaffold step already handles module name
    replacement, and app_builder.py contains only framework boilerplate
    with no server-specific content that needs modification.

    Args:
        source: The scaffolded app_builder.py content.
        plan: The server plan (unused, kept for interface consistency).

    Returns:
        Source text unchanged.
    """
    logger.debug("patch_app_builder called (no-op)")
    return source
