<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Local package -> Docker image workflow

This guide walks through building Node Wire packages locally (as wheels) and using those wheels to build the Docker images in `docker/`.

The Dockerfiles in this repo install local wheel artifacts from `packages/**/dist/*.whl`, so **you must build wheels first**.

---

## Prerequisites

- Python 3.13+ available in your shell
- Docker installed and running
- Build tooling installed:

```bash
python -m pip install --upgrade build cython wheel
```

Run all commands from the repository root. The build context must be the repository root (`.`) so the Dockerfiles' `COPY src/` and `COPY config/` resolve.

---

## 1) Build wheel packages locally

Build all runtime + connector wheels:

```bash
bash scripts/build-packages.sh
```

Build only specific packages (faster when iterating):

```bash
bash scripts/build-packages.sh \
  packages/runtime \
  packages/connectors/smtp \
  packages/connectors/stripe
```

The script (`scripts/build-packages.sh` in default mode, not `--all`):
- builds host wheels and Linux-compatible wheels (via a local `nw-wheel-builder:local` Docker image — not published to a registry; layer-cached after the first build),
- writes artifacts under each package's `dist/` folder,
- fails if any `.py` source files leak into a wheel.

Use `--linux-only` when you only need container wheels, or `--host-only` to skip Docker. For optional local `cibuildwheel` builds (broader wheel matrix on your host), see **Optional: broader wheels** in [packaging.md](packaging.md).

---

## 2) Confirm wheel artifacts exist

Quick check (example for SMTP):

```bash
ls packages/runtime/dist/*.whl
ls packages/connectors/smtp/dist/*.whl
ls packages/connectors/stripe/dist/*.whl
```

If `ls` fails, rebuild that package before continuing.

---

## 3) Build Docker images from local wheels

### Build all MCP connector images

```bash
./scripts/build-mcp-images.sh
```

Each image is tagged `latest` and the version, which defaults to the one in `pyproject.toml`. Pass `--version` to override it:

```bash
./scripts/build-mcp-images.sh --version 1.0.0
```

| Image | Dockerfile |
|---|---|
| `nw-google-drive` | `docker/google-drive/Dockerfile` |
| `nw-smartonfhir-epic` | `docker/fhir-epic/Dockerfile` |
| `nw-smartonfhir-cerner` | `docker/fhir-cerner/Dockerfile` |
| `nw-smtp` | `docker/smtp/Dockerfile` |
| `nw-stripe` | `docker/stripe/Dockerfile` |
| `nw-salesforce` | `docker/salesforce/Dockerfile` |
| `nw-slack` | `docker/slack/Dockerfile` |

### Build one image manually

```bash
docker build -f docker/smtp/Dockerfile -t nw-smtp:local .
```

---

## 4) Run them with Docker Compose

`docker-compose.mcp.yml` starts every MCP server as a stdio container, which is useful for local
validation before configuring ToolHive. It needs the images built and your `.env` populated with
the credentials of the connectors you run. Each service pins `NW_ALLOWED_CONNECTORS` to its own
connector, so a broad value in `.env` does not make a per-connector image import optional
dependencies it does not contain.

```bash
docker compose -f docker-compose.mcp.yml up --build
docker compose -f docker-compose.mcp.yml up --build nw-smartonfhir-epic   # one server only
```

To register these images in ToolHive, see the [ToolHive agent scenario](toolhive_agent_scenario.md).

---

## Wheel requirements by image

Each Dockerfile expects specific wheel files to exist in `dist/`. Keep this table in sync with the Dockerfiles in `docker/` — add a row here whenever you add a Tier 3 standalone MCP image (see the [ship checklist](packaging.md#tier-3-standalone-mcp-server-optional)).

| Image | Required wheels |
|---|---|
| `docker/smtp/Dockerfile` | `packages/runtime/dist/*.whl`, `packages/connectors/smtp/dist/*.whl` |
| `docker/google-drive/Dockerfile` | `packages/runtime/dist/*.whl`, `packages/connectors/google_drive/dist/*.whl` |
| `docker/fhir-epic/Dockerfile` | `packages/runtime/dist/*.whl`, `packages/connectors/fhir_epic/dist/*.whl` |
| `docker/fhir-cerner/Dockerfile` | `packages/runtime/dist/*.whl`, `packages/connectors/fhir_cerner/dist/*.whl` |
| `docker/stripe/Dockerfile` | `packages/runtime/dist/*.whl`, `packages/connectors/stripe/dist/*.whl` |
| `docker/salesforce/Dockerfile` | `packages/runtime/dist/*.whl`, `packages/connectors/salesforce/dist/*.whl` |
| `docker/slack/Dockerfile` | `packages/runtime/dist/*.whl`, `packages/connectors/slack/dist/*.whl` |
| `Dockerfile` (unified MCP server) | runtime + connector wheels (`http_generic`, `stripe`, `smtp`, `slack`, `google_drive`, `fhir_epic`, `fhir_cerner`, `salesforce`) |

---

## Common failures and fixes

### `COPY ... dist/*.whl` failed: no source files were specified

A required wheel is missing. Re-run `scripts/build-packages.sh` for the missing package(s), then rebuild the image.

### Docker build cannot find `src/` or `config/`

Use repo root as build context (`.`):

```bash
docker build -f docker/smtp/Dockerfile -t nw-smtp:local .
```

Do not run `docker build` from inside `docker/<name>/`.

### Docker daemon not running

Start Docker Desktop (or daemon) and retry package/image builds.

---

## Recommended local loop

```bash
# 1) Rebuild changed packages
bash scripts/build-packages.sh packages/runtime packages/connectors/smtp

# 2) Build image(s)
docker build -f docker/smtp/Dockerfile -t nw-smtp:local .

# 3) Verify image exists
docker images --filter reference=nw-smtp
```
