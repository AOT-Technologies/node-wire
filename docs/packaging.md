# Packaging & Publishing

Node Wire ships as **seven independent PyPI packages** built from a single monorepo. All wheels are binary-only (Cython-compiled `.so`/`.pyd` files) — no `.py` source is included in any published wheel.

---

## Package inventory

| PyPI name | Source path | Entry-point key |
|---|---|---|
| `node-wire-runtime` | `src/node_wire_runtime/` | — (no entry point; this is the runtime) |
| `node-wire-fhir-cerner` | `src/node_wire_fhir_cerner/` | `fhir_cerner` |
| `node-wire-fhir-epic` | `src/node_wire_fhir_epic/` | `fhir_epic` |
| `node-wire-google-drive` | `src/node_wire_google_drive/` | `google_drive` |
| `node-wire-http-generic` | `src/node_wire_http_generic/` | `http_generic` |
| `node-wire-smtp` | `src/node_wire_smtp/` | `smtp` |
| `node-wire-stripe` | `src/node_wire_stripe/` | `stripe` |

Each connector's `pyproject.toml` lives at `packages/connectors/<name>/pyproject.toml`; the runtime's is at `packages/runtime/pyproject.toml`.

---

## Python package build lifecycle

### Build all packages

```bash
bash scripts/build-packages.sh
```

The script iterates every `pyproject.toml` under `packages/`, runs `python -m build --wheel` for each, and then verifies that zero `.py` files appear in the resulting wheels. It exits non-zero if any `.py` file leaks.

### Build a single package

```bash
bash scripts/build-packages.sh packages/connectors/stripe
```

### Inspect wheel contents

After building, confirm no source leaks:

```bash
unzip -l dist/node_wire_stripe-*.whl
# Must show .so/.pyd files only — no .py files
```

### Install from wheels and verify entry points

```bash
# Install into an active (clean) virtual env
pip install dist/node_wire_runtime-*.whl dist/node_wire_stripe-*.whl

# Confirm entry points registered
python -c "
from importlib.metadata import entry_points
print(list(entry_points(group='node_wire.connectors')))
"
```

### Verify connector loading

```bash
python -c "
from node_wire_runtime.connector_registry import auto_register
loaded = auto_register()
print('Loaded:', loaded)
"
```

---

## Client consumption model

A downstream client installs only what it needs:

```bash
pip install node-wire-runtime node-wire-stripe node-wire-fhir-epic
```

At startup, `auto_register()` discovers all installed connectors via the `node_wire.connectors` [entry-point group](https://packaging.python.org/en/latest/specifications/entry-points/) — no explicit import list required.

### Runtime loading knobs

| Env var | Default | Purpose |
|---|---|---|
| `NW_ALLOWED_CONNECTORS` | _(all discovered)_ | Comma-separated allowlist of entry-point names (e.g. `stripe,fhir_epic`). In development, leave unset to load everything. In production, set explicitly. |
| `NW_CONNECTOR_MODULE_PREFIX` | `node_wire_` | Connectors whose target module doesn't start with this prefix are skipped with a warning. Set to `""` to disable the check. |

---

## `connectors.yaml` and secrets

### Minimal `connectors.yaml`

```yaml
connectors:
  stripe:
    enabled: true
    exposed_via: ["mcp"]
  fhir_epic:
    enabled: false
    exposed_via: []
```

`enabled` gates whether the connector is instantiated. `exposed_via` controls which protocols (`rest`, `grpc`, `mcp`) surface it. A connector that is installed but `enabled: false` will not run.

See `config/connectors.yaml` for the full working example and `src/node_wire_runtime/connectors.yaml.sample` for a commented template with all supported fields.

For per-connector detail (operations, env vars, request/response shapes) see `docs/connectors.md` and each connector's `README.md` under `src/node_wire_<name>/`.

### Secret backend (`NW_SECRET_BACKEND`)

| Value | Behavior |
|---|---|
| `env` _(default)_ | Reads from process environment. Raises `SecretNotFoundError` for absent keys (fail-closed). |
| `aws_env` | Tries AWS Secrets Manager JSON bundle first; falls back to env on `SecretNotFoundError`. Propagates `SecretProviderError` immediately (broken provider is never silently swallowed). |

Required env vars for `aws_env`:

- `NW_AWS_SECRETS_MANAGER_SECRET_ID` — secret name or ARN (required)
- `AWS_REGION` — defaults to `us-east-1`

**Legacy flag:** `NW_ENV_SECRET_LEGACY_EMPTY=true` returns `""` for missing keys instead of raising. This exists for backwards compatibility only — do not use in production.

Additional cloud backends (`vault`, `azure`, `gcp`) ship as optional extras in `node-wire-runtime` but are not currently wired into the factory:

```bash
pip install "node-wire-runtime[aws]"    # boto3
pip install "node-wire-runtime[vault]"  # hvac
pip install "node-wire-runtime[azure]"  # azure-keyvault-secrets
pip install "node-wire-runtime[gcp]"    # google-cloud-secret-manager
```

---

## CI publish flow (Trusted Publisher)

**Workflow:** `.github/workflows/publish.yml` — manual `workflow_dispatch`.

**Required inputs:**

| Input | Example | Notes |
|---|---|---|
| `package_path` | `packages/connectors/stripe` | Must match an entry in the workflow's allowlist |
| `version` | `0.1.0` | Must match `[project].version` in the package's `pyproject.toml` |

**Pipeline steps:**

1. Validate `package_path` against allowlist (prevents path traversal)
2. Matrix-build wheels on Ubuntu, macOS, Windows via `cibuildwheel` (Python 3.11, 3.12; Linux manylinux + aarch64, macOS x86_64 + arm64, Windows amd64)
3. Post-build gate: verify zero `.py` files per wheel; record SHA256 checksums
4. Merge artifacts; `pip-audit --fail-on HIGH` CVE gate
5. Generate SBOM via `cyclonedx-py`
6. Publish to PyPI via OIDC Trusted Publisher with Sigstore attestations (all action SHAs pinned for immutability)

---

## Docker demo images

The `docker/*/Dockerfile` images are **demonstration templates** for packaging a single connector as a standalone MCP server. They are not production orchestration artefacts.

For a local end-to-end walkthrough (build wheels first, then build Docker images that consume those wheels), see [docs/local-packages-to-images.md](local-packages-to-images.md).

```bash
docker build -f docker/smtp/Dockerfile -t nw-smtp .
docker build -f docker/google-drive/Dockerfile -t nw-google-drive .
docker build -f docker/fhir-epic/Dockerfile -t nw-smartonfhir-epic .
docker build -f docker/fhir-cerner/Dockerfile -t nw-smartonfhir-cerner .
```

For compose and ToolHive registration see `docs/mcp-servers.md`.

---

## Pre-PyPI local validation checklist

Run these gates before triggering the CI publish workflow:

- [ ] `bash scripts/build-packages.sh` exits 0
- [ ] `unzip -l dist/<pkg>-*.whl` shows no `.py` files
- [ ] Install wheels into a clean venv; confirm entry points resolve
- [ ] `auto_register()` loads expected connectors
- [ ] `pytest tests/test_connector_registry.py tests/test_connectors_basic.py` passes
- [ ] Wheel SHA256 checksums recorded and match expected values
- [ ] `package_path` and `version` inputs match the allowlist and `pyproject.toml` version before dispatching the workflow
