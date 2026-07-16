# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""CLI for nw-mcp-builder (connector mode only).

Usage:
  uv run nw-mcp-builder google_drive
  uv run nw-mcp-builder salesforce --force-output --skip-build-wheels
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from nw_mcp_builder.from_connector import format_success_message, run_from_connector


def _package_root() -> Path:
    # src/nw_mcp_builder/cli.py → package root is parents[2]
    return Path(__file__).resolve().parents[2]


def _default_node_wire_root() -> Path:
    # nw-mcp-builder lives inside the node-wire repo
    return _package_root().parent


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="nw-mcp-builder",
        description=(
            "Build wheels, write a connector-mode scope fixture, and generate "
            "a thin MCP host under out/."
        ),
    )
    parser.add_argument(
        "connector_id",
        help="node-wire connector id (e.g. google_drive, salesforce, fhir_epic)",
    )
    parser.add_argument(
        "--node-wire-root",
        type=Path,
        default=None,
        help="node-wire repo root (default: parent of nw-mcp-builder/)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for generated hosts (default: nw-mcp-builder/out)",
    )
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=None,
        help="Directory for *_nw.yaml fixtures (default: nw-mcp-builder/fixtures)",
    )
    parser.add_argument(
        "--skip-build-wheels",
        action="store_true",
        help="Reuse existing wheels in packages/*/dist",
    )
    parser.add_argument(
        "--force-fixture",
        action="store_true",
        help="Overwrite fixtures/<connector_id>_nw.yaml from connector source",
    )
    parser.add_argument(
        "--force-output",
        action="store_true",
        help="Replace existing out/<server>-mcp/ if present",
    )
    parser.add_argument(
        "--python",
        default=None,
        help="Python for wheel builds (sets UV_PYTHON)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Debug logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    package_root = _package_root()
    node_wire_root = (args.node_wire_root or _default_node_wire_root()).resolve()

    try:
        project_dir = run_from_connector(
            args.connector_id,
            node_wire_root=node_wire_root,
            package_root=package_root,
            output_dir=args.output_dir,
            fixtures_dir=args.fixtures_dir,
            skip_build_wheels=args.skip_build_wheels,
            force_fixture=args.force_fixture,
            force_output=args.force_output,
            python=args.python,
        )
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(format_success_message(project_dir, args.connector_id))


if __name__ == "__main__":
    main()
