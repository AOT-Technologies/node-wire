from __future__ import annotations

from typing import Any, Dict, List, Type

from pydantic import BaseModel

from runtime import BaseConnector

# FHIR connectors expose a single `execute` entrypoint with a discriminated `action`
# field; expand these for REST/MCP discovery so routes remain per-operation.
_FHIR_DISCRIMINATED_ACTIONS: Dict[str, List[str]] = {
    "fhir_cerner": [
        "read_patient",
        "search_patients",
        "search_encounter",
        "create_document_reference",
        "search_document_reference",
    ],
    "fhir_epic": [
        "read_patient",
        "search_patients",
        "search_encounter",
        "create_document_reference",
        "search_document_reference",
    ],
}


def _schema_for(model: Type[BaseModel]) -> Dict[str, Any]:
    return model.model_json_schema()


def build_manifest(connectors: List[BaseConnector[Any, Any]]) -> List[Dict[str, Any]]:
    """
    Build a simple manifest for discovery.

    Each entry describes a connector/action pair and includes JSON Schemas
    for the input and output models. This is consumed by Layer C for
    REST route generation and MCP tool manifests.
    """
    manifest: List[Dict[str, Any]] = []
    for connector in connectors:
        input_model = connector._input_model_cls  # type: ignore[attr-defined]
        output_model = connector._output_model_cls  # type: ignore[attr-defined]
        cid = connector.connector_id
        if cid in _FHIR_DISCRIMINATED_ACTIONS and getattr(connector, "action", None) == "execute":
            for sub_action in _FHIR_DISCRIMINATED_ACTIONS[cid]:
                manifest.append(
                    {
                        "connector_id": cid,
                        "action": sub_action,
                        "input_schema": _schema_for(input_model),
                        "output_schema": _schema_for(output_model),
                    }
                )
        else:
            manifest.append(
                {
                    "connector_id": cid,
                    "action": connector.action,
                    "input_schema": _schema_for(input_model),
                    "output_schema": _schema_for(output_model),
                }
            )
    return manifest

