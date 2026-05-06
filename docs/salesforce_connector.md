# Salesforce Connector (`src/node_wire_salesforce`)

The Salesforce connector provides a secure, asynchronous interface for managing CRM records (Leads and Contacts). It leverages Node Wire's `OAuth2AuthProvider` to handle token refresh automatically, allowing for seamless integration into agentic workflows and medical-to-CRM pipelines.

## Capabilities

The connector exposes full CRUD (Create, Read, Update, Delete) operations for the two most common Salesforce objects used in healthcare and enterprise outreach:

| Action | Description |
|---|---|
| `create_lead` | Create a new Lead record. Requires `LastName` and `Company`. |
| `read_lead` | Fetch a single Lead record by ID. |
| `update_lead` | Update specific fields on an existing Lead. |
| `delete_lead` | Remove a Lead record. |
| `create_contact` | Create a new Contact record. Requires `LastName`. |
| `read_contact` | Fetch a single Contact record by ID. |
| `update_contact` | Update specific fields on an existing Contact. |
| `delete_contact` | Remove a Contact record. |

## Configuration

Add the following to your `config/connectors.yaml`:

```yaml
connectors:
  salesforce:
    enabled: true
    exposed_via: ["rest", "grpc", "mcp"]
    auth:
      provider: oauth2
      grant_method: refresh_token
      token_url_secret: SALESFORCE_TOKEN_URL
      client_id_secret: SALESFORCE_CLIENT_ID
      client_secret_secret: SALESFORCE_CLIENT_SECRET
      refresh_token_secret: SALESFORCE_REFRESH_TOKEN
```

## Environment Variables

The following secrets must be provided (e.g., in `.env` or via your secret manager):

| Variable | Example |
|---|---|
| `SALESFORCE_INSTANCE_URL` | `https://your-domain.my.salesforce.com` |
| `SALESFORCE_TOKEN_URL` | `https://login.salesforce.com/services/oauth2/token` |
| `SALESFORCE_CLIENT_ID` | `3MVG9...` |
| `SALESFORCE_CLIENT_SECRET` | `A1B2...` |
| `SALESFORCE_REFRESH_TOKEN` | `5Aep...` |

## Example Usage

### REST API

```bash
curl -X POST http://localhost:8000/connectors/salesforce/create_lead \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{
    "LastName": "Doe",
    "Company": "Acme Corp",
    "Email": "john.doe@example.com",
    "Status": "Open - Not Contacted"
  }'
```

### Agentic (MCP)

If registered via MCP, the agent can call `salesforce.create_lead` with the following arguments:

```json
{
  "LastName": "Smith",
  "Company": "HealthTech",
  "Email": "jane@smith.com"
}
```

## Playground Interface

The Node Wire playground includes a **CRM Synchronization** panel specifically for Salesforce. This interface allows you to:

1.  **Toggle between Lead and Contact management**: Use the action dropdown to switch contexts.
2.  **Execute full CRUD operations**: The form dynamically adjusts based on whether you are creating, reading, updating, or deleting a record.
3.  **Real-time Pipeline Visualization**: Watch the synchronization steps (Authentication → Fetch/Update → Verification) in real-time.
4.  **Instant Record Validation**: See the exact Salesforce resource IDs and data returned by the API.

Access the playground at `http://localhost:8000/playground` (when running locally).

## Security Note

- **OAuth2**: Tokens are never stored in plain text in logs. Node Wire's `AuthProvider` handles encryption and secure memory storage.
- **Refresh Token Support**: The connector is configured to use `grant_method: refresh_token`, ensuring it can stay authenticated for long-running agentic tasks.
- **Traceability**: All actions are logged with a `trace_id` for auditing and idempotency tracking.
- **PII Protection**: Ensure your logging levels are set correctly; by default, the connector logs the metadata of the transaction but not the full PII payload.

