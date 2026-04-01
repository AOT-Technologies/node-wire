"""
FastMCP Server Entrypoint — SMART on FHIR (Cerner)
===================================================
Standalone MCP server that dynamically registers every action exposed by
the fhir_cerner connector:

  • fhir_cerner_read_patient              — fetch a single Patient by ID or name search
  • fhir_cerner_search_patients           — fetch multiple Patients (fan-out or name search)
  • fhir_cerner_search_encounter          — search Encounters by patient / status / date
  • fhir_cerner_create_document_reference — create a FHIR DocumentReference
  • fhir_cerner_search_document_reference — search FHIR DocumentReferences

New actions added to the connector are automatically picked up at startup —
no changes to this file are required.

Usage:
    python -m agents.fhir_cerner_mcp
"""
from __future__ import annotations

import logging
import os
import uuid

from dotenv import load_dotenv

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agents.fhir_cerner_mcp")


def _make_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ImportError("mcp SDK not installed. Run: pip install 'node-wire[agents]'") from exc

    from bindings.factory import ConnectorFactory
    from connectors import auto_register
    from connectors.fhir_cerner.schema import (
        FhirCernerDocumentReferenceCreateInput,
        FhirCernerDocumentReferenceSearchInput,
        FhirCernerEncounterSearchInput,
        FhirCernerPatientReadInput,
        FhirCernerPatientSearchInput,
    )

    auto_register()
    factory = ConnectorFactory()
    factory.load()

    mcp = FastMCP("nw-smartonfhir-cerner")

    def _get_connector():
        cerner = factory._connectors.get("fhir_cerner")
        if not cerner:
            raise RuntimeError("fhir_cerner connector not configured")
        return cerner

    # ------------------------------------------------------------------
    # Tool: fhir_cerner_read_patient
    # ------------------------------------------------------------------
    @mcp.tool(
        name="fhir_cerner_read_patient",
        description=(
            "Fetch a single patient's demographic record from Cerner FHIR R4. "
            "Provide patient_id for a direct lookup, or family_name/given_name/name "
            "for a name-based search. "
            "Note: Cerner sandbox name search is case-sensitive."
        ),
    )
    async def fhir_cerner_read_patient(
        patient_id: str = "",
        family_name: str = "",
        given_name: str = "",
        name: str = "",
        birthdate: str = "",
    ) -> dict:
        trace_id = str(uuid.uuid4())
        action = _get_connector().get_action("read_patient")

        if patient_id:
            params = FhirCernerPatientReadInput(resource_id=patient_id)
        elif family_name or given_name or name:
            params = FhirCernerPatientReadInput(
                family_name=family_name or None,
                given_name=given_name or None,
                name=name or None,
                birthdate=birthdate or None,
            )
        else:
            raise ValueError("Provide patient_id OR at least one of family_name / given_name / name")

        result = await action.internal_execute(params, trace_id=trace_id)
        resource = result.resource

        name_parts = resource.get("name", [{}])[0]
        full_name = " ".join(name_parts.get("given", []) + [name_parts.get("family", "")]).strip()
        addr = resource.get("address", [{}])[0]
        full_addr = (
            f"{addr.get('line', [''])[0]}, {addr.get('city', '')}, "
            f"{addr.get('state', '')} {addr.get('postalCode', '')}"
        ).strip(", ")

        return {
            "patient_id": resource.get("id"),
            "full_name": full_name or "Unknown",
            "gender": resource.get("gender"),
            "birth_date": resource.get("birthDate"),
            "address_summary": full_addr,
            "source": "Cerner FHIR",
        }

    # ------------------------------------------------------------------
    # Tool: fhir_cerner_search_patients
    # ------------------------------------------------------------------
    @mcp.tool(
        name="fhir_cerner_search_patients",
        description=(
            "Search / fetch multiple patients from Cerner FHIR R4. "
            "Mode 1 — pass a comma-separated list of patient IDs in resource_ids for a concurrent "
            "fan-out lookup. "
            "Mode 2 — pass family_name, given_name, name, and/or birthdate for a name-based "
            "FHIR search that returns all matching Bundle entries. "
            "Cerner sandbox name search is case-sensitive. "
            "Partial failures in Mode 1 are captured in the 'errors' list rather than raising."
        ),
    )
    async def fhir_cerner_search_patients(
        resource_ids: str = "",
        family_name: str = "",
        given_name: str = "",
        name: str = "",
        birthdate: str = "",
    ) -> dict:
        trace_id = str(uuid.uuid4())
        action = _get_connector().get_action("search_patients")

        ids_list = [i.strip() for i in resource_ids.split(",") if i.strip()] if resource_ids else None

        params = FhirCernerPatientSearchInput(
            resource_ids=ids_list,
            family_name=family_name or None,
            given_name=given_name or None,
            name=name or None,
            birthdate=birthdate or None,
        )

        result = await action.internal_execute(params, trace_id=trace_id)
        return {
            "resources": result.resources,
            "total": result.total,
            "errors": result.errors,
            "source": "Cerner FHIR",
        }

    # ------------------------------------------------------------------
    # Tool: fhir_cerner_search_encounter
    # ------------------------------------------------------------------
    @mcp.tool(
        name="fhir_cerner_search_encounter",
        description=(
            "Search FHIR Encounter resources in Cerner R4. "
            "Filter by patient_id (maps to the FHIR 'patient' parameter), encounter "
            "status (e.g. 'finished', 'arrived'), and/or date / date range "
            "(e.g. '2024', 'gt2023-01-01'). "
            "At least one filter must be provided."
        ),
    )
    async def fhir_cerner_search_encounter(
        patient_id: str = "",
        status: str = "",
        date: str = "",
    ) -> dict:
        trace_id = str(uuid.uuid4())
        action = _get_connector().get_action("search_encounter")

        if not patient_id and not status and not date:
            raise ValueError("Provide at least one of patient_id, status, or date")

        params = FhirCernerEncounterSearchInput(
            patient_id=patient_id or None,
            status=status or None,
            date=date or None,
        )

        result = await action.internal_execute(params, trace_id=trace_id)
        return {
            "resources": result.resources,
            "total": result.total,
            "source": "Cerner FHIR",
        }

    # ------------------------------------------------------------------
    # Tool: fhir_cerner_create_document_reference
    # ------------------------------------------------------------------
    @mcp.tool(
        name="fhir_cerner_create_document_reference",
        description=(
            "Create a FHIR DocumentReference resource in Cerner R4. "
            "Required: status ('current'), subject (Patient reference, e.g. 'Patient/12345678'). "
            "Provide text (raw string) or data (base64-encoded bytes). "
            "The connector auto-encodes text to base64 and applies required Cerner formatting "
            "(charset, docStatus, CodeSet 72 type system). "
            "For the type_system, use Cerner CodeSet 72 "
            "('https://fhir.cerner.com/{tenant_id}/codeSet/72') with a valid code. "
            "context_encounter_id is required for clinical note document types. "
            "Returns the new DocumentReference resource ID."
        ),
    )
    async def fhir_cerner_create_document_reference(
        status: str,
        subject: str,
        type_system: str,
        type_code: str,
        type_display: str,
        text: str = "",
        data: str = "",
        doc_status: str = "final",
        content_type: str = "text/plain",
        attachment_title: str = "Document",
        description: str = "",
        context_encounter_id: str = "",
        author_reference: str = "",
    ) -> dict:
        trace_id = str(uuid.uuid4())
        action = _get_connector().get_action("create_document_reference")

        if not text and not data:
            raise ValueError("Provide either 'text' (raw string) or 'data' (base64-encoded content)")

        doc_type = {
            "coding": [{
                "system": type_system,
                "code": type_code,
                "display": type_display,
                "userSelected": True,
            }],
            "text": type_display,
        }

        context = None
        if context_encounter_id:
            from datetime import datetime, timezone
            now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            context = {
                "encounter": [{"reference": f"Encounter/{context_encounter_id}"}],
                "period": {"start": now, "end": now},
            }

        author = None
        if author_reference:
            author = [{"reference": author_reference}]

        params = FhirCernerDocumentReferenceCreateInput(
            status=status,
            doc_status=doc_status,
            type=doc_type,
            subject=subject,
            text=text or None,
            data=data or None,
            content_type=content_type,
            attachment_title=attachment_title,
            description=description or None,
            context=context,
            author=author,
        )

        result = await action.internal_execute(params, trace_id=trace_id)
        return {
            "resource_id": result.resource_id,
            "resource": result.resource,
            "source": "Cerner FHIR",
        }

    # ------------------------------------------------------------------
    # Tool: fhir_cerner_search_document_reference
    # ------------------------------------------------------------------
    @mcp.tool(
        name="fhir_cerner_search_document_reference",
        description=(
            "Search FHIR DocumentReference resources in Cerner R4. "
            "Pass search parameters as key=value pairs separated by '&', "
            "e.g. 'patient=12345678' or 'patient=12345678&status=current'. "
            "The 'patient' parameter is required by most Cerner configurations."
        ),
    )
    async def fhir_cerner_search_document_reference(
        search_query: str,
    ) -> dict:
        trace_id = str(uuid.uuid4())
        action = _get_connector().get_action("search_document_reference")

        # Parse 'key=value&key2=value2' into a dict
        search_params: dict = {}
        for part in search_query.split("&"):
            part = part.strip()
            if "=" in part:
                k, _, v = part.partition("=")
                search_params[k.strip()] = v.strip()

        if not search_params:
            raise ValueError(
                "Provide search_query as 'key=value' pairs (e.g. 'patient=12345678')"
            )

        params = FhirCernerDocumentReferenceSearchInput(search_params=search_params)

        result = await action.internal_execute(params, trace_id=trace_id)
        return {
            "resources": result.resources,
            "total": result.total,
            "source": "Cerner FHIR",
        }

    logger.info(
        "Registered %d Cerner FHIR MCP tools: %s",
        5,
        [
            "fhir_cerner_read_patient",
            "fhir_cerner_search_patients",
            "fhir_cerner_search_encounter",
            "fhir_cerner_create_document_reference",
            "fhir_cerner_search_document_reference",
        ],
    )
    return mcp


def main() -> None:
    server = _make_server()
    logger.info("Starting nw-smartonfhir-cerner MCP server (stdio transport)")
    server.run()


if __name__ == "__main__":
    main()
