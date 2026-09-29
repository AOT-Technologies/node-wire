# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""The ``runtime:`` block node-wire adds to stacklok's ``mcp-scope.yaml``.

```yaml
runtime:
  type: node_wire
  connector_id: pet_store
```

With it, every scoped tool runs through that node-wire connector instead of a generated HTTP
client. Without it, the vendored generator behaves exactly like upstream stacklok.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NodeWireRuntime(BaseModel):
    """Run the scoped tools on a node-wire connector."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["node_wire"]
    connector_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


class NodeWirePlan(BaseModel):
    """What the node-wire renderers need beyond stacklok's own plan."""

    model_config = ConfigDict(extra="forbid")

    connector_id: str
    node_wire_root: str
