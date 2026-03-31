from __future__ import annotations

from typing import Any, Dict, List, Type

from pydantic import BaseModel

from runtime import BaseConnector


def _schema_for(model: Type[BaseModel]) -> Dict[str, Any]:
    return model.model_json_schema()


def _fhir_action_schemas() -> Dict[str, Dict[str, Type[BaseModel]]]:
    """Return per-action input model classes for FHIR connectors (lazy import)."""
    from connectors.fhir_cerner.schema import (
        FhirCernerDocumentReferenceCreateInput,
        FhirCernerDocumentReferenceSearchInput,
        FhirCernerEncounterSearchInput,
        FhirCernerPatientReadInput,
        FhirCernerPatientSearchInput,
    )
    from connectors.fhir_epic.schema import (
        FhirDocumentReferenceCreateInput,
        FhirDocumentReferenceSearchInput,
        FhirEncounterSearchInput,
        FhirPatientReadInput,
        FhirPatientSearchInput,
    )

    return {
        "fhir_cerner": {
            "read_patient": FhirCernerPatientReadInput,
            "search_patients": FhirCernerPatientSearchInput,
            "search_encounter": FhirCernerEncounterSearchInput,
            "create_document_reference": FhirCernerDocumentReferenceCreateInput,
            "search_document_reference": FhirCernerDocumentReferenceSearchInput,
        },
        "fhir_epic": {
            "read_patient": FhirPatientReadInput,
            "search_patients": FhirPatientSearchInput,
            "search_encounter": FhirEncounterSearchInput,
            "create_document_reference": FhirDocumentReferenceCreateInput,
            "search_document_reference": FhirDocumentReferenceSearchInput,
        },
    }


def build_manifest(connectors: List[BaseConnector[Any, Any]]) -> List[Dict[str, Any]]:
    """
    Build a simple manifest for discovery.

    Each entry describes a connector/action pair and includes JSON Schemas
    for the input and output models. This is consumed by Layer C for
    REST route generation and MCP tool manifests.
    """
    manifest: List[Dict[str, Any]] = []
    fhir_schemas: Dict[str, Dict[str, Type[BaseModel]]] | None = None

    for connector in connectors:
        output_model = connector._output_model_cls  # type: ignore[attr-defined]
        cid = connector.connector_id
        if getattr(connector, "action", None) == "execute" and cid in ("fhir_cerner", "fhir_epic"):
            if fhir_schemas is None:
                fhir_schemas = _fhir_action_schemas()
            for sub_action, input_cls in fhir_schemas[cid].items():
                manifest.append(
                    {
                        "connector_id": cid,
                        "action": sub_action,
                        "input_schema": _schema_for(input_cls),
                        "output_schema": _schema_for(output_model),
                    }
                )
        else:
            input_model = connector._input_model_cls  # type: ignore[attr-defined]
            manifest.append(
                {
                    "connector_id": cid,
                    "action": connector.action,
                    "input_schema": _schema_for(input_model),
                    "output_schema": _schema_for(output_model),
                }
            )
    return manifest

