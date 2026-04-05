# Local package -> Docker image workflow

This guide walks through building Node Wire packages locally (as wheels) and using those wheels to build the Docker images in `docker/`.

The Dockerfiles in this repo install local wheel artifacts from `packages/**/dist/*.whl`, so **you must build wheels first**.

---

## Prerequisites

- Python 3.12 available in your shell
- Docker installed and running
- Build tooling installed:

```bash
python -m pip install --upgrade build cython wheel
```

Run all commands from the repository root:

```bash
cd /path/to/vinaayakh-node-wire
```

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
  packages/connectors/smtp
```

The script:
- builds host wheels and Linux-compatible wheels,
- writes artifacts under each package's `dist/` folder,
- fails if any `.py` source files leak into a wheel.

---

## 2) Confirm wheel artifacts exist

Quick check (example for SMTP):

```bash
ls packages/runtime/dist/*.whl
ls packages/connectors/smtp/dist/*.whl
```

If `ls` fails, rebuild that package before continuing.

---

## 3) Build Docker images from local wheels

### Build all MCP connector images

```bash
./scripts/build-mcp-images.sh
```

With explicit version tag:

```bash
./scripts/build-mcp-images.sh --version 0.1.0
```

This builds:
- `nw-google-drive`
- `nw-smartonfhir-epic`
- `nw-smartonfhir-cerner`
- `nw-smtp`

### Build one image manually

```bash
docker build -f docker/smtp/Dockerfile -t nw-smtp:local .
```

---

## Wheel requirements by image

Each Dockerfile expects specific wheel files to exist in `dist/`:

| Image | Required wheels |
|---|---|
| `docker/smtp/Dockerfile` | `packages/runtime/dist/*.whl`, `packages/connectors/smtp/dist/*.whl` |
| `docker/google-drive/Dockerfile` | `packages/runtime/dist/*.whl`, `packages/connectors/google_drive/dist/*.whl` |
| `docker/fhir-epic/Dockerfile` | `packages/runtime/dist/*.whl`, `packages/connectors/fhir_epic/dist/*.whl` |
| `docker/fhir-cerner/Dockerfile` | `packages/runtime/dist/*.whl`, `packages/connectors/fhir_cerner/dist/*.whl` |
| `Dockerfile` (unified MCP server) | runtime + all connector wheels (`http_generic`, `stripe`, `smtp`, `google_drive`, `fhir_epic`, `fhir_cerner`) |

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
docker images | rg "nw-smtp"
```

