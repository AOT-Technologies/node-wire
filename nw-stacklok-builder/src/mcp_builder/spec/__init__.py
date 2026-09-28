"""Low-level OpenAPI spec parsing.

An OpenAPI spec is a machine-readable description of a REST API, written in
YAML or JSON. It lists every URL path the API exposes (e.g. ``/items/{itemId}``),
what HTTP methods each path supports (GET, POST, ...), and what data each
endpoint expects and returns.

This package turns raw spec files into typed Python objects so that the
code-generation pipeline can work with them safely. It handles:

    - **Loading** — reading a YAML/JSON file into a typed model (``loader``).
    - **Parameters** — extracting the inputs an endpoint expects.
      In a REST API, inputs arrive in two places:

        * **URL parameters** — values embedded in the URL itself.
          Path parameters like ``{itemId}`` in ``/items/{itemId}`` and query
          parameters like ``?fields=name`` are both URL parameters.
        * **Body fields** — properties of the JSON object sent in the request
          body (typically for POST/PUT/PATCH). For example, ``{"name": "Foo"}``
          has one body field called ``name``.

    - **$ref resolution** — OpenAPI specs use JSON Reference pointers
      (``$ref: '#/components/parameters/owner'``) to avoid repeating the same
      definition in multiple places. The ``resolver`` module looks up these
      pointers and returns the actual object they point to.

Public API (re-exported here for convenience) is intentionally narrow — the
names callers need to extract endpoint inputs and describe them by type.
Lower-level helpers (individual ``resolve_*`` functions, schema/location
literal types) live in the submodules ``loader``, ``parameters``, ``resolver``,
and ``types``; import directly from there if you need them (tests do).
"""

from mcp_builder.spec.loader import load_openapi_spec
from mcp_builder.spec.parameters import (
    get_body_fields,
    get_parameters,
    get_response_content_types,
    parse_endpoint,
)
from mcp_builder.spec.resolver import extract_schema_type
from mcp_builder.spec.types import (
    OPENAPI_TYPE_MAP,
    ExtractedBodyField,
    ExtractedParameter,
    ExtractedResponse,
    OpenAPISpec,
    PythonType,
)

__all__ = [
    "OPENAPI_TYPE_MAP",
    "ExtractedBodyField",
    "ExtractedParameter",
    "ExtractedResponse",
    "OpenAPISpec",
    "PythonType",
    "extract_schema_type",
    "get_body_fields",
    "get_parameters",
    "get_response_content_types",
    "load_openapi_spec",
    "parse_endpoint",
]
