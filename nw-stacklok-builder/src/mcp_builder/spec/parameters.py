"""Parameter and body field extraction from OpenAPI operations.

Given an HTTP method and URL path (e.g. ``GET /items/{itemId}``), the
functions in this module look up the matching operation in the OpenAPI spec
and extract its inputs as typed Python objects:

    - **get_parameters()** returns URL parameters — values the caller passes
      in the URL path (like ``{itemId}``) or query string (like ``?fields=name``).

      Example: for ``GET /items/{itemId}?fields=name``, this returns two
      ``ExtractedParameter`` objects: one for ``itemId`` (path, required) and
      one for ``fields`` (query, optional).

    - **get_body_fields()** returns request body fields — properties of the
      JSON object sent in the request body (typically POST/PUT/PATCH).

      Example: for ``POST /items`` with body ``{"name": "Widget"}``, this
      returns one ``ExtractedBodyField`` for ``name`` (string, required).

Uses the resolver module for $ref resolution and the types module for
data models.
"""

from __future__ import annotations

import structlog
from openapi_pydantic.v3.v3_0 import Operation as Op30
from openapi_pydantic.v3.v3_0 import Parameter as OAParam30
from openapi_pydantic.v3.v3_0 import PathItem as PathItem30
from openapi_pydantic.v3.v3_0 import Reference as Ref30
from openapi_pydantic.v3.v3_1 import Operation as Op31
from openapi_pydantic.v3.v3_1 import Parameter as OAParam31
from openapi_pydantic.v3.v3_1 import PathItem as PathItem31
from openapi_pydantic.v3.v3_1 import Reference as Ref31

from mcp_builder.spec.resolver import (
    extract_schema_type,
    resolve_composed_schema,
    resolve_parameter_ref,
    resolve_request_body_ref,
    resolve_response_ref,
    resolve_schema_ref,
    schema_to_type,
)
from mcp_builder.spec.types import (
    ExtractedBodyField,
    ExtractedParameter,
    ExtractedResponse,
    OpenAPISpec,
)

logger = structlog.get_logger()


def parse_endpoint(endpoint: str) -> tuple[str, str]:
    """Parse an endpoint string into its HTTP method and path.

    Example:
        >>> parse_endpoint("GET /items/{itemId}")
        ("GET", "/items/{itemId}")

    Args:
        endpoint: Endpoint string in "METHOD /path" format.

    Returns:
        Tuple of (method, path).

    Raises:
        ValueError: If the endpoint string is not in the expected format.
    """
    parts = endpoint.split(" ", 1)
    if len(parts) != 2:
        raise ValueError(f"Endpoint '{endpoint}' must be in 'METHOD /path' format")
    return parts[0], parts[1]


def _get_operation(
    spec: OpenAPISpec, method: str, path: str
) -> tuple[PathItem30 | PathItem31, Op30 | Op31]:
    """Look up a path item and operation from the spec.

    Shared by get_parameters() and get_body_fields() to avoid
    duplicating the path/method lookup and error handling.

    Raises:
        KeyError: If the path or method is not found in the spec.
    """
    path_item = (spec.paths or {}).get(path)
    if path_item is None:
        raise KeyError(f"Path '{path}' not found in spec")
    operation = getattr(path_item, method.lower(), None)
    if operation is None:
        raise KeyError(f"Method '{method}' not found for path '{path}'")
    logger.debug(
        "resolved operation",
        method=method,
        path=path,
        operation_id=getattr(operation, "operationId", None),
    )
    return path_item, operation


def get_parameters(
    spec: OpenAPISpec, method: str, path: str
) -> list[ExtractedParameter]:
    """Extract all path/query parameters for an operation.

    Merges path-level and operation-level parameters. When both define a
    parameter with the same (name, location), the operation-level one wins.

    Args:
        spec: Typed OpenAPI spec.
        method: HTTP method (e.g., "GET").
        path: URL path (e.g., "/items/{itemId}").

    Returns:
        List of extracted parameters. Empty if the operation has none.

    Raises:
        KeyError: If the path or method is not found in the spec.
    """
    path_item, operation = _get_operation(spec, method, path)

    # Merge path-level and operation-level params. Operation wins on conflict.
    merged: dict[tuple[str, str], OAParam30 | OAParam31] = {}

    for param in path_item.parameters or []:
        if isinstance(param, Ref30 | Ref31):
            param = resolve_parameter_ref(spec, param.ref)
        merged[(param.name, param.param_in.value)] = param

    for param in operation.parameters or []:
        if isinstance(param, Ref30 | Ref31):
            param = resolve_parameter_ref(spec, param.ref)
        merged[(param.name, param.param_in.value)] = param

    path_level_count = len(path_item.parameters or [])
    op_level_count = len(operation.parameters or [])
    logger.debug(
        "merged parameters",
        method=method,
        path=path,
        path_level=path_level_count,
        operation_level=op_level_count,
        unique=len(merged),
    )

    result = []
    for param in merged.values():
        schema_type = extract_schema_type(param, spec)
        result.append(
            ExtractedParameter(
                name=param.name,
                location=param.param_in.value,
                required=param.required,
                schema_type=schema_type,
                description=param.description or "",
            )
        )
        logger.debug(
            "extracted parameter",
            name=param.name,
            location=param.param_in.value,
            schema_type=schema_type,
            required=param.required,
        )

    if not result:
        logger.debug("no parameters found", method=method, path=path)
    else:
        logger.debug(
            "parameter extraction complete",
            method=method,
            path=path,
            count=len(result),
        )
    return result


