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


def strip_source_metadata(resource: Dict[str, Any], target_system: str = "") -> Dict[str, Any]:
    """Return a copy of *resource* with system-specific fields removed.

    Keys like ``id``, ``meta``, and ``text`` are stripped.
    Additionally, target-specific logic is applied (e.g., Epic sandbox workarounds).
    """
    import copy
    import os
    payload = copy.deepcopy(resource)
    
    # Strip basic structural metadata
    for key in ("id", "meta", "text"):
        payload.pop(key, None)
        
    if target_system == "epic":
        # Remove unsupported Epic fields
        for key in ("extension", "maritalStatus", "contact", "communication", "generalPractitioner"):
            payload.pop(key, None)
            
        # 1. SSN Identifier Mapping
        real_ssn = None
        for ident in payload.get("identifier", []):
            if ident.get("system") == "urn:oid:2.16.840.1.113883.4.1" or any(c.get("code") == "SS" for c in ident.get("type", {}).get("coding", [])):
                val = ident.get("value", "")
                digits = "".join(c for c in val if c.isdigit())
                if len(digits) == 9:
                    real_ssn = f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
                    break
                    
        if not real_ssn:
            fallback_ssn = os.getenv("EPIC_SANDBOX_TEST_SSN")
            if fallback_ssn:
                real_ssn = fallback_ssn
                
        if real_ssn:
            payload["identifier"] = [{
                "use": "official",
                "type": {
                    "coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0203", "code": "SS"}],
                    "text": "SSN"
                },
                "system": "urn:oid:2.16.840.1.113883.4.1",
                "value": real_ssn
            }]
        else:
            payload.pop("identifier", None)
            
        # 2. Gender Mapping
        if payload.get("gender") == "other":
            payload["gender"] = "unknown"
            
        # Clean up names (Epic rejects many 'use' codes like 'old' or 'official')
        if "name" in payload and isinstance(payload["name"], list):
            clean_names = []
            for n in payload["name"]:
                if "use" in n:
                    n.pop("use") # Remove 'use' to let the target system default it
                clean_names.append(n)
            payload["name"] = clean_names[:1] 
            
        # 3. Telecom Filtering
        if "telecom" in payload and isinstance(payload["telecom"], list):
            clean_telecoms = []
            for t in payload["telecom"]:
                if t.get("use") not in ("mobile", "home", "work"):
                    continue
                for k in ["id", "extension"]:
                    t.pop(k, None)
                if t.get("system") == "phone" and "value" in t:
                    digits = "".join(c for c in str(t["value"]) if c.isdigit())
                    if len(digits) == 11 and digits.startswith("1"):
                        digits = digits[1:]
                    if len(digits) == 10:
                        t["value"] = f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
                        clean_telecoms.append(t)
                elif t.get("system") == "email" and "value" in t:
                    clean_telecoms.append(t)
                    
            if clean_telecoms:
                payload["telecom"] = clean_telecoms
            else:
                payload.pop("telecom", None)
                    
        # Clean address: Epic rejects invalid countries and multiple "home" addresses.
        if "address" in payload and isinstance(payload["address"], list):
            clean_addresses = []
            has_home = False
            for a in payload["address"]:
                for k in ["id", "extension", "country"]:
                    a.pop(k, None) # Strip country to rely on Epic's local default
                if a.get("use") == "home":
                    if has_home:
                        continue # Epic allows only 1 home address on creation
                    has_home = True
                clean_addresses.append(a)
            payload["address"] = clean_addresses[:1]
    elif target_system == "cerner":
        # Cerner is incredibly strict. We rebuild the payload from an allow-list.
        clean_payload = { "resourceType": "Patient" }
        
        for base_key in ["active", "gender", "birthDate"]:
            if base_key in payload:
                clean_payload[base_key] = payload[base_key]
                
        if "name" in payload and isinstance(payload["name"], list):
            clean_names = []
            for n in payload["name"]:
                safe_name = {}
                for k in ["family", "given", "use", "prefix", "suffix"]:
                    if k in n:
                        safe_name[k] = n[k]
                if safe_name:
                    clean_names.append(safe_name)
            if clean_names:
                clean_payload["name"] = clean_names[:1]
                
        if "telecom" in payload and isinstance(payload["telecom"], list):
            clean_telecoms = []
            for t in payload["telecom"]:
                safe_telecom = {}
                for k in ["system", "value", "use"]:
                    if k in t:
                        safe_telecom[k] = t[k]
                if safe_telecom:
                    clean_telecoms.append(safe_telecom)
            if clean_telecoms:
                clean_payload["telecom"] = clean_telecoms
                
        if "address" in payload and isinstance(payload["address"], list):
            clean_addrs = []
            for a in payload["address"]:
                safe_addr = {}
                for k in ["use", "line", "city", "state", "postalCode", "country"]:
                    if k in a:
                        safe_addr[k] = a[k]
                if safe_addr:
                    clean_addrs.append(safe_addr)
            if clean_addrs:
                clean_payload["address"] = clean_addrs[:1]
                
        # Cerner strictly requires exactly one identifier with ONLY an assigner (no value, system, etc)
        org_ref = os.getenv("CERNER_SANDBOX_ORG")
        if not org_ref:
            raise ValueError("CERNER_SANDBOX_ORG environment variable is not set")
        clean_payload["identifier"] = [
            {
                "assigner": {
                    "reference": org_ref if org_ref.startswith("Organization/") else f"Organization/{org_ref}"
                }
            }
        ]

        return clean_payload
        
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
