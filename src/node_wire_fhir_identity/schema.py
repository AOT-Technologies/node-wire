from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


class UnifiedPatientSearchInput(BaseModel):
    """Input for searching a patient deterministically across systems."""
    action: Literal["search_patient"] = "search_patient"

    # Core demographics
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    birthdate: Optional[str] = None

    # Optional enrichment fields
    gender: Optional[str] = None        # "male" | "female" | "unknown" | "other"
    phone: Optional[str] = None         # Raw phone — normalized server-side
    email: Optional[str] = None         # Normalized to lowercase server-side
    address_line: Optional[str] = None  # e.g. "123 Main St"
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None

    # Direct ID shortcuts (bypass demographic search)
    epic_patient_id: Optional[str] = None
    cerner_patient_id: Optional[str] = None


class UnifiedPatientSyncInput(BaseModel):
    """Input for syncing a patient from one system to another."""
    action: Literal["sync_patient"] = "sync_patient"

    source_system: Literal["epic", "cerner"]
    source_patient_id: str
    target_system: Literal["epic", "cerner"]


class UnifiedPatientOperationOutput(BaseModel):
    """Output for Identity Coordinator operations."""
    status: str
    message: Optional[str] = None

    epic_resource_id: Optional[str] = None
    epic_resource: Optional[Dict[str, Any]] = None

    cerner_resource_id: Optional[str] = None
    cerner_resource: Optional[Dict[str, Any]] = None

    # MPI decision fields (Global / Best Overall)
    match_status: Optional[str] = None          # "definite" | "probable" | "no_match"
    match_confidence: Optional[float] = None    # 0.0 – 100.0
    match_score: Optional[float] = None         # raw weighted points
    match_reasons: Optional[List[str]] = None   # ["birthDate exact (+40)", ...]

    # MPI Candidates & Specific System Results
    epic_match_status: Optional[str] = None
    epic_match_score: Optional[float] = None
    epic_candidates: Optional[List[Dict[str, Any]]] = None

    cerner_match_status: Optional[str] = None
    cerner_match_score: Optional[float] = None
    cerner_candidates: Optional[List[Dict[str, Any]]] = None
