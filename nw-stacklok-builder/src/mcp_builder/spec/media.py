"""Media-type predicates shared across spec parsing and codegen stages."""

from __future__ import annotations

import re

# Matches application/json, text/json, and any RFC 6839 structured-suffix
# JSON type (application/vnd.api+json, application/ld+json, etc.).
_JSON_MEDIA_RE = re.compile(
    r"^(?:application|text)/(?:[\w.+-]+\+)?json$", re.IGNORECASE
)

# Matches application/xml and application/foo+xml structured-suffix
# variants. text/* types are handled separately since every text/*
# subtype is text-decodable.
_XML_MEDIA_RE = re.compile(r"^application/(?:[\w.+-]+\+)?xml$", re.IGNORECASE)


def is_json_media_type(media_type: str) -> bool:
    """Return True if the media type is JSON-decodable.

    Accepts ``application/json``, ``text/json`` (legacy), and any
    ``application/foo+json`` / ``text/foo+json`` structured-suffix
    variant per RFC 6839. Media-type parameters like
    ``"; charset=utf-8"`` are stripped before matching.
    """
    bare = media_type.split(";", 1)[0].strip()
    return bool(_JSON_MEDIA_RE.match(bare))


def is_text_media_type(media_type: str) -> bool:
    """Return True if the media type is non-JSON text.

    Accepts every ``text/*`` subtype (``text/plain``, ``text/html``,
    ``text/csv``, ``text/markdown``, ...) and XML variants
    (``application/xml``, ``application/foo+xml``). JSON variants
    (including ``text/json`` and ``text/foo+json``) are excluded so
    the predicate is mutually exclusive with
    :func:`is_json_media_type`; those belong under ``response_kind:
    json`` instead.

    Known limitation: human-readable ``application/*`` subtypes that
    are not XML — ``application/javascript``, ``application/yaml``,
    ``application/sql``, ``application/graphql``, etc. — return False
    today and route through the binary path, which base64-wraps the
    body. Expanding the allowlist is tracked as a follow-up.
    """
    bare = media_type.split(";", 1)[0].strip()
    if is_json_media_type(bare):
        return False
    if bare.lower().startswith("text/"):
        return True
    return bool(_XML_MEDIA_RE.match(bare))
