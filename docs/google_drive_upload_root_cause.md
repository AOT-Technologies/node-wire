# Google Drive `files.upload` — root cause analysis

## Summary verdict

| Layer | Verdict |
|--------|---------|
| **Connector** | **Not at fault** for the observed errors. It validates and executes `FilesUploadOperation` as documented. |
| **MCP server** | **Behaves as designed**: injects `action` only when absent (`setdefault`); does not override a wrong `action` from the caller. |
| **Agent / LLM** | **Primary fault**: tool arguments did not match the published JSON Schema (`mimeType` vs `mime_type`, `action: "upload"` vs `files.upload`, missing fields). |
| **Groq 429** | **Secondary**: rate limits after many failed retries increased token usage and ended the run. |

**Overall:** **Agent-side** (LLM tool-call payload), not a connector bug.

---

## Evidence from production logs (`terminals/11.txt`)

| Step | Observed `google_drive.files.upload` args (excerpt) | Error |
|------|------------------------------------------------------|--------|
| 1 | `mimeType`, `name`, `parents`, `content` | Extra property `mimeType`; wrong field name for MIME type |
| 2 | `name`, `parents`, `content` (no `mime_type`) | `action` required (schema lists it as required) |
| 3 | `action: "upload"`, … | `mime_type` required / union mismatch |
| 4 | `mime_type` without correct `action` | `action` required |
| 5 | `action: "upload"`, `mime_type`, … | **`'files.upload' was expected`** — wrong discriminator |

These align with **strict Pydantic validation** on `FilesUploadOperation` (`extra="forbid"`, discriminator `action`).

---

## MCP contract (`tools/list`)

For `google_drive.files.upload`, the manifest exposes **per-action** input schema (`FilesUploadOperation`), not the full union:

- **`required`:** `action`, `name`, `mime_type`
- **`action`:** JSON Schema `const: "files.upload"`
- **No `mimeType`** property — only `mime_type`

Source: [`src/bindings/mcp_server/server.py`](../src/bindings/mcp_server/server.py) (`list_tools` + `invoke_tool`), [`src/connectors/manifest.py`](../src/connectors/manifest.py), [`src/connectors/google_drive/schema.py`](../src/connectors/google_drive/schema.py).

---

## Server dispatch behavior

In `McpServer.invoke_tool`:

```python
run_args = normalize_mcp_tool_arguments(connector_id, action, arguments)
if isinstance(connector, SDKConnector):
    run_args.setdefault("action", action)
```

- If the LLM **omits** `action`, the server sets `action` to the suffix from the tool name (`files.upload`) → valid for minimal calls.
- If the LLM sends **`action: "upload"`**, `setdefault` **does not** replace it → validation fails (`union_tag_invalid`), matching log **`'files.upload' was expected`**.

---

## Reproduction (local `invoke_tool`)

| Payload | Result |
|---------|--------|
| `name`, `mime_type`, `parents`, `content` only (no `action`) | **Success** (server adds `action`) — assumes valid Drive credentials |
| `mimeType` instead of `mime_type` | `VALIDATION_ERROR`: `mime_type` missing, `mimeType` extra forbidden |
| `action: "upload"` + valid other fields | `VALIDATION_ERROR`: `union_tag_invalid` (expected tags include `files.upload`, not `upload`) |

---

## Payload matrix

| Issue | Owner | Notes |
|-------|--------|------|
| `mimeType` vs `mime_type` | Agent | Schema only defines `mime_type` |
| Missing `action` when schema says required | Agent / schema UX | Server can still inject `action` if omitted; LLM may omit and still work |
| `action: "upload"` | Agent | Must be literal `files.upload` |
| Nested `file` object | Agent | Not in schema |
| Connector rejects valid `files.upload` payload | N/A | Not observed |

---

## Recommendations (optional follow-ups)

1. **Agent prompt / tool-calling**: Implemented in [`src/agents/toolhive.py`](../src/agents/toolhive.py) — step 2 now states flat JSON, `mime_type`, and correct `action` / no nested `file`.
2. **Normalization** (server): Implemented in [`src/bindings/mcp_server/server.py`](../src/bindings/mcp_server/server.py) — `_normalize_google_drive_files_upload` maps `mimeType` → `mime_type`, coerces `action: "upload"` → `files.upload`, merges a nested `file` dict when canonical keys are absent, and strips `mimeType`.
3. **Groq**: Operational — smaller context, higher TPM tier, or fewer agent steps still help if the model ignores schema; normalization reduces validation failure loops.

---

## References

- [`src/connectors/google_drive/schema.py`](../src/connectors/google_drive/schema.py) — `FilesUploadOperation`
- [`src/bindings/mcp_server/server.py`](../src/bindings/mcp_server/server.py) — `normalize_mcp_tool_arguments`, `invoke_tool`
- [`src/agents/toolhive.py`](../src/agents/toolhive.py) — sends tool args to MCP as returned by the LLM; the MCP server normalizes Google Drive upload aliases before `connector.run`.
