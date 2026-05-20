<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Security & Architecture Gap Report — Node Wire MCP Platform

> **Perspective:** Secure MCP server platform integrator reviewing the runtime and connectors for production readiness.
> **Date:** 2026-04-01
> **Branch:** `feature/python-packages`

---

## Executive Summary

The platform has a solid foundation: clean layered architecture (runtime → connectors → bindings), Pydantic-enforced input validation, OpenTelemetry observability, and resilience patterns (circuit breaker, retry). However, **critical security gaps must be addressed before production use**, particularly around authentication, credential management, PHI/PII logging, and network security.

**Finding counts:** 5 Critical · 10 High · 7 Medium · 5 Low

---

## Severity Definitions

| Severity | Meaning |
|----------|---------|
| **CRITICAL** | Exploit path exists now; immediate remediation required |
| **HIGH** | Significant attack surface; address before production |
| **MEDIUM** | Increases risk; address in next sprint |
| **LOW** | Best-practice gap; backlog item |

---

## CRITICAL Findings

### C1 — Credentials in `.env` Committed to Repository

- **Location:** `.env` (repository root)
- **What's exposed:** `EPIC_PRIVATE_KEY` (RSA private key), `CERNER_PRIVATE_KEY`, `EPIC_CLIENT_ID`, `GROQ_API_KEY`, `SMTP_PASSWORD`, path to `connectorplatform-*.json` service account file
- **Impact:** Anyone with read access to the repo can impersonate the Epic/Cerner OAuth client, read Google Drive, send email as the platform, and call Groq
- **Fix:**
  1. Revoke all exposed credentials immediately and rotate
  2. Add `.env` and `connectorplatform-*.json` to `.gitignore`
  3. Move secrets to a secrets manager (HashiCorp Vault, AWS Secrets Manager, K8s Secrets)

---

### C2 — PHI Logged in Error Paths (HIPAA Violation)

- **Location:** `src/connectors/fhir_epic/logic.py` (~line 485), `src/connectors/fhir_cerner/logic.py` (~line 592)
- **What's logged:** Full FHIR `DocumentReference` payload on failure — contains patient names, birthdates, MRNs, diagnoses
- **Code pattern:**
  ```python
  logger.error("... sent_payload=%s", json.dumps(doc_ref))
  ```
- **Impact:** Violates HIPAA § 164.312(b) audit controls; PHI written to log aggregation systems in plaintext
- **Fix:** Log only resource type, resource ID, and HTTP status code. Implement a `PHIScrubber` log filter for all healthcare connectors

---

### C3 — No Authentication on REST API or gRPC Binding

- **Location:** `src/bindings/rest_api/app.py`, `src/bindings/grpc_server/server.py`
- **What's missing:** Zero authentication or authorization on any endpoint
- **gRPC uses an insecure port:**
  ```python
  server.add_insecure_port(f"[::]:{port}")  # no TLS, no mTLS
  ```
- **Impact:** Any network-adjacent caller can invoke any connector action with no audit trail
- **Fix:**
  - REST: Add API key or OAuth2 bearer token middleware to FastAPI
  - gRPC: Switch to `add_secure_port` with TLS credentials; enforce mTLS for service-to-service

---

### C4 — SSRF via HTTP Generic Connector

- **Location:** `src/connectors/http_generic/schema.py`, `src/connectors/http_generic/logic.py`
- **What's missing:** `HttpUrl` validates URL format but not destination host
- **Attack path:**
  ```json
  { "url": "http://169.254.169.254/latest/meta-data", "method": "GET" }
  ```
  → Returns AWS instance metadata including IAM credentials
- **Fix:** Block RFC-1918, loopback (`127.0.0.0/8`), and link-local (`169.254.0.0/16`) address ranges at the schema validator level; optionally implement an egress allowlist

---

### C5 — Configurable Secret Key Names in SMTP Connector

- **Location:** `src/connectors/smtp/schema.py` (fields `username_secret_key`, `password_secret_key`)
- **Attack path:** Caller provides `"username_secret_key": "STRIPE_API_KEY"` → connector fetches the Stripe key and uses it as an SMTP credential; SMTP auth error may reveal whether the key value exists or its format
- **Impact:** Secret enumeration and partial exfiltration via error side-channel
- **Fix:** Hardcode secret key names inside the connector; remove `username_secret_key` and `password_secret_key` from the public input schema entirely

