# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

# Quality and security gates

This document defines how Node Wire enforces security scanning in CI.

This repository enforces security gates at both PR time and publish time.

## CI quality gates

Workflow: `.github/workflows/quality-gates.yml`

Runs on every pull request and on pushes to `main`/`master`.

Required jobs:

- `bandit`: writes `bandit-report.json` (with `--exit-zero` so low/medium findings do not fail the job before the gate), prints a log summary, uploads the artifact, then fails only on **high**-severity findings in the enforce step.

Workflow: `.github/workflows/codeql.yml`

Runs GitHub CodeQL static analysis for Python on pull requests, pushes to `main`/`master`, and weekly (Mondays). No repository secrets are required.

Workflow: `.github/workflows/pytest.yml`

Runs the full test suite on **Linux, macOS, and Windows** (Python 3.11 and 3.12
matrix) with coverage on every pull request and push to `main`/`master`.
Playground integration tests remain manual (`workflow_dispatch`) on Ubuntu only.

Workflow: `.github/workflows/lint.yml` also runs `lockfile-check` (`uv lock --check`) to fail PRs when `pyproject.toml` changes without an updated `uv.lock`.

Workflow: `.github/workflows/secret-scan.yml`

Runs [Gitleaks](https://github.com/gitleaks/gitleaks) on pull requests, pushes to
`main`/`master`, weekly (Mondays), and on manual dispatch. The workflow checks
out **full git history** (`fetch-depth: 0`) so secrets in past commits are
scanned, not only the working tree.

Required checks to add in branch protection:

- `Lint and Type Check / Lockfile freshness`
- `Quality gates / Bandit security scan`
- `CodeQL / Analyze (Python)`
- `Secret scan / Gitleaks secret scan`
- `CI – Pytest / Run pytest (ubuntu-latest, Python 3.11)`
- `CI – Pytest / Run pytest (ubuntu-latest, Python 3.12)`
- `CI – Pytest / Run pytest (macos-latest, Python 3.11)`
- `CI – Pytest / Run pytest (macos-latest, Python 3.12)`
- `CI – Pytest / Run pytest (windows-latest, Python 3.11)`
- `CI – Pytest / Run pytest (windows-latest, Python 3.12)`
- `Python package security PR checks / Vulnerability scan (packages/runtime)`
- `Python package security PR checks / Vulnerability scan (packages/connectors/http_generic)`
- `Python package security PR checks / Vulnerability scan (packages/connectors/stripe)`
- `Python package security PR checks / Vulnerability scan (packages/connectors/smtp)`
- `Python package security PR checks / Vulnerability scan (packages/connectors/google_drive)`
- `Python package security PR checks / Vulnerability scan (packages/connectors/fhir_cerner)`
- `Python package security PR checks / Vulnerability scan (packages/connectors/fhir_epic)`
- `Python package security PR checks / Vulnerability scan (packages/connectors/salesforce)`
- `Python package security PR checks / Vulnerability scan (packages/connectors/slack)`

Configure branch protection so pull requests cannot merge unless all required checks pass.

## CVE scanning policy

- PR and push-to-main scanning runs in `.github/workflows/security-pr.yml`.
- Release-time scanning remains in `.github/workflows/publish.yml` as defense in depth.
- The PR/push gate (`security-pr.yml`) runs `pip-audit` with no `--fail-on` threshold, so it **blocks on any vulnerability**. The release workflow (`publish.yml`) uses `pip-audit --fail-on HIGH` as defense in depth.
- Scheduled scans catch newly disclosed CVEs even when code does not change.

**Monorepo install note:** Connector packages under `packages/connectors/*` declare `node-wire-runtime>=1.0.0` as a normal PyPI dependency name. The security workflow installs `packages/runtime` from the checkout **together with** each matrix package (`pip install packages/runtime "<matrix path>"`) so `pip` can resolve `node-wire-runtime` without requiring a published wheel on PyPI. Locally, mirror that when auditing a single connector: `pip install packages/runtime packages/connectors/<name>`.

## Secret scanning

Workflow: `.github/workflows/secret-scan.yml` (Gitleaks).

Policy:

- Scan on every PR and push to `main`/`master`, plus a weekly scheduled run.
- Full repository history is included (`fetch-depth: 0`).
- Findings fail the workflow; remediate by rotating exposed credentials and
  removing secrets from the codebase (never commit live secrets).

### Run locally

Install [Gitleaks](https://github.com/gitleaks/gitleaks) (e.g. `brew install gitleaks`
on macOS), then from the repository root:

```bash
# Working tree (staged + unstaged changes vs HEAD)
gitleaks detect --source . --redact --verbose

# Full git history (matches CI intent)
gitleaks detect --source . --redact --verbose --log-opts="--all"
```

If GitHub Advanced Security secret scanning is enabled at the organization level,
treat it as defense in depth; the in-repo workflow provides auditable CI evidence.

## Run checks locally

```bash
# Install dev tools from committed lockfile
uv sync --frozen --all-extras --dev

# Security gate (matches CI failure threshold)
uv run bandit -c pyproject.toml -r src --severity-level high

# Optional: JSON report + same summary as CI logs
uv run bandit -c pyproject.toml -r src -f json -o bandit-report.json --exit-zero
python scripts/bandit_report_summary.py bandit-report.json

# Tests + coverage (run via pytest.yml in CI)
uv run pytest tests/ -v
```

## Deterministic pytest environment

To keep pytest collection and REST app startup deterministic, `tests/conftest.py` sets a fixed environment before imports:

- `NW_REST_LOAD_DOTENV=false` so REST startup does not merge a repo-root `.env` over test variables.
- `NW_CONFIG_PATH=tests/fixtures/connectors_for_tests.yaml` so optional connectors outside the pytest allowlist remain `enabled: false` (for example `slack` and `salesforce`).
- `NW_ALLOWED_CONNECTORS=http_generic,smtp,stripe,google_drive,fhir_epic,fhir_cerner` so only the supported test connector set is loaded during collection.

Do not rely on `.env` values during pytest collection. The test harness intentionally overrides them so local developer state does not affect CI or test outcomes.

### Pre-commit

```bash
pre-commit install
pre-commit run --all-files
```

## Bandit policy

Bandit is configured in `pyproject.toml` under `[tool.bandit]`.

### Exit codes and CI behavior

By default, **Bandit exits with a non-zero status whenever it reports any finding**, including low and medium severity. That affects `-f json -o ...` the same as text output.

CI splits responsibilities:

1. **JSON artifact + log summary** — `bandit ... -f json -o bandit-report.json --exit-zero` so the workflow always produces the report and runs `scripts/bandit_report_summary.py` for readable logs. Low/medium issues are visible here without failing the job.
2. **Enforcement** — `bandit ... --severity-level high` fails the job only on high-severity findings (matches branch-protection intent).

Locally, mirror CI with the commands in [Run checks locally](#run-checks-locally).

### Scope

Policy:

- Scan target: `src/` (runtime, bindings, in-tree connector implementations installed via the root package).
- Exclude: `.venv`, `venv`, `tests`, `playground`, `dist`, `htmlcov`.
- CI enforcement threshold: `--severity-level high`.
- **Packages tree:** connector distributions under `packages/connectors/*` are audited for CVEs in `.github/workflows/security-pr.yml` (`pip-audit`). Run Bandit against those paths separately if you need SAST on a standalone checkout.

If legacy findings block adoption, create a baseline once and track deltas:

```bash
bandit -c pyproject.toml -r src -f json -o bandit-baseline.json --exit-zero
bandit -c pyproject.toml -r src --baseline bandit-baseline.json --severity-level high
```

## SBOM generation

CycloneDX SBOM (`sbom.json`) is generated by:

- `scripts/run-compliance-checks.sh` for local compliance runs.
- `.github/workflows/github-release.yml` at release time (attached to the GitHub Release).

## Acceptance criteria mapping

- Security scan runs on every PR: enforced by `quality-gates.yml` (Bandit) and `codeql.yml` (CodeQL).
- Builds fail on high-severity Bandit findings: Bandit gate in CI.
- Static analysis visible in GitHub Security tab: CodeQL upload from CI.
- Tests run on every PR: enforced by `pytest.yml` (Linux/macOS/Windows × Python 3.11/3.12).
- Developers run checks locally: documented commands and pre-commit (Bandit).
- Config version-controlled: `pyproject.toml`, `.pre-commit-config.yaml`, workflow files.
