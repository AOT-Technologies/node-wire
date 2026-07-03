#!/usr/bin/env bash
##
## SPDX-FileCopyrightText: 2026 AOT Technologies
## SPDX-License-Identifier: Apache-2.0
##
## Build a ToolHive-ready MCP server from e2e fixture scopes.
##
## Usage:
##   scripts/build-mcp-server.sh spotify
##   scripts/build-mcp-server.sh spotify --root /path/to/mcp-builder
##   scripts/build-mcp-server.sh --list --root G:/SPACE/mcp-builder
##
## Repo root resolution (first match wins):
##   1. --root PATH
##   2. MCP_BUILDER_ROOT environment variable
##   3. Parent of this script's directory (when script lives in repo/scripts/)
##
## Environment:
##   MCP_BUILDER_ROOT   Path to mcp-builder repo
##   MCP_TEMPLATE_DIR   Path to mcp-template-py checkout
##   UV                 Full path to uv when not on PATH (common on Windows Git Bash/WSL)
##
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Defaults (overridden by flags)
ROOT_OVERRIDE=""
OUTPUT_DIR=""
TEMPLATE_DIR="${MCP_TEMPLATE_DIR:-}"
DO_DOWNLOAD=1
DO_VALIDATE=1
DO_GENERATE=1
DO_SYNC=1
DO_CHECK=1
DO_FORCE=1
LIST_ONLY=0
SERVER_ARG=""

UV_CMD=""

log() {
  echo "==> $*"
}

info() {
  echo "    $*"
}

usage() {
  cat <<'EOF'
Usage: scripts/build-mcp-server.sh <server> [options]
       scripts/build-mcp-server.sh --list [options]

Build an MCP server project from e2e fixture scopes (validate → generate → uv sync → task check).

Arguments:
  <server>              Registry alias (spotify, github, slack, google-drive, …)

Options:
  --root PATH           Path to mcp-builder repo (or set MCP_BUILDER_ROOT)
  --list                List known server aliases and exit
  --template DIR        mcp-template-py checkout (default: ../mcp-template-py)
  --skip-download       Do not fetch OpenAPI spec (fail if missing)
  --skip-validate       Skip mcp-builder validate
  --skip-sync           Skip uv sync in generated project
  --skip-check          Skip task check in generated project
  --force               Remove out/<server>-mcp before generate (default)
  --no-force            Fail if output project directory already exists
  -h, --help            Show this help

Examples:
  scripts/build-mcp-server.sh spotify --root G:/SPACE/mcp-builder
  scripts/build-mcp-server.sh github --root /home/user/mcp-builder --skip-check
  scripts/build-mcp-server.sh --list --root G:/SPACE/mcp-builder
EOF
}

normalize_server_arg() {
  local s="$1"
  s="${s%,}"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  echo "$s"
}

# Convert Windows paths (G:\foo) to a form cd accepts in Git Bash/WSL when possible.
normalize_root_path() {
  local p="$1"
  if [[ "$p" =~ ^[A-Za-z]:\\ ]] || [[ "$p" =~ ^[A-Za-z]:/ ]]; then
    if command -v cygpath &>/dev/null; then
      cygpath "$p"
      return
    fi
    if command -v wslpath &>/dev/null; then
      wslpath -u "$p" 2>/dev/null && return
    fi
    # Git Bash: G:\SPACE\mcp-builder → /g/SPACE/mcp-builder
    if [[ "$p" =~ ^([A-Za-z]):[/\\](.*)$ ]]; then
      local drive="${BASH_REMATCH[1],}"
      local rest="${BASH_REMATCH[2]//\\//}"
      echo "/${drive}/${rest}"
      return
    fi
  fi
  echo "$p"
}

