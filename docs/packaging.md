<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Packaging & Publishing

Node Wire ships as multiple independent PyPI packages (the runtime plus one package per connector) built from a single monorepo. All wheels are binary-only (Cython-compiled `.so`/`.pyd` files) — no `.py` source is included in any published wheel.

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

**Source of truth:** Keep this table in sync with `ALL_PACKAGES` in [`scripts/build-packages.sh`](https://github.com/AOT-Technologies/node-wire/blob/main/scripts/build-packages.sh). MCP Docker images are a **separate subset** — see [Docker demo images](#docker-demo-images). `http_generic` is publishable on PyPI but does not have a standalone MCP container image.

---

## Adding a new publishable connector

After implementing the connector runtime (see [connectors.md](connectors.md)), update these files to ship it on PyPI and optionally as a standalone MCP server.

### Tier 1 — Runtime (dev, always required)

| File / area | Purpose |
|---|---|
| `src/node_wire_<name>/` | `schema.py`, `logic.py`, optional `registration.py`, `action_spec.py`, `README.md` |
| Root `pyproject.toml` | `[project.entry-points."node_wire.connectors"]` for editable dev install |
| `config/connectors.yaml` | `enabled`, `exposed_via`, `auth:` |
| [`sample.env`](https://github.com/AOT-Technologies/node-wire/blob/main/sample.env) | Commented placeholders for connector secrets |
| Tests | e.g. `tests/test_connectors_basic.py`, registry tests |

`auto_register()` discovers the connector via the entry point — no factory branch required.

### Tier 2 — Publishable PyPI package

| File | Purpose |
|---|---|
| `packages/connectors/<name>/pyproject.toml` | Publishable package metadata, version, entry point |
| `packages/connectors/<name>/setup.py` | Cython/build glue — see [Tier 2 templates](#tier-2-templates) below |
| [`scripts/build-packages.sh`](https://github.com/AOT-Technologies/node-wire/blob/main/scripts/build-packages.sh) | Add path to `ALL_PACKAGES` |
| [`.github/workflows/publish.yml`](https://github.com/AOT-Technologies/node-wire/blob/main/.github/workflows/publish.yml) | Add to `allowed` set — see [CI allowlist updates](#ci-allowlist-updates) below |
| [`.github/workflows/github-release.yml`](https://github.com/AOT-Technologies/node-wire/blob/main/.github/workflows/github-release.yml) | Add to `package_paths` list — see [CI allowlist updates](#ci-allowlist-updates) below |
| [`.github/workflows/security-pr.yml`](https://github.com/AOT-Technologies/node-wire/blob/main/.github/workflows/security-pr.yml) | Add to matrix `package_path` — see [CI allowlist updates](#ci-allowlist-updates) below |
| This doc — [Package inventory](#package-inventory) | Add row |
| Root + all package `pyproject.toml` | Version bump on release |
| `CHANGELOG.md` | Release section |

### Tier 2 templates

#### `pyproject.toml` template

```toml
# packages/connectors/<name>/pyproject.toml
[project]
name = "node-wire-<name>"
version = "1.0.0"
description = "Node Wire connector — <short description>"
requires-python = ">=3.11"
license = "Apache-2.0"
authors = [{ name = "AOT Technologies", email = "opensource@aot-technologies.com" }]

dependencies = [
    "node-wire-runtime>=1.0.0",
    # Add vendor SDK or HTTP client here, e.g.:
    # "httpx>=0.27.0,<0.28.0",
]

[project.entry-points."node_wire.connectors"]
<connector_id> = "node_wire_<name>.logic"

[build-system]
requires = ["setuptools>=69.0.0", "cython>=3.0", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["../../../src"]
include = ["node_wire_<name>*"]
```

#### `setup.py` template

```python
# packages/connectors/<name>/setup.py
import glob
import os
from Cython.Build import cythonize
from setuptools import setup
from setuptools.command.build_py import build_py as _BuildPy


class NoPyBuild(_BuildPy):
    def find_package_modules(self, package, package_dir):
        return []


src_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../src/node_wire_<name>"))
py_files = glob.glob(os.path.join(src_root, "**", "*.py"), recursive=True)

setup(
    cmdclass={"build_py": NoPyBuild},
    ext_modules=cythonize(py_files, compiler_directives={"language_level": "3"}, build_dir="build"),
)
```

Replace `<name>` with the connector's snake_case name (e.g. `my_service`) and `<connector_id>` with the entry-point key (same string used in `config/connectors.yaml` and `NW_ALLOWED_CONNECTORS`).

### CI allowlist updates

Three workflow files each maintain a hardcoded list of publishable packages. Add one entry to each when shipping a new connector.

#### `.github/workflows/publish.yml` — `allowed` set

Inside the `validate` step, add your package path to the `allowed` Python set:

```python
# .github/workflows/publish.yml  (inside the inline Python script)
allowed = {
    "packages/runtime",
    "packages/connectors/http_generic",
    "packages/connectors/stripe",
    # ... existing entries ...
    "packages/connectors/<name>",   # ← add this line
}
```

#### `.github/workflows/github-release.yml` — `package_paths` list

Inside the release-manifest step, add your path to the `package_paths` Python list:

```python
# .github/workflows/github-release.yml  (inside the inline Python script)
package_paths = [
    "packages/runtime",
    "packages/connectors/http_generic",
    # ... existing entries ...
    "packages/connectors/<name>",   # ← add this line
]
```

#### `.github/workflows/security-pr.yml` — matrix `package_path`

Add a new YAML list item under `jobs.<job>.strategy.matrix.package_path`:

```yaml
# .github/workflows/security-pr.yml
matrix:
  package_path:
    - packages/runtime
    - packages/connectors/http_generic
    # ... existing entries ...
    - packages/connectors/<name>   # ← add this line
```

### Tier 3 — Standalone MCP server (optional)

> **Prerequisite:** Tier 2 (the PyPI wheel) must be completed first. The Dockerfile copies pre-built `.whl` files from `packages/connectors/<name>/dist/`; that directory does not exist until you run `bash scripts/build-packages.sh packages/connectors/<name>`.

Use when you need a dedicated Docker/ToolHive image for a single connector (not required for the combined `agents.mcp_entrypoint` server). For the entrypoint code template and Dockerfile template see [mcp-servers.md — Adding a row for a new connector](mcp-servers.md#adding-a-row-for-a-new-connector).

| File | Purpose |
|---|---|
| `src/agents/<name>_mcp.py` | Per-connector MCP agent entrypoint |
| Root `pyproject.toml` | `[project.scripts]` e.g. `nw-<kebab-name>` |
| `docker/<name>/Dockerfile` | Demo MCP image |
| [`scripts/build-mcp-images.sh`](https://github.com/AOT-Technologies/node-wire/blob/main/scripts/build-mcp-images.sh) | `docker build` block |
| [`docker-compose.mcp.yml`](https://github.com/AOT-Technologies/node-wire/blob/main/docker-compose.mcp.yml) | Service + `NW_ALLOWED_CONNECTORS` |
| [mcp-servers.md](mcp-servers.md) | Naming conventions table row |
| [local-packages-to-images.md](local-packages-to-images.md) | Wheel → image mapping |

---

## Python package build lifecycle

Prerequisites: `pip install build cython wheel` (and a usable `python` on the host). Run `bash scripts/build-packages.sh --help` for usage.

### Build all packages (default)

```bash
bash scripts/build-packages.sh
```

Default mode builds each package path listed in `ALL_PACKAGES` in the script (see the [Package inventory](#package-inventory) for the current set): `python -m build --wheel` on the **host**, then again inside **Docker** (`python:3.12-slim`) so you get Linux-tagged wheels suitable for containers. **Docker must be installed and the daemon running.** After each package, the script scans every produced wheel and fails if any `.py` file appears inside the archive.


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

## Release process (tag-first)

Releases are **tag-driven**. Create and push a SemVer tag first; the GitHub Release
workflow validates the tag and creates the release. Package publishing is a separate
manual step per package, bound to that tag.

### Step 1 — Prepare the release

1. Bump version in the root `pyproject.toml` and all connector package `pyproject.toml` files (one per entry in `ALL_PACKAGES` in `scripts/build-packages.sh`).
2. Add a dated `CHANGELOG.md` section and release link for the target version.
3. Merge to `main` and confirm required CI checks are green.

### Step 2 — Create the GitHub Release

```bash
git tag -a v1.0.0 -m "Release 1.0.0"
git push origin v1.0.0
```

Then dispatch **GitHub Release** in Actions with `version` set to `1.0.0` (no leading `v`).

**Workflow:** `.github/workflows/github-release.yml` — manual `workflow_dispatch`
after the tag has been pushed.

The workflow:

1. Validates all package versions match the tag.
2. Verifies `CHANGELOG.md` has the matching section and release link.
3. Generates `sbom.json` (release-level SBOM).
4. Creates `release-manifest.txt` listing all publishable package paths (one per entry in `github-release.yml`'s `package_paths` list).
5. Creates the GitHub Release with changelog notes, SBOM, and manifest attached.

### Step 3 — Publish packages to PyPI

After the GitHub Release exists, dispatch `.github/workflows/publish.yml` **once per
package** (once per entry in the `allowed` set in that workflow).

**Required inputs:**

| Input | Example | Notes |
|---|---|---|
| `tag` | `v1.0.0` | Must match an existing release tag |
| `package_path` | `packages/connectors/stripe` | Must match the workflow allowlist |

**Prerequisites checked before build:**

- Tag resolves to a valid SemVer version.
- `package_path` is allowlisted.
- Package `pyproject.toml` version matches the tag.
- `CHANGELOG.md` contains the matching release section/link.
- A GitHub Release exists for the tag.

**Pipeline steps:**

1. Matrix-build wheels on Ubuntu, macOS, Windows via `cibuildwheel` (Python 3.11, 3.12)
2. Post-build gate: verify zero `.py` files per wheel; record SHA256 checksums
3. Merge artifacts; `pip-audit --fail-on HIGH` CVE gate
4. Publish to PyPI via OIDC Trusted Publisher with Sigstore attestations

> **Note:** The release-level SBOM is attached to the GitHub Release (step 2).
> Package publish produces PyPI Sigstore attestations per wheel; it does not
> generate a separate SBOM.

> **PyPI Trusted Publisher:** The workflow file is kept as `publish.yml` and the
> workflow name as `Publish Node Wire package` so existing PyPI publisher
> configuration continues to work.

If a published release must be withdrawn or replaced, follow
[release-rollback.md](release-rollback.md) (PyPI yank, corrective patch release,
and GitHub tag/release handling).

---

## CI publish flow (Trusted Publisher)

See [Release process (tag-first)](#release-process-tag-first) above for the full
end-to-end flow. The package publish workflow is `.github/workflows/publish.yml`.

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
- [ ] `package_path` and `tag` inputs match the allowlist and an existing release tag before dispatching the workflow
