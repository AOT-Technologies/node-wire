FROM python:3.12-slim

LABEL org.opencontainers.image.title="nw-smartonfhir-cerner" \
      org.opencontainers.image.description="Node Wire — SMART on FHIR Cerner MCP server" \
      org.opencontainers.image.source="https://github.com/AOT-Technologies/connector-platform"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/
COPY config/ ./config/

RUN pip install --no-cache-dir -e ".[agents]"

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD \
    python -c "from agents.fhir_cerner_mcp import _make_server; print('ok')" || exit 1

CMD ["python", "-m", "agents.fhir_cerner_mcp"]

