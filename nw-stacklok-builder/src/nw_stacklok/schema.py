# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""``mcp-builder-schema``: write the mcp-scope.yaml JSON schema (stacklok's ``task generate-schema``).

The vendored ai-scoping skill reads it during Phase 1. The schema includes node-wire's optional
``runtime:`` block.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp_builder.schema.models import MCPScope

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "mcp-scope-schema.json"


def write_schema(path: Path = SCHEMA_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(MCPScope.model_json_schema(), indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    print(write_schema())
