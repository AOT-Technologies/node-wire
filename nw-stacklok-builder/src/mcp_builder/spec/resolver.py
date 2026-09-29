"""$ref resolution and schema-level type utilities.

OpenAPI specs avoid repetition by using **$ref pointers** — instead of
writing out the same parameter or schema definition in every endpoint,
the spec says ``$ref: '#/components/parameters/owner'`` and defines
``owner`` once under ``components``. Think of it like a symbolic link:
the pointer says "go look over there for the real definition."

This module resolves those pointers: given a ``$ref`` string, it finds
the matching object in ``spec.components`` and returns it as a typed
Python object. It also provides schema-to-type extraction, which maps
OpenAPI schema types (``string``, ``integer``, ...) to the type names
used in code generation.
"""

from __future__ import annotations

from typing import cast

import structlog
from openapi_pydantic.v3.v3_0 import Parameter as OAParam30
from openapi_pydantic.v3.v3_0 import Reference as Ref30
from openapi_pydantic.v3.v3_0 import RequestBody as ReqBody30
from openapi_pydantic.v3.v3_0 import Response as Resp30
from openapi_pydantic.v3.v3_1 import Parameter as OAParam31
from openapi_pydantic.v3.v3_1 import Reference as Ref31
from openapi_pydantic.v3.v3_1 import RequestBody as ReqBody31
from openapi_pydantic.v3.v3_1 import Response as Resp31

from mcp_builder.spec.types import (
    OPENAPI_TYPE_MAP,
    OpenAPISchema,
    OpenAPISpec,
    SchemaType,
)

logger = structlog.get_logger()


def resolve_parameter_ref(spec: OpenAPISpec, ref: str) -> OAParam30 | OAParam31:
    """Look up an OpenAPI ``$ref`` string in ``spec.components.parameters``.

    OpenAPI specs use JSON Reference pointers like
    ``#/components/parameters/owner`` instead of inlining a parameter object.
    This function extracts the component name from the pointer (``owner``),
    finds the matching entry in ``spec.components.parameters``, and returns
    the resolved parameter object.

    Raises:
        ValueError: If the ref is external/non-component, the components
            section is missing, the named parameter doesn't exist, or it
            is itself a nested ``$ref``.
    """
    if not ref.startswith("#/components/parameters/"):
        raise ValueError(
            f"Cannot resolve parameter $ref '{ref}': "
            "only local '#/components/parameters/...' refs are supported."
        )
    param_name = ref.rsplit("/", 1)[-1]
    logger.debug("resolving parameter $ref", ref=ref, component=param_name)
    if spec.components is None:
        raise ValueError(
            f"Cannot resolve parameter $ref '{ref}': spec has no 'components' section."
        )
    params = spec.components.parameters or {}
    param = params.get(param_name)
    if param is None:
        raise ValueError(
            f"Cannot resolve parameter $ref '{ref}': "
            f"'{param_name}' not found in components.parameters."
        )
    if isinstance(param, Ref30 | Ref31):
        raise ValueError(
            f"Cannot resolve parameter $ref '{ref}': "
            f"'{param_name}' is itself a nested $ref, which is not supported."
        )
    logger.debug("resolved parameter $ref", param_name=param_name)
    return param


def resolve_request_body_ref(spec: OpenAPISpec, ref: str) -> ReqBody30 | ReqBody31:
    """Look up an OpenAPI ``$ref`` string in ``spec.components.requestBodies``.

    OpenAPI specs use JSON Reference pointers like
    ``#/components/requestBodies/CreateUserRequest`` instead of inlining a
    request body object. This function extracts the component name from the
    pointer, finds the matching entry in ``spec.components.requestBodies``,
    and returns the resolved request body object.

    Raises:
        ValueError: If the ref is external/non-component, the components
            section is missing, the named request body doesn't exist, or it
            is itself a nested ``$ref``.
    """
    if not ref.startswith("#/components/requestBodies/"):
        raise ValueError(
            f"Cannot resolve requestBody $ref '{ref}': "
            "only local '#/components/requestBodies/...' refs are supported."
        )
    body_name = ref.rsplit("/", 1)[-1]
    logger.debug("resolving requestBody $ref", ref=ref, component=body_name)
    if spec.components is None:
        raise ValueError(
            f"Cannot resolve requestBody $ref '{ref}': "
            "spec has no 'components' section."
        )
    bodies = spec.components.requestBodies or {}
    body = bodies.get(body_name)
    if body is None:
        raise ValueError(
            f"Cannot resolve requestBody $ref '{ref}': "
            f"'{body_name}' not found in components.requestBodies."
        )
    if isinstance(body, Ref30 | Ref31):
        raise ValueError(
            f"Cannot resolve requestBody $ref '{ref}': "
            f"'{body_name}' is itself a nested $ref, which is not supported."
        )
    logger.debug("resolved requestBody $ref", body_name=body_name)
    return body


