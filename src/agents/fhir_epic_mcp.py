"""
FastMCP Server Entrypoint — SMART on FHIR (Epic)
===============================================
Standalone MCP server exposing only the Epic FHIR patient read tool.

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


def _make_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ImportError("mcp SDK not installed. Run: pip install 'node-wire[agents]'") from exc

    from bindings.factory import ConnectorFactory
    from connectors import auto_register
    from connectors.fhir_epic.schema import FhirPatientReadInput as FhirEpicPatientReadInput

    auto_register()
    factory = ConnectorFactory()
    factory.load()

    mcp = FastMCP("nw-smartonfhir-epic")

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
        birthdate: str = "",
    ) -> dict:
        trace_id = str(uuid.uuid4())
        epic = factory._connectors.get("fhir_epic")
        if not epic:
            raise RuntimeError("fhir_epic connector not configured")

        action = epic.get_action("read_patient")

        if patient_id:
            params = FhirEpicPatientReadInput(resource_id=patient_id)
        elif family_name or given_name:
            search = {
                k: v
                for k, v in {
                    "family": family_name,
                    "given": given_name,
                    "birthdate": birthdate,
                }.items()
                if v
            }
            params = FhirEpicPatientReadInput(search_params=search)
        else:
            raise ValueError("Provide patient_id OR at least family_name/given_name")

        result = await action.internal_execute(params, trace_id=trace_id)
        resource = result.resource

        name_parts = resource.get("name", [{}])[0]
        full_name = " ".join(name_parts.get("given", []) + [name_parts.get("family", "")]).strip()

        addr = resource.get("address", [{}])[0]
        full_addr = (
            f"{addr.get('line', [''])[0]}, {addr.get('city', '')}, {addr.get('state', '')} {addr.get('postalCode', '')}"
        ).strip(", ")

        return {
            "patient_id": resource.get("id"),
            "full_name": full_name or "Unknown",
            "gender": resource.get("gender"),
            "birth_date": resource.get("birthDate"),
            "address_summary": full_addr,
            "source": "Epic FHIR",
        }

    return mcp


def main() -> None:
    server = _make_server()
    logger.info("Starting nw-smartonfhir-epic MCP server (stdio transport)")
    server.run()


if __name__ == "__main__":
    main()

