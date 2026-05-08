"""node_wire_fhir_identity — internal package for cross-system FHIR identity.

This is NOT a connector.  It provides:

* ``transforms`` — pure data-transformation utilities (no I/O).
* ``IdentityCoordinator`` — stateless orchestrator that callers instantiate
  with pre-built Epic / Cerner connector instances.
* Pydantic schemas for unified search / sync inputs and outputs.
"""

from .logic import IdentityCoordinator
from .schema import (
    UnifiedPatientOperationOutput,
    UnifiedPatientSearchInput,
    UnifiedPatientSyncInput,
)
from .transforms import (
    demographics_match,
    ensure_resource_type,
    extract_demographics,
    strip_source_metadata,
)

__all__ = [
    # Orchestrator
    "IdentityCoordinator",
    # Schemas
    "UnifiedPatientSearchInput",
    "UnifiedPatientSyncInput",
    "UnifiedPatientOperationOutput",
    # Pure transforms (importable independently)
    "extract_demographics",
    "strip_source_metadata",
    "ensure_resource_type",
    "demographics_match",
]
