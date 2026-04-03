# Node Wire — Docker Image
# ========================
# This image packages the connector platform as an MCP stdio server (manifest-driven).
# ToolHive runs it as a container, injects secrets as env vars,
# and proxies the stdio MCP transport to HTTP/SSE.
#
# Build:
#   docker build -t node-wire:latest .
#
# ToolHive registration (see docs/toolhive_agent_scenario.md for full command):
#   thv run --name node-wire-connectors --transport stdio \
#     --secret ... node-wire:latest

# Digest-pinned base (update when bumping tag). See .github/workflows/docker-policy.yml.
FROM python:3.12-slim@sha256:3d5ed973e45820f5ba5e46bd065bd88b3a504ff0724d85980dcd05eab361fcf4

# Install system deps needed by some connector libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy source (build context = repo root)
COPY pyproject.toml ./
COPY src/ ./src/
COPY config/ ./config/

# Install platform + agents extras
RUN pip install --no-cache-dir -e ".[agents]"

RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home /app app \
    && chown -R app:app /app

USER app

# Expose nothing — ToolHive manages the stdio proxy port internally
# MCP_PORT / FASTMCP_PORT will be set by ToolHive if ever needed

# Healthcheck: verify the package is importable
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD \
    python -c "from agents.mcp_entrypoint import main; assert callable(main); print('ok')" || exit 1

# Default entrypoint: run the MCP server on stdio
CMD ["python", "-m", "agents.mcp_entrypoint"]
