FROM python:3.12-slim@sha256:3d5ed973e45820f5ba5e46bd065bd88b3a504ff0724d85980dcd05eab361fcf4

LABEL org.opencontainers.image.title="nw-google-drive" \
      org.opencontainers.image.description="Node Wire — Google Drive MCP server" \
      org.opencontainers.image.source="https://github.com/AOT-Technologies/node-wire"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY src/ ./src/
COPY config/ ./config/
COPY packages/runtime/dist/*.whl /wheels/
COPY packages/connectors/google_drive/dist/*.whl /wheels/

ENV PYTHONPATH=/app/src \
    NW_ALLOWED_CONNECTORS=google_drive

RUN pip install --no-cache-dir --find-links=/wheels \
    node-wire-runtime node-wire-google-drive "mcp>=1.6.0" \
    && rm -rf /wheels

RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home /app app \
    && chown -R app:app /app

USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD \
    python -c "from agents.google_drive_mcp import main; assert callable(main); print('ok')" || exit 1

CMD ["python", "-m", "agents.google_drive_mcp"]