resolve_mcp_builder_root() {
  local root=""
  if [[ -n "$ROOT_OVERRIDE" ]]; then
    root="$(normalize_root_path "$ROOT_OVERRIDE")"
  elif [[ -n "${MCP_BUILDER_ROOT:-}" ]]; then
    root="$(normalize_root_path "$MCP_BUILDER_ROOT")"
  else
    root="$(cd "${SCRIPT_DIR}/.." && pwd)"
  fi

  if ! cd "$root" 2>/dev/null; then
    echo "ERROR: cannot access mcp-builder root: ${root}" >&2
    exit 1
  fi
  ROOT="$(pwd)"
  REGISTRY="${ROOT}/scripts/mcp-servers.registry"
  OUTPUT_DIR="${ROOT}/out"

  if [[ ! -f "${ROOT}/pyproject.toml" ]]; then
    echo "ERROR: not a valid mcp-builder repo (missing pyproject.toml): ${ROOT}" >&2
    exit 1
  fi
  if [[ ! -f "$REGISTRY" ]]; then
    echo "ERROR: registry not found: ${REGISTRY}" >&2
    echo "Ensure scripts/mcp-servers.registry exists in the repo." >&2
    exit 1
  fi
}

registry_lines() {
  grep -v '^[[:space:]]*#' "$REGISTRY" | grep -v '^[[:space:]]*$' || true
}

list_servers() {
  echo "Known MCP server aliases (scripts/mcp-servers.registry):"
  echo "  mcp-builder root: ${ROOT}"
  echo ""
  while IFS='|' read -r alias _scope _spec _url server_name; do
    printf "  %-16s -> %s/out/%s-mcp\n" "$alias" "$ROOT" "$server_name"
  done < <(registry_lines)
  echo ""
  echo "Run: scripts/build-mcp-server.sh <alias> [--root PATH]"
}

resolve_alias() {
  local alias="$1"
  local line
  line="$(registry_lines | grep -E "^${alias}\\|" | head -n 1 || true)"
  if [[ -z "$line" ]]; then
    echo "ERROR: unknown server alias: ${alias}" >&2
    echo "Run with --list to see known aliases." >&2
    exit 1
  fi
  echo "$line"
}

uses_windows_uv() {
  [[ "$UV_CMD" == *.exe ]]
}

resolve_uv() {
  if [[ -n "$UV_CMD" ]]; then
    return 0
  fi

  if [[ -n "${UV:-}" ]]; then
    if [[ -x "$UV" || -f "$UV" ]]; then
      UV_CMD="$UV"
      return 0
    fi
    echo "ERROR: UV is set to '${UV}' but that file is not executable." >&2
    exit 1
  fi

  if command -v uv &>/dev/null; then
    UV_CMD="$(command -v uv)"
    return 0
  fi
  if command -v uv.exe &>/dev/null; then
    UV_CMD="$(command -v uv.exe)"
    return 0
  fi

  local candidates=(
    "${ROOT}/.venv/Scripts/uv.exe"
    "${ROOT}/.venv/bin/uv"
    "${HOME}/.local/bin/uv"
    "${HOME}/.local/bin/uv.exe"
  )

  if [[ -n "${USERPROFILE:-}" ]]; then
    local uf="${USERPROFILE//\\//}"
    candidates+=(
      "${uf}/.local/bin/uv.exe"
      "${uf}/AppData/Roaming/Python/Python313/Scripts/uv.exe"
    )
  fi

  if [[ -d "/mnt/c/Users" ]] && command -v cmd.exe &>/dev/null; then
    local wuser
    wuser="$(cmd.exe /c "echo %USERNAME%" 2>/dev/null | tr -d '\r\n' | xargs)" || true
    if [[ -n "$wuser" ]]; then
      candidates+=(
        "/mnt/c/Users/${wuser}/.local/bin/uv.exe"
        "/mnt/c/Users/${wuser}/AppData/Roaming/Python/Python313/Scripts/uv.exe"
      )
    fi
  fi

  local c
  for c in "${candidates[@]}"; do
    if [[ -f "$c" ]]; then
      UV_CMD="$c"
      return 0
    fi
  done

  if command -v cmd.exe &>/dev/null; then
    local winpath
    winpath="$(cmd.exe /c "where uv" 2>/dev/null | head -n 1 | tr -d '\r\n' | xargs)" || true
    if [[ -n "$winpath" ]]; then
      if command -v wslpath &>/dev/null; then
        UV_CMD="$(wslpath "$winpath" 2>/dev/null || true)"
      elif command -v cygpath &>/dev/null; then
        UV_CMD="$(cygpath "$winpath" 2>/dev/null || true)"
      elif [[ "$winpath" =~ ^([A-Za-z]):\\(.*)$ ]]; then
        local drive="${BASH_REMATCH[1],}"
        local rest="${BASH_REMATCH[2]//\\//}"
        UV_CMD="/${drive}/${rest}"
      else
        UV_CMD="$winpath"
      fi
      if [[ -n "$UV_CMD" && -f "$UV_CMD" ]]; then
        return 0
      fi
    fi
  fi

  echo "ERROR: 'uv' not found." >&2
  echo "  Install: https://docs.astral.sh/uv/" >&2
  echo "  Or set UV to the full path to uv." >&2
  exit 1
}

