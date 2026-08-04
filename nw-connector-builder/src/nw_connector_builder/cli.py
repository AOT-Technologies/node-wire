# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""CLI for nw-connector-builder.

Usage:
  nw-connector-builder from-openapi --path <SPEC> --id <connector_id> [--wire] [--force] [--no-mcp]
  nw-connector-builder mcp -c <connector_id> [--force-output] [--skip-build-wheels] ...

Legacy (still accepted):
  nw-connector-builder --path <SPEC> --id <connector_id> ...
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from nw_connector_builder.pipeline import BuildError, UsageError, run_build
from nw_mcp_builder.cli import add_mcp_arguments, configure_logging, run_mcp_from_args


def _package_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_node_wire_root() -> Path:
    return _package_root().parent


_SUBCOMMANDS = frozenset({"from-openapi", "mcp"})


def _normalize_argv(argv: list[str] | None) -> list[str] | None:
    """Prepend ``from-openapi`` for legacy flat ``--path`` / ``--id`` invocations."""
    if argv is None:
        return None
    if not argv:
        return argv
    if argv[0] in _SUBCOMMANDS or argv[0] in {"-h", "--help"}:
        return argv
    # Legacy: flags without a subcommand → from-openapi
    if argv[0].startswith("-"):
        return ["from-openapi", *argv]
    return argv


def _add_from_openapi_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--path",
        required=True,
        dest="spec",
        help="Local path or http(s) URL to an OpenAPI/Swagger document",
    )
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


def _run_from_openapi(args: argparse.Namespace) -> None:
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="nw-connector-builder",
        description=(
            "Build node-wire connectors from OpenAPI/Swagger, and/or generate "
            "MCP hosts from existing connectors."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    from_openapi = subparsers.add_parser(
        "from-openapi",
        help="Generate a connector (and optionally an MCP host) from an OpenAPI/Swagger spec",
        description=(
            "Turn a Swagger/OpenAPI spec into a node-wire connector "
            "(and optionally an MCP server via MCP hand-off)."
        ),
    )
    _add_from_openapi_arguments(from_openapi)

    mcp = subparsers.add_parser(
        "mcp",
        help="Generate an MCP host from an existing connector",
        description=(
            "Build wheels, write a connector-mode scope fixture, and generate "
            "a thin MCP host under nw-mcp-builder/out/."
        ),
    )
    add_mcp_arguments(mcp)

    args = parser.parse_args(_normalize_argv(argv))
    configure_logging(verbose=bool(getattr(args, "verbose", False)))

    if args.command == "from-openapi":
        _run_from_openapi(args)
    elif args.command == "mcp":
        run_mcp_from_args(args)
    else:  # pragma: no cover — argparse required=True
        parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