---

## HIGH Findings

### H1 — OAuth Error Response Body Logged

- **Location:** `src/connectors/fhir_epic/logic.py` (~line 130), `src/connectors/fhir_cerner/logic.py`
- **What's logged:** Full `token_response.text` on OAuth failure — may include client credential reflections, token hints, or infrastructure error details
- **Fix:** Log only the `error` and `error_description` fields from the JSON response

---

### H2 — Unvalidated HTTP Method in Generic Connector

- **Location:** `src/connectors/http_generic/schema.py`
- **Current:** `method: str` — accepts any string value
- **Risk:** Arbitrary or non-standard HTTP methods forwarded to target servers; undefined server behavior
- **Fix:**
  ```python
  method: Literal["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]
  ```

---

### H3 — Stripe Global API Key (Race Condition)

- **Location:** `src/connectors/stripe/logic.py` (~line 48)
- **Pattern:** `stripe.api_key = api_key` mutates global module state
- **Risk:** Concurrent requests clobber each other's API key; Stripe exceptions may include the key value in tracebacks
- **Fix:** Pass `api_key=` explicitly to each Stripe API call rather than setting global state

---

### H4 — Auto-Discovery Loads All Connector Modules Without Allowlist

- **Location:** `src/connectors/__init__.py` (`auto_register()`)
- **Risk:** Any file placed at `src/connectors/*/logic.py` is imported and executed automatically on startup — no explicit allowlist
- **Fix:** Validate each discovered connector ID against an explicit allowlist from `connectors.yaml`; skip unknown modules with a logged warning

---

### H5 — No Rate Limiting on Any Binding

- **Location:** All binding layers (REST, gRPC, MCP)
- **Risk:** Unlimited invocation rate; no protection against DoS, credential-stuffing, or API quota exhaustion at upstream services
- **Fix:** Add `slowapi` (or equivalent) rate-limiting middleware; define per-tenant quotas in connector configuration

---

### H6 — Circuit Breaker Shared Across Tenants

- **Location:** `src/runtime/base_connector.py` (constructor — `self._breaker = CircuitBreaker(...)`)
- **Risk:** One tenant triggering repeated failures opens the circuit breaker for all tenants — classic noisy-neighbor DoS
- **Fix:** Key the circuit breaker on `(connector_id, tenant_id)` rather than connector class alone

---

### H7 — Unvalidated Base64 Content in FHIR Connectors

- **Location:** `src/connectors/fhir_cerner/logic.py`, `src/connectors/fhir_epic/logic.py`
- **Pattern:**
  ```python
  attachment["data"] = params.data  # no format check, no size limit
  ```
- **Risk:** Malformed base64 forwarded to EHR; unbounded payload size enables memory exhaustion
- **Fix:** Add a Pydantic `field_validator` that calls `base64.b64decode(v, validate=True)` and enforces a max size (e.g., 10 MB)

---

### H8 — No Dependency Vulnerability Scanning

- **Location:** `pyproject.toml`, `uv.lock`
- **Gap:** No `pip-audit`, `safety`, or Dependabot configured; several deps use unbounded `>=VERSION` (e.g., `tenacity>=8.2.0`, `fastapi>=0.111.0`, `uvicorn>=0.30.0`)
- **Fix:** Add `pip-audit` to CI pipeline; add upper bounds to all runtime dependencies; add `detect-secrets` as a pre-commit hook

---

### H9 — No Structured Audit Trail for Policy Hook Decisions

- **Location:** `src/runtime/base_connector.py` (policy hook execution block)
- **Gap:** Policy denials are logged at `WARNING` level but not emitted as structured security events queryable by a SIEM
- **Fix:** Emit a structured `POLICY_DENIED` event containing `principal`, `tenant_id`, `connector_id`, `action`, and `reason` to a dedicated audit log sink

---

### H10 — Zero Security Test Coverage

