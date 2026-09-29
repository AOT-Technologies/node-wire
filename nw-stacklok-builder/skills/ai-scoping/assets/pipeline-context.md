# mcp-builder Pipeline Context

This document provides shared context for AI agents working on the mcp-builder pipeline. Read this before starting your task.

## What is mcp-builder?

mcp-builder is a pipeline that transforms an OpenAPI 3.x spec and plain-English workflow descriptions into a production-quality, ToolHive-ready MCP server. It has four phases:

1. **Phase 1 — AI Scoping**: Parse the spec, semantically group endpoints, assign LLM-optimized tool names and descriptions, detect auth, and emit a validated `mcp-scope.yaml` + `scoping-summary.md`.
2. **Phase 2 — Human Review**: A human edits the YAML and summary, fixing any issues flagged in Phase 1.
3. **Phase 3 — Deterministic Code Generation**: Reads the YAML + OpenAPI spec and scaffolds a complete MCP server. No AI involved — this is a pure function of the config.
4. **Phase 4 — AI Validation**: Reviews the generated code for correctness, makes improvements guided by hints in the config, and verifies the build succeeds.

## What is mcp-scope.yaml?

`mcp-scope.yaml` is the contract between the AI/human world (Phases 1-2) and the deterministic code generator (Phase 3). It defines:

- **Server identity**: name (DNS label), description
- **Spec metadata**: source URL/path, format (OpenAPI 3.0 or 3.1), base URL, endpoint counts
- **Workflows**: plain-English descriptions of what users do with this API (minimum 3)
- **Groups**: semantic clusters of related tools (e.g., "file-operations", "comments")
- **Tools**: individual API endpoints with LLM-optimized names, descriptions, parameter overrides, and hints
- **Auth**: authentication type (oauth2, oidc, api_key, none) with OAuth details if applicable

The formal JSON schema is at `docs/mcp-scope-schema.json` (auto-generated from the Pydantic models via `uv run mcp-builder-schema`).

### Key constraints enforced by the schema

- Server name must be a valid DNS label: `[a-z0-9]([a-z0-9-]*[a-z0-9])?`, max 63 chars
- Tool names must be snake_case: `[a-z][a-z0-9_]*`, max 40 chars
- Tool names must be globally unique across all groups
- Each group must have at least one tool
- Endpoint format: `METHOD /path` (e.g., `GET /files/{fileId}`)
- Endpoint paths must match the spec's `paths` exactly — do not prepend the base URL path
- When auth type is `oauth2`, `authorization_url` and `token_url` are required; when `oidc`, `issuer` is required

### Parameter semantics

A tool's `parameters` list is an **allowlist**: only the listed parameters are included in the generated MCP tool. Parameters present in the OpenAPI spec but absent from the YAML list are excluded from code generation.

- Every `{placeholder}` in the endpoint path **must** have a corresponding parameter with `location: path`. This is validated at load time — missing path params cause an error.
- Each parameter must include a `location` field (`path`, `query`, or `body`) indicating where it belongs in the HTTP request.
- The `description` and `required` fields in the YAML override the spec values for matching parameters.

This means the endpoint-scoper agent should actively curate parameters — including only what's useful for the described workflows and excluding noise.

