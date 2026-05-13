# Quality and security gates

This document defines how Node Wire enforces security scanning and SonarQube analysis in CI, plus the SonarQube Community Edition setup required for centralized reporting.

This repository enforces security gates at both PR time and publish time.

## CI quality gates

Workflow: `.github/workflows/quality-gates.yml`

Runs on every pull request and on pushes to `main`/`master`.

Required jobs:

- `bandit`: publishes `bandit-report.json` and fails on high-severity findings.
- `test`: runs `pytest` and produces `coverage.xml`.
- `sonar`: runs SonarQube scan and waits for quality gate result (runs after `bandit` and `test`).

Required checks to add in branch protection:

- `Quality gates / Bandit security scan`
- `Quality gates / Tests and coverage`
- `Python package security PR checks / Vulnerability scan (packages/runtime)`
- `Python package security PR checks / Vulnerability scan (packages/connectors/http_generic)`
- `Python package security PR checks / Vulnerability scan (packages/connectors/stripe)`
- `Python package security PR checks / Vulnerability scan (packages/connectors/smtp)`
- `Python package security PR checks / Vulnerability scan (packages/connectors/google_drive)`
- `Python package security PR checks / Vulnerability scan (packages/connectors/fhir_cerner)`
- `Python package security PR checks / Vulnerability scan (packages/connectors/fhir_epic)`

Configure branch protection so pull requests cannot merge unless all required checks pass.

## CVE scanning policy

- PR and push-to-main scanning runs in `.github/workflows/security-pr.yml`.
- Release-time scanning remains in `.github/workflows/publish.yml` as defense in depth.
- `pip-audit --fail-on HIGH` is the vulnerability gate threshold.
- Scheduled scans catch newly disclosed CVEs even when code does not change.

**Monorepo install note:** Connector packages under `packages/connectors/*` declare `node-wire-runtime>=0.1.0` as a normal PyPI dependency name. The security workflow installs `packages/runtime` from the checkout **together with** each matrix package (`pip install packages/runtime "<matrix path>"`) so `pip` can resolve `node-wire-runtime` without requiring a published wheel on PyPI. Locally, mirror that when auditing a single connector: `pip install packages/runtime packages/connectors/<name>`.

## Local commands

```bash
pip install -e ".[dev,agents]"
bandit -c pyproject.toml -r src --severity-level high
pytest tests/ -v
pre-commit install
pre-commit run --all-files
```

## Local Sonar scan with Docker

After generating `coverage.xml`, run scanner from the repository root:

```bash
docker run --rm \
  -e SONAR_TOKEN=YOUR_TOKEN \
  -v "G:\SPACE\node-wire:/usr/src" \
  -w /usr/src \
  sonarsource/sonar-scanner-cli \
  -Dsonar.host.url=http://host.docker.internal:9000 \
  -Dsonar.token=YOUR_TOKEN
```

## Bandit policy

Bandit is configured in `pyproject.toml` under `[tool.bandit]`.

Policy:

- Scan target: `src/`.
- Exclude: `.venv`, `venv`, `tests`, `playground`, `dist`, `htmlcov`.
- CI enforcement threshold: `--severity-level high`.

If legacy findings block adoption, create a baseline once and track deltas:

```bash
bandit -c pyproject.toml -r src -f json -o bandit-baseline.json
bandit -c pyproject.toml -r src --baseline bandit-baseline.json --severity-level high
```

## SonarQube Community Edition setup

### 1) Run SonarQube CE (example Docker)

```bash
docker volume create sonarqube_data
docker volume create sonarqube_logs
docker volume create sonarqube_extensions

docker run -d --name sonarqube \
  -p 9000:9000 \
  -v sonarqube_data:/opt/sonarqube/data \
  -v sonarqube_logs:/opt/sonarqube/logs \
  -v sonarqube_extensions:/opt/sonarqube/extensions \
  sonarqube:lts-community
```

For production, place SonarQube behind HTTPS/reverse proxy and persistent backup strategy.

### 2) Create project and token

1. Open SonarQube UI (`http://<host>:9000`).
2. Create project key `node-wire` (or update `sonar-project.properties` if using a different key).
3. Generate project analysis token.

### 3) Configure GitHub secrets

In repository settings, add:

- `SONAR_HOST_URL`
- `SONAR_TOKEN`

### 4) Configure quality gate

Create or update a quality gate to enforce at minimum:

- No new blocker issues.
- No new critical vulnerabilities.
- Coverage on new code >= 80%.

Attach the gate to the Node Wire project.

## Acceptance criteria mapping

- Security scan runs on every PR: enforced by `quality-gates.yml` (Bandit).
- Builds fail on high-severity Bandit findings: Bandit gate in CI.
- SonarQube dashboard visible: SonarQube CE project + scanner upload from CI.
- Coverage visible in SonarQube: `pytest-cov` generates `coverage.xml`, scanner consumes it via `sonar.python.coverage.reportPaths`.
- Developers run checks locally: documented commands and pre-commit (Bandit).
- Config version-controlled: `pyproject.toml`, `.pre-commit-config.yaml`, `sonar-project.properties`, workflow file.
