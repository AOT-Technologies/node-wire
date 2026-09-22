#!/usr/bin/env bash
##
## SPDX-FileCopyrightText: 2026 AOT Technologies
## SPDX-License-Identifier: Apache-2.0
##

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PROTO_DIR="src/bindings/grpc_server"
PROTO_FILE="${PROTO_DIR}/connector.proto"

# grpcio-tools is the optional ``grpc-codegen`` extra — not a runtime dependency.
if command -v uv >/dev/null 2>&1; then
  PYTHON=(uv run --extra grpc-codegen python)
else
  PYTHON=(python3)
  if ! "${PYTHON[@]}" -c "import grpc_tools" 2>/dev/null; then
    echo "ERROR: grpcio-tools is required to regenerate gRPC stubs." >&2
    echo "  With uv:  uv sync --extra grpc-codegen" >&2
    echo "  With pip: pip install -e '.[grpc-codegen]'" >&2
    exit 1
  fi
fi

"${PYTHON[@]}" -m grpc_tools.protoc \
  -I "${PROTO_DIR}" \
  --python_out="${PROTO_DIR}" \
  --grpc_python_out="${PROTO_DIR}" \
  "${PROTO_FILE}"

# Ensure package-relative import in generated servicer module.
if grep -q '^import connector_pb2 as connector__pb2' "${PROTO_DIR}/connector_pb2_grpc.py"; then
  sed -i.bak 's/^import connector_pb2 as connector__pb2/from . import connector_pb2 as connector__pb2/' \
    "${PROTO_DIR}/connector_pb2_grpc.py"
  rm -f "${PROTO_DIR}/connector_pb2_grpc.py.bak"
fi

echo "PASS: gRPC stubs generated in ${PROTO_DIR}"
# Generated *_pb2*.py files are excluded from Ruff and Mypy in pyproject.toml.
