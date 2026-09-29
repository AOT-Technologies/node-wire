"""Type definitions and constants for OpenAPI spec parsing.

This module contains only type aliases, pydantic models, and constants.
No functions — it serves as the shared vocabulary for the spec package.

OpenAPI terminology for newcomers:

    **Parameter** — a named input that travels in the URL, not in the
    request body. In ``GET /items/{itemId}?fields=name``:

        * ``itemId`` is a **path parameter** (embedded in the URL path).
        * ``fields`` is a **query parameter** (after the ``?``).

    Parameters can also arrive in HTTP headers or cookies, though those are
    less common.

    **Body field** — a property of the JSON object sent as the HTTP request
    body, typically for POST/PUT/PATCH requests. In a request like
    ``POST /items`` with body ``{"name": "Widget", "price": 9.99}``:

        * ``name`` and ``price`` are body fields.

    Body fields and parameters are separate concepts in OpenAPI — they live
    in different parts of the spec and arrive at different places in the HTTP
    request.
"""

from __future__ import annotations

from typing import Annotated, Literal

from openapi_pydantic.v3.v3_0 import OpenAPI as OpenAPI30
from openapi_pydantic.v3.v3_0 import Schema as Schema30
from openapi_pydantic.v3.v3_1 import OpenAPI as OpenAPI31
from openapi_pydantic.v3.v3_1 import Schema as Schema31
from pydantic import BaseModel, StringConstraints

# Union of both OpenAPI versions. The field interfaces are identical
# (same names, same types) so downstream code can treat them uniformly.
OpenAPISpec = OpenAPI30 | OpenAPI31
OpenAPISchema = Schema30 | Schema31

# The set of parameter locations, schema types, and Python types we support,
# expressed as Literal types for static type safety.
ParameterLocation = Literal["path", "query", "header", "cookie"]
PythonType = Literal["str", "int", "float", "bool", "list", "dict"]

# OpenAPI types that map to Python built-in types. We intentionally error
# on unknown types rather than silently defaulting to str — this catches
# spec issues early rather than producing subtly wrong generated code.
OPENAPI_TYPE_MAP: dict[str, PythonType] = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
}
SchemaType = Literal["string", "integer", "number", "boolean", "array", "object"]

# Valid OpenAPI response status-code keys: three-digit codes (200, 404, …),
# the 3.1 range patterns (1XX–5XX), and the "default" fallback. Enforced at
# pydantic model construction; downstream code keeps treating the value as
# a string (``.startswith("2")`` etc.) rather than an int.
StatusCode = Annotated[str, StringConstraints(pattern=r"^(\d{3}|[1-5]XX|default)$")]


class ExtractedParameter(BaseModel):
    """A single parameter extracted from an OpenAPI operation.

    Consumed by: codegen.plan.build_server_plan() to build ParamPlan objects.

    Example (from GET /items/{itemId}):
        ExtractedParameter(
            name="itemId", location="path", required=True,
            schema_type="string", description="The ID of the item."
        )
    """

    name: str
    location: ParameterLocation
    required: bool = False
    schema_type: SchemaType = "string"
    description: str = ""


class ExtractedBodyField(BaseModel):
    """A single field from a request body schema.

    Consumed by: codegen.plan.build_server_plan() to build ParamPlan objects
    with location="body".

    Example (from POST /items with CreateItemRequest body):
        ExtractedBodyField(
            name="name", schema_type="string",
            description="The name of the item.", required=True
        )
    """

    name: str
    schema_type: SchemaType = "string"
    description: str = ""
    required: bool = False


class ExtractedResponse(BaseModel):
    """Declared media types for a single 2xx response of an operation.

    Consumed by the codegen validator to cross-check a scope's declared
    ``response_kind`` (``json``, ``text``, or ``binary``; ``auto`` opts
    out of the cross-check) against the media types the spec actually
    declares.

    ``media_types`` is sorted for stable error messages. An empty list
    means the response is declared with no ``content`` block (e.g. 204
    No Content) — distinct from "no 2xx response declared at all",
    which is represented by an empty list of ``ExtractedResponse`` at
    the caller level.

    Example (from GET /employees/{id}/photo):
        ExtractedResponse(status_code="200", media_types=["image/jpeg"])
    """

    status_code: StatusCode
    # media_types stays ``list[str]`` deliberately. Media-type grammar
    # (RFC 6838) allows arbitrary vendor types, so a narrower Literal or
    # enum would reject valid inputs. Shape parsing isn't useful here —
    # is_json_media_type() only asks "is this JSON?", which is a cheap
    # regex, not a grammar walk.
    media_types: list[str]
