from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel


class UnifiedPatientSearchInput(BaseModel):
    """Input for searching a patient deterministically across systems."""
    action: Literal["search_patient"] = "search_patient"
    
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    birthdate: Optional[str] = None
    
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
