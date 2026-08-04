#!/usr/bin/env bash
##
## SPDX-FileCopyrightText: 2026 AOT Technologies
## SPDX-License-Identifier: Apache-2.0
##

# build-packages.sh — Build Node Wire packages as binary-only wheels.
#
# Default mode (host + Linux via local Docker builder image):
#   scripts/build-packages.sh
#   scripts/build-packages.sh packages/runtime
#
# Host-only / Linux-only:
#   scripts/build-packages.sh --host-only
#   scripts/build-packages.sh --linux-only packages/runtime
#
# All-platform mode (local cibuildwheel; see notes below):
#   scripts/build-packages.sh --all
#   scripts/build-packages.sh --all packages/runtime
#
# Prerequisites (default / --linux-only):
#   python3 or python on PATH; pip install build cython wheel (host build)
#   docker (for Linux wheels); builds local image nw-wheel-builder:local
#
# Prerequisites (--host-only):
#   python3 or python on PATH; pip install build cython wheel
#
# Prerequisites (--all mode):
#   python -m pip install 'cibuildwheel>=2.16.0'
#
# Security guarantee:
#   Each wheel is verified to contain zero .py source files before printing "PASS".
#   Any leaked .py files trigger an exit 1.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

WHEEL_BUILDER_IMAGE="nw-wheel-builder:local"
WHEEL_BUILDER_CONTEXT="$ROOT_DIR/docker/wheel-builder"

ALL_PACKAGES=(
  packages/runtime
  packages/connectors/google_drive
  packages/connectors/fhir_epic
  packages/connectors/fhir_cerner
  packages/connectors/smtp
  packages/connectors/stripe
  packages/connectors/salesforce
  packages/connectors/http_generic
  packages/connectors/slack
)


usage() {
  cat <<'USAGE'
Usage:
  scripts/build-packages.sh [--help]
  scripts/build-packages.sh [--host-only|--linux-only] [packages/...]
  scripts/build-packages.sh --all [packages/...]

  Default:     build each package on the host and again in Docker (Linux wheels).
  --host-only: build host wheels only (no Docker).
  --linux-only: build Linux wheels only (via local nw-wheel-builder image).
  --all:       build with cibuildwheel (targets depend on host; for full OS matrix use CI publish.yml).

  --host-only and --linux-only cannot be combined with each other or with --all.

  Linux builds use a local Docker image (nw-wheel-builder:local) built from
  docker/wheel-builder/Dockerfile. It is never pushed to a registry; Docker
  layer cache makes subsequent builds fast when the Dockerfile is unchanged.

Examples:
  scripts/build-packages.sh
  scripts/build-packages.sh packages/connectors/smtp
  scripts/build-packages.sh --host-only packages/connectors/smtp
  scripts/build-packages.sh --linux-only packages/runtime
  scripts/build-packages.sh --all
  scripts/build-packages.sh --all packages/runtime
USAGE
}

ALL_MODE=0
HOST_ONLY=0
LINUX_ONLY=0
PACKAGES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)
      ALL_MODE=1
      shift
      ;;
    --host-only)
      HOST_ONLY=1
      shift
      ;;
    --linux-only)
      LINUX_ONLY=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      PACKAGES+=("$1")
      shift
      ;;
  esac
done

if [[ "$HOST_ONLY" -eq 1 && "$LINUX_ONLY" -eq 1 ]]; then
  echo "ERROR: --host-only and --linux-only cannot be combined." >&2
  exit 1
fi

if [[ "$ALL_MODE" -eq 1 && ( "$HOST_ONLY" -eq 1 || "$LINUX_ONLY" -eq 1 ) ]]; then
  echo "ERROR: --host-only and --linux-only cannot be combined with --all." >&2
  exit 1
fi

