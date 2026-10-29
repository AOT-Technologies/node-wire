<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Packaging & publishing

Node Wire ships as multiple independent PyPI packages (the runtime plus one package per connector) built from a single monorepo. Every package builds one pure-Python wheel (`py3-none-any`), which installs on every supported Python version (3.11–3.13) and operating system.

---

## Package inventory

The following table lists every package the build script produces:

| PyPI name | Source path | Entry-point key |
|---|---|---|
| `node-wire-runtime` | `src/node_wire_runtime/` | — (no entry point; this is the runtime) |
| `node-wire-bindings` **(not published)** | `src/bindings/` (MCP surface) | — (factory / invoke / mcp_server for MCP images) |
| `node-wire-toolhive` **(not published)** | `src/node_wire_toolhive/` | — (credential relay, tenant header, config tools for `nw gen-stacklok` servers) |
| `node-wire-fhir-cerner` | `src/node_wire_fhir_cerner/` | `fhir_cerner` |
| `node-wire-fhir-epic` | `src/node_wire_fhir_epic/` | `fhir_epic` |
| `node-wire-google-drive` | `src/node_wire_google_drive/` | `google_drive` |
| `node-wire-http` | `src/node_wire_http_generic/` | `http_generic` |
| `node-wire-salesforce` | `src/node_wire_salesforce/` | `salesforce` |
| `node-wire-slack` | `src/node_wire_slack/` | `slack` |
| `node-wire-smtp` | `src/node_wire_smtp/` | `smtp` |
| `node-wire-stripe` | `src/node_wire_stripe/` | `stripe` |

Each connector's `pyproject.toml` lives at `packages/connectors/<name>/pyproject.toml`; the runtime's is at `packages/runtime/pyproject.toml`; MCP bindings at `packages/bindings/pyproject.toml`.

**Only the runtime, the connectors, and `node-wire-connectors` ship to PyPI.** `node-wire-connectors` is metadata only (`packages/meta`); it is not in the preceding table or in `ALL_PACKAGES`. `node-wire-bindings` is built as a
local wheel (`scripts/build-packages.sh`, `nw gen-whl --bindings`) for MCP Docker
images and is deliberately absent from the package lists in `publish.yml`,
`github-release.yml`, and `security-pr.yml`. Do not add it to them. The same applies to
`node-wire-toolhive`, built only for `nw gen-stacklok` images (`packages/toolhive/`).

