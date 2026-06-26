#!/usr/bin/env bash
##
## SPDX-FileCopyrightText: 2026 AOT Technologies
## SPDX-License-Identifier: Apache-2.0
##
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

VERSION=""

usage() {
  cat <<'EOF'
Usage: scripts/build-mcp-images.sh [--version X.Y.Z]

Builds all per-connector MCP server images with consistent naming and tagging.

Images:
  - nw-google-drive
  - nw-smartonfhir-epic
  - nw-smartonfhir-cerner
  - nw-smtp
  - nw-stripe
  - nw-salesforce
  - nw-slack
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      VERSION="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${VERSION}" ]]; then
  VERSION="$(python3 -c "import tomllib, pathlib; p=pathlib.Path('${ROOT_DIR}')/'pyproject.toml'; print(tomllib.loads(p.read_text())['project']['version'])")"
fi

echo "Building MCP images (version=${VERSION}) from ${ROOT_DIR}"

cd "${ROOT_DIR}"

docker build -f docker/google-drive/Dockerfile \
  -t nw-google-drive:latest \
  -t "nw-google-drive:${VERSION}" \
  .

docker build -f docker/fhir-epic/Dockerfile \
  -t nw-smartonfhir-epic:latest \
  -t "nw-smartonfhir-epic:${VERSION}" \
  .

docker build -f docker/fhir-cerner/Dockerfile \
  -t nw-smartonfhir-cerner:latest \
  -t "nw-smartonfhir-cerner:${VERSION}" \
  .

docker build -f docker/smtp/Dockerfile \
  -t nw-smtp:latest \
  -t "nw-smtp:${VERSION}" \
  .

docker build -f docker/stripe/Dockerfile \
  -t nw-stripe:latest \
  -t "nw-stripe:${VERSION}" \
  .

docker build -f docker/salesforce/Dockerfile \
  -t nw-salesforce:latest \
  -t "nw-salesforce:${VERSION}" \
  .


docker build -f docker/slack/Dockerfile \
  -t nw-slack:latest \
  -t "nw-slack:${VERSION}" \
  .

echo "Done."