- **Location:** `tests/` (all files)
- **Gaps:** No tests exist for authentication failures, SSRF attempts, malformed payloads, credential leakage in error messages, multi-tenant isolation, rate limit enforcement, or concurrent state safety
- **Fix:** Add a `tests/security/` suite covering at minimum:
  - SSRF via HTTP Generic (`127.0.0.1`, `169.254.169.254`)
  - Secret enumeration via SMTP `username_secret_key`
  - PHI absence in FHIR error log output
  - Circuit breaker isolation across tenants

---

## MEDIUM Findings

### M1 — Stripe Input Has No Validation Bounds

- **Location:** `src/connectors/stripe/schema.py`
- `amount: int` — no minimum (1 cent) or maximum cap
- `currency: str` — no ISO 4217 pattern check
- **Fix:**
  ```python
  amount: int = Field(..., ge=1, le=999_999_999)
  currency: str = Field(..., pattern=r'^[A-Z]{3}$')
  ```

---

### M2 — Config Variable Substitution (`${VAR:default}`) Not Implemented

- **Location:** `src/bindings/factory.py`; `config/connectors.yaml` uses `${VAR:default}` syntax in comments and values
- **Current behavior:** Variables are loaded as literal strings — the `${...}` is never expanded
- **Fix:** Implement regex-based substitution in the YAML loader; raise at startup if a required variable is unset

---

### M3 — Factory Returns `None` on Missing Connector (Silent Failure)

- **Location:** `src/bindings/factory.py` (`get_for_protocol()`)
- **Risk:** A misconfigured connector silently returns `None`; failures surface at request time rather than startup
- **Fix:** Validate all enabled connectors during factory initialization and raise immediately on any misconfiguration

---

### M4 — Hardcoded Timeouts and Circuit Breaker Parameters

- **Location:** `src/connectors/http_generic/logic.py` (`timeout=30.0`), `src/runtime/base_connector.py` (`fail_max=5, reset_timeout=30`)
- **Risk:** A 30-second timeout is inappropriate for large FHIR document uploads; a 5-failure threshold may be too sensitive for high-traffic deployments
- **Fix:** Expose these as per-connector configuration keys in `connectors.yaml`

---

### M5 — OpenTelemetry Trace Data May Export PHI

- **Location:** `src/runtime/observability.py`
- **Risk:** Span attributes populated by FHIR connectors may include patient identifiers that flow unfiltered to the OTLP collector
- **Fix:** Add a `SpanSanitizer` processor that removes or hashes known PHI field names before export

---

### M6 — Google Drive `query` Parameter Accepts Arbitrary String

- **Location:** `src/connectors/google_drive/schema.py` (`query: Optional[str]`)
- **Risk:** No client-side validation; the platform relies entirely on Google's server-side handling of malformed or adversarial query strings
- **Fix:** Document and enforce the allowed query syntax subset; reject queries that don't match a safe pattern

---

### M7 — Service Account File Path Not Sandboxed

- **Location:** `src/connectors/google_drive/logic.py`
- **Pattern:** `Credentials.from_service_account_file(raw_sa.strip())` — path is fully controlled by the env var
- **Risk:** Path traversal if the environment variable is tampered with
- **Fix:** Resolve the path with `Path.resolve()` and assert it falls within the application directory before opening

---

## LOW Findings

### L1 — SMTP Connector Logs Recipient Email Addresses

- **Location:** `src/connectors/smtp/logic.py`
- `"from_email": str(params.from_email)` written to structured log output
- **Risk:** Email addresses are PII; log aggregators retain them indefinitely, creating a compliance liability
- **Fix:** Log only recipient count and domain (e.g., `example.com`), never full addresses

---

### L2 — MCP Manifest Lacks Security Metadata

- **Location:** `src/connectors/manifest.py`
- **Missing:** Required OAuth scopes, auth requirements, per-action rate limits, deprecation status
- **Impact:** LLM clients have no way to determine required permissions before invoking a tool
- **Fix:** Add an optional `security` block to each manifest entry describing required scopes and auth type

---

### L3 — No MCP Prompt Templates Defined

- **Location:** `src/bindings/mcp_server/server.py`
- **Gap:** The MCP spec supports pre-built prompt templates to guide safe, correct tool use
- **Risk:** Without templates, LLM clients must independently discover correct multi-step usage patterns (e.g., FHIR patient lookup → document create)
- **Fix:** Define prompt templates for common connector flows