**Source of truth:** Keep this table in sync with `ALL_PACKAGES` in [`scripts/build-packages.sh`](https://github.com/AOT-Technologies/node-wire/blob/main/scripts/build-packages.sh) (which also builds the unpublished bindings wheel); `tests/test_docs_lifecycle.py` fails unless the names here match `ALL_PACKAGES` exactly (no missing rows, no extra rows). MCP Docker images are a **separate subset** — see [Docker demo images](#docker-demo-images). `http_generic` is publishable on PyPI but does not have a standalone MCP container image.

---

## Adding a new publishable connector

This is the one checklist for shipping a connector. Write it first ([Build a connector](connectors-build.md)), or generate it ([`nw gen-all`](cli/nw-cli.md), which already does the Tier 1 wiring and the `ALL_PACKAGES` entry unless you pass `--no-wire`). Then update these files to ship it on PyPI and, optionally, as a standalone MCP server.

### Tier 1 — Runtime (dev, always required)

Update the following files and areas:

| File / area | Purpose |
|---|---|
| `src/node_wire_<name>/` | `__init__.py` (required, may be empty), `schema.py`, `logic.py` (with optional `error_map`), optional `action_spec.py`, `README.md` |
| Root `pyproject.toml` | `[project.entry-points."node_wire.connectors"]` for editable dev install. `uv run` rebuilds the editable install on its next call once `pyproject.toml` changes. If you run `.venv/bin/python` or an activated venv directly, run `uv sync` first or the added entry point is not visible. |
| `config/connectors.yaml` | `enabled`, `exposed_via`, `auth:` |
| [`sample.env`](https://github.com/AOT-Technologies/node-wire/blob/main/sample.env) | Commented placeholders for connector secrets, and the connector id added to the `NW_ALLOWED_CONNECTORS` line |
| [Connector catalog](connector-reference.md#connector-catalog) | Add an entry with the same fields as the others |
| Tests | `tests/test_<name>.py`. At minimum, assert three things: the connector instantiates with its `connector_id`, `nw_action_metas()` lists the expected actions, and `auto_register()` (with `NW_ALLOWED_CONNECTORS` set) imports your `logic` module. See the [minimal HTTP connector example](connectors-build.md#minimal-http-connector-end-to-end) and `tests/test_connectors_basic.py`. Run the suite as in [Code quality — Tests](code-quality-compliance.md#tests). |

`auto_register()` discovers the connector via the entry point — no factory branch required.

### Tier 2 — Publishable PyPI package

Update the following files:

| File | Purpose |
|---|---|
| `packages/connectors/<name>/pyproject.toml` | Publishable package metadata, version, entry point — see [Tier 2 templates](#tier-2-templates) |
| `packages/connectors/<name>/README.md` | The PyPI project page — see [`README.md` template](#readmemd-template) |
| `packages/connectors/<name>/LICENSE` | Copy of the repository `LICENSE`; `license-files` ships it in the wheel |
| [`scripts/build-packages.sh`](https://github.com/AOT-Technologies/node-wire/blob/main/scripts/build-packages.sh) | Add path to `ALL_PACKAGES` |
| [`.github/workflows/publish.yml`](https://github.com/AOT-Technologies/node-wire/blob/main/.github/workflows/publish.yml) | Add to the `package` dropdown **and** the `allowed` set — see [CI allowlist updates](#ci-allowlist-updates) |
| [`.github/workflows/github-release.yml`](https://github.com/AOT-Technologies/node-wire/blob/main/.github/workflows/github-release.yml) | Add to `package_paths` list — see [CI allowlist updates](#ci-allowlist-updates) |
| [`.github/workflows/security-pr.yml`](https://github.com/AOT-Technologies/node-wire/blob/main/.github/workflows/security-pr.yml) | Add to the `pip install` list in the connector audit job — see [CI allowlist updates](#ci-allowlist-updates) |
| This doc — [Package inventory](#package-inventory) | Add row |
| Root + all package `pyproject.toml` | Version bump on release via `./scripts/bump-version.py` (`nw-mcp-builder` excluded) |
| `CHANGELOG.md` | Release section (scaffolded by the bump script; fill in notes before tagging) |

### Tier 2 templates

Every connector package uses the same `pyproject.toml` and `README.md` layout, so the PyPI pages read
alike. Only the marked fields change from one connector to the next.

#### `pyproject.toml` template

This is the package's only build file; there is no `setup.py`. Start from the following `pyproject.toml`:

```toml
# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

[project]
name = "node-wire-<dist-name>"
version = "1.1.0"
# "Node Wire connector for <Service>: <the actions, in plain words>"
description = "Node Wire connector for <Service>: <what it does>"
readme = "README.md"
requires-python = ">=3.11"
license = "Apache-2.0"
license-files = ["LICENSE"]
authors = [{ name = "AOT Technologies", email = "opensource@aot-technologies.com" }]
maintainers = [{ name = "AOT Technologies", email = "opensource@aot-technologies.com" }]
# Always node-wire, connector, mcp; then the service and its domain.
keywords = ["node-wire", "connector", "mcp", "<service>", "<domain>"]
classifiers = [
  "Development Status :: 5 - Production/Stable",
  "Framework :: AsyncIO",
  "Intended Audience :: Developers",
  "Operating System :: MacOS",
  "Operating System :: Microsoft :: Windows",
  "Operating System :: POSIX :: Linux",
  "Programming Language :: Python :: 3",
  "Programming Language :: Python :: 3 :: Only",
  "Programming Language :: Python :: 3.11",
  "Programming Language :: Python :: 3.12",
  "Programming Language :: Python :: 3.13",
  "Programming Language :: Python :: Implementation :: CPython",
  "Topic :: Software Development :: Libraries :: Application Frameworks",
  # Plus one or two Topic classifiers for the service's domain, e.g.
  # "Topic :: Communications :: Email".
]

dependencies = [
    "node-wire-runtime>=1.1.0",
    # Add vendor SDK or HTTP client here, for example:
    # "httpx[http2]>=0.27.0,<0.28.0",
]

[project.entry-points."node_wire.connectors"]
<connector_id> = "node_wire_<name>.logic"

[project.urls]
Homepage = "https://github.com/AOT-Technologies/node-wire"
# The connector's setup guide if it has one, else its connector-reference entry.
Documentation = "https://aot-technologies.github.io/node-wire/connector-reference/#<connector_id>"
Repository = "https://github.com/AOT-Technologies/node-wire"
Issues = "https://github.com/AOT-Technologies/node-wire/issues"
Changelog = "https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md"

[build-system]
requires = ["setuptools>=69.0.0", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["../../../src"]
include = ["node_wire_<name>*"]
```

#### `README.md` template

The README is the PyPI project page, so every link is an absolute URL — relative links break on
PyPI. Keep the sections in this order and drop an optional one rather than leave it empty.

````markdown
<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# node-wire-<dist-name>

<One or two sentences: which service, and what an agent or app can do with it.>

Part of [Node Wire](https://github.com/AOT-Technologies/node-wire). Every call runs through the
Node Wire runtime, so it gets input validation, retries with a circuit breaker, scope policy,
a shared error taxonomy, and OpenTelemetry tracing. A Node Wire host exposes each action over REST,
gRPC and MCP.

## Install

```bash
pip install node-wire-<dist-name>
```

This pulls in [`node-wire-runtime`](https://pypi.org/project/node-wire-runtime/). Pre-releases
are published as `X.Y.ZbN`; install one with `pip install --pre node-wire-<dist-name>`. To install
every connector at once, use [`node-wire-connectors`](https://pypi.org/project/node-wire-connectors/).

The wheel is pure Python (`py3-none-any`) and installs on CPython 3.11, 3.12 and 3.13 on any
OS and CPU.

## Actions

The connector registers as `<connector_id>`. MCP tool names are `<connector_id>_<action>`, with
dots replaced by underscores.

| Action | MCP tool | What it does |
|---|---|---|
| `<action>` | `<connector_id>_<action>` | <one line> |

## Configuration

Add the connector to `config/connectors.yaml`:

```yaml
connectors:
  <connector_id>:
    enabled: true
    exposed_via: ["rest", "grpc", "mcp"]
    auth:
      provider: <provider>
      # ...
```

Secrets are read through the configured secret provider (environment variables by default):

| Variable | Purpose |
|---|---|
| `<VAR>` | <what it holds> |

## Usage

Set `NW_ALLOWED_CONNECTORS=<connector_id>` (comma-separate several). Node Wire loads only the
connectors listed there, and discovers them through the `node_wire.connectors` entry point.
A Node Wire host (the REST, gRPC or MCP server, or `ConnectorFactory`) then builds the connector
from the configuration above. See [Use a connector](https://aot-technologies.github.io/node-wire/connectors/).

## Security notes

<Optional. Egress restrictions, sandboxing, privacy of logged data.>

## Documentation

- [Node Wire on GitHub](https://github.com/AOT-Technologies/node-wire): source, issues and full documentation
- [<Service> setup guide](<url>) <!-- optional -->
- [Connector reference](https://aot-technologies.github.io/node-wire/connector-reference/#<connector_id>)
- [Configuration](https://aot-technologies.github.io/node-wire/configuration/)
- [Error codes](https://aot-technologies.github.io/node-wire/errors/)
- [Changelog](https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md)

## License

Apache-2.0. See [LICENSE](https://github.com/AOT-Technologies/node-wire/blob/main/LICENSE).
````

Replace the following:

- `<name>`: the connector's snake_case name, for example, `my_service`.
- `<dist-name>`: the same name in kebab case, for example, `my-service`.
- `<connector_id>`: the entry-point key, the same string used in `config/connectors.yaml` and `NW_ALLOWED_CONNECTORS`.

### CI allowlist updates

Three workflows each hold a hard-coded list of publishable packages. Each workflow file is the
source of truth for its list. Add `packages/connectors/<name>` to each:

| Workflow | Where the list is | Rule |
|---|---|---|
| [`publish.yml`](https://github.com/AOT-Technologies/node-wire/blob/main/.github/workflows/publish.yml) | `on.workflow_dispatch.inputs.package.options` **and** the `allowed` set in the validate step | Both must agree. A path missing from `allowed` fails at validate; a path missing from the dropdown is unreachable. |
| [`github-release.yml`](https://github.com/AOT-Technologies/node-wire/blob/main/.github/workflows/github-release.yml) | `package_paths` in the release-manifest step | Lists what the release manifest covers. |
| [`security-pr.yml`](https://github.com/AOT-Technologies/node-wire/blob/main/.github/workflows/security-pr.yml) | The `pip install` list in the **Install packages and audit tool** step | All listed packages install into one environment that a single `pip-audit` run scans. |

Never add `packages/bindings` or `packages/toolhive` to them. Those are local-only wheels.

### Tier 3 — Standalone MCP server (optional)

Complete Tier 2 (the PyPI wheel) first. The Dockerfile copies pre-built `.whl` files from `packages/connectors/<name>/dist/`; that directory does not exist until you run `bash scripts/build-packages.sh packages/connectors/<name>`.

Use when you need a dedicated Docker/ToolHive image for a single connector (not required for the combined `agents.mcp_entrypoint` server). For the wheel → image mapping see [local-packages-to-images.md](local-packages-to-images.md); for an existing connector's entrypoint/Dockerfile, use `src/agents/<name>_mcp.py` and `docker/<name>/Dockerfile` as templates.

| File | Purpose |
|---|---|
| `src/agents/<name>_mcp.py` | Per-connector MCP agent entrypoint |
| Root `pyproject.toml` | `[project.scripts]` entry, for example, `nw-<kebab-name>` |
| `docker/<name>/Dockerfile` | Demo MCP image |
| [`scripts/build-mcp-images.sh`](https://github.com/AOT-Technologies/node-wire/blob/main/scripts/build-mcp-images.sh) | `docker build` block |
| [`docker-compose.mcp.yml`](https://github.com/AOT-Technologies/node-wire/blob/main/docker-compose.mcp.yml) | Service + `NW_ALLOWED_CONNECTORS` |
| [local-packages-to-images.md](local-packages-to-images.md) | Add a row to the wheel → image table |
| [nw-mcp-builder](cli/nw-mcp-builder.md#supported-connectors) | Add to "Supported connectors" if it also gets a generated host |

---

## Python package build lifecycle

Prerequisites: [uv](https://docs.astral.sh/uv/) (the script uses `uv build`), or `python3 -m pip install build`. No compiler or Docker is needed. Run `bash scripts/build-packages.sh --help` for usage.

### Build all packages (default)

For a single connector (or runtime) from the orchestrator CLI, see [`nw gen-whl`](cli/nw-cli.md), which wraps this script. To build every package, run:

```bash
bash scripts/build-packages.sh
```

This builds each package path listed in `ALL_PACKAGES` in the script (see the [Package inventory](#package-inventory) for the current set). Each package gets one `py3-none-any` wheel. The same wheel goes into the MCP host images (Debian), the `nw gen-stacklok` images (Alpine), and PyPI. Before each build the script clears the package's `build/` directory and old `dist/*.whl` files. Afterwards it checks that the wheel is `py3-none-any` and contains no compiled `.so`/`.pyd` files.

The old platform flags (`--host-only`, `--linux-only`, `--all`, `--musllinux`, `--cibw-linux`) are accepted and ignored, with a note.

### Artifact layout and safe command usage

`scripts/build-packages.sh` writes wheels per package under `packages/**/dist/` (there is no single repo-root `dist/` output).

Before using wildcard wheel commands, clear old wheel artifacts so commands do not accidentally match stale versions:

```bash
rm -f packages/runtime/dist/*.whl
rm -f packages/connectors/stripe/dist/*.whl
```

### Build a single package

Pass the package path:

```bash
bash scripts/build-packages.sh packages/connectors/stripe
```

### Inspect wheel contents

```bash
unzip -l packages/connectors/stripe/dist/node_wire_stripe-*-py3-none-any.whl
# Shows the package's .py modules and its .dist-info metadata
```

### Install from wheels and verify entry points

Install the wheels into a clean environment and list the registered entry points:

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

Check that `auto_register()` loads the installed connectors:

```bash
python -c "
from node_wire_runtime.connector_registry import auto_register
loaded = auto_register()
print('Loaded:', loaded)
"
```

---

## Client consumption model

`pip install node-wire-connectors` installs the runtime and every public connector. The meta package has no extras. A subset is the connector packages themselves:

```bash
pip install node-wire-smtp node-wire-google-drive
```

A downstream client can also name the runtime and individual connectors:

```bash
pip install node-wire-runtime node-wire-stripe node-wire-fhir-epic
```

At startup, `auto_register()` discovers all installed connectors via the `node_wire.connectors` [entry-point group](https://packaging.python.org/en/latest/specifications/entry-points/) — no explicit import list required.

### Runtime loading knobs

Fail-closed connector loading and related env vars are documented in
[configuration.md](configuration.md#required-variables). Packaging clients must
still set `NW_ALLOWED_CONNECTORS` and may set `NW_CONNECTOR_MODULE_PREFIX`
(default `node_wire_`) when embedding wheels.

---

## `connectors.yaml` and secrets

An installed connector runs only if it is `enabled` in `connectors.yaml` and named in
`NW_ALLOWED_CONNECTORS`. Keys: [Connector reference](connector-reference.md#configconnectorsyaml).
Per-connector secrets: [catalog](connector-reference.md#connector-catalog). Secret backends:
[Configuration — Secrets management](configuration.md#secrets-management).
`src/node_wire_runtime/connectors.yaml.sample` is a commented template with every supported field.

---

## Release process (tag-first)

Stable releases are **tag-driven**: create and push the tag first, then publish each
package from it. Betas need no tag — publish derives the pre-release version itself.

- **Stable** (`v1.2.0`) — Create Release Tag → GitHub Release → publish with `channel: release`
- **Beta** — publish with `channel: beta` from any branch or tag (no tag, no GitHub Release)

Both channels use the same `.github/workflows/publish.yml`; its `channel` input
selects the behaviour, and the version always comes from the selected package's
`pyproject.toml` rather than from a tag name. PyPI treats PEP 440 pre-releases as
pre-releases: `pip install node-wire-runtime` stays on stable;
`pip install --pre node-wire-runtime` or an exact pin (for example
`node-wire-runtime==1.2.0b1`) installs a beta.

**Create Release Tag** (`.github/workflows/create-tag.yml`) accepts both
`MAJOR.MINOR.PATCH` and PEP 440 pre-releases (`aN` / `bN` / `rcN`). It checks
that the tag is free, package versions match, and `CHANGELOG.md` has the matching
entry, then pushes `v…`. Stable releases use it with the version from the following steps; [Beta PyPI publish](#beta-pypi-publish) does not use it at all.

### Stable release

#### Step 1 — Prepare the release

1. Bump lockstep package versions with `./scripts/bump-version.py X.Y.Z` (root
   `pyproject.toml`, every `packages/**/pyproject.toml`, the `nw-*` tool
   `pyproject.toml` files and `__version__` strings, and package
   `node-wire-runtime>=…` floors), then run `uv lock` in the root and each
   `nw-*` directory.
2. Fill in the scaffolded `CHANGELOG.md` notes. Create Release Tag and GitHub
   Release both require a dated `## [X.Y.Z] - YYYY-MM-DD` section and a footer
   link `[X.Y.Z]: …/releases/tag/vX.Y.Z` (the bump script scaffolds these when
   missing).
3. Merge to `main` and confirm required CI checks are green.

#### Step 2 — Create the tag

Dispatch **Create Release Tag** in Actions with `version` set to `1.0.0` (no
leading `v`). For stable releases, run this before dispatching "GitHub Release"
in Step 3.

Manual fallback, if you must create the tag by hand (bypasses the preceding validation):

```bash
git tag -a v1.0.0 -m "Release 1.0.0"
git push origin v1.0.0
```

#### Step 3 — Create the GitHub Release

Dispatch **GitHub Release** in Actions with `version` set to `1.0.0` (no leading `v`).
Stable versions only — the workflow rejects PEP 440 pre-releases.

**Workflow:** `.github/workflows/github-release.yml` — manual `workflow_dispatch`
after the tag has been pushed.

The workflow does the following:

1. Validates all package versions match the tag.
2. Verifies `CHANGELOG.md` has the matching section and release link.
3. Generates `sbom.json` (release-level SBOM).
4. Creates `release-manifest.txt` listing all publishable package paths (one per entry in `github-release.yml`'s `package_paths` list).
5. Creates the GitHub Release with changelog notes, SBOM, and manifest attached.

#### Step 4 — Publish packages to PyPI

After the GitHub Release exists, dispatch `.github/workflows/publish.yml` **once per
package** (once per entry in the `allowed` set in that workflow). There is no version
input — the version is read from the selected package's `pyproject.toml`, so dispatch
from the release tag via the **Use workflow from** dropdown.

The workflow takes the following required inputs:

| Input | Example | Notes |
|---|---|---|
| `channel` | `release` | `release` publishes the declared version as-is; `beta` derives the next pre-release |
| `package` | `packages/connectors/stripe` | Dropdown backed by the workflow allowlist |

For `channel: release`, the workflow checks the following before it builds:

- `package` is allowlisted.
- Package `pyproject.toml` declares a stable `X.Y.Z` version.
- That version is not already published on PyPI.
- `CHANGELOG.md` contains the matching release section/link.
- A GitHub Release exists for `v<version>`.

The pipeline runs the following steps:

1. Build the package's single `py3-none-any` wheel in one Ubuntu job (`python -m build --wheel`)
2. Post-build gate: the wheel is `py3-none-any` with no compiled files (`packages/meta`: no modules at all); record SHA256 checksums
3. Install the wheel; `pip-audit` CVE gate (any known vulnerability blocks publish)
4. Publish to PyPI via OIDC Trusted Publisher with Sigstore attestations

The release-level SBOM is attached to the GitHub Release (step 3).
Package publish produces PyPI Sigstore attestations per wheel; it does not
generate a separate SBOM.

The workflow file is kept as `publish.yml` and the
workflow name as `Publish Node Wire package` so the existing PyPI Trusted Publisher
configuration continues to work. Stable and beta both use this same publisher.

If a published release must be withdrawn or replaced, follow
[release-rollback.md](release-rollback.md) (PyPI yank, corrective patch release,
and GitHub tag/release handling).

### Beta PyPI publish

Beta builds ship to the **same PyPI projects** as PEP 440 pre-releases. They
**never** create a GitHub Release (`github-release.yml` rejects pre-release
versions).

Leave `pyproject.toml` on the plain `X.Y.Z` version you are working towards —
publish derives the pre-release suffix itself.

1. Confirm the packages declare the target stable version (for example, `1.2.0`) and CI is green.
2. Dispatch `.github/workflows/publish.yml` from that branch or tag, once per
   allowlisted package, with `channel: beta`. The workflow reads what is already on
   PyPI and publishes the next unused suffix — `1.2.0b1`, then `1.2.0b2`, and so on —
   stamping it into the wheel at build time. No changelog entry, git tag, or GitHub
   Release is required; pipeline steps are otherwise identical to stable.

> [!NOTE]
> `./scripts/bump-version.py` still accepts `aN`/`bN`/`rcN`, but publish
> rejects a pre-release `project.version`. Keep the source tree on `X.Y.Z` and let
> `channel: beta` derive the suffix.

Install a beta with:

```bash
pip install --pre node-wire-runtime
# or pin exactly:
pip install node-wire-runtime==1.2.0b1
```

---

## CI publish flow (Trusted Publisher)

See [Release process (tag-first)](#release-process-tag-first) for the full
end-to-end flow (stable and beta). The package publish workflow is
`.github/workflows/publish.yml`.

---

## Docker demo images

The `docker/*/Dockerfile` images are demonstration templates for running one connector as a
standalone MCP server. They are not production orchestration artefacts. Building them, the wheels
each one needs, and `docker-compose.mcp.yml`: [Local wheels → images](local-packages-to-images.md).

---

## Pre-PyPI local validation checklist

Run these gates before triggering the CI publish workflow (default `build-packages.sh` is enough; `--all` is optional for broader local wheels):

- [ ] `bash scripts/build-packages.sh` exits 0
- [ ] `unzip -l packages/<pkg>/dist/*.whl` shows no `.py` files
- [ ] Install wheels into a clean venv; confirm entry points resolve
- [ ] `auto_register()` loads expected connectors
- [ ] `pytest tests/test_connector_registry.py tests/test_connectors_basic.py` passes
- [ ] Wheel SHA256 checksums recorded and match expected values
- [ ] `package` and `channel` inputs are correct before dispatching, and for `channel: release` the tag and GitHub Release already exist

---

## Related docs

- [Local package to Docker image workflow](local-packages-to-images.md) — build wheels and MCP images locally
- [Release rollback and unpublish runbook](release-rollback.md) — yank and corrective releases
- [Versioning policy](versioning.md) — when to bump MAJOR, MINOR, or PATCH
