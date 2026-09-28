"""OpenAPI spec file loading.

An OpenAPI spec is a YAML or JSON file that describes a REST API: its URL
paths, HTTP methods, expected inputs, and responses. Loading one gives you
a fully typed Python object you can inspect programmatically instead of
manually parsing YAML.

File I/O only — reads JSON/YAML, parses via openapi-pydantic, rejects
Swagger 2.0 specs with a clear conversion hint.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog
import yaml
from openapi_pydantic import parse_obj

from mcp_builder.spec.types import OpenAPISpec

logger = structlog.get_logger()


def load_openapi_spec(path: str | Path) -> OpenAPISpec:
    """Load an OpenAPI spec from a JSON or YAML file into a typed model.

    Supports OpenAPI 3.0.x and 3.1.x specs. The version is auto-detected
    from the ``openapi`` field in the document.

    Args:
        path: Path to the OpenAPI spec file (.json, .yaml, or .yml).

    Returns:
        Typed OpenAPI model (OpenAPI30 or OpenAPI31).

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the spec cannot be parsed into a valid OpenAPI model.
    """
    path = Path(path)
    logger.info("loading openapi spec", path=str(path))
    with path.open() as f:
        if path.suffix in (".yaml", ".yml"):
            raw = yaml.safe_load(f)
        else:
            raw = json.load(f)
    logger.debug("detected spec format", suffix=path.suffix)

    # Swagger 2.0 specs use "swagger" instead of "openapi". The
    # openapi-pydantic library only supports 3.x, so fail early with a
    # message that tells the caller how to convert.
    if "swagger" in raw and not raw.get("openapi"):
        is_yaml = path.suffix in (".yaml", ".yml")
        output = path.with_suffix(".openapi3.yaml" if is_yaml else ".openapi3.json")
        yaml_flag = " --yaml" if is_yaml else ""
        raise ValueError(
            f"'{path}' is a Swagger {raw['swagger']} spec. "
            "Only OpenAPI 3.0+ is supported. Convert with: "
            f"npm install -g swagger2openapi && "
            f"swagger2openapi {path} -o {output}{yaml_flag}"
        )

    spec = parse_obj(raw)
    if spec is None:
        raise ValueError(f"Failed to parse OpenAPI spec from '{path}'")

    path_count = len(spec.paths) if spec.paths else 0
    logger.info(
        "openapi spec loaded",
        openapi_version=raw.get("openapi", "unknown"),
        title=spec.info.title,
        spec_version=spec.info.version,
        path_count=path_count,
    )
    return spec