def get_response_content_types(
    spec: OpenAPISpec, method: str, path: str
) -> list[ExtractedResponse]:
    """Extract declared media types for each 2xx response of an operation.

    "2xx" refers to HTTP status codes in the 200–299 range — the success
    family (200 OK, 201 Created, 204 No Content, etc.). Those are the
    only responses that shape the return type of a generated tool, so
    we ignore 4xx/5xx (errors) and 3xx (redirects) here.

    Walks ``operation.responses`` and returns one ``ExtractedResponse``
    per matching status code, with sorted media types from the
    response's ``content`` block. Resolves ``$ref`` on Response objects.

    OpenAPI's ``"default"`` key is the fallback for status codes not
    otherwise listed. It's only included here when no explicit 2xx
    status exists — otherwise ``default`` is conventionally the error
    shape and including it would conflate error and success bodies.

    Return-shape semantics:

    - Empty list: spec declares no 2xx responses at all.
    - ``ExtractedResponse`` with empty ``media_types``: response is
      declared with no ``content`` block (204 No Content-style).

    Args:
        spec: Typed OpenAPI spec.
        method: HTTP method (e.g., "GET").
        path: URL path (e.g., "/employees/{id}/photo").

    Returns:
        List of ExtractedResponse records, one per 2xx status code.

    Raises:
        KeyError: If the path or method is not found in the spec.
    """
    _, operation = _get_operation(spec, method, path)
    responses = getattr(operation, "responses", None) or {}

    has_explicit_2xx = any(code.startswith("2") for code in responses)

    result: list[ExtractedResponse] = []
    for status_code, response in responses.items():
        is_2xx = status_code.startswith("2")
        is_default_success = status_code == "default" and not has_explicit_2xx
        if not (is_2xx or is_default_success):
            continue
        if isinstance(response, Ref30 | Ref31):
            logger.debug(
                "resolving response $ref",
                ref=response.ref,
                method=method,
                path=path,
                status=status_code,
            )
            response = resolve_response_ref(spec, response.ref)
        media_types = sorted((response.content or {}).keys())
        result.append(
            ExtractedResponse(status_code=status_code, media_types=media_types)
        )
        logger.debug(
            "extracted response content types",
            method=method,
            path=path,
            status=status_code,
            media_types=media_types,
        )

    return result


def get_body_fields(
    spec: OpenAPISpec, method: str, path: str
) -> list[ExtractedBodyField]:
    """Extract request body fields for an operation.

    Looks for an application/json request body with either inline properties
    or a $ref to a component schema. Resolves the $ref and extracts fields.

    Args:
        spec: Typed OpenAPI spec.
        method: HTTP method (e.g., "POST").
        path: URL path (e.g., "/items").

    Returns:
        List of extracted body fields. Empty if the operation has no JSON body.

    Raises:
        KeyError: If the path or method is not found in the spec.
    """
    _, operation = _get_operation(spec, method, path)

    if operation.requestBody is None:
        logger.debug("no request body", method=method, path=path)
        return []

    req_body = operation.requestBody
    if isinstance(req_body, Ref30 | Ref31):
        req_body = resolve_request_body_ref(spec, req_body.ref)

    json_media = (req_body.content or {}).get("application/json")
    if json_media is None:
        logger.debug("no application/json media type", method=method, path=path)
        return []

    schema = json_media.media_type_schema
    if schema is None:
        logger.debug("no schema in json media type", method=method, path=path)
        return []

    # Resolve $ref to component schema
    if isinstance(schema, Ref30 | Ref31):
        ref_str = schema.ref
        logger.debug(
            "resolving body schema $ref", ref=ref_str, method=method, path=path
        )
        schema = resolve_schema_ref(spec, ref_str)

    required_names = set(schema.required or [])
    fields = []
    # Flatten allOf/oneOf/anyOf into merged properties before extraction
    if any(getattr(schema, k, None) for k in ("allOf", "oneOf", "anyOf")):
        logger.debug(
            "resolving schema composition",
            method=method,
            path=path,
            allOf=bool(schema.allOf),
            oneOf=bool(schema.oneOf),
            anyOf=bool(schema.anyOf),
        )
        schema = resolve_composed_schema(spec, schema)
        required_names = set(schema.required or [])

    if not (schema.properties or {}):
        logger.warning("body schema has no properties", method=method, path=path)

    for name, prop in (schema.properties or {}).items():
        # Resolve $ref properties to their underlying schema
        if isinstance(prop, Ref30 | Ref31):
            logger.debug(
                "resolving body property $ref",
                property_name=name,
                ref=prop.ref,
                method=method,
                path=path,
            )
            prop = resolve_schema_ref(spec, prop.ref)
        prop_type = schema_to_type(prop)
        fields.append(
            ExtractedBodyField(
                name=name,
                schema_type=prop_type,
                description=prop.description or "",
                required=name in required_names,
            )
        )
        logger.debug(
            "extracted body field",
            name=name,
            schema_type=prop_type,
            required=name in required_names,
        )

    logger.debug(
        "body field extraction complete",
        method=method,
        path=path,
        field_count=len(fields),
        required_fields=sorted(required_names),
    )
    return fields
