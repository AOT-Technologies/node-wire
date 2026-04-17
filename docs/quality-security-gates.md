# Quality And Security Gates

This repository enforces security gates at both PR time and publish time.

## Required CI checks

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

## Notes

- PR checks catch vulnerabilities before merge.
- Scheduled scans catch newly disclosed CVEs even when code did not change.
