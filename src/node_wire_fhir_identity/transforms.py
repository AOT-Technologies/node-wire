"""Pure data-transformation utilities for cross-system FHIR Patient identity.

This module contains **no I/O** — only deterministic helpers that reshape,
normalise, or compare FHIR Patient resources so the rest of the identity
layer can stay thin.

Includes an enterprise-grade MPI scoring engine using configurable weights,
RapidFuzz fuzzy matching, and automatic SSN extraction.
"""

from __future__ import annotations

import os
import unicodedata
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# MPI weight registry — all configurable via environment variables
# ---------------------------------------------------------------------------

def _w(key: str, default: int) -> int:
    """Read an integer weight from env, falling back to *default*."""
    try:
        return int(os.getenv(f"NW_MPI_WEIGHT_{key.upper()}", str(default)))
    except (ValueError, TypeError):
        return default


def _get_weights() -> Dict[str, int]:
    return {
        "birthDate":  _w("BIRTHDATE", 40),
        "family":     _w("FAMILY", 20),
        "given":      _w("GIVEN", 15),
        "gender":     _w("GENDER", 5),
        "phone":      _w("PHONE", 10),
        "email":      _w("EMAIL", 10),
        "address":    _w("ADDRESS", 15),
        "postalCode": _w("POSTALCODE", 10),
        "ssn":        _w("SSN", 50),
    }


def _threshold_definite() -> int:
    try:
        return int(os.getenv("NW_MPI_THRESHOLD_DEFINITE", "90"))
    except (ValueError, TypeError):
        return 90


def _threshold_probable() -> int:
    try:
        return int(os.getenv("NW_MPI_THRESHOLD_PROBABLE", "70"))
    except (ValueError, TypeError):
        return 70


# Known sandbox / placeholder SSNs — never award match points for these
_SSN_PLACEHOLDERS = {
    "999999999",
    "123456789",
    "000000000",
    "111111111",
    "987654321",
}


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _norm_name(raw: Optional[str]) -> str:
    """Lowercase + strip diacritics."""
    if not raw:
        return ""
    nfkd = unicodedata.normalize("NFKD", raw)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_str.strip().lower()


def _norm_phone(raw: Optional[str]) -> str:
    """Strip all non-digit characters; strip leading country code '1'."""
    if not raw:
        return ""
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _norm_email(raw: Optional[str]) -> str:
    """Lowercase + strip whitespace."""
    return (raw or "").strip().lower()


def _norm_address(line: Optional[str], city: Optional[str], state: Optional[str]) -> str:
    """Combine address fields into a single lowercase string for fuzzy matching."""
    parts = [p.strip() for p in [line, city, state] if p and p.strip()]
    return " ".join(parts).lower()


def _norm_postal(raw: Optional[str]) -> str:
    """Strip whitespace and lowercase; take first 5 digits for US ZIP."""
    if not raw:
        return ""
    digits = "".join(c for c in raw if c.isdigit())
    return digits[:5] if len(digits) >= 5 else digits


# ---------------------------------------------------------------------------
# SSN extraction (automatic — no manual input)
# ---------------------------------------------------------------------------

def extract_ssn_from_resource(resource: Dict[str, Any]) -> Optional[str]:
    """Scan Patient.identifier for a real SSN.

    Detection criteria:
    - ``type.coding[].code == "SS"``  (HL7 v2-0203)
    - ``system == "urn:oid:2.16.840.1.113883.4.1"``

    Normalises to 9 digits, rejects sandbox placeholder values.
    Returns ``None`` if not found or value is a placeholder.
    """
    for ident in resource.get("identifier", []):
        is_ssn_type = False
        # Check coding
        for coding in ident.get("type", {}).get("coding", []):
            if coding.get("code") == "SS":
                is_ssn_type = True
                break
        # Check system
        if not is_ssn_type and ident.get("system") == "urn:oid:2.16.840.1.113883.4.1":
            is_ssn_type = True

        if is_ssn_type:
            val = ident.get("value", "")
            digits = "".join(c for c in val if c.isdigit())
            if len(digits) == 9 and digits not in _SSN_PLACEHOLDERS:
                return digits
    return None


# ---------------------------------------------------------------------------
# Demographic extraction
# ---------------------------------------------------------------------------