def resolve_response_ref(spec: OpenAPISpec, ref: str) -> Resp30 | Resp31:
    """Look up an OpenAPI ``$ref`` string in ``spec.components.responses``.

    OpenAPI specs may factor out shared response definitions under
    ``components.responses`` and reference them from operations. This
    function resolves a pointer like ``#/components/responses/BinaryImage``
    to the actual Response object.

    Raises:
        ValueError: If the ref is external/non-component, the components
            section is missing, the named response doesn't exist, or it
            is itself a nested ``$ref``.
    """
    if not ref.startswith("#/components/responses/"):
        raise ValueError(
            f"Cannot resolve response $ref '{ref}': "
            "only local '#/components/responses/...' refs are supported."
        )
    resp_name = ref.rsplit("/", 1)[-1]
    logger.debug("resolving response $ref", ref=ref, component=resp_name)
    if spec.components is None:
        raise ValueError(
            f"Cannot resolve response $ref '{ref}': spec has no 'components' section."
        )
    responses = spec.components.responses or {}
    resp = responses.get(resp_name)
    if resp is None:
        raise ValueError(
            f"Cannot resolve response $ref '{ref}': "
            f"'{resp_name}' not found in components.responses."
        )
    if isinstance(resp, Ref30 | Ref31):
        raise ValueError(
            f"Cannot resolve response $ref '{ref}': "
            f"'{resp_name}' is itself a nested $ref, which is not supported."
        )
    logger.debug("resolved response $ref", resp_name=resp_name)
    return resp


def resolve_schema_ref(spec: OpenAPISpec, ref: str) -> OpenAPISchema:
    """Resolve a $ref string like '#/components/schemas/Foo' to the schema object.

    Raises:
        ValueError: If the ref is external/non-component, the components
            section is missing, the named schema doesn't exist, or it
            is itself a nested ``$ref``.
    """
    if not ref.startswith("#/components/schemas/"):
        raise ValueError(
            f"Cannot resolve schema $ref '{ref}': "
            "only local '#/components/schemas/...' refs are supported."
        )
    schema_name = ref.rsplit("/", 1)[-1]
    logger.debug("resolving schema $ref", ref=ref, component=schema_name)
    if spec.components is None:
        raise ValueError(
            f"Cannot resolve schema $ref '{ref}': spec has no 'components' section."
        )
    schemas = spec.components.schemas or {}
    schema = schemas.get(schema_name)
    if schema is None:
        raise ValueError(
            f"Cannot resolve schema $ref '{ref}': "
            f"'{schema_name}' not found in components.schemas."
        )
    if isinstance(schema, Ref30 | Ref31):
        raise ValueError(
            f"Cannot resolve schema $ref '{ref}': "
            f"'{schema_name}' is itself a nested $ref, which is not supported."
        )
    logger.debug("resolved schema $ref", schema_name=schema_name)
    return schema


def resolve_composed_schema(
    spec: OpenAPISpec,
    schema: OpenAPISchema,
    *,
    _seen: frozenset[str] = frozenset(),
) -> OpenAPISchema:
    """Flatten ``allOf``/``oneOf``/``anyOf`` into a single schema with merged properties.

    Resolves ``$ref`` sub-schemas and recurses into nested composition.
    A ``_seen`` set guards against circular ``$ref`` chains.

    For ``allOf``, required lists are unioned (all sub-schema requirements apply).
    For ``oneOf``/``anyOf``, all properties are collected but none are marked
    required — we cannot know which variant the caller will use.
    """

    merged_props: dict[str, Ref30 | Ref31 | OpenAPISchema] = {}
    allof_required: set[str] = set()

    def _collect(sub: OpenAPISchema, from_allof: bool) -> None:
        """Merge a single sub-schema's properties and required list."""
        nonlocal merged_props, allof_required
        for name, prop in (sub.properties or {}).items():
            merged_props[name] = prop
        if from_allof:
            allof_required.update(sub.required or [])

    def _resolve_sub(sub: Ref30 | Ref31 | OpenAPISchema, from_allof: bool) -> None:
        """Resolve one sub-schema and fold it into the merged result.

        Three cases: ``$ref`` (resolve, skip on cycle), nested composition
        (recurse via ``resolve_composed_schema``), plain inline (merge
        directly). ``from_allof`` gates whether ``required`` contributes —
        only ``allOf`` members do, since ``oneOf``/``anyOf`` are alternatives.
        """
        nonlocal _seen
        # Resolve $ref pointers
        if isinstance(sub, (Ref30, Ref31)):
            if sub.ref in _seen:
                logger.warning("circular $ref skipped", ref=sub.ref)
                return
            _seen = _seen | {sub.ref}
            sub = resolve_schema_ref(spec, sub.ref)

        # Recurse if the resolved schema itself has composition
        if any(getattr(sub, k, None) for k in ("allOf", "oneOf", "anyOf")):
            sub = resolve_composed_schema(spec, sub, _seen=_seen)

        _collect(sub, from_allof)

    for sub in schema.allOf or []:
        _resolve_sub(sub, from_allof=True)
    for sub in schema.oneOf or []:
        _resolve_sub(sub, from_allof=False)
    for sub in schema.anyOf or []:
        _resolve_sub(sub, from_allof=False)

    # Include the parent schema's own properties and required
    for name, prop in (schema.properties or {}).items():
        merged_props[name] = prop
    allof_required.update(schema.required or [])

    logger.debug(
        "resolved schema composition",
        property_count=len(merged_props),
        required_count=len(allof_required),
    )

    return schema.model_copy(
        update={
            "properties": merged_props or None,
            "required": sorted(allof_required) or None,
            "allOf": None,
            "oneOf": None,
            "anyOf": None,
        }
    )


