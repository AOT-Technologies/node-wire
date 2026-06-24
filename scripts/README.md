# build-mcp-server.sh

Shell script to build a ToolHive-ready MCP server from a scope fixture and OpenAPI spec. It runs validate, generate, dependency sync, and optional quality checks in one pass.

---

## Prerequisites

| Requirement | Notes |
|-------------|--------|
| [uv](https://docs.astral.sh/uv/) | On `PATH`, or set `UV` to the full path |
| mcp-builder repo | Clone with `pyproject.toml`, `src/`, and `e2e/fixtures/` |
| [mcp-template-py](https://github.com/stacklok/mcp-template-py) | Default: `../mcp-template-py` relative to mcp-builder root |
| [Task](https://taskfile.dev/) | Optional; skipped with a warning if missing |
| `curl` | Downloads OpenAPI specs when missing |
| `swagger2openapi` | Required for Slack only |

### One-time setup

```bash
cd /path/to/mcp-builder
uv sync

git clone https://github.com/stacklok/mcp-template-py.git ../mcp-template-py
```

---

## Usage

### From the repo

Run from the mcp-builder root when the script lives in `scripts/`:

```bash
scripts/build-mcp-server.sh spotify
```

Builds the Spotify MCP server using the repo as root (resolved automatically from the script location).

### From anywhere with explicit root

Pass `--root` when your working directory is not the mcp-builder clone:

```bash
scripts/build-mcp-server.sh spotify --root G:/SPACE/mcp-builder
```

Same clone from WSL:

```bash
scripts/build-mcp-server.sh spotify --root /mnt/g/SPACE/mcp-builder
```

### List servers

Print all registry aliases and their output directories:

```bash
scripts/build-mcp-server.sh --list --root G:/SPACE/mcp-builder
```

### Options

Skip quality checks for a faster build:

```bash
scripts/build-mcp-server.sh github --root G:/SPACE/mcp-builder --skip-check
```

---

## Repo root resolution

The script needs the mcp-builder repository root (where `pyproject.toml` lives). First match wins:

1. `--root PATH`
2. `MCP_BUILDER_ROOT` environment variable
3. Parent of the `scripts/` directory

The root must contain `pyproject.toml` and `scripts/mcp-servers.registry`.

---

## Supported servers

Aliases are defined in `mcp-servers.registry` (pipe-delimited):

```text
alias|scope_yaml|openapi_spec|download_url|server_name
```

| Alias | Output directory |
|-------|------------------|
| `spotify` | `out/spotify-mcp` |
| `github` | `out/github-mcp` |
| `slack` | `out/slack-mcp` |
| `google-drive`, `google_drive` | `out/google-drive-mcp` |
| `stripe` | `out/stripe-mcp` |
| `jira`, `jira-cloud` | `out/jira-cloud-mcp` |
| `bamboohr` | `out/bamboohr-mcp` |
| `twilio` | `out/twilio-mcp` |
| `zoom` | `out/zoom-mcp` |
| `petstore` | `out/petstore-mcp` |

OpenAPI specs are downloaded on first run when a URL is configured (Slack uses a special Swagger 2.0 conversion — see below).

---

## Options

| Option | Description |
|--------|-------------|
| `--root PATH` | Path to mcp-builder repo |
| `--list` | List registry aliases and exit |
| `--template DIR` | Path to `mcp-template-py` (default: `../mcp-template-py`) |
| `--skip-download` | Do not fetch OpenAPI spec; fail if file missing |
| `--skip-validate` | Skip `mcp-builder validate` |
| `--skip-sync` | Skip `uv sync` in generated project |
| `--skip-check` | Skip `task check` in generated project |
| `--force` | Remove `out/<server>-mcp` before generate **(default)** |
| `--no-force` | Fail if output directory already exists |
| `-h`, `--help` | Show help |

### Environment variables

| Variable | Description |
|----------|-------------|
| `MCP_BUILDER_ROOT` | Default repo root if `--root` is omitted |
| `MCP_TEMPLATE_DIR` | Default template path if `--template` is omitted |
| `UV` | Full path to `uv` when not on PATH |
| `PYTHONUTF8` | Set to `1` on Windows for UTF-8 OpenAPI files (set automatically by the script) |

---

## What the script does

1. Resolve mcp-builder root and load scope/spec paths from the registry.
2. Download the OpenAPI spec if missing (unless `--skip-download`).
3. Run `uv sync` in the mcp-builder repo.
4. Run `mcp-builder validate` (unless `--skip-validate`).
5. Remove `out/<server>-mcp` if it exists (default `--force`).
6. Run `mcp-builder generate`.
7. Run `uv sync` in the generated project (unless `--skip-sync`).
8. Run `task check` (unless `--skip-check`).

---

## Output

Generated projects are written to:

```text
<repo-root>/out/<server_name>-mcp/
```

Typical layout:

```text
out/<server>-mcp/
├── src/<server>_mcp/
├── deploy/
├── Dockerfile
├── Taskfile.yml
└── pyproject.toml
```

---

## Slack OpenAPI download

Slack’s upstream spec is Swagger 2.0. The registry entry uses `slack:swagger2`, which requires:

```bash
npm install -g swagger2openapi
```

---

## Adding a server to the registry

1. Add a scope YAML under `e2e/fixtures/real/`.
2. Add a line to `mcp-servers.registry`:

```text
my-api|e2e/fixtures/real/my_api.yaml|e2e/fixtures/real/my_api_openapi.yaml|https://example.com/openapi.yaml|my-api
```

3. Build:

```bash
scripts/build-mcp-server.sh my-api --root /path/to/mcp-builder
```

| Field | Meaning |
|-------|---------|
| `alias` | CLI name |
| `scope_yaml` | Path relative to repo root |
| `openapi_spec` | Path relative to repo root |
| `download_url` | `curl` URL, `slack:swagger2`, or empty |
| `server_name` | Output directory: `out/<server_name>-mcp` |

---

## Platform notes

### WSL and Windows `uv`

From WSL, the script can use a Windows `uv.exe` if Linux `uv` is not on PATH. Paths are converted automatically and `PYTHONUTF8` is passed through when needed.

### Encoding on Windows

Some OpenAPI specs contain non-ASCII content. The script sets `PYTHONUTF8=1` automatically. If you run `mcp-builder` commands manually on Windows, export `PYTHONUTF8=1` first.

### Re-running generate

`mcp-builder generate` fails if the output directory already exists. By default the script removes it (`--force`). Use `--no-force` to keep an existing build and fail instead.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `uv` not found | Install uv or set `UV` to the full path |
| `charmap` codec error | Set `PYTHONUTF8=1` when running mcp-builder manually on Windows |
| `FileExistsError` on generate | Use default `--force`, or delete `out/<server>-mcp` manually |
| `mcp-template-py not found` | Clone the template or pass `--template` |
| Unknown server alias | Run `--list` and check `mcp-servers.registry` |
| Slack download fails | Install `swagger2openapi` |

---

## See also

- [mcp-builder README](../README.md)
- [e2e/download_openapi_specs.sh](../e2e/download_openapi_specs.sh)