require_uv() {
  resolve_uv
  info "using uv: ${UV_CMD}"
}

setup_python_utf8() {
  export PYTHONUTF8=1
  # WSL: Windows uv.exe does not inherit Linux env unless listed in WSLENV.
  if uses_windows_uv && [[ -f /proc/version ]] && grep -qi microsoft /proc/version; then
    case ":${WSLENV:-}:" in
      *:PYTHONUTF8:*) ;;
      *) export WSLENV="${WSLENV:+${WSLENV}:}PYTHONUTF8" ;;
    esac
  fi
}

to_uv_path() {
  local p="$1"
  if uses_windows_uv && command -v wslpath &>/dev/null; then
    wslpath -w "$p"
  elif uses_windows_uv && command -v cygpath &>/dev/null; then
    cygpath -w "$p"
  else
    echo "$p"
  fi
}

run() {
  log "$*"
  "$@"
}

remove_existing_project() {
  local dir="$1"
  if [[ ! -d "$dir" ]]; then
    return 0
  fi
  log "Removing existing project: ${dir}"
  chmod -R u+w "$dir" 2>/dev/null || true
  rm -rf "$dir"
}

download_slack_spec() {
  local spec_path="$1"
  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/slack_swagger.XXXXXX.json")"
  curl -fsSL -o "$tmp" \
    "https://raw.githubusercontent.com/slackapi/slack-api-specs/master/web-api/slack_web_openapi_v2.json"
  if ! command -v swagger2openapi &>/dev/null; then
    rm -f "$tmp"
    echo "ERROR: slack requires swagger2openapi. Install: npm install -g swagger2openapi" >&2
    exit 1
  fi
  swagger2openapi "$tmp" -o "$spec_path" --yaml
  rm -f "$tmp"
}

download_spec() {
  local spec_rel="$1"
  local download_url="$2"
  local spec_path="${ROOT}/${spec_rel}"

  if [[ -z "$download_url" ]]; then
    if [[ ! -f "$spec_path" ]]; then
      echo "ERROR: OpenAPI spec not found: ${spec_path} (no download URL configured)" >&2
      exit 1
    fi
    info "OpenAPI spec present: ${spec_rel}"
    return 0
  fi

  if [[ -f "$spec_path" ]]; then
    info "OpenAPI spec found (skipping download): ${spec_path}"
    return 0
  fi

  mkdir -p "$(dirname "$spec_path")"
  if [[ "$download_url" == "slack:swagger2" ]]; then
    log "Downloading and converting Slack spec → ${spec_rel}"
    download_slack_spec "$spec_path"
    return 0
  fi

  log "Downloading OpenAPI spec → ${spec_rel}"
  curl -fsSL -o "$spec_path" "$download_url"
}

