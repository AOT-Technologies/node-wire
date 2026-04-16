# Quality and security gates

This document defines how Node Wire enforces security scanning and SonarQube analysis in CI, plus the SonarQube Community Edition setup required for centralized reporting.

## CI quality gates

Workflow: `.github/workflows/quality-gates.yml`

Runs on every pull request and on pushes to `main`/`master`.

Required jobs:

- `bandit`: publishes `bandit-report.json` and fails on high-severity findings.
- `test`: runs `pytest` and produces `coverage.xml`.
- `sonar`: runs SonarQube scan and waits for quality gate result (runs after `bandit` and `test`).

## Local commands

```bash
pip install -e ".[dev,agents]"
bandit -c pyproject.toml -r src --severity-level high
pytest tests/ -v
pre-commit install
pre-commit run --all-files
```

## Connector hardening (Google Drive)

- **`GOOGLE_DRIVE_SA_JSON`**: the Google Drive connector accepts **inline service account JSON only** (no file-path fallback).
- **`files.list` `query`**: validated for basic hygiene (length cap, no ASCII control characters); Google Drive still validates query syntax.

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
