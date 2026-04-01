"""
FastMCP Server Entrypoint — SMART on FHIR (Epic)
=================================================
Standalone MCP server that dynamically registers every action exposed by
the fhir_epic connector:

  • fhir_epic_read_patient           — fetch a single Patient by ID or name search
  • fhir_epic_search_patients        — fetch multiple Patients (fan-out or name search)
  • fhir_epic_search_encounter       — search Encounters by patient / status / date
  • fhir_epic_create_document_reference — create a FHIR DocumentReference
  • fhir_epic_search_document_reference — search FHIR DocumentReferences

New actions added to the connector are automatically picked up at startup —
no changes to this file are required.

Usage:
    python -m agents.fhir_epic_mcp
"""
from __future__ import annotations

import logging
import os
import uuid

from dotenv import load_dotenv

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agents.fhir_epic_mcp")


# ---------------------------------------------------------------------------
# Per-action tool definitions
# Each entry: (mcp_tool_name, description, input_schema_cls, handler_fn)
# The handler_fn receives (**kwargs) from FastMCP and returns a dict/list.
# ---------------------------------------------------------------------------

def _make_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ImportError("mcp SDK not installed. Run: pip install 'node-wire[agents]'") from exc

    from bindings.factory import ConnectorFactory
    from connectors import auto_register
    from connectors.fhir_epic.schema import (
        FhirDocumentReferenceCreateInput,
        FhirDocumentReferenceSearchInput,
        FhirEncounterSearchInput,
        FhirPatientReadInput,
        FhirPatientSearchInput,
    )

    auto_register()
    factory = ConnectorFactory()
    factory.load()

    mcp = FastMCP("nw-smartonfhir-epic")

    def _get_connector():
        epic = factory._connectors.get("fhir_epic")
        if not epic:
            raise RuntimeError("fhir_epic connector not configured")
        return epic

    # ------------------------------------------------------------------
    # Tool: fhir_epic_read_patient
    # ------------------------------------------------------------------
    @mcp.tool(
        name="fhir_epic_read_patient",
        description=(
            "Fetch a single patient's demographic record from Epic FHIR R4. "
            "Provide patient_id for a direct lookup, or family_name/given_name/name "
            "for a name-based search. "
            "Epic patient IDs typically start with 'e' (e.g. 'eXYZ123')."
        ),
    )
    async def fhir_epic_read_patient(
        patient_id: str = "",
        family_name: str = "",
        given_name: str = "",
        name: str = "",
        birthdate: str = "",
    ) -> dict:
        trace_id = str(uuid.uuid4())
        action = _get_connector().get_action("read_patient")

        if patient_id:
            params = FhirPatientReadInput(resource_id=patient_id)
        elif family_name or given_name or name:
            params = FhirPatientReadInput(
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
            "source": "Epic FHIR",
        }

    # ------------------------------------------------------------------
    # Tool: fhir_epic_search_patients
    # ------------------------------------------------------------------
    @mcp.tool(
        name="fhir_epic_search_patients",
        description=(
            "Search / fetch multiple patients from Epic FHIR R4. "
            "Mode 1 — pass a comma-separated list of patient IDs in resource_ids for a concurrent "
            "fan-out lookup. "
            "Mode 2 — pass family_name, given_name, name, and/or birthdate for a name-based "
            "FHIR search that returns all matching Bundle entries. "
            "Partial failures in Mode 1 are captured in the 'errors' list rather than raising."
        ),
    )
    async def fhir_epic_search_patients(
        resource_ids: str = "",
        family_name: str = "",
        given_name: str = "",
        name: str = "",
        birthdate: str = "",
    ) -> dict:
        trace_id = str(uuid.uuid4())
        action = _get_connector().get_action("search_patients")

        ids_list = [i.strip() for i in resource_ids.split(",") if i.strip()] if resource_ids else None

        params = FhirPatientSearchInput(
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
            "source": "Epic FHIR",
        }

    # ------------------------------------------------------------------
    # Tool: fhir_epic_search_encounter
    # ------------------------------------------------------------------
    @mcp.tool(
        name="fhir_epic_search_encounter",
        description=(
            "Search FHIR Encounter resources in Epic R4. "
            "Filter by patient_id (maps to the FHIR 'patient' parameter), encounter "
            "status (e.g. 'finished', 'arrived'), and/or date / date range "
            "(e.g. '2024', 'gt2023-01-01'). "
            "At least one filter must be provided."
        ),
    )
    async def fhir_epic_search_encounter(
        patient_id: str = "",
        status: str = "",
        date: str = "",
    ) -> dict:
        trace_id = str(uuid.uuid4())
        action = _get_connector().get_action("search_encounter")

        if not patient_id and not status and not date:
            raise ValueError("Provide at least one of patient_id, status, or date")

        params = FhirEncounterSearchInput(
            patient_id=patient_id or None,
            status=status or None,
            date=date or None,
        )

        result = await action.internal_execute(params, trace_id=trace_id)
        return {
            "resources": result.resources,
            "total": result.total,
            "source": "Epic FHIR",
        }

    # ------------------------------------------------------------------
    # Tool: fhir_epic_create_document_reference
    # ------------------------------------------------------------------
    @mcp.tool(
        name="fhir_epic_create_document_reference",
        description=(
            "Create a FHIR DocumentReference resource in Epic R4. "
            "Required: status ('current'), type (CodeableConcept with LOINC code), "
            "subject (Patient reference string, e.g. 'Patient/eXYZ'), "
            "data (base64-encoded content). "
            "Optional: identifier, category, author, description, context "
            "(Epic requires context.encounter for clinical note types such as LOINC 34108-1). "
            "Returns the new DocumentReference resource ID."
        ),
    )
    async def fhir_epic_create_document_reference(
        status: str,
        subject: str,
        data: str,
        type_code: str = "34133-9",
        type_system: str = "http://loinc.org",
        type_display: str = "Summary of episode note",
        content_type: str = "text/plain",
        description: str = "",
        encounter_id: str = "",
        author_reference: str = "",
    ) -> dict:
        trace_id = str(uuid.uuid4())
        action = _get_connector().get_action("create_document_reference")

        doc_type = {
            "coding": [{"system": type_system, "code": type_code, "display": type_display}]
        }

        identifier = [{"system": "urn:ietf:rfc:3986", "value": f"urn:uuid:{uuid.uuid4()}"}]

        context = None
        if encounter_id:
            context = {"encounter": [{"reference": f"Encounter/{encounter_id}"}]}

        author = None
        if author_reference:
            author = [{"reference": author_reference}]

        params = FhirDocumentReferenceCreateInput(
            identifier=identifier,
            status=status,
            type=doc_type,
            subject=subject,
            data=data,
            content_type=content_type,
            description=description or None,
            context=context,
            author=author,
        )

        result = await action.internal_execute(params, trace_id=trace_id)
        return {
            "resource_id": result.resource_id,
            "resource": result.resource,
            "source": "Epic FHIR",
        }

    # ------------------------------------------------------------------
    # Tool: fhir_epic_search_document_reference
    # ------------------------------------------------------------------
    @mcp.tool(
        name="fhir_epic_search_document_reference",
        description=(
            "Search FHIR DocumentReference resources in Epic R4. "
            "Pass search parameters as key=value pairs separated by '&', "
            "e.g. 'patient=eXYZ123' or 'patient=eXYZ123&type=34133-9'. "
            "The 'patient' parameter is required by most Epic configurations."
        ),
    )
    async def fhir_epic_search_document_reference(
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
                "Provide search_query as 'key=value' pairs (e.g. 'patient=eXYZ123')"
            )

        params = FhirDocumentReferenceSearchInput(search_params=search_params)

        result = await action.internal_execute(params, trace_id=trace_id)
        return {
            "resources": result.resources,
            "total": result.total,
            "source": "Epic FHIR",
        }

    logger.info(
        "Registered %d Epic FHIR MCP tools: %s",
        5,
        [
            "fhir_epic_read_patient",
            "fhir_epic_search_patients",
            "fhir_epic_search_encounter",
            "fhir_epic_create_document_reference",
            "fhir_epic_search_document_reference",
        ],
    )
    return mcp


def main() -> None:
    server = _make_server()
    logger.info("Starting nw-smartonfhir-epic MCP server (stdio transport)")
    server.run()


if __name__ == "__main__":
    main()
