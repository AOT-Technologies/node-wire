#!/usr/bin/env bash
##
## SPDX-FileCopyrightText: 2026 AOT Technologies
## SPDX-License-Identifier: Apache-2.0
##

# build-packages.sh — Build Node Wire packages as pure-Python wheels.
#
#   scripts/build-packages.sh                     # every package in ALL_PACKAGES
#   scripts/build-packages.sh packages/runtime    # just these
#
# Each package builds one py3-none-any wheel into <package>/dist/. It installs on every
# supported Python version and OS, so there is no per-platform build.
#
# Prerequisites: uv (uses `uv build`), or python3 with `pip install build`.
#
# Each wheel is checked to be pure Python (py3-none-any, no compiled .so/.pyd) before "PASS";
# a failed check exits 1.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ALL_PACKAGES=(
  packages/runtime
  packages/bindings
  packages/toolhive
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
  scripts/build-packages.sh [--help] [packages/...]

  Builds a pure-Python (py3-none-any) wheel for each package path, or for every
  package in ALL_PACKAGES when none is given. Wheels are written to <package>/dist/.

Examples:
  scripts/build-packages.sh
  scripts/build-packages.sh packages/connectors/smtp
  scripts/build-packages.sh packages/runtime packages/bindings
USAGE
}

PACKAGES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      usage
      exit 0
      ;;
    --host-only|--linux-only|--all|--musllinux|--cibw-linux)
      # Platform modes from the compiled-wheel era; every wheel is now platform-independent.
      echo "NOTE: $1 is no longer needed and is ignored (wheels are pure Python)." >&2
      shift
      ;;
    -*)
      echo "ERROR: unknown option: $1" >&2
      usage >&2
      exit 1
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

if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "ERROR: python3 or python is required but neither was found in PATH." >&2
  exit 1
fi

if command -v uv >/dev/null 2>&1; then
  BUILD=(uv build --wheel --out-dir dist)
elif "$PYTHON" -c "import build" >/dev/null 2>&1; then
  BUILD=("$PYTHON" -m build --wheel --outdir dist)
else
  echo "ERROR: install uv, or run: $PYTHON -m pip install build" >&2
  exit 1
fi

# Fail unless the wheel is py3-none-any and holds no compiled extension modules.
verify_pure_wheel() {
  "$PYTHON" - "$1" <<'PYCHECK'
import sys
import zipfile

wheel_path = sys.argv[1]
if not wheel_path.endswith("-py3-none-any.whl"):
    sys.exit(f"not a py3-none-any wheel: {wheel_path}")
with zipfile.ZipFile(wheel_path) as zf:
    names = zf.namelist()
compiled = [n for n in names if n.endswith((".so", ".pyd", ".c"))]
if compiled:
    sys.exit("compiled files in the wheel:\n" + "\n".join(compiled))
if not any(n.endswith(".py") for n in names):
    sys.exit("no .py modules in the wheel")
PYCHECK
}

echo "=== Node Wire — building ${#PACKAGES[@]} package(s) ==="

FAILED=()
for PKG in "${PACKAGES[@]}"; do
  echo ""
  echo "--- Building: $PKG ---"

  if [[ ! -f "$PKG/pyproject.toml" ]]; then
    echo "ERROR: Not a package (no pyproject.toml): $PKG" >&2
    FAILED+=("$PKG (missing pyproject.toml)")
    continue
  fi

  # build/ can hold compiled modules from older builds, which setuptools would copy into
  # the wheel; old wheels in dist/ would be picked up by the MCP generators.
  rm -rf "$PKG/build"
  mkdir -p "$PKG/dist"
  rm -f "$PKG"/dist/*.whl

  if ! (cd "$PKG" && "${BUILD[@]}"); then
    echo "ERROR: wheel build failed for $PKG" >&2
    FAILED+=("$PKG (build failed)")
    continue
  fi

  shopt -s nullglob
  WHEELS=("$PKG"/dist/*.whl)
  shopt -u nullglob
  if [[ ${#WHEELS[@]} -ne 1 ]]; then
    echo "ERROR: expected one wheel in $PKG/dist, found ${#WHEELS[@]}" >&2
    FAILED+=("$PKG (no wheel)")
    continue
  fi

  if ! verify_pure_wheel "${WHEELS[0]}"; then
    echo "ERROR: ${WHEELS[0]} is not a pure-Python wheel" >&2
    FAILED+=("$PKG (not pure Python)")
    continue
  fi

  echo "PASS: ${WHEELS[0]}"
done

echo ""
if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo "=== FAILED packages ==="
  for F in "${FAILED[@]}"; do echo "  - $F"; done
  exit 1
fi

echo "=== All packages built and verified ==="
