#!/usr/bin/env bash
# build-packages.sh — Build Node Wire packages as binary-only wheels.
#
# Default mode (host + Linux via Docker):
#   scripts/build-packages.sh
#   scripts/build-packages.sh packages/runtime
#
# All-platform mode (local cibuildwheel; see notes below):
#   scripts/build-packages.sh --all
#   scripts/build-packages.sh --all packages/runtime
#
# Prerequisites (default mode):
#   python3 or python on PATH; pip install build cython wheel
#   docker (for Linux wheels)
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

ALL_PACKAGES=(
  packages/runtime
  packages/connectors/google_drive
  packages/connectors/fhir_epic
  packages/connectors/fhir_cerner
  packages/connectors/smtp
  packages/connectors/stripe
  packages/connectors/salesforce
  packages/connectors/http_generic
)


usage() {
  cat <<'USAGE'
Usage:
  scripts/build-packages.sh [--help]
  scripts/build-packages.sh [packages/...]
  scripts/build-packages.sh --all [packages/...]

  Default: build each package on the host and again in Docker (Linux wheels).
  --all:    build with cibuildwheel (targets depend on host; for full OS matrix use CI publish.yml).

Examples:
  scripts/build-packages.sh
  scripts/build-packages.sh packages/connectors/smtp
  scripts/build-packages.sh --all
  scripts/build-packages.sh --all packages/runtime
USAGE
}

ALL_MODE=0
PACKAGES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)
      ALL_MODE=1
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

# ─── Default mode (host + Linux Docker) ───────────────────────────────────
echo "=== Node Wire — building ${#PACKAGES[@]} package(s) (host + linux) ==="

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

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is required to build Linux wheels but was not found in PATH." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker daemon is not running. Start Docker and retry." >&2
  exit 1
fi

FAILED=()

for PKG in "${PACKAGES[@]}"; do
  echo ""
  echo "--- Building: $PKG ---"

  (
    cd "$PKG"
    "$PYTHON_HOST" -m build --wheel --no-isolation
  )

  docker run --rm \
    -v "$ROOT_DIR:/work" \
    -w "/work/$PKG" \
    python:3.12-slim \
    bash -lc "apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/* && python -m pip install --no-cache-dir setuptools build cython wheel && python -m build --wheel --no-isolation" || {
      echo "ERROR: Linux wheel build failed for $PKG" >&2
      FAILED+=("$PKG (linux build failed)")
      continue
    }

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
