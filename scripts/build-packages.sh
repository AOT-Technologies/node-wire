#!/usr/bin/env bash
# build-packages.sh — Build Node Wire packages as binary-only wheels (host + Linux).
#
# Usage:
#   scripts/build-packages.sh                  # build all packages (host + linux)
#   scripts/build-packages.sh packages/runtime # build one package (host + linux)
#
# Prerequisites:
#   pip install build cython wheel
#   docker (for Linux wheels)
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
  packages/connectors/http_generic
)

# If a specific package path is given, build only that one.
if [[ $# -gt 0 ]]; then
  PACKAGES=("$@")
else
  PACKAGES=("${ALL_PACKAGES[@]}")
fi

echo "=== Node Wire — building ${#PACKAGES[@]} package(s) (host + linux) ==="

FAILED=()

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is required to build Linux wheels but was not found in PATH." >&2
  exit 1
fi

# Fail early if Docker daemon is unavailable.
if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker daemon is not running. Start Docker and retry." >&2
  exit 1
fi

for PKG in "${PACKAGES[@]}"; do
  echo ""
  echo "--- Building: $PKG ---"
  (
    cd "$PKG"
    python -m build --wheel --no-isolation
  )

  # Build Linux wheel(s) in a Linux container so tags are Docker-compatible.
  docker run --rm \
    -v "$ROOT_DIR:/work" \
    -w "/work/$PKG" \
    python:3.12-slim \
    bash -lc "apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/* && python -m pip install --no-cache-dir setuptools build cython wheel && python -m build --wheel --no-isolation" || {
      echo "ERROR: Linux wheel build failed for $PKG" >&2
      FAILED+=("$PKG (linux build failed)")
      continue
    }

  # Security gate: verify no .py source files leaked into any wheel.
  WHEELS=("$PKG"/dist/*.whl)
  if [[ ${#WHEELS[@]} -eq 0 || "${WHEELS[0]}" == "$PKG/dist/*.whl" ]]; then
    echo "ERROR: No wheels produced for $PKG" >&2
    FAILED+=("$PKG (no wheels)")
    continue
  fi

  PKG_FAILED=0
  for WHL in "${WHEELS[@]}"; do
    PY_LEAK=$(python3 - "$WHL" <<'PYCHECK'
import sys, zipfile, glob
whl = sys.argv[1]
with zipfile.ZipFile(whl) as zf:
    leaked = [n for n in zf.namelist() if n.endswith(".py")]
if leaked:
    print("\n".join(leaked))
    sys.exit(1)
PYCHECK
    2>&1) || {
      echo "SECURITY FAIL: .py files leaked into $WHL:" >&2
      echo "$PY_LEAK" >&2
      FAILED+=("$PKG (.py leak)")
      PKG_FAILED=1
      break
    }
  done

  if [[ $PKG_FAILED -eq 0 ]]; then
    echo "PASS: ${#WHEELS[@]} wheel(s) for $PKG — no .py source files"
  fi
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
