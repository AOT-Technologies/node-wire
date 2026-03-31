"""
FastMCP Server Entrypoint — SMART on FHIR (Cerner)
=================================================
Standalone MCP server exposing only the Cerner FHIR patient read tool.

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
    from connectors.fhir_cerner.schema import FhirCernerPatientReadInput

    auto_register()
    factory = ConnectorFactory()
    factory.load()

    mcp = FastMCP("nw-smartonfhir-cerner")

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
        }

    return mcp


def main() -> None:
    server = _make_server()
    logger.info("Starting nw-smartonfhir-cerner MCP server (stdio transport)")
    server.run()


if __name__ == "__main__":
    main()

