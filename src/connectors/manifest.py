from __future__ import annotations

from typing import Any, Dict, List, Type

from pydantic import BaseModel

from runtime import BaseConnector


def _schema_for(model: Type[BaseModel]) -> Dict[str, Any]:
    return model.model_json_schema()


def build_manifest(connectors: List[BaseConnector]) -> List[Dict[str, Any]]:
    """
    One manifest entry per SDK @sdk_action (specific input/output schemas).
    """
    manifest: List[Dict[str, Any]] = []
    for connector in connectors:
        cid = connector.connector_id
        for action_name, meta in type(connector).sdk_action_metas().items():
            manifest.append(
                {
                    "connector_id": cid,
                    "action": action_name,
                    "input_schema": _schema_for(meta.input_model),
                    "output_schema": _schema_for(meta.output_model),
                }
            )
    return manifest
