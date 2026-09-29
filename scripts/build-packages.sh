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
# Linux wheels for a specific image, via cibuildwheel + Docker (nw gen-stacklok uses this with the
# selector read from the generated server's base image):
#   CIBW_BUILD='cp313-musllinux_*' scripts/build-packages.sh --cibw-linux packages/runtime
#   scripts/build-packages.sh --musllinux packages/runtime      # shorthand for cp313-musllinux_*
#   (arch: NW_WHEEL_ARCHS, default native; e.g. NW_WHEEL_ARCHS="x86_64 aarch64")
#   (C flags: NW_WHEEL_CFLAGS, default "-fno-strict-overflow -DNDEBUG -O1 -g0"; see the note in
#   the cibuildwheel section)
#
# Prerequisites (--all / --cibw-linux / --musllinux):
#   cibuildwheel (a dev dependency: uv sync --all-extras --dev); Docker for the Linux modes
#
# Security guarantee:
#   Each wheel is verified to contain zero .py source files and at least one
#   .so/.pyd extension before printing "PASS". A leak or an empty wheel exits 1.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

WHEEL_BUILDER_IMAGE="nw-wheel-builder:local"
WHEEL_BUILDER_CONTEXT="$ROOT_DIR/docker/wheel-builder"

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
  scripts/build-packages.sh [--help]
  scripts/build-packages.sh [--host-only|--linux-only] [packages/...]
  scripts/build-packages.sh --all [packages/...]
  scripts/build-packages.sh --cibw-linux [packages/...]   (CIBW_BUILD selects the target)
  scripts/build-packages.sh --musllinux [packages/...]

  Default:     build each package on the host and again in Docker (Linux wheels).
  --host-only: build host wheels only (no Docker).
  --linux-only: build Linux wheels only (via local nw-wheel-builder image).
  --all:       build with cibuildwheel (targets depend on host; for full OS matrix use CI publish.yml).
  --cibw-linux: build Linux wheels with cibuildwheel (Docker) for the CIBW_BUILD selector
               (default cp313-musllinux_*); replaces only matching wheels in dist/.
               Arch: NW_WHEEL_ARCHS (default native).
  --musllinux: --cibw-linux with CIBW_BUILD=cp313-musllinux_* (Alpine images).

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
MUSL_MODE=0
HOST_ONLY=0
LINUX_ONLY=0
PACKAGES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --all)
      ALL_MODE=1
      shift
      ;;
    --musllinux)
      ALL_MODE=1
      MUSL_MODE=1
      export CIBW_BUILD="cp313-musllinux_*"
      shift
      ;;
    --cibw-linux)
      ALL_MODE=1
      MUSL_MODE=1
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

# Verify wheels are binary-only: no .py sources, at least one compiled
# extension. First arg: python binary.
verify_binary_only_wheels() {
  local py="$1"
  shift
  local -a wheels=("$@")
  local whl
  local check_out
  local pkg_failed=0

  for whl in "${wheels[@]}"; do
    check_out=$("$py" - "$whl" 2>&1 <<'PYCHECK'
import sys
import zipfile

wheel_path = sys.argv[1]
with zipfile.ZipFile(wheel_path) as zf:
    names = zf.namelist()

leaked = [name for name in names if name.endswith(".py")]
if leaked:
    print("leaked .py files:")
    print("\n".join(leaked))
    sys.exit(1)

compiled = [name for name in names if name.endswith((".so", ".pyd"))]
if not compiled:
    print("no .so or .pyd extensions in the wheel")
    sys.exit(1)
PYCHECK
    ) || {
      echo "SECURITY FAIL: $whl is not a binary-only wheel:" >&2
      echo "$check_out" >&2
      pkg_failed=1
      break
    }
  done
  return "$pkg_failed"
}

