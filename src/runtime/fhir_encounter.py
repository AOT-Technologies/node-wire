"""FHIR Encounter search helpers shared by Epic/Cerner connectors."""

from __future__ import annotations

from typing import Dict


def assert_encounter_query_has_patient(query_params: Dict[str, str]) -> None:
    """
    Require a patient filter on Encounter search (enterprise default).

    Prevents broad or accidental unscoped queries that return 400 from the vendor
    or leak unrelated encounters.
    """
    p = query_params.get("patient")
    if not p or not str(p).strip():
        raise ValueError(
            "Encounter search requires a patient-scoped filter: set patient_id, "
            "or include patient in search_params."
        )
