"""
FastMCP Server Entrypoint
=========================
This module is the main entrypoint for the Node Wire MCP server.
When run, it exposes healthcare workflow tools via the MCP stdio transport:

  • fhir_cerner_read_patient       — fetch a patient from Cerner FHIR R4
  • fhir_cerner_search_patients    — search multiple patients in Cerner
  • fhir_cerner_search_encounters   — search encounters in Cerner
  • fhir_epic_read_patient          — fetch a patient from Epic FHIR R4
  • fhir_epic_search_patients       — search multiple patients in Epic
  • fhir_epic_search_encounters     — search encounters in Epic
  • google_drive_upload_file       — write a file to Google Drive
  • smtp_send_email                — send an email via SMTP

ToolHive manages the container lifecycle, injects secrets as environment
variables, and proxies the stdio MCP stream to HTTP/SSE for clients.

Usage (run directly by ToolHive):
    python -m agents.mcp_entrypoint

Environment variables (injected by ToolHive via --secret flags):
    CERNER_FHIR_BASE_URL, CERNER_CLIENT_ID, CERNER_KID,
    CERNER_PRIVATE_KEY, CERNER_TOKEN_URL, CERNER_SCOPES
    GOOGLE_DRIVE_SA_JSON
    SMTP_USERNAME, SMTP_PASSWORD, SMTP_HOST, SMTP_PORT
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dotenv import load_dotenv

# Load .env variables for local stdio transport
# Try both CWD and script's own folder to be safe
load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agents.mcp_entrypoint")


def _make_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ImportError(
            "mcp SDK not installed. Run: pip install 'node-wire[agents]'"
        ) from exc

    from bindings.factory import ConnectorFactory
    from connectors import auto_register
    from connectors.fhir_cerner.schema import (
        FhirCernerPatientReadInput,
        FhirCernerPatientSearchInput,
        FhirCernerEncounterSearchInput,
    )
    from connectors.fhir_epic.schema import (
        FhirPatientReadInput as FhirEpicPatientReadInput,
        FhirPatientSearchInput as FhirEpicPatientSearchInput,
        FhirEncounterSearchInput as FhirEpicEncounterSearchInput,
    )
    from connectors.google_drive.schema import GoogleDriveOperationInput
    from connectors.smtp.schema import SmtpSendInput

    auto_register()
    factory = ConnectorFactory()
    factory.load()

    mcp = FastMCP("Node Wire")

    # ------------------------------------------------------------------
    # Tool 1: Fetch patient from Cerner FHIR R4
    # ------------------------------------------------------------------

    @mcp.tool(
        name="fhir_cerner_read_patient",
        description=(
            "Fetch a patient's demographic record from Cerner FHIR R4. "
            "Returns name, date of birth, gender, identifiers, and contact details."
        ),
    )
    async def fhir_cerner_read_patient(
        patient_id: str = "",
        family_name: str = "",
        given_name: str = "",
        name: str = "",
        birthdate: str = "",
    ) -> dict:
        """
        Parameters
        ----------
        patient_id : str
            FHIR Patient resource ID (direct lookup — use this if you have it).
        family_name : str
            Patient family/last name (used for search when no ID is known).
        given_name : str
            Patient given/first name.
        name : str
            Full or partial patient name (convenience — use when you only have a
            single combined name string and no split given/family available).
        birthdate : str
            Patient date of birth in YYYY-MM-DD format.
        """
        trace_id = str(uuid.uuid4())
        cerner = factory._connectors.get("fhir_cerner")
        if not cerner:
            raise RuntimeError("fhir_cerner connector not configured")

        if patient_id:
            params = FhirCernerPatientReadInput(action="read_patient", resource_id=patient_id)
        elif family_name or given_name or name:
            params = FhirCernerPatientReadInput(
                action="read_patient",
                given_name=given_name or None,
                family_name=family_name or None,
                name=name or None,
                birthdate=birthdate or None,
            )
        else:
            raise ValueError("Provide patient_id OR at least family_name / given_name / name")

        result = await cerner.internal_execute(params, trace_id=trace_id)
        resource = result.resource

        # Extract a clean summary for the LLM
        name_parts = resource.get("name", [{}])[0]
        full_name = " ".join(
            name_parts.get("given", []) + [name_parts.get("family", "")]
        ).strip()

        # Drastically simplify to keep token count low
        ids = ", ".join([f"{i.get('system')}: {i.get('value')}" for i in resource.get("identifier", [])])
        phones = ", ".join([t.get("value") for t in resource.get("telecom", []) if t.get("system") == "phone"])
        emails = ", ".join([t.get("value") for t in resource.get("telecom", []) if t.get("system") == "email"])
        addr = resource.get("address", [{}])[0]
        full_addr = f"{addr.get('line', [''])[0]}, {addr.get('city', '')}, {addr.get('state', '')} {addr.get('postalCode', '')}".strip(", ")

        return {
            "patient_id": resource.get("id"),
            "full_name": full_name or "Unknown",
            "gender": resource.get("gender"),
            "birth_date": resource.get("birthDate"),
            "address_summary": full_addr,
        }

    # ------------------------------------------------------------------
    # Tool 2: Fetch patient from Epic FHIR R4
    # ------------------------------------------------------------------

    @mcp.tool(
        name="fhir_epic_read_patient",
        description=(
            "Fetch a patient's demographic record from Epic FHIR R4. "
            "Returns name, date of birth, gender, identifiers, and contact details. "
            "Epic IDs typically start with 'e' (e.g. 'e12345')."
        ),
    )
    async def fhir_epic_read_patient(
        patient_id: str = "",
        family_name: str = "",
        given_name: str = "",
        name: str = "",
        birthdate: str = "",
    ) -> dict:
        """
        Parameters
        ----------
        patient_id : str
            FHIR Patient resource ID (Epic specific, usually starts with 'e').
        family_name : str
            Patient family/last name.
        given_name : str
            Patient given/first name.
        name : str
            Full or partial patient name (convenience — use when you only have a
            single combined name string and no split given/family available).
        birthdate : str
            Patient date of birth in YYYY-MM-DD format.
        """
        trace_id = str(uuid.uuid4())
        epic = factory._connectors.get("fhir_epic")
        if not epic:
            raise RuntimeError("fhir_epic connector not configured")

        if patient_id:
            params = FhirEpicPatientReadInput(action="read_patient", resource_id=patient_id)
        elif family_name or given_name or name:
            params = FhirEpicPatientReadInput(
                action="read_patient",
                given_name=given_name or None,
                family_name=family_name or None,
                name=name or None,
                birthdate=birthdate or None,
            )
        else:
            raise ValueError("Provide patient_id OR at least family_name / given_name / name")

        result = await epic.internal_execute(params, trace_id=trace_id)
        resource = result.resource

        # Clean extract for LLM
        name_parts = resource.get("name", [{}])[0]
        full_name = " ".join(
            name_parts.get("given", []) + [name_parts.get("family", "")]
        ).strip()

        addr = resource.get("address", [{}])[0]
        full_addr = f"{addr.get('line', [''])[0]}, {addr.get('city', '')}, {addr.get('state', '')} {addr.get('postalCode', '')}".strip(", ")

        return {
            "patient_id": resource.get("id"),
            "full_name": full_name or "Unknown",
            "gender": resource.get("gender"),
            "birth_date": resource.get("birthDate"),
            "address_summary": full_addr,
            "source": "Epic FHIR",
        }

    # ------------------------------------------------------------------
    # Tool 3: Search patients in Cerner (multi-ID or name-based)
    # ------------------------------------------------------------------

    @mcp.tool(
        name="fhir_cerner_search_patients",
        description=(
            "Search for multiple patients in Cerner FHIR R4. "
            "Pass a comma-separated list of Patient IDs for concurrent lookup, "
            "or supply name/birthdate fields for a name-based search returning all matches."
        ),
    )
    async def fhir_cerner_search_patients(
        patient_ids: str = "",
        family_name: str = "",
        given_name: str = "",
        name: str = "",
        birthdate: str = "",
    ) -> dict:
        """
        Parameters
        ----------
        patient_ids : str
            Comma-separated Patient IDs for concurrent multi-ID lookup
            (e.g. '12345678,87654321'). Takes priority over name fields.
        family_name : str
            Patient family/last name (name-search mode).
        given_name : str
            Patient given/first name (name-search mode).
        name : str
            Full or partial name string — FHIR 'name' token search.
        birthdate : str
            Date of birth in YYYY-MM-DD format (name-search mode).
        """
        trace_id = str(uuid.uuid4())
        cerner = factory._connectors.get("fhir_cerner")
        if not cerner:
            raise RuntimeError("fhir_cerner connector not configured")

        if patient_ids.strip():
            ids = [i.strip() for i in patient_ids.split(",") if i.strip()]
            params = FhirCernerPatientSearchInput(action="search_patients", resource_ids=ids)
        elif family_name or given_name or name or birthdate:
            params = FhirCernerPatientSearchInput(
                action="search_patients",
                given_name=given_name or None,
                family_name=family_name or None,
                name=name or None,
                birthdate=birthdate or None,
            )
        else:
            raise ValueError(
                "Provide patient_ids (comma-separated) OR at least one of "
                "family_name / given_name / name / birthdate"
            )

        result = await cerner.internal_execute(params, trace_id=trace_id)

        summaries = []
        for resource in result.resources:
            name_parts = resource.get("name", [{}])[0]
            full_name = " ".join(
                name_parts.get("given", []) + [name_parts.get("family", "")]
            ).strip()
            summaries.append({
                "patient_id": resource.get("id"),
                "full_name": full_name or "Unknown",
                "gender": resource.get("gender"),
                "birth_date": resource.get("birthDate"),
            })

        return {
            "patients": summaries,
            "total": result.total,
            "errors": result.errors,
        }

    # ------------------------------------------------------------------
    # Tool 4: Search patients in Epic (multi-ID or name-based)
    # ------------------------------------------------------------------

    @mcp.tool(
        name="fhir_epic_search_patients",
        description=(
            "Search for multiple patients in Epic FHIR R4. "
            "Pass a comma-separated list of Patient IDs for concurrent lookup, "
            "or supply name/birthdate fields for a name-based search returning all matches. "
            "Epic IDs typically start with 'e' (e.g. 'e12345')."
        ),
    )
    async def fhir_epic_search_patients(
        patient_ids: str = "",
        family_name: str = "",
        given_name: str = "",
        name: str = "",
        birthdate: str = "",
    ) -> dict:
        """
        Parameters
        ----------
        patient_ids : str
            Comma-separated Patient IDs for concurrent multi-ID lookup
            (e.g. 'eABC,eDEF'). Takes priority over name fields.
        family_name : str
            Patient family/last name (name-search mode).
        given_name : str
            Patient given/first name (name-search mode).
        name : str
            Full or partial name string — FHIR 'name' token search.
        birthdate : str
            Date of birth in YYYY-MM-DD format (name-search mode).
        """
        trace_id = str(uuid.uuid4())
        epic = factory._connectors.get("fhir_epic")
        if not epic:
            raise RuntimeError("fhir_epic connector not configured")

        if patient_ids.strip():
            ids = [i.strip() for i in patient_ids.split(",") if i.strip()]
            params = FhirEpicPatientSearchInput(action="search_patients", resource_ids=ids)
        elif family_name or given_name or name or birthdate:
            params = FhirEpicPatientSearchInput(
                action="search_patients",
                given_name=given_name or None,
                family_name=family_name or None,
                name=name or None,
                birthdate=birthdate or None,
            )
        else:
            raise ValueError(
                "Provide patient_ids (comma-separated) OR at least one of "
                "family_name / given_name / name / birthdate"
            )

        result = await epic.internal_execute(params, trace_id=trace_id)

        summaries = []
        for resource in result.resources:
            name_parts = resource.get("name", [{}])[0]
            full_name = " ".join(
                name_parts.get("given", []) + [name_parts.get("family", "")]
            ).strip()
            summaries.append({
                "patient_id": resource.get("id"),
                "full_name": full_name or "Unknown",
                "gender": resource.get("gender"),
                "birth_date": resource.get("birthDate"),
                "source": "Epic FHIR",
            })

        return {
            "patients": summaries,
            "total": result.total,
            "errors": result.errors,
        }

    # ------------------------------------------------------------------
    # Tool 5: Search encounters in Cerner FHIR R4
    # ------------------------------------------------------------------

    @mcp.tool(
        name="fhir_cerner_search_encounters",
        description=(
            "Search for encounters in Cerner FHIR R4. "
            "Returns a list of encounter summaries for a given patient or filter."
        ),
    )
    async def fhir_cerner_search_encounters(
        patient_id: str = "",
        status: str = "",
        date: str = "",
    ) -> dict:
        """
        Parameters
        ----------
        patient_id : str
            Cerner Patient ID to find encounters for.
        status : str
            Filter by encounter status (e.g. 'finished', 'in-progress').
        date : str
            Filter by date or date range (e.g. '2024', 'ge2023-01-01').
        """
        trace_id = str(uuid.uuid4())
        cerner = factory._connectors.get("fhir_cerner")
        if not cerner:
            raise RuntimeError("fhir_cerner connector not configured")

        params = FhirCernerEncounterSearchInput(
            action="search_encounter",
            patient_id=patient_id or None,
            status=status or None,
            date=date or None,
        )

        result = await cerner.internal_execute(params, trace_id=trace_id)

        summaries = []
        for resource in result.resources:
            summaries.append({
                "encounter_id": resource.get("id"),
                "status": resource.get("status"),
                "class": resource.get("class", {}).get("display"),
                "period_start": resource.get("period", {}).get("start"),
                "period_end": resource.get("period", {}).get("end"),
                "type": resource.get("type", [{}])[0].get("text"),
            })

        return {
            "encounters": summaries,
            "total": result.total,
        }

    # ------------------------------------------------------------------
    # Tool 6: Search encounters in Epic FHIR R4
    # ------------------------------------------------------------------

    @mcp.tool(
        name="fhir_epic_search_encounters",
        description=(
            "Search for encounters in Epic FHIR R4. "
            "Returns a list of encounter summaries for a given patient or filter. "
            "Epic IDs typically start with 'e' (e.g. 'e12345')."
        ),
    )
    async def fhir_epic_search_encounters(
        patient_id: str = "",
        status: str = "",
        date: str = "",
    ) -> dict:
        """
        Parameters
        ----------
        patient_id : str
            Epic Patient ID to find encounters for.
        status : str
            Filter by encounter status (e.g. 'finished').
        date : str
            Filter by date or date range.
        """
        trace_id = str(uuid.uuid4())
        epic = factory._connectors.get("fhir_epic")
        if not epic:
            raise RuntimeError("fhir_epic connector not configured")

        params = FhirEpicEncounterSearchInput(
            action="search_encounter",
            patient_id=patient_id or None,
            status=status or None,
            date=date or None,
        )

        result = await epic.internal_execute(params, trace_id=trace_id)

        summaries = []
        for resource in result.resources:
            summaries.append({
                "encounter_id": resource.get("id"),
                "status": resource.get("status"),
                "class": resource.get("class", {}).get("display"),
                "period_start": resource.get("period", {}).get("start"),
                "period_end": resource.get("period", {}).get("end"),
                "type": resource.get("type", [{}])[0].get("text"),
            })

        return {
            "encounters": summaries,
            "total": result.total,
            "source": "Epic FHIR",
        }

    # ------------------------------------------------------------------
    # Tool 7: Upload a file to Google Drive
    # ------------------------------------------------------------------

    @mcp.tool(
        name="google_drive_upload_file",
        description=(
            "Upload a text file to Google Drive. "
            "Returns the file ID and a shareable web view link."
        ),
    )
    async def google_drive_upload_file(
        file_name: str,
        content: str,
        folder_id: str = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", ""),
        mime_type: str = "text/plain",
    ) -> dict:
        """
        Parameters
        ----------
        file_name : str
            Name for the file in Google Drive (e.g. 'patient_summary_12345.txt').
        content : str
            UTF-8 text content to write into the file.
        folder_id : str
            Optional Google Drive folder ID to place the file in.
        mime_type : str
            MIME type (default: text/plain).
        """
        trace_id = str(uuid.uuid4())
        drive = factory._connectors.get("google_drive")
        if not drive:
            raise RuntimeError("google_drive connector not configured")

        payload: dict = {
            "action": "files.upload",
            "name": file_name,
            "mime_type": mime_type,
            "content": content,
        }
        if folder_id:
            payload["parents"] = [folder_id]

        params = GoogleDriveOperationInput(**payload)
        result = await drive.internal_execute(params, trace_id=trace_id)

        raw = result.raw
        return {
            "file_id": raw.get("id"),
            "file_name": raw.get("name"),
            "web_view_link": raw.get("webViewLink"),
            "description": result.description,
        }

    # ------------------------------------------------------------------
    # Tool 4: Send email via SMTP
    # ------------------------------------------------------------------

    @mcp.tool(
        name="smtp_send_email",
        description=(
            "Send an email to a recipient via SMTP. "
            "Credentials are picked up from environment variables."
        ),
    )
    async def smtp_send_email(
        to_email: str,
        subject: str,
        body: str,
        from_email: str = "",
    ) -> dict:
        """
        Parameters
        ----------
        to_email : str
            Recipient email address.
        subject : str
            Email subject line.
        body : str
            Plain-text email body.
        from_email : str
            Sender address — defaults to SMTP_USERNAME env var if empty.
        """
        trace_id = str(uuid.uuid4())
        smtp = factory._connectors.get("smtp")
        if not smtp:
            raise RuntimeError("smtp connector not configured")

        smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip(" '\"")
        smtp_port_raw = os.environ.get("SMTP_PORT", "587").strip(" '\"")
        smtp_port = int(smtp_port_raw)
        smtp_use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
        
        # Guardrail: Handle placeholder strings from LLM or empty input
        sender = from_email.strip(" '\"")
        if not sender or "@" not in sender or "system_default" in sender:
            sender = (os.environ.get("FROM_EMAIL") or os.environ.get("SMTP_USERNAME") or "noreply@node-wire.local").strip(" '\"")
            
        # Pydantic EmailStr does not like "Name <email@addr.com>"
        # Extract just the email part if needed
        import re
        def _extract_email(s: str) -> str:
            match = re.search(r"<(.+?)>", s)
            return match.group(1) if match else s.strip()

        sender = _extract_email(sender)
        recipient = _extract_email(to_email)
        
        logger.info("SMTP Tool | from=%s to=%s subject=%s", sender, recipient, subject)

        params = SmtpSendInput(
            host=smtp_host,
            port=smtp_port,
            use_tls=smtp_use_tls,
            username_secret_key="SMTP_USERNAME",
            password_secret_key="SMTP_PASSWORD",
            from_email=sender,
            to=[recipient],
            subject=subject,
            body=body,
        )
        result = await smtp.internal_execute(params, trace_id=trace_id)
        return {"sent": result.sent, "message_id": result.message_id}

    return mcp


def main() -> None:
    server = _make_server()
    logger.info("Starting Node Wire MCP server (stdio transport)")
    server.run()  # stdio — ToolHive proxies this to HTTP/SSE


if __name__ == "__main__":
    main()