if [[ ${#PACKAGES[@]} -eq 0 ]]; then
  PACKAGES=("${ALL_PACKAGES[@]}")
fi

# Verify wheels contain no .py files (binary-only wheels). First arg: python binary.
verify_wheels_no_py() {
  local py="$1"
  shift
  local -a wheels=("$@")
  local whl
  local py_leak
  local pkg_failed=0

  for whl in "${wheels[@]}"; do
    py_leak=$("$py" - "$whl" <<'PYCHECK'
import sys
import zipfile

wheel_path = sys.argv[1]
with zipfile.ZipFile(wheel_path) as zf:
    leaked = [name for name in zf.namelist() if name.endswith(".py")]

if leaked:
    print("\n".join(leaked))
    sys.exit(1)
PYCHECK
    2>&1) || {
      echo "SECURITY FAIL: .py files leaked into $whl:" >&2
      echo "$py_leak" >&2
      pkg_failed=1
      break
    }
  done
  return "$pkg_failed"
}

# ─── All-platform mode (cibuildwheel) ───────────────────────────────────────
if [[ "$ALL_MODE" -eq 1 ]]; then
  export CIBW_BUILD="${CIBW_BUILD:-cp311-* cp312-*}"
  export CIBW_SKIP="${CIBW_SKIP:-*-win32 *-manylinux_i686 pp*}"

  echo "=== Node Wire — cibuildwheel build for ${#PACKAGES[@]} package(s) ==="
  echo "CIBW_BUILD=$CIBW_BUILD"
  echo "CIBW_SKIP=$CIBW_SKIP"

  if command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
  elif command -v python >/dev/null 2>&1; then
    PYTHON=python
  else
    echo "ERROR: python or python3 is required but not found in PATH." >&2
    exit 1
  fi

  if ! "$PYTHON" -c "import cibuildwheel" >/dev/null 2>&1; then
    echo "ERROR: cibuildwheel is not installed in the current Python environment." >&2
    echo "Install with: $PYTHON -m pip install --upgrade 'cibuildwheel>=2.16.0'" >&2
    exit 1
  fi

  shopt -s nullglob
  FAILED=()

  for PKG in "${PACKAGES[@]}"; do
    echo ""
    echo "--- Building: $PKG ---"

    if [[ ! -d "$PKG" ]]; then
      echo "ERROR: Package path not found: $PKG" >&2
      FAILED+=("$PKG (missing path)")
      continue
    fi

    if [[ ! -f "$PKG/pyproject.toml" ]]; then
      echo "ERROR: Missing pyproject.toml in $PKG" >&2
      FAILED+=("$PKG (missing pyproject.toml)")
      continue
    fi

    mkdir -p "$PKG/dist"
    rm -f "$PKG"/dist/*.whl

    if ! (
      cd "$PKG"
      "$PYTHON" -m cibuildwheel --output-dir dist
    ); then
      echo "ERROR: cibuildwheel build failed for $PKG" >&2
      FAILED+=("$PKG (build failed)")
      continue
    fi

    WHEELS=("$PKG"/dist/*.whl)
    if [[ ${#WHEELS[@]} -eq 0 ]]; then
      echo "ERROR: No wheels produced for $PKG" >&2
      FAILED+=("$PKG (no wheels)")
      continue
    fi

    if ! verify_wheels_no_py "$PYTHON" "${WHEELS[@]}"; then
      FAILED+=("$PKG (.py leak)")
      continue
    fi

    echo "PASS: ${#WHEELS[@]} wheel(s) for $PKG — no .py source files"
  done

  echo ""
  if [[ ${#FAILED[@]} -gt 0 ]]; then
    echo "=== FAILED packages ==="
    for F in "${FAILED[@]}"; do echo "  - $F"; done
    exit 1
  fi

  echo "=== All packages built and verified successfully ==="
  echo ""
  echo "Wheels are in:"
  for PKG in "${PACKAGES[@]}"; do
    ls "$PKG"/dist/*.whl 2>/dev/null || true
  done
  exit 0
fi

# ─── Default mode (host and/or Linux Docker) ───────────────────────────────
BUILD_HOST=1
BUILD_LINUX=1
if [[ "$HOST_ONLY" -eq 1 ]]; then
  BUILD_LINUX=0
elif [[ "$LINUX_ONLY" -eq 1 ]]; then
  BUILD_HOST=0
fi

MODE_LABEL="host + linux"
if [[ "$BUILD_HOST" -eq 1 && "$BUILD_LINUX" -eq 0 ]]; then
  MODE_LABEL="host-only"
elif [[ "$BUILD_HOST" -eq 0 && "$BUILD_LINUX" -eq 1 ]]; then
  MODE_LABEL="linux-only"
fi

echo "=== Node Wire — building ${#PACKAGES[@]} package(s) ($MODE_LABEL) ==="

FAILED=()

if command -v python3 >/dev/null 2>&1; then
  PYTHON_HOST=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON_HOST=python
else
  echo "ERROR: python3 or python is required on the host to build wheels but neither was found in PATH." >&2
  exit 1
fi

# Validate paths first so typos fail without Docker installed or running.
for PKG in "${PACKAGES[@]}"; do
  if [[ ! -d "$PKG" ]]; then
    echo "ERROR: Package path not found: $PKG" >&2
    FAILED+=("$PKG (missing path)")
    continue
  fi
  if [[ ! -f "$PKG/pyproject.toml" ]]; then
    echo "ERROR: Missing pyproject.toml in $PKG" >&2
    FAILED+=("$PKG (missing pyproject.toml)")
    continue
  fi
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo ""
  echo "=== FAILED packages ==="
  for F in "${FAILED[@]}"; do echo "  - $F"; done
  exit 1
fi

if [[ "$BUILD_LINUX" -eq 1 ]]; then
  if [[ ! -f "$WHEEL_BUILDER_CONTEXT/Dockerfile" ]]; then
    echo "ERROR: Wheel builder Dockerfile not found: $WHEEL_BUILDER_CONTEXT/Dockerfile" >&2
    exit 1
  fi

  if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker is required to build Linux wheels but was not found in PATH." >&2
    exit 1
  fi

  if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker daemon is not running. Start Docker and retry." >&2
    exit 1
  fi

  echo ""
  echo "--- Ensuring local wheel builder image ($WHEEL_BUILDER_IMAGE) ---"
  docker build -t "$WHEEL_BUILDER_IMAGE" "$WHEEL_BUILDER_CONTEXT"
fi

FAILED=()

for PKG in "${PACKAGES[@]}"; do
  echo ""
  echo "--- Building: $PKG ---"

  if [[ "$BUILD_HOST" -eq 1 ]]; then
    (
      cd "$PKG"
      "$PYTHON_HOST" -m build --wheel --no-isolation
    )
  fi

  if [[ "$BUILD_LINUX" -eq 1 ]]; then
    docker run --rm \
      -v "$ROOT_DIR:/work" \
      -w "/work/$PKG" \
      "$WHEEL_BUILDER_IMAGE" \
      python -m build --wheel --no-isolation || {
        echo "ERROR: Linux wheel build failed for $PKG" >&2
        FAILED+=("$PKG (linux build failed)")
        continue
      }
  fi

  shopt -s nullglob
  WHEELS=("$PKG"/dist/*.whl)
  shopt -u nullglob
  if [[ ${#WHEELS[@]} -eq 0 ]]; then
    echo "ERROR: No wheels produced for $PKG" >&2
    FAILED+=("$PKG (no wheels)")
    continue
  fi

  if ! verify_wheels_no_py "$PYTHON_HOST" "${WHEELS[@]}"; then
    FAILED+=("$PKG (.py leak)")
    continue
  fi

  echo "PASS: ${#WHEELS[@]} wheel(s) for $PKG — no .py source files"
done

echo ""
if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo "=== FAILED packages ==="
  for F in "${FAILED[@]}"; do echo "  - $F"; done
  exit 1
fi

echo "=== All packages built and verified successfully ==="
echo ""
echo "Wheels are in:"
for PKG in "${PACKAGES[@]}"; do
  ls "$PKG/dist/"*.whl 2>/dev/null || true
done