def extract_schema_type(param: OAParam30 | OAParam31, spec: OpenAPISpec) -> SchemaType:
    """Extract the schema type string from a parameter's schema.

    If the schema is a ``$ref`` (common in specs that share component
    schemas across parameters, e.g. BambooHR, Stripe, GitHub), the ref
    is resolved against ``spec.components.schemas`` before type extraction.
    Nested ref chains (ref → ref → inline) are followed with cycle
    detection.

    Raises:
        ValueError: If the parameter has no schema, the ``$ref`` cannot be
            resolved, a ref cycle is detected, or the resolved schema's
            type is missing, null-only, or unknown.
    """
    schema = param.param_schema
    if schema is None:
        raise ValueError(
            f"Parameter '{param.name}' has no inline schema "
            f"(schema_kind=None). Cannot determine type."
        )

    if isinstance(schema, Ref30 | Ref31):
        seen: set[str] = set()
        while isinstance(schema, Ref30 | Ref31):
            if schema.ref in seen:
                raise ValueError(
                    f"Parameter '{param.name}' has a circular $ref chain "
                    f"through '{schema.ref}'."
                )
            seen.add(schema.ref)
            schema = resolve_schema_ref_allow_chain(spec, schema.ref)

    return schema_to_type(schema)


def resolve_schema_ref_allow_chain(
    spec: OpenAPISpec, ref: str
) -> Ref30 | Ref31 | OpenAPISchema:
    """Resolve a schema ``$ref`` without rejecting nested refs.

    Unlike :func:`resolve_schema_ref`, this returns the component as-is —
    if it's another ``$ref``, the caller is expected to keep walking.
    Used by callers that handle ref chains themselves (with cycle detection).

    Raises:
        ValueError: If the ref is external/non-component, the components
            section is missing, or the named schema doesn't exist.
    """
    if not ref.startswith("#/components/schemas/"):
        raise ValueError(
            f"Cannot resolve schema $ref '{ref}': "
            "only local '#/components/schemas/...' refs are supported."
        )
    schema_name = ref.rsplit("/", 1)[-1]
    if spec.components is None:
        raise ValueError(
            f"Cannot resolve schema $ref '{ref}': spec has no 'components' section."
        )
    schemas = spec.components.schemas or {}
    schema = schemas.get(schema_name)
    if schema is None:
        raise ValueError(
            f"Cannot resolve schema $ref '{ref}': "
            f"'{schema_name}' not found in components.schemas."
        )
    return schema


def schema_to_type(schema: OpenAPISchema) -> SchemaType:
    """Extract the type string from an inline schema object.

    Schemas without an explicit ``type`` field default to ``"object"`` — this
    is common in composed schemas where the type is implied by ``properties``.

    Raises:
        ValueError: If the type list contains only nulls, or the type is
            not in OPENAPI_TYPE_MAP.
    """
    raw_type = schema.type
    if raw_type is None:
        # No explicit type: default to "object" (dict). OpenAPI specs
        # commonly omit type on schemas that have properties, use
        # composition (allOf/oneOf/anyOf), or are intentionally free-form.
        logger.warning(
            "schema has no 'type' field, defaulting to object",
            description=schema.description[:80] if schema.description else None,
        )
        return cast(SchemaType, "object")
    # v3.1 can return a list of types; take the first non-null one
    if isinstance(raw_type, list):
        for t in raw_type:
            if t.value != "null":
                raw_type = t
                break
        else:
            raise ValueError("Schema type list contains only null types.")
    type_str: str = raw_type.value if hasattr(raw_type, "value") else str(raw_type)
    if type_str not in OPENAPI_TYPE_MAP:
        raise ValueError(
            f"Unknown OpenAPI schema type '{type_str}'. "
            f"Supported types: {sorted(OPENAPI_TYPE_MAP.keys())}"
        )
    # We've verified type_str is in OPENAPI_TYPE_MAP, which only contains valid
    # SchemaType values, so this cast is safe.
    return cast(SchemaType, type_str)
