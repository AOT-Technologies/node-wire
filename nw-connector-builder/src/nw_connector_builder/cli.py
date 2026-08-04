# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""CLI for nw-connector-builder.

Usage:
  nw-connector-builder <SPEC> --id <connector_id> [--wire] [--force] [--no-mcp]
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

from nw_connector_builder.pipeline import BuildError, UsageError, run_build


def _package_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_node_wire_root() -> Path:
    return _package_root().parent


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="nw-connector-builder",
        description=(
            "Turn a Swagger/OpenAPI spec into a node-wire connector "
            "(and optionally an MCP server via nw-mcp-builder)."
        ),
    )
    parser.add_argument("spec", help="Local path or http(s) URL to an OpenAPI/Swagger document")
    parser.add_argument(
        "--id",
        required=True,
        dest="connector_id",
        help="Connector id ([a-z][a-z0-9_]*)",
    )
    parser.add_argument(
        "--wire",
        action="store_true",
        help="After a clean promote, edit connectors.yaml + sample.env",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing connector and force MCP project regeneration",
    )
    parser.add_argument(
        "--no-mcp",
        action="store_true",
        help="Stop after a clean connector build (skip MCP hand-off)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override baked-in default base URL (else servers[0])",
    )
    parser.add_argument(
        "--node-wire-root",
        type=Path,
        default=None,
        help="node-wire repo root (default: parent of nw-connector-builder/)",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="Where to write report.json on abort (default: cwd)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if not re.fullmatch(r"[a-z][a-z0-9_]*", args.connector_id):
        print(
            f"usage error: invalid --id {args.connector_id!r} "
            "(must match [a-z][a-z0-9_]*)",
            file=sys.stderr,
        )
        raise SystemExit(2)

    node_wire_root = (args.node_wire_root or _default_node_wire_root()).resolve()
    try:
        code = run_build(
            spec=args.spec,
            connector_id=args.connector_id,
            node_wire_root=node_wire_root,
            wire=args.wire,
            force=args.force,
            no_mcp=args.no_mcp,
            base_url=args.base_url,
            report_path=args.report_path,
        )
    except UsageError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except BuildError as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    raise SystemExit(code)


if __name__ == "__main__":
    main()
