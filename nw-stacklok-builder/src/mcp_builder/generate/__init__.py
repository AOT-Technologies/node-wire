"""Code generation: turn a scope + OpenAPI spec into an MCP server project.

This package is the engine behind ``mcp-builder generate``. It is pure —
no CLI argument parsing, no top-level I/O orchestration. The CLI entry point
lives in ``mcp_builder.cli``; the orchestration wrapper lives in
``mcp_builder.pipeline``. Peer domain operations (``validate`` and
``analyze``) live as sibling modules under ``mcp_builder``, not here.

Reading order:
    1. plan — builds a typed ServerPlan (scope + spec → ServerPlan)
    2. renderers/ — each renderer takes a ServerPlan and emits one file

OpenAPI parsing lives in ``mcp_builder.spec``, not here. The distinction
between ``mcp_builder.spec`` (strict, used here) and ``mcp_builder.analyze``
(tolerant, used by the ``analyze`` CLI) is documented in those modules.
"""
