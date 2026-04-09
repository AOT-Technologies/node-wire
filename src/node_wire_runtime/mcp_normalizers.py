"""
Per-action MCP tool argument normalizers.

Each function mutates the arguments dict in place (same contract as before refactor).
Registered on actions via @sdk_action(..., mcp_normalize=...) or SdkActionSpec(..., mcp_normalize=...).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from node_wire_runtime.mcp_contract import (
    legacy_gdrive_action_upload_mode,
    log_legacy_gdrive_action_upload_usage,
)

logger = logging.getLogger("runtime.mcp_normalizers")


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


def _is_missing_or_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def normalize_fhir_read_patient(args: Dict[str, Any]) -> None:
    """Map legacy LLM keys for FHIR read_patient (Epic/Cerner)."""
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
    Map common LLM/FHIR mistakes for search_encounter (Epic/Cerner).

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
    """Map legacy LLM keys for FHIR search_patients (Epic/Cerner)."""
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


def normalize_google_drive_files_upload(args: Dict[str, Any]) -> None:
    """
    Map common LLM mistakes for files.upload to FilesUploadOperation fields.
    Mutates args in place. Canonical keys already set on the root win over aliases/nesting.
    """
    media = args.get("media")
    if media is not None:
        if isinstance(media, dict):
            if _is_missing_or_blank(args.get("name")) and not _is_missing_or_blank(
                media.get("name")
            ):
                args["name"] = media.get("name")

            if _is_missing_or_blank(args.get("mime_type")):
                mt = media.get("mime_type") or media.get("mimeType")
                if not _is_missing_or_blank(mt):
                    args["mime_type"] = mt

            if _is_missing_or_blank(args.get("parents")):
                parents = media.get("parents")
                if isinstance(parents, list) and parents:
                    args["parents"] = parents
                elif isinstance(parents, str) and parents.strip():
                    args["parents"] = _split_ids(parents)

            if _is_missing_or_blank(args.get("content_base64")) and _is_missing_or_blank(
                args.get("content")
            ):
                b64 = media.get("content_base64") or media.get("base64") or media.get("data")
                if not _is_missing_or_blank(b64):
                    args["content_base64"] = b64
                else:
                    text = media.get("content") or media.get("text") or media.get("body")
                    if not _is_missing_or_blank(text):
                        args["content"] = text
        elif isinstance(media, str):
            if _is_missing_or_blank(args.get("content_base64")) and _is_missing_or_blank(
                args.get("content")
            ):
                if media.strip():
                    args["content"] = media

        args.pop("media", None)

    args.pop("media_body", None)

    nested = args.get("file")
    if isinstance(nested, dict):
        for key in ("name", "mime_type", "parents", "content", "content_base64"):
            if key in nested and _is_missing_or_blank(args.get(key)):
                args[key] = nested[key]
        if _is_missing_or_blank(args.get("mime_type")) and nested.get("mimeType"):
            args["mime_type"] = nested["mimeType"]
        args.pop("file", None)

    if not _is_missing_or_blank(args.get("mimeType")) and _is_missing_or_blank(
        args.get("mime_type")
    ):
        args["mime_type"] = args["mimeType"]
    args.pop("mimeType", None)

    if args.get("action") == "upload":
        mode = legacy_gdrive_action_upload_mode()
        if mode == "reject":
            logger.warning(
                "Rejected legacy action value 'upload' for google_drive.files.upload "
                "(set %s=allow or omit action; tool name is authoritative).",
                "NODE_WIRE_LEGACY_GDRIVE_ACTION_UPLOAD",
            )
        else:
            if mode == "warn":
                logger.warning(
                    "Deprecated: action 'upload' in google_drive.files.upload payload; "
                    "omit 'action' or use 'files.upload'. "
                    "Set NODE_WIRE_LEGACY_GDRIVE_ACTION_UPLOAD=reject to hard-fail."
                )
                log_legacy_gdrive_action_upload_usage()
            args["action"] = "files.upload"


def normalize_smtp_send_email(args: Dict[str, Any]) -> None:
    """Map common LLM aliases for smtp.send_email to SmtpSendInput fields."""
    if _is_missing_or_blank(args.get("from_email")):
        for alias in ("from", "sender", "from_addr"):
            if not _is_missing_or_blank(args.get(alias)):
                args["from_email"] = args[alias]
                break
    for alias in ("from", "sender", "from_addr"):
        args.pop(alias, None)

    if isinstance(args.get("to"), str):
        args["to"] = [args["to"]]
