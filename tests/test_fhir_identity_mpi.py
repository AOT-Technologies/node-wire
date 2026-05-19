"""Tests for the enterprise MPI scoring engine in node_wire_fhir_identity.transforms."""
from __future__ import annotations

import os
import pytest
from unittest.mock import patch

from node_wire_fhir_identity.transforms import (
    _norm_phone,
    _norm_email,
    _norm_address,
    _norm_postal,
    _norm_name,
    extract_ssn_from_resource,
    score_candidate,
    demographics_match,
    _SSN_PLACEHOLDERS,
    _get_weights,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patient(**kwargs) -> dict:
    """Build a minimal synthetic FHIR Patient resource."""
    resource = {"resourceType": "Patient"}
    if "given" in kwargs or "family" in kwargs:
        resource["name"] = [{
            "given": [kwargs.pop("given")] if "given" in kwargs else [],
            "family": kwargs.pop("family", None),
        }]
    if "birthDate" in kwargs:
        resource["birthDate"] = kwargs.pop("birthDate")
    if "gender" in kwargs:
        resource["gender"] = kwargs.pop("gender")
    if "phone" in kwargs:
        resource.setdefault("telecom", []).append({"system": "phone", "value": kwargs.pop("phone")})
    if "email" in kwargs:
        resource.setdefault("telecom", []).append({"system": "email", "value": kwargs.pop("email")})
    if "address_line" in kwargs or "city" in kwargs or "state" in kwargs or "postalCode" in kwargs:
        resource["address"] = [{
            "line": [kwargs.pop("address_line", "")],
            "city": kwargs.pop("city", None),
            "state": kwargs.pop("state", None),
            "postalCode": kwargs.pop("postalCode", None),
        }]
    if "ssn" in kwargs:
        resource["identifier"] = [{
            "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0203", "code": "SS"}]},
            "system": "urn:oid:2.16.840.1.113883.4.1",
            "value": kwargs.pop("ssn"),
        }]
    return resource


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def test_norm_phone_strips_formatting():
    assert _norm_phone("(555) 123-4567") == "5551234567"

def test_norm_phone_strips_country_code():
    assert _norm_phone("+1-555-123-4567") == "5551234567"
    assert _norm_phone("15551234567") == "5551234567"

def test_norm_email_lowercases():
    assert _norm_email("  Patient@EXAMPLE.COM  ") == "patient@example.com"

def test_norm_address_combines():
    result = _norm_address("123 Main St", "Austin", "TX")
    assert result == "123 main st austin tx"

def test_norm_postal_five_digits():
    assert _norm_postal("78701-1234") == "78701"
    assert _norm_postal("78701") == "78701"

def test_norm_name_strips_diacritics():
    assert _norm_name("José") == "jose"
    assert _norm_name("Müller") == "muller"


# ---------------------------------------------------------------------------
# SSN extraction
# ---------------------------------------------------------------------------

def test_ssn_extraction_by_coding():
    resource = _patient(ssn="123-45-6789")
    # 123456789 is a placeholder
    assert extract_ssn_from_resource(resource) is None

def test_ssn_extraction_real_ssn():
    resource = _patient(ssn="987-65-4320")  # not in placeholder list
    result = extract_ssn_from_resource(resource)
    assert result == "987654320"

def test_ssn_extraction_by_system():
    resource = {
        "identifier": [{
            "system": "urn:oid:2.16.840.1.113883.4.1",
            "value": "234-56-7890",
        }]
    }
    result = extract_ssn_from_resource(resource)
    assert result == "234567890"

def test_ssn_placeholder_ignored():
    for placeholder in list(_SSN_PLACEHOLDERS)[:3]:
        resource = {"identifier": [{
            "system": "urn:oid:2.16.840.1.113883.4.1",
            "value": placeholder,
        }]}
        assert extract_ssn_from_resource(resource) is None, f"Placeholder {placeholder} should be ignored"

def test_ssn_no_identifier_returns_none():
    assert extract_ssn_from_resource({"resourceType": "Patient"}) is None


# ---------------------------------------------------------------------------
# score_candidate — core scoring
# ---------------------------------------------------------------------------

def test_score_exact_dob_and_family():
    """DOB(40) + family(20) = 60 pts = 80% of 75 → probable (above 70% threshold)."""
    query = {"family": "Smith", "birthdate": "1990-01-01"}
    candidate = _patient(family="Smith", birthDate="1990-01-01")
    raw, pct, status, reasons = score_candidate(query, candidate)
    W = _get_weights()
    assert raw == W["birthDate"] + W["family"]
    assert status == "probable"
    assert any("birthDate" in r for r in reasons)
    assert any("family" in r for r in reasons)

def test_score_no_match_below_threshold():
    """Only a fuzzy given name match — well below 70% threshold."""
    query = {"given": "John", "birthdate": "2000-01-01"}
    candidate = _patient(given="Johnny", birthDate="1985-05-05")
    raw, pct, status, reasons = score_candidate(query, candidate)
    assert status == "no_match"

def test_score_phone_normalised():
    """Phone points awarded when digit-normalised values match."""
    query = {
        "family": "Davis",
        "birthdate": "1993-08-18",
        "phone": "(555) 123-4567",
    }
    candidate = _patient(family="Davis", birthDate="1993-08-18", phone="15551234567")
    raw, pct, status, reasons = score_candidate(query, candidate)
    W = _get_weights()
    assert raw >= W["birthDate"] + W["family"] + W["phone"]
    assert any("phone" in r and "+" in r for r in reasons)

