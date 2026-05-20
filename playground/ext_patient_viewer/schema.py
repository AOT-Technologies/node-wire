"""
External Patient Viewer — Pydantic input/output schemas.

Read-only: no writes occur in any scenario using these models.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ExternalPatientViewerInput(BaseModel):
    """
    Payload for the 'Load External Chart' workflow.

    The viewer resolves the patient via direct ID (preferred) or falls back
    to identity-layer search using name + birthdate. Both paths remain
    read-only; no FHIR resources are created or mutated.
    """

    # --- Identity Resolution ---
    patient_id: Optional[str] = None
    """Direct FHIR Patient resource ID. When supplied, name/DOB are ignored."""

    patient_family: Optional[str] = None
    """Family (last) name used for identity-layer search when patient_id is absent."""

    patient_given: Optional[str] = None
    """Given (first) name used for identity-layer search when patient_id is absent."""

    patient_birthdate: Optional[str] = None
    """ISO-8601 birthdate (YYYY-MM-DD) for identity-layer search disambiguation."""

    # --- Source System ---
    source_system: str = "epic"
    """EHR source to query: 'epic' (default) or 'cerner'."""

    # --- Retrieval Scope ---
    max_encounters: int = 5
    """Maximum number of recent encounters to retrieve (1–20)."""

    max_documents: int = 10
    """Maximum number of document references to retrieve (1–50)."""
