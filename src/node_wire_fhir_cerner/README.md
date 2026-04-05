# FHIR Cerner Connector — Technical Documentation

> **Platform:** Node Wire
> **Standard:** FHIR R4
> **Auth Method:** SMART Backend Services — `private_key_jwt` (RS384)
> **Actions:** `read_patient` · `search_encounter` · `create_document_reference` · `search_document_reference`
> **Source:** `src/connectors/fhir_cerner/`
> **Test Collection:** `postman_fhir_cerner_collection.json`

---

## 1. Architecture Overview

The FHIR Cerner connector is designed to interface with Cerner EHR systems using the FHIR R4 standard. It follows the **"Fat Connector"** architecture, where a single connector class manages multiple related actions.

### Logic Consolidation

A single configuration entry in `connectors.yaml` exposes multiple distinct operations (actions), sharing the same authentication state and underlying infrastructure:

- **`FhirCernerConnector`**: The central controller that encapsulates all shared logic, authentication flows, and specific implementation methods for each action.
- **`_FhirCernerAction`**: A lightweight internal wrapper that inherits from `BaseConnector`. This ensures compatibility with the platform's standard routing and manifest generation while centralizing the actual execution logic.

---

## 2. Authentication Flow

The connector implements the **SMART Backend Services** specification (specifically the `private_key_jwt` flow) required by Cerner:

1. **JWT Creation**: Generates a signed JWT using `RS384` with the provided `cerner_private_key`.
2. **Secret Decryption**: Newlines in the private key (often escaped as `\n` in environment variables) are automatically processed.
3. **Token Exchange**: The JWT is posted to the `cerner_token_url` to obtain a short-lived (5 min) `access_token`.
4. **Request Execution**: The token is attached as a `Bearer` header to subsequent FHIR API calls.

### Required Secrets

| Secret Key | Description |
|---|---|
| `cerner_fhir_base_url` | Base URL for the Cerner FHIR R4 endpoint |
| `cerner_private_key` | RSA private key PEM |
| `cerner_kid` | Key ID registered in the Cerner Code Console |
| `cerner_client_id` | Client ID from Cerner app registration |
| `cerner_token_url` | OAuth2 token endpoint URL |

---

## 3. Supported Operations

The connector exposes four primary actions, each with standardized request/response models.

### `read_patient`

Retrieves patient details either by a direct resource ID or through search parameters (e.g., family/given name).

| Field | Detail |
|---|---|
| **Input** | `resource_id` OR `search_params` |
| **Output** | Raw FHIR Patient resource |

---

### `search_encounter`

Searches for medical encounters for a specific patient. This is often a prerequisite for creating clinical notes, as Cerner requires a valid encounter reference.

| Field | Detail |
|---|---|
| **Input** | `search_params` (e.g., `{"patient": "12345"}`) |
| **Output** | List of Encounter resources and the total count |

---

### `create_document_reference`

The most complex operation, designed to push clinical notes or documents into Cerner. It includes several **Auto-Injection** features to satisfy Cerner's strict validation:

- **Automatic Base64 Encoding**: If `text` is provided instead of `data`, the connector handles the encoding for you.
- **Automatic Charset Injection**: Appends `;charset=utf-8` to `text/*` content types, which is mandatory for Cerner.
- **Required Field Guardrails**:
  - **CodeSet 72**: Validates that document types use the proprietary Cerner system, not generic LOINC.
  - **Attachment Metadata**: Ensures `title` and `creation` (ISO 8601 with time components) are present.
  - **Context Period**: Automatically generates a `context.period` if an `encounter` is provided but no period is specified.
  - **Authentication/Author**: Synchronizes the `author` and `authenticator` fields.

---

### `search_document_reference`

Queries for existing documents for a patient.

| Field | Detail |
|---|---|
| **Input** | `search_params` (e.g., `{"patient": "12345"}`) |
| **Output** | List of DocumentReference resources |

---

## 4. Cerner-Specific Nuances

Cerner's FHIR implementation (especially in the sandbox) has several unique requirements that the connector handles internally:

1. **CodeSet 72**: Unlike many EHRs that accept LOINC codes for document types, Cerner strictly requires proprietary codes from CodeSet 72.
2. **Date Precision**: Cerner requires a full time component (e.g., `.000Z`) for many date fields; generic date strings like `2024-01-01` will be rejected.
3. **Mandatory Authorship**: Clinical notes *must* have an author and an authenticator defined during the `POST` request.
4. **No Identifiers on Create**: While many FHIR resources support external identifiers, Cerner will reject `DocumentReference` creation if an `identifier` field is present in the root of the JSON.

---

## 5. Directory Structure

| File / Path | Purpose |
|---|---|
| `src/connectors/fhir_cerner/logic.py` | Core logic, authentication, and action dispatch |
| `src/connectors/fhir_cerner/schema.py` | Pydantic input/output models and field-level documentation |
| `src/connectors/fhir_cerner/registration.py` | Error mapping and exception handling specifically for Cerner API errors |
| `postman_fhir_cerner_collection.json` | Pre-configured requests to test endpoints end-to-end (at repo root) |

---

## 6. Usage Example — `create_document_reference`

When calling `create_document_reference`, ensure your `type` field uses the Cerner CodeSet 72 system:

```json
{
  "status": "current",
  "subject": "Patient/12345",
  "attachment_title": "Follow-up Note",
  "text": "Patient is recovering well...",
  "type": {
    "coding": [{
      "system": "https://fhir.cerner.com/ec2458f7-xxx/codeSet/72",
      "code": "2820557",
      "display": "Consult Note"
    }]
  },
  "author": [{"reference": "Practitioner/67890"}]
}
```