---

### L4 — No Sampling or Pagination Limits in MCP Binding

- **Location:** `src/bindings/mcp_server/server.py`
- **Gap:** A single `files.list` or FHIR search with a large page size could return megabytes of data in one tool response
- **Fix:** Enforce maximum page sizes at the MCP binding layer; add streaming for large result sets

---

### L5 — PEM Key Reconstruction Is Brittle

- **Location:** `src/connectors/fhir_cerner/logic.py`, `src/connectors/fhir_epic/logic.py`
- **Pattern:** `private_key_str.replace("\\n", "\n")` to reconstruct a PEM key from env var
- **Risk:** Silently produces an invalid key if the env var format is wrong; error only surfaces at JWT signing time
- **Fix:** Parse and validate the key with the `cryptography` library at connector startup; reject the connector if the key is unparseable

---

## Summary Table

| ID | Category | Issue | Severity |
|----|----------|-------|----------|
| C1 | Credentials | `.env` with real secrets committed to repo | CRITICAL |
| C2 | Privacy | PHI logged in FHIR error paths | CRITICAL |
| C3 | AuthN/AuthZ | No authentication on REST or gRPC bindings | CRITICAL |
| C4 | Network | SSRF via HTTP Generic connector | CRITICAL |
| C5 | AuthN | Configurable secret key names in SMTP | CRITICAL |
| H1 | Privacy | OAuth error response body logged | HIGH |
| H2 | Validation | Unvalidated HTTP method in generic connector | HIGH |
| H3 | Concurrency | Stripe global API key mutation (race condition) | HIGH |
| H4 | Supply Chain | All connector modules auto-loaded without allowlist | HIGH |
| H5 | DoS | No rate limiting on any binding | HIGH |
| H6 | Isolation | Circuit breaker shared across all tenants | HIGH |
| H7 | Validation | Unvalidated base64 content in FHIR connectors | HIGH |
| H8 | Dependencies | No CVE scanning; unbounded version ranges | HIGH |
| H9 | Audit | Policy hook denials not structured/auditable | HIGH |
| H10 | Testing | Zero security test coverage | HIGH |
| M1 | Validation | Stripe amount/currency unbounded | MEDIUM |
| M2 | Config | `${VAR}` substitution not implemented | MEDIUM |
| M3 | Reliability | Silent `None` on missing connector config | MEDIUM |
| M4 | Config | Hardcoded timeouts and circuit breaker params | MEDIUM |
| M5 | Privacy | OTel traces may export PHI to collector | MEDIUM |
| M6 | Validation | Drive query accepts arbitrary string | MEDIUM |
| M7 | Path Safety | Service account file path not sandboxed | MEDIUM |
| L1 | Privacy | SMTP logs full recipient email addresses | LOW |
| L2 | MCP | Manifest lacks security metadata (scopes, auth) | LOW |
| L3 | MCP | No MCP prompt templates defined | LOW |
| L4 | MCP | No sampling/pagination limits in MCP binding | LOW |
| L5 | Reliability | Brittle PEM key reconstruction from env var | LOW |

---

## Recommended Remediation Order

### Immediate — before any external network access

1. Revoke all credentials in `.env`; rotate FHIR private keys, Groq key, SMTP password (C1)
2. Remove PHI from FHIR error log lines (C2)
3. Add API key middleware to REST binding (C3)
4. Block RFC-1918 / loopback hosts in HTTP Generic URL validator (C4)
5. Hardcode SMTP secret key names; remove from input schema (C5)

### Before production

6. Add TLS + mTLS to gRPC server (C3 continuation)
7. Add connector allowlist validation in `auto_register()` (H4)
8. Add rate limiting middleware to all bindings (H5)
9. Add `pip-audit` to CI and pin dependency upper bounds (H8)
10. Write `tests/security/` suite (H10)

### Next sprint

11. Implement per-tenant circuit breakers (H6)
12. Add OTLP `SpanSanitizer` for PHI fields (M5)
13. Implement `${VAR:default}` config substitution (M2)
14. Add Stripe `amount`/`currency` field validators (M1)
15. Add manifest `security` metadata block (L2)

---

*Generated: 2026-04-01 | Branch: feature/python-packages*