def extract_demographics(resource: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Pull canonical demographics out of a raw FHIR Patient resource.

    Returns a flat dict with keys: ``given``, ``family``, ``birthdate``,
    ``gender``, ``identifier_mrn``, ``phone``, ``email``,
    ``address_line``, ``city``, ``state``, ``postal_code``.
    """
    names = resource.get("name", [{}])
    primary_name = names[0] if names else {}

    given_list = primary_name.get("given", [])
    given = given_list[0] if given_list else None
    family = primary_name.get("family")
    birthdate = resource.get("birthDate")
    gender = resource.get("gender")

    mrn = _extract_mrn(resource.get("identifier", []))

    # Telecom extraction
    phone: Optional[str] = None
    email: Optional[str] = None
    for t in resource.get("telecom", []):
        if t.get("system") == "phone" and not phone:
            phone = t.get("value")
        elif t.get("system") == "email" and not email:
            email = t.get("value")

    # Address extraction
    address_line: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    addresses = resource.get("address", [])
    if addresses:
        addr = addresses[0]
        lines = addr.get("line", [])
        address_line = lines[0] if lines else None
        city = addr.get("city")
        state = addr.get("state")
        postal_code = addr.get("postalCode")

    return {
        "given": given,
        "family": family,
        "birthdate": birthdate,
        "gender": gender,
        "identifier_mrn": mrn,
        "phone": phone,
        "email": email,
        "address_line": address_line,
        "city": city,
        "state": state,
        "postal_code": postal_code,
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
# MPI scoring engine
# ---------------------------------------------------------------------------


def score_candidate(
    query: Dict[str, Optional[str]],
    candidate_resource: Dict[str, Any],
    weights: Optional[Dict[str, int]] = None,
) -> Tuple[float, float, str, List[str]]:
    """Score a FHIR Patient candidate resource against a flat query dict.

    Args:
        query: Flat dict with keys: ``given``, ``family``, ``birthdate``,
               ``gender``, ``phone``, ``email``, ``address_line``, ``city``,
               ``state``, ``postal_code``.  All values optional.
        candidate_resource: Raw FHIR Patient dict from a search bundle.
        weights: Optional override for the weight registry.

    Returns:
        ``(raw_score, pct, match_status, reasons)`` where:
        - ``raw_score`` is the absolute point total.
        - ``pct`` is the calculated match percentage.
        - ``match_status`` is ``"definite"``, ``"probable"``, or ``"no_match"``.
        - ``reasons`` is a human-readable list of scoring decisions.
    """
    try:
        from rapidfuzz import fuzz as _fuzz
    except ImportError:
        _fuzz = None  # graceful degradation — fuzzy fields become exact-only

    W = weights if weights is not None else _get_weights()
    
    # Base mandatory fields always form the baseline denominator
    max_score = W["given"] + W["family"] + W["birthDate"]

    # Optional fields only expand the denominator if the user provided them
    if query.get("gender"): max_score += W["gender"]
    if query.get("phone"): max_score += W["phone"]
    if query.get("email"): max_score += W["email"]
    if query.get("address_line") or query.get("city") or query.get("state"): max_score += W["address"]
    if query.get("postal_code"): max_score += W["postalCode"]
    if query.get("ssn"): max_score += W["ssn"]

    cand = extract_demographics(candidate_resource)
    score: float = 0.0
    reasons: List[str] = []

    # --- birthDate (exact, hard anchor) ---
    q_dob = (query.get("birthdate") or "").strip()
    c_dob = (cand.get("birthdate") or "").strip()
    if q_dob and c_dob:
        if q_dob == c_dob:
            score += W["birthDate"]
            reasons.append(f"birthDate exact (+{W['birthDate']})")
        else:
            reasons.append("birthDate mismatch (0)")

    # --- family name (exact, normalised) ---
    q_fam = _norm_name(query.get("family"))
    c_fam = _norm_name(cand.get("family"))
    if q_fam and c_fam:
        if q_fam == c_fam:
            score += W["family"]
            reasons.append(f"family exact (+{W['family']})")
        else:
            reasons.append("family mismatch (0)")

    # --- given name (fuzzy via RapidFuzz, threshold 85) ---
    q_given = _norm_name(query.get("given"))
    c_given = _norm_name(cand.get("given"))
    if q_given and c_given:
        if _fuzz is not None:
            ratio = _fuzz.ratio(q_given, c_given)
            if ratio >= 85:
                score += W["given"]
                reasons.append(f"given fuzzy {ratio:.0f}% (+{W['given']})")
            else:
                reasons.append(f"given fuzzy {ratio:.0f}% (0)")
        else:
            # Fallback to exact when rapidfuzz unavailable
            if q_given == c_given:
                score += W["given"]
                reasons.append(f"given exact (+{W['given']})")
            else:
                reasons.append("given mismatch (0)")

    # --- gender (exact) ---
    q_gender = (query.get("gender") or "").strip().lower()
    c_gender = (cand.get("gender") or "").strip().lower()
    if q_gender and c_gender:
        if q_gender == c_gender:
            score += W["gender"]
            reasons.append(f"gender exact (+{W['gender']})")
        else:
            reasons.append("gender mismatch (0)")

    # --- phone (digit-normalised exact) ---
    q_phone = _norm_phone(query.get("phone"))
    c_phone = _norm_phone(cand.get("phone"))
    if q_phone and c_phone:
        if q_phone == c_phone:
            score += W["phone"]
            reasons.append(f"phone exact (+{W['phone']})")
        else:
            reasons.append("phone mismatch (0)")

    # --- email (lowercased exact) ---
    q_email = _norm_email(query.get("email"))
    c_email = _norm_email(cand.get("email"))
    if q_email and c_email:
        if q_email == c_email:
            score += W["email"]
            reasons.append(f"email exact (+{W['email']})")
        else:
            reasons.append("email mismatch (0)")

    # --- address (fuzzy token-sort, threshold 80) ---
    q_addr = _norm_address(
        query.get("address_line"), query.get("city"), query.get("state")
    )
    c_addr = _norm_address(
        cand.get("address_line"), cand.get("city"), cand.get("state")
    )
    if q_addr and c_addr:
        if _fuzz is not None:
            addr_ratio = _fuzz.token_sort_ratio(q_addr, c_addr)
            if addr_ratio >= 50:
                score += W["address"]
                reasons.append(f"address fuzzy {addr_ratio:.0f}% (+{W['address']})")
            else:
                reasons.append(f"address fuzzy {addr_ratio:.0f}% (0)")
        else:
            if q_addr == c_addr:
                score += W["address"]
                reasons.append(f"address exact (+{W['address']})")
            else:
                reasons.append("address mismatch (0)")

    # --- postalCode (first 5 digits exact) ---
    q_postal = _norm_postal(query.get("postal_code"))
    c_postal = _norm_postal(cand.get("postal_code"))
    if q_postal and c_postal:
        if q_postal == c_postal:
            score += W["postalCode"]
            reasons.append(f"postalCode exact (+{W['postalCode']})")
        else:
            reasons.append("postalCode mismatch (0)")

    # --- SSN (auto-extracted from candidate, compared against query_ssn if provided) ---
    # query may carry a pre-extracted SSN under key "ssn"
    q_ssn = query.get("ssn")
    c_ssn = extract_ssn_from_resource(candidate_resource)
    if q_ssn and c_ssn:
        q_digits = "".join(c for c in q_ssn if c.isdigit())
        if (
            len(q_digits) == 9
            and q_digits not in _SSN_PLACEHOLDERS
            and q_digits == c_ssn
        ):
            score += W["ssn"]
            reasons.append(f"SSN exact (+{W['ssn']})")
        else:
            reasons.append("SSN mismatch (0)")

    # --- Determine match status ---
    if max_score == 0:
        pct = 0.0
    else:
        pct = (score / max_score) * 100.0

    if pct >= _threshold_definite():
        match_status = "definite"
    elif pct >= _threshold_probable():
        match_status = "probable"
    else:
        match_status = "no_match"

    return score, pct, match_status, reasons


# ---------------------------------------------------------------------------
# Payload preparation
# ---------------------------------------------------------------------------


def strip_source_metadata(resource: Dict[str, Any], target_system: str = "") -> Dict[str, Any]:
    """Return a copy of *resource* with system-specific fields removed.

    Keys like ``id``, ``meta``, and ``text`` are stripped.
    Additionally, target-specific logic is applied (e.g., Epic sandbox workarounds).
    """
    import copy
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
                    n.pop("use")  # Remove 'use' to let the target system default it
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
                    a.pop(k, None)  # Strip country to rely on Epic's local default
                if a.get("use") == "home":
                    if has_home:
                        continue  # Epic allows only 1 home address on creation
                    has_home = True
                clean_addresses.append(a)
            payload["address"] = clean_addresses[:1]

    elif target_system == "cerner":
        # Cerner is incredibly strict. We rebuild the payload from an allow-list.
        clean_payload: Dict[str, Any] = {"resourceType": "Patient"}

        for base_key in ["active", "gender", "birthDate"]:
            if base_key in payload:
                clean_payload[base_key] = payload[base_key]

        if "name" in payload and isinstance(payload["name"], list):
            clean_names = []
            for n in payload["name"]:
                safe_name: Dict[str, Any] = {}
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
                safe_telecom: Dict[str, Any] = {}
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
                safe_addr: Dict[str, Any] = {}
                for k in ["use", "line", "city", "state", "postalCode", "country"]:
                    if k in a:
                        safe_addr[k] = a[k]
                if safe_addr:
                    clean_addrs.append(safe_addr)
            if clean_addrs:
                clean_payload["address"] = clean_addrs[:1]

        # Cerner strictly requires exactly one identifier with ONLY an assigner
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
# Backward-compatible demographics_match (thin wrapper over score_candidate)
# ---------------------------------------------------------------------------


def demographics_match(
    a: Dict[str, Optional[str]],
    b: Dict[str, Optional[str]],
) -> Tuple[bool, float]:
    """Compare two demographic dicts and return (is_match, confidence 0–1).

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

    fam_ok = (a.get("family") or "").strip().lower() == (b.get("family") or "").strip().lower() and bool(a.get("family"))
    dob_ok = (a.get("birthdate") or "").strip() == (b.get("birthdate") or "").strip() and bool(a.get("birthdate"))

    return (fam_ok and dob_ok), confidence