def test_score_email_exact():
    W = _get_weights()
    query = {"family": "Lee", "birthdate": "1980-03-15", "email": "Patient@Example.COM"}
    candidate = _patient(family="Lee", birthDate="1980-03-15", email="patient@example.com")
    raw, pct, status, reasons = score_candidate(query, candidate)
    assert raw >= W["birthDate"] + W["family"] + W["email"]

def test_score_ssn_match():
    """Real SSN match should add SSN weight."""
    W = _get_weights()
    real_ssn = "234-56-7890"
    real_digits = "234567890"
    query = {"family": "Johnson", "birthdate": "1975-11-20", "ssn": real_digits}
    candidate = _patient(family="Johnson", birthDate="1975-11-20", ssn=real_ssn)
    raw, pct, status, reasons = score_candidate(query, candidate)
    assert any("SSN" in r and "+" in r for r in reasons), f"SSN not awarded. Reasons: {reasons}"
    assert raw >= W["birthDate"] + W["family"] + W["ssn"]

def test_score_ssn_placeholder_ignored():
    """Placeholder SSN should never award points."""
    W = _get_weights()
    query = {"family": "Brown", "birthdate": "1970-06-01", "ssn": "999999999"}
    candidate = _patient(family="Brown", birthDate="1970-06-01", ssn="999-99-9999")
    raw, pct, status, reasons = score_candidate(query, candidate)
    ssn_points = W["ssn"]
    # SSN should NOT be in awarded reasons
    assert not any("SSN" in r and "+" in r for r in reasons)

def test_score_definite_full_match():
    """Full demographic match without SSN = 125/125 = 100% → definite.
    With SSN it becomes 175/175 = 100% → definite."""
    W = _get_weights()
    # Without SSN
    query = {
        "given": "Jason",
        "family": "Smith",
        "birthdate": "1990-01-01",
        "gender": "male",
        "phone": "5551234567",
        "email": "jason@example.com",
        "address_line": "123 Main St",
        "city": "Austin",
        "state": "TX",
        "postal_code": "78701",
    }
    candidate = _patient(
        given="Jason", family="Smith", birthDate="1990-01-01",
        gender="male", phone="5551234567", email="jason@example.com",
        address_line="123 Main St", city="Austin", state="TX", postalCode="78701",
    )
    raw, pct, status, reasons = score_candidate(query, candidate)
    assert raw == 125.0, f"Expected 125 pts, got {raw}. Reasons={reasons}"
    assert status == "definite", f"Expected definite (100%), got {status}."

    # With real SSN: 175/175 = 100% → definite
    real_ssn = "234-56-7890"
    real_digits = "234567890"
    query_with_ssn = dict(query, ssn=real_digits)
    candidate_with_ssn = _patient(
        given="Jason", family="Smith", birthDate="1990-01-01",
        gender="male", phone="5551234567", email="jason@example.com",
        address_line="123 Main St", city="Austin", state="TX", postalCode="78701",
        ssn=real_ssn,
    )
    raw2, pct2, status2, reasons2 = score_candidate(query_with_ssn, candidate_with_ssn)
    assert raw2 == 175.0, f"Expected 175 pts, got {raw2}. Reasons={reasons2}"
    assert status2 == "definite", f"Expected definite, got {status2}."


# ---------------------------------------------------------------------------
# demographics_match (backward-compat wrapper)
# ---------------------------------------------------------------------------

def test_demographics_match_perfect():
    """demographics_match returns True when family + birthdate agree."""
    a = {"given": "John", "family": "Smith", "birthdate": "1990-01-01", "gender": "male"}
    b = {"given": "John", "family": "Smith", "birthdate": "1990-01-01", "gender": "male"}
    is_match, conf = demographics_match(a, b)
    assert is_match is True
    assert conf > 0.0

def test_demographics_match_requires_family_and_dob():
    a = {"given": "John", "family": "Smith", "birthdate": None}
    b = {"given": "John", "family": "Jones", "birthdate": None}
    is_match, conf = demographics_match(a, b)
    assert is_match is False

def test_demographics_match_dob_mismatch():
    a = {"given": "Alice", "family": "Brown", "birthdate": "1980-01-01"}
    b = {"given": "Alice", "family": "Brown", "birthdate": "1980-01-02"}
    is_match, conf = demographics_match(a, b)
    assert is_match is False


# ---------------------------------------------------------------------------
# Weight override via env vars
# ---------------------------------------------------------------------------

def test_weight_env_override():
    """NW_MPI_WEIGHT_SSN=0 should produce 0 SSN weight."""
    with patch.dict(os.environ, {"NW_MPI_WEIGHT_SSN": "0"}):
        W = _get_weights()
        assert W["ssn"] == 0

def test_threshold_env_override():
    """Custom thresholds should change match_status boundary."""
    from node_wire_fhir_identity.transforms import _threshold_definite, _threshold_probable
    with patch.dict(os.environ, {"NW_MPI_THRESHOLD_DEFINITE": "99", "NW_MPI_THRESHOLD_PROBABLE": "80"}):
        assert _threshold_definite() == 99
        assert _threshold_probable() == 80
