#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
Cerner-specific MCP argument normalizers and search guards.

Owned by this connector (see docs/adr/0002-connector-specific-logic-stays-in-the-connector.md):
duplicated from node_wire_fhir_epic's copy rather than shared via node_wire_runtime.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _split_ids(value: Any) -> List[str]:
    """Turn comma-separated string or list into a list of non-empty IDs."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    s = str(value).strip()
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def _is_missing_or_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _normalize_search_params_keys(sp: Dict[str, Any]) -> Dict[str, Any]:
    """Map legacy/LLM keys inside search_params to FHIR-friendly names."""
    if not sp:
        return {}
    out = dict(sp)
    if "patientId" in out and "identifier" not in out:
        out["identifier"] = out.pop("patientId")
    if "givenName" in out and "given" not in out:
        out["given"] = out.pop("givenName")
    if "familyName" in out and "family" not in out:
        out["family"] = out.pop("familyName")
    return out


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


def normalize_fhir_read_patient(args: Dict[str, Any]) -> None:
    """Map legacy LLM keys for FHIR read_patient."""
    if not (args.get("resource_id") or "").strip():
        pid = args.get("patient_id") or args.get("patientId")
        if pid is not None and str(pid).strip():
            args["resource_id"] = str(pid).strip()
    args.pop("patient_id", None)
    args.pop("patientId", None)
    if not args.get("family_name") and args.get("familyName"):
        args["family_name"] = args.pop("familyName")
    if not args.get("given_name") and args.get("givenName"):
        args["given_name"] = args.pop("givenName")
    if args.get("search_params") and isinstance(args["search_params"], dict):
        args["search_params"] = _normalize_search_params_keys(args["search_params"])


def normalize_fhir_search_encounter(args: Dict[str, Any]) -> None:
    """
    Map common LLM/FHIR mistakes for search_encounter.

    - Root ``patient`` / ``patientId`` -> ``patient_id`` (strip ``Patient/`` prefix).
    - Root ``sort`` -> FHIR ``_sort`` (merged into ``search_params``).
    - ``sort`` inside ``search_params`` -> ``_sort``.
    """
    if not (args.get("patient_id") or "").strip():
        p = args.get("patient") or args.get("patientId")
        if p is not None and str(p).strip():
            p_str = str(p).strip()
            if p_str.startswith("Patient/"):
                p_str = p_str[len("Patient/") :]
            args["patient_id"] = p_str
    args.pop("patient", None)
    args.pop("patientId", None)

    sp: Dict[str, Any] = {
        **(dict(args["search_params"]) if isinstance(args.get("search_params"), dict) else {})
    }
    root_sort = args.pop("sort", None)
    root_usort = args.pop("_sort", None)
    if root_sort is not None and str(root_sort).strip() and "_sort" not in sp:
        sp["_sort"] = str(root_sort).strip()
    elif root_usort is not None and str(root_usort).strip() and "_sort" not in sp:
        sp["_sort"] = str(root_usort).strip()
    if "sort" in sp and "_sort" not in sp:
        sp["_sort"] = str(sp.pop("sort")).strip()
    if sp:
        args["search_params"] = sp


def normalize_fhir_search_patients(args: Dict[str, Any]) -> None:
    """Map legacy LLM keys for FHIR search_patients."""
    if not args.get("resource_ids"):
        raw = args.get("patient_ids") or args.get("patientIds")
        ids = _split_ids(raw)
        if ids:
            args["resource_ids"] = ids
    args.pop("patient_ids", None)
    args.pop("patientIds", None)
    if not args.get("family_name") and args.get("familyName"):
        args["family_name"] = args.pop("familyName")
    if not args.get("given_name") and args.get("givenName"):
        args["given_name"] = args.pop("givenName")
    if args.get("search_params") and isinstance(args["search_params"], dict):
        args["search_params"] = _normalize_search_params_keys(args["search_params"])