# ─── All-platform mode (cibuildwheel) ───────────────────────────────────────
if [[ "$ALL_MODE" -eq 1 ]]; then
  CIBW_ARGS=(--output-dir dist)
  WHEEL_GLOB="*.whl"
  if [[ "$MUSL_MODE" -eq 1 ]]; then
    # Each package's setup.py compiles ../../src; the build container only gets a copy of the
    # package directory, so bind-mount the shared src/ tree at /src (as publish.yml does).
    export CIBW_BUILD="${CIBW_BUILD:-cp313-musllinux_*}"
    export CIBW_ARCHS="${NW_WHEEL_ARCHS:-native}"
    export CIBW_CONTAINER_ENGINE="${CIBW_CONTAINER_ENGINE:-docker; create_args: --volume=$ROOT_DIR/src:/src}"
    # CFLAGS replaces Python's default "-fno-strict-overflow -DNDEBUG -g -O3 ...", so the flags
    # CPython builds extensions with are kept and only the level changes. Generated connector
    # modules hold one huge module-init function that gcc's -O2/-O3 passes take minutes on
    # (slack_web schema.c: 173s at -O3, 37s at -O1). The code is almost all Python C-API calls,
    # so -O1 costs little at run time; -g0 drops debug info, roughly halving wheel size.
    export CIBW_ENVIRONMENT="${CIBW_ENVIRONMENT:+$CIBW_ENVIRONMENT }CFLAGS='${NW_WHEEL_CFLAGS:--fno-strict-overflow -DNDEBUG -O1 -g0}'"
    CIBW_ARGS+=(--platform linux)
    # Replace only the wheels this selector produces (cp313-musllinux_* → *-cp313-cp313-musllinux_*).
    _py="${CIBW_BUILD%%-*}"
    _platform="${CIBW_BUILD#*-}"
    WHEEL_GLOB="*-${_py}-${_py}-${_platform%%\*}*.whl"
  fi
  export CIBW_BUILD="${CIBW_BUILD:-cp313-*}"
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

  # cibuildwheel: the current Python first (the repo's dev environment pins it, so `uv run`
  # has it), then a standalone `cibuildwheel` on PATH (pipx / uv tool), then uvx.
  if "$PYTHON" -c "import cibuildwheel" >/dev/null 2>&1; then
    CIBUILDWHEEL=("$PYTHON" -m cibuildwheel)
  elif command -v cibuildwheel >/dev/null 2>&1; then
    CIBUILDWHEEL=(cibuildwheel)
  elif command -v uvx >/dev/null 2>&1; then
    CIBUILDWHEEL=(uvx --from 'cibuildwheel==4.2.1' cibuildwheel)
  else
    echo "ERROR: cibuildwheel is not available." >&2
    echo "Run 'uv sync --all-extras --dev' (it is a dev dependency), or install it:" >&2
    echo "  python3 -m pip install 'cibuildwheel==4.2.1'" >&2
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
    # shellcheck disable=SC2086 # WHEEL_GLOB is a glob pattern
    rm -f "$PKG"/dist/$WHEEL_GLOB

    if ! (
      cd "$PKG"
      "${CIBUILDWHEEL[@]}" "${CIBW_ARGS[@]}"
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

    if ! verify_binary_only_wheels "$PYTHON" "${WHEELS[@]}"; then
      FAILED+=("$PKG (not binary-only)")
      continue
    fi

    echo "PASS: ${#WHEELS[@]} wheel(s) for $PKG — compiled extensions, no .py source"
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
    # Match the host uid/gid so Cython/setuptools can write build/ and dist/
    # into the bind-mounted workspace (Dockerfile USER app is uid 1000; GH
    # Actions runners are typically 1001). HOME=/tmp keeps tool caches writable
    # when the overridden uid has no /etc/passwd entry in the image.
    mkdir -p "$PKG/build" "$PKG/dist"
    docker run --rm \
      --user "$(id -u):$(id -g)" \
      -e HOME=/tmp \
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

  if ! verify_binary_only_wheels "$PYTHON_HOST" "${WHEELS[@]}"; then
    FAILED+=("$PKG (not binary-only)")
    continue
  fi

  echo "PASS: ${#WHEELS[@]} wheel(s) for $PKG — compiled extensions, no .py source"
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
