<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Packaging & Publishing

Node Wire ships as **nine independent PyPI packages** (the runtime plus eight connectors) built from a single monorepo. All wheels are binary-only (Cython-compiled `.so`/`.pyd` files) — no `.py` source is included in any published wheel.

---

## Package inventory

| PyPI name | Source path | Entry-point key |
|---|---|---|
| `node-wire-runtime` | `src/node_wire_runtime/` | — (no entry point; this is the runtime) |
| `node-wire-fhir-cerner` | `src/node_wire_fhir_cerner/` | `fhir_cerner` |
| `node-wire-fhir-epic` | `src/node_wire_fhir_epic/` | `fhir_epic` |
| `node-wire-google-drive` | `src/node_wire_google_drive/` | `google_drive` |
| `node-wire-http-generic` | `src/node_wire_http_generic/` | `http_generic` |
| `node-wire-salesforce` | `src/node_wire_salesforce/` | `salesforce` |
| `node-wire-slack` | `src/node_wire_slack/` | `slack` |
| `node-wire-smtp` | `src/node_wire_smtp/` | `smtp` |
| `node-wire-stripe` | `src/node_wire_stripe/` | `stripe` |

Each connector's `pyproject.toml` lives at `packages/connectors/<name>/pyproject.toml`; the runtime's is at `packages/runtime/pyproject.toml`.

---

## Python package build lifecycle

Prerequisites: `pip install build cython wheel` (and a usable `python` on the host). Run `bash scripts/build-packages.sh --help` for usage.

### Build all packages (default)

```bash
bash scripts/build-packages.sh
```

Default mode builds each of the **nine** known package paths (see inventory above): `python -m build --wheel` on the **host**, then again inside **Docker** (`python:3.12-slim`) so you get Linux-tagged wheels suitable for containers. **Docker must be installed and the daemon running.** After each package, the script scans every produced wheel and fails if any `.py` file appears inside the archive.


### Artifact layout and safe command usage

`scripts/build-packages.sh` writes wheels per package under `packages/**/dist/` (there is no single repo-root `dist/` output).

Before using wildcard wheel commands, clear old wheel artifacts so commands do not accidentally match stale versions:

```bash
rm -f packages/runtime/dist/*.whl
rm -f packages/connectors/stripe/dist/*.whl
```

### Build a single package

```bash
bash scripts/build-packages.sh packages/connectors/stripe
```

### Optional: broader wheels with cibuildwheel (`--all`)

For additional platform wheels from your **current machine** (whatever `cibuildwheel` can target there), install it and use the same script:

```bash
python -m pip install 'cibuildwheel>=2.16.0'
bash scripts/build-packages.sh --all
bash scripts/build-packages.sh --all packages/runtime
```

`CIBW_BUILD` / `CIBW_SKIP` default to the same patterns as `.github/workflows/publish.yml` unless you override them in the environment. Full Linux + macOS + Windows coverage is still best done in CI, not guaranteed from one laptop.

### Inspect wheel contents

After building, confirm no source leaks:

```bash
unzip -l packages/connectors/stripe/dist/node_wire_stripe-*.whl
# Must show .so/.pyd files only — no .py files
```

### Install from wheels and verify entry points

```bash
# Install into an active (clean) virtual env
pip install \
  packages/runtime/dist/node_wire_runtime-*.whl \
  packages/connectors/stripe/dist/node_wire_stripe-*.whl

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
| `NW_ALLOWED_CONNECTORS` | _(empty — load nothing)_ | Comma-separated allowlist of entry-point names (e.g. `stripe,fhir_epic`). **Unset or empty loads no connectors** (fail-closed). Set explicitly in production and local `.env`. |
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

If a published release must be withdrawn or replaced, follow
[release-rollback.md](release-rollback.md) (PyPI yank, corrective patch release,
and GitHub tag/release handling).

---

## Docker demo images

The `docker/*/Dockerfile` images are **demonstration templates** for packaging a single connector as a standalone MCP server. They are not production orchestration artefacts.

For a local end-to-end walkthrough (build wheels first, then build Docker images that consume those wheels), see [docs/local-packages-to-images.md](local-packages-to-images.md).

```bash
docker build -f docker/smtp/Dockerfile -t nw-smtp .
docker build -f docker/google-drive/Dockerfile -t nw-google-drive .
docker build -f docker/fhir-epic/Dockerfile -t nw-smartonfhir-epic .
docker build -f docker/fhir-cerner/Dockerfile -t nw-smartonfhir-cerner .
docker build -f docker/stripe/Dockerfile -t nw-stripe .
docker build -f docker/salesforce/Dockerfile -t nw-salesforce .
docker build -f docker/slack/Dockerfile -t nw-slack .
```

For compose and ToolHive registration see `docs/mcp-servers.md`.

---

## Pre-PyPI local validation checklist

Run these gates before triggering the CI publish workflow (default `build-packages.sh` is enough; `--all` is optional for broader local wheels):

- [ ] `bash scripts/build-packages.sh` exits 0
- [ ] `unzip -l packages/<pkg>/dist/*.whl` shows no `.py` files
- [ ] Install wheels into a clean venv; confirm entry points resolve
- [ ] `auto_register()` loads expected connectors
- [ ] `pytest tests/test_connector_registry.py tests/test_connectors_basic.py` passes
- [ ] Wheel SHA256 checksums recorded and match expected values
- [ ] `package_path` and `version` inputs match the allowlist and `pyproject.toml` version before dispatching the workflow
