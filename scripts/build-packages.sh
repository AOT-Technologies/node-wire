#!/usr/bin/env bash
# build-packages.sh — Build all Node Wire PyPI packages as binary-only wheels.
#
# Usage:
#   scripts/build-packages.sh                  # build all packages
#   scripts/build-packages.sh packages/runtime # build a single package
#
# Prerequisites:
#   pip install build cython wheel
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

echo "=== Node Wire — building ${#PACKAGES[@]} package(s) ==="

FAILED=()

for PKG in "${PACKAGES[@]}"; do
  echo ""
  echo "--- Building: $PKG ---"
  (
    cd "$PKG"
    python -m build --wheel --no-isolation
  )

  # Security gate: verify no .py source files leaked into the wheel.
  WHL=$(ls "$PKG/dist/"*.whl 2>/dev/null | head -1)
  if [[ -z "$WHL" ]]; then
    echo "ERROR: No wheel produced for $PKG" >&2
    FAILED+=("$PKG (no wheel)")
    continue
  fi

  PY_LEAK=$(python3 - <<'PYCHECK'
import sys, zipfile, glob
whl = sys.argv[1]
with zipfile.ZipFile(whl) as zf:
    leaked = [n for n in zf.namelist() if n.endswith(".py")]
if leaked:
    print("\n".join(leaked))
    sys.exit(1)
PYCHECK
  "$WHL" 2>&1) || {
    echo "SECURITY FAIL: .py files leaked into $WHL:" >&2
    echo "$PY_LEAK" >&2
    FAILED+=("$PKG (.py leak)")
    continue
  }

  echo "PASS: $WHL — no .py source files"
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
