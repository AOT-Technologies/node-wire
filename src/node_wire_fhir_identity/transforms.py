"""Pure data-transformation utilities for cross-system FHIR Patient identity.

This module contains **no I/O** — only deterministic helpers that reshape,
normalise, or compare FHIR Patient resources so the rest of the identity
layer can stay thin.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Demographic extraction
# ---------------------------------------------------------------------------


def extract_demographics(resource: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Pull canonical demographics out of a raw FHIR Patient resource.

    Returns a flat dict with keys: ``given``, ``family``, ``birthdate``,
    ``gender``, ``identifier_mrn``.
    """
    names = resource.get("name", [{}])
    primary_name = names[0] if names else {}

    given_list = primary_name.get("given", [])
    given = given_list[0] if given_list else None
    family = primary_name.get("family")
    birthdate = resource.get("birthDate")
    gender = resource.get("gender")

    # Try to pull an MRN from the identifiers list
    mrn = _extract_mrn(resource.get("identifier", []))

    return {
        "given": given,
        "family": family,
        "birthdate": birthdate,
        "gender": gender,
        "identifier_mrn": mrn,
    }


def _extract_mrn(identifiers: List[Dict[str, Any]]) -> Optional[str]:
    """Find the first MRN-type identifier value, if any."""
    for ident in identifiers:
        codings = ident.get("type", {}).get("coding", [])
        for coding in codings:
            if coding.get("code") == "MR":
                return ident.get("value")
    return None


# ---------------------------------------------------------------------------
# Payload preparation
# ---------------------------------------------------------------------------


def strip_source_metadata(resource: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *resource* with system-specific fields removed.

    Keys like ``id``, ``meta``, and ``text`` are stripped so the payload is
    safe to POST / PUT to a *different* EHR.
    """
    payload = dict(resource)
    for key in ("id", "meta", "text"):
        payload.pop(key, None)
    return payload


def ensure_resource_type(payload: Dict[str, Any], resource_type: str = "Patient") -> Dict[str, Any]:
    """Guarantee ``resourceType`` is present in the payload."""
    payload = dict(payload)
    payload.setdefault("resourceType", resource_type)
    return payload


# ---------------------------------------------------------------------------
# Duplicate / match scoring
# ---------------------------------------------------------------------------


def demographics_match(
    a: Dict[str, Optional[str]],
    b: Dict[str, Optional[str]],
) -> Tuple[bool, float]:
    """Compare two demographic dicts and return (is_match, confidence).

    A match requires *at least* family-name + birthdate to agree.
    Confidence is a simple 0-1 score based on how many fields align.

    >>> demographics_match(
    ...     {"given": "John", "family": "Smith", "birthdate": "1990-01-01", "gender": "male", "identifier_mrn": None},
    ...     {"given": "John", "family": "Smith", "birthdate": "1990-01-01", "gender": "male", "identifier_mrn": None},
    ... )
    (True, 1.0)
    """
    fields = ["given", "family", "birthdate", "gender", "identifier_mrn"]
    comparable = 0
    matched = 0

    for f in fields:
        va = (a.get(f) or "").strip().lower()
        vb = (b.get(f) or "").strip().lower()
        if va and vb:
            comparable += 1
            if va == vb:
                matched += 1

    if comparable == 0:
        return False, 0.0

    confidence = matched / comparable

    # Hard requirement: family + birthdate must both match.
    fam_ok = (a.get("family") or "").strip().lower() == (b.get("family") or "").strip().lower() and bool(a.get("family"))
    dob_ok = (a.get("birthdate") or "").strip() == (b.get("birthdate") or "").strip() and bool(a.get("birthdate"))

    return (fam_ok and dob_ok), confidence
