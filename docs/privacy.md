# Privacy Policy and Compliance

The Node-Wire project is committed to ensuring privacy and secure data handling out-of-the-box. As a framework facilitating the orchestration of integrations between Large Language Models (LLMs) and various enterprise/healthcare systems, Node-Wire adheres to strict principles to prevent inadvertent data exposure.

## Core Privacy Principles

1. **No Telemetry or Phone Home:** 
   The Node-Wire open-source framework does not collect, transmit, or store any usage data, telemetry, or analytics. It operates entirely within the infrastructure where it is deployed.

2. **No Data Persistence by Default:**
   Node-Wire acts as an orchestration and routing layer. It does not contain a built-in database for persistent storage of transaction data, logs, or payloads. Any data persistence must be explicitly configured by the user via connectors (e.g., storing a file in Google Drive).

3. **Zero PII/PHI in Source Control:**
   The repository is routinely audited to ensure no Personally Identifiable Information (PII) or Protected Health Information (PHI) is committed to source control. 

## Testing and Dummy Data

All unit tests, integration tests, and example scenarios within the `tests/` and `playground/` directories strictly utilize fabricated placeholder data. 

- **Dummy Emails:** `doc@example.com`, `patient@example.com`, `noreply@node-wire.local`
- **Dummy Patient IDs:** `12724066`, `eXYZ123`
- **Dummy Credentials:** Credentials in tests use explicit `dummy` or `test` prefixes (e.g., `sk_test_dummy`).

If you are contributing to Node-Wire, you **must** ensure that no real data from your environment is included in your commits.

## Logging

By default, Node-Wire logging is configured to provide operational visibility without exposing sensitive payloads. However, when running the MCP Server or REST API in `DEBUG` mode, certain raw HTTP requests and responses may be logged for troubleshooting. 

**Guidance:** Do not run Node-Wire in `DEBUG` logging mode in production environments to prevent the accidental leakage of sensitive data into system logs.

## Security Disclosures

If you discover a potential privacy or security vulnerability within Node-Wire, please do not disclose it publicly. Refer to our [Security Policy](security-gap-report.md) for instructions on how to securely report issues to the maintainers.
