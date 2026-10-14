<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# HIPAA compliance considerations

Node Wire provides connectors for healthcare systems, including Epic and Cerner FHIR APIs. While Node Wire is designed with security in mind, deploying Node Wire in a healthcare environment to process Protected Health Information (PHI) requires careful consideration to maintain compliance with the Health Insurance Portability and Accountability Act (HIPAA).

!!! warning
    Node Wire is a software framework, not a managed service. You are solely responsible
    for ensuring that your deployment, configuration, and infrastructure meet all
    applicable HIPAA requirements.

## Business associate agreements (BAAs)

Since Node Wire acts as a middleware layer routing data between various systems (for example, your EHR, your LLM provider, and external services), you must have a Business Associate Agreement (BAA) in place with **every** third-party service provider that touches PHI.

- **LLM Providers:** If you are using OpenAI, Anthropic, Google, or Groq to process PHI via Node Wire agents, you must have a BAA signed with that provider and ensure you are using their HIPAA-eligible endpoints/models.
- **Hosting Infrastructure:** If you deploy Node Wire on AWS, Azure, Google Cloud, or another cloud provider, you must have a BAA with the hosting provider.
- **External Connectors:** If you use connectors like SMTP or Google Drive to send or store PHI, those services must also be covered under a BAA.

## Data in transit (encryption)

All network traffic involving PHI must be encrypted.

- **EHR Communication:** Node Wire's FHIR connectors use HTTPS/TLS to communicate with Epic and Cerner APIs.
- **Client Communication:** When deploying the Node Wire REST API or MCP Server, you must place it behind a reverse proxy (for example, Nginx, Traefik) or API Gateway configured with strict TLS 1.2+ encryption.

## Data at rest (persistence)

Node Wire itself does not include a database and does not persistently store PHI. It processes data in memory during execution. However, consider the following:

- **Logs:** Ensure that your logging infrastructure does not capture PHI. Node Wire's default `INFO` logging levels do not log payloads, but running in `DEBUG` or `TRACE` mode may expose PHI to logs. You must configure your logging systems to redact PHI or ensure the logging environment is HIPAA-compliant.
- **Caching:** If you implement caching layers on top of Node Wire, ensure the cache is encrypted at rest.

## Authentication and authorization

- **API Keys & JWTs:** Node Wire's REST API supports API keys and JWTs. Ensure these secrets are strong, rotated regularly, and never hardcoded in source control.
- **OAuth 2.0 / SMART on FHIR:** The FHIR connectors rely on the underlying authentication provided by the EHR. Ensure that the service accounts or client applications registered in Epic/Cerner are provisioned with the principle of least privilege, granting access only to the specific FHIR resources required by the agents.

## Safe secret management

Do not store credentials (for example, `client_secret`, API keys) in plain text environment files in production. Out of the box, `NW_SECRET_BACKEND` selects between `env` (default) and `aws_env` (AWS Secrets Manager + env fallback via `ChainedSecretProvider`) — see [Configuration — Secrets management](../configuration.md#secrets-management). HashiCorp Vault and Azure Key Vault provider classes exist in `node_wire_runtime.secrets` and ship as optional extras, but are not wired into `NW_SECRET_BACKEND`; using them requires custom composition code. You should use a secure secret management solution to inject credentials at runtime.

## Related

- [Privacy policy and compliance](../privacy.md): how Node Wire handles data and logs.
- [Configuration](../configuration.md): secret backends and security settings.