build_one() {
  local alias="$1"
  local scope_rel spec_rel download_url server_name
  local scope_path spec_path project_dir template_dir

  UV_CMD=""

  IFS='|' read -r _alias scope_rel spec_rel download_url server_name <<< "$(resolve_alias "$alias")"
  scope_path="${ROOT}/${scope_rel}"
  spec_path="${ROOT}/${spec_rel}"
  project_dir="${OUTPUT_DIR}/${server_name}-mcp"

  if [[ -n "$TEMPLATE_DIR" ]]; then
    template_dir="$(normalize_root_path "$TEMPLATE_DIR")"
  else
    template_dir="$(cd "${ROOT}/../mcp-template-py" 2>/dev/null && pwd || echo "${ROOT}/../mcp-template-py")"
  fi

  if [[ ! -d "$template_dir" ]]; then
    echo "ERROR: mcp-template-py not found at: ${template_dir}" >&2
    echo "Clone: git clone https://github.com/stacklok/mcp-template-py.git <path>" >&2
    echo "Or pass: --template PATH" >&2
    exit 1
  fi

  if [[ ! -f "$scope_path" ]]; then
    echo "ERROR: scope not found: ${scope_path}" >&2
    exit 1
  fi

  if [[ "$DO_DOWNLOAD" -eq 1 ]]; then
    download_spec "$spec_rel" "$download_url"
  elif [[ ! -f "$spec_path" ]]; then
    echo "ERROR: OpenAPI spec not found: ${spec_path}" >&2
    exit 1
  fi

  echo ""
  log "Building MCP server: ${server_name} (alias: ${alias})"
  echo "    mcp-builder root: ${ROOT}"
  echo "    scope:   ${scope_rel}"
  echo "    spec:    ${spec_rel}"
  echo "    output:  ${project_dir}"

  cd "$ROOT"
  require_uv
  setup_python_utf8
  echo "    PYTHONUTF8=1"

  run "$UV_CMD" sync

  if [[ "$DO_VALIDATE" -eq 1 ]]; then
    run "$UV_CMD" run mcp-builder validate "$(to_uv_path "$scope_path")" --openapi-spec "$(to_uv_path "$spec_path")"
  fi

  if [[ "$DO_GENERATE" -eq 1 ]]; then
    if [[ "$DO_FORCE" -eq 1 ]]; then
      remove_existing_project "$project_dir"
    fi
    run "$UV_CMD" run mcp-builder generate \
      "$(to_uv_path "$scope_path")" \
      "$(to_uv_path "$spec_path")" \
      "$(to_uv_path "$template_dir")" \
      --output-dir "$(to_uv_path "$OUTPUT_DIR")"
  fi

  if [[ "$DO_SYNC" -eq 1 ]]; then
    if [[ ! -d "$project_dir" ]]; then
      echo "ERROR: expected project at ${project_dir}" >&2
      exit 1
    fi
    log "Installing dependencies in ${project_dir}"
    (cd "$project_dir" && "$UV_CMD" sync)
  fi

  if [[ "$DO_CHECK" -eq 1 ]]; then
    if ! command -v task &>/dev/null; then
      echo "WARNING: 'task' not found; skipping task check. Install: https://taskfile.dev/" >&2
    else
      log "Running task check in ${project_dir}"
      (cd "$project_dir" && task check)
    fi
  fi

  cat <<EOF

Generated project: ${project_dir}

Manual steps:
  1. Register an OAuth/API app with the upstream provider if needed
  2. Edit ${project_dir}/deploy/ placeholders (REPLACE_ME_DOMAIN, REPLACE_ME)
  3. cd ${project_dir} && task run
EOF
}

# --- argument parsing ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --list)
      LIST_ONLY=1
      shift
      ;;
    --root)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --root requires a path argument" >&2
        exit 2
      fi
      ROOT_OVERRIDE="$2"
      shift 2
      ;;
    --template)
      TEMPLATE_DIR="$2"
      shift 2
      ;;
    --skip-download)
      DO_DOWNLOAD=0
      shift
      ;;
    --skip-validate)
      DO_VALIDATE=0
      shift
      ;;
    --skip-sync)
      DO_SYNC=0
      shift
      ;;
    --skip-check)
      DO_CHECK=0
      shift
      ;;
    --force)
      DO_FORCE=1
      shift
      ;;
    --no-force)
      DO_FORCE=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [[ -z "$SERVER_ARG" ]]; then
        arg="$(normalize_server_arg "$1")"
        if [[ -n "$arg" ]]; then
          SERVER_ARG="$arg"
        fi
      else
        extra="$(normalize_server_arg "$1")"
        if [[ -n "$extra" ]]; then
          echo "Unexpected argument: $1" >&2
          usage >&2
          exit 2
        fi
      fi
      shift
      ;;
  esac
done

resolve_mcp_builder_root

if [[ "$LIST_ONLY" -eq 1 ]]; then
  list_servers
  exit 0
fi

if [[ -z "$SERVER_ARG" ]]; then
  usage >&2
  exit 2
fi

build_one "$SERVER_ARG"
