<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Code Quality and Compliance

This project uses **Ruff** for linting and formatting, **Mypy** for static type checking, **Bandit** for SAST, **pip-audit** for dependency vulnerability checks, and **REUSE** for open-source licensing compliance.

This page owns the commands you run **locally**. What CI enforces, and which checks branch protection requires, is in [Quality and security gates](quality-security-gates.md).

## Manual usage for developers

Install development dependencies from the committed lockfile:

```bash
uv sync --frozen --extra agents --dev
```

Then run the local quality checks:

- **Check formatting and linting errors:** `uv run ruff check .`
- **Auto-fix and format code:** `uv run ruff check --fix . && uv run ruff format .`
- **Run static type validation:** `uv run mypy`

`mypy` uses the default `files` target from `[tool.mypy]` in `pyproject.toml`, which is currently `src`. Avoid `mypy .`, because it can pull in packaging `setup.py` scripts under `packages/` and produce duplicate-module noise. To include tests explicitly, run:

```bash
mypy src tests
```

## Tests

```bash
uv run pytest tests/ -v
```

`tests/playground/` (integration tests against real connector credentials) is excluded by default
via `--ignore=tests/playground` in `pyproject.toml`'s pytest `addopts`. Run it explicitly with the
relevant secret env vars set (see the `playground-integration` job in `.github/workflows/pytest.yml`):
`uv run pytest tests/playground/ --no-cov -v`.

`tests/conftest.py` fixes the environment before imports so collection is deterministic:
`NW_REST_LOAD_DOTENV=false`, `NW_CONFIG_PATH=tests/fixtures/connectors_for_tests.yaml`, and
`NW_ALLOWED_CONNECTORS` set to the eight publishable connectors. Do not rely on `.env` values
during pytest collection.

**Move your `.env` aside before running the suite.** `NW_REST_LOAD_DOTENV=false` only switches off
the dotenv loads in the REST/MCP bindings. Importing `bindings.rest_api.app` also mounts the
playground, and `playground/scenarios.py` calls `load_dotenv()` without checking that flag. So a
repo-root `.env` still leaks into `os.environ` for every key that conftest or the test has not set
already. With a `.env` copied from `sample.env`, the leaked `NW_RATE_LIMIT_PER_IDENTITY_*` values
(`MAX_REQUESTS=120`) take precedence over the legacy `NW_REST_RATE_LIMIT_MAX_REQUESTS=2` that
`tests/test_rest_rate_limit_enforcement.py` sets. As a result
`test_rest_rate_limit_returns_429_and_retry_after` and
`test_rest_rate_limit_ignores_spoofed_xff_when_proxy_hops_zero` fail with `assert 200 == 429`.
CI has no `.env`, so it runs the suite clean. To do the same locally:

```bash
mv .env .env.off && uv run pytest tests/ -v; mv .env.off .env
```

## Security scans

Bandit, with the same scan roots and failure threshold as CI:

```bash
uv run bandit -c pyproject.toml \
  -r src nw-cli/src nw-mcp-builder/src nw-connector-builder/src \
  --severity-level high

# Optional: JSON report + the same summary CI prints
uv run bandit -c pyproject.toml \
  -r src nw-cli/src nw-mcp-builder/src nw-connector-builder/src \
  -f json -o bandit-report.json --exit-zero
python scripts/bandit_report_summary.py bandit-report.json
```

If legacy findings block adoption, create a baseline once and track deltas with
`--baseline bandit-baseline.json`.

Secret scanning with [Gitleaks](https://github.com/gitleaks/gitleaks) (for example `brew install gitleaks`):

```bash
gitleaks detect --source . --redact --verbose                        # working tree
gitleaks detect --source . --redact --verbose --log-opts="--all"     # full history, as CI does
```

## Docs checks

```bash
uv run --group docs mkdocs build --strict      # dead links, bad anchors, missing nav files
uv run pytest tests/test_docs_lifecycle.py     # lifecycle split, public API, package inventory
```

The rules these enforce are in [Changing the docs](reading.md#changing-the-docs).

## Pre-commit hooks

You can attach `.pre-commit-config.yaml` so checks run before each commit:

```bash
pre-commit install
```

To run all configured hooks across the repository:

```bash
pre-commit run --all-files
```

The current pre-commit setup includes Ruff, Ruff formatting, Mypy, REUSE, and Bandit (plus generic hygiene hooks: trailing-whitespace, end-of-file-fixer, check-yaml).

## Copyright headers and REUSE compliance

This repository enforces open-source licensing compliance using [REUSE](https://reuse.software/). First-party files should contain the appropriate SPDX copyright and license headers.

### Verify compliance

```bash
uv pip install reuse
uv run reuse lint
```

### Add missing headers

If `reuse lint` reports missing headers, you can apply the repository header template with:

```bash
bash scripts/add-license-headers.sh
```

## Dependency lockfile

`uv.lock` is the source of truth for CI and local development installs. CI uses `uv sync --frozen --extra agents --dev`, which refuses to resolve new versions and verifies package hashes from the lockfile.

### Update workflow

1. Edit dependency declarations in `pyproject.toml`.
2. Regenerate the lockfile: `uv lock`
3. Verify freshness: `uv lock --check`
4. Commit both `pyproject.toml` and `uv.lock`.

`DEPENDENCIES.md` is a human-readable license inventory (from `pip-licenses`). `sbom.json` is a CycloneDX SBOM generated by the compliance script (`scripts/run-compliance-checks.sh`) and at release time via `.github/workflows/github-release.yml`.

## Dependency inventory and compliance

To maintain an open-source compliant dependency set, the repository tracks third-party packages and their licenses in `DEPENDENCIES.md`.

### License classification criteria

- **Safe (permissive):** MIT, Apache-2.0, BSD, PSF. Safe for the Apache-2.0 release.
- **Needs review:** Custom or uncommon licenses that require manual review.
- **Risky (copyleft):** GPLv2, GPLv3, AGPL. Not allowed in the runtime application. They may be acceptable only for isolated, non-distributed development tooling.

### Update the inventory and run compliance checks

When adding dependencies or preparing a release, run the unified compliance script:

```bash
bash scripts/run-compliance-checks.sh
```

That script:

1. Syncs the locked environment (`uv sync --frozen --extra agents --dev`).
2. Regenerates `DEPENDENCIES.md`.
3. Generates `sbom.json` (CycloneDX SBOM).
4. Runs **Bandit** for static application security testing (`--severity-level high`).
5. Runs **pip-audit** for dependency vulnerability scanning.
6. Runs **REUSE lint** for licensing compliance.

## Related docs

- [Quality and security gates](quality-security-gates.md)
- [Installation guide](installation.md)
