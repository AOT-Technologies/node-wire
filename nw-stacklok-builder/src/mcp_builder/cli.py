"""CLI entry point for mcp-builder.

Pipeline stage: CLI (arg parsing, exit codes, error rendering).

Each subcommand is a thin wrapper that calls one domain function:
    - generate: pipeline.run_pipeline() → scaffold a complete MCP server project
    - analyze:  analyze.analyze_spec() → summarize an OpenAPI spec
    - validate: validate.validate_scope() → check a scope against an optional spec

Usage:
    uv run mcp-builder generate scope.yaml openapi.yaml /path/to/mcp-template-py
    uv run mcp-builder analyze openapi.yaml
    uv run mcp-builder validate scope.yaml --openapi-spec openapi.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from mcp_builder.analyze import analyze_spec
from mcp_builder.log import configure_logging
from mcp_builder.pipeline import run_pipeline
from mcp_builder.schema.models import load_scope
from mcp_builder.spec import load_openapi_spec
from mcp_builder.validate import validate_scope


def _cmd_generate(args: argparse.Namespace) -> None:
    """Subcommand: generate a complete MCP server project."""
    project_dir = run_pipeline(
        args.scope_yaml, args.openapi_spec, args.template_dir, args.output_dir
    )
    print(project_dir)


def _cmd_analyze(args: argparse.Namespace) -> None:
    """Subcommand: analyze an OpenAPI spec."""
    spec = load_openapi_spec(args.openapi_spec)
    print(analyze_spec(spec).model_dump_json(indent=2, by_alias=True))


def _cmd_validate(args: argparse.Namespace) -> None:
    """Subcommand: validate a scope against an optional spec."""
    scope = load_scope(args.scope_yaml)
    spec = load_openapi_spec(args.openapi_spec) if args.openapi_spec else None
    result = validate_scope(scope, spec)
    print(result.model_dump_json(indent=2))
    if result.errors:
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser. Three subcommands:

    - ``generate <scope> <spec> <template-dir> [-o OUT]`` → ``run_pipeline``
    - ``analyze <spec>`` → ``analyze_spec`` (emits JSON)
    - ``validate <scope> [--openapi-spec SPEC]`` → ``validate_scope`` (non-zero exit on errors)

    Global flags: ``-v`` / ``--log-level`` control verbosity.
    """
    parser = argparse.ArgumentParser(
        prog="mcp-builder",
        description="Generate a ToolHive-ready MCP server from an OpenAPI spec.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Shorthand for --log-level debug"
    )
    parser.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error"],
        default=None,
        help="Set logging level (default: info, or debug if --verbose)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # generate
    gen = subparsers.add_parser("generate", help="Generate an MCP server project")
    gen.add_argument("scope_yaml", type=Path, help="Path to mcp-scope.yaml")
    gen.add_argument(
        "openapi_spec", type=Path, help="Path to the OpenAPI spec (YAML or JSON)"
    )
    gen.add_argument("template_dir", type=Path, help="Path to mcp-template-py checkout")
    gen.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Output directory (default: current directory)",
    )
    gen.set_defaults(func=_cmd_generate)

    # analyze
    anl = subparsers.add_parser("analyze", help="Analyze an OpenAPI spec")
    anl.add_argument(
        "openapi_spec", type=Path, help="Path to the OpenAPI spec (YAML or JSON)"
    )
    anl.set_defaults(func=_cmd_analyze)

    # validate
    val = subparsers.add_parser(
        "validate", help="Validate a scope against an optional spec"
    )
    val.add_argument("scope_yaml", type=Path, help="Path to mcp-scope.yaml")
    val.add_argument(
        "--openapi-spec",
        type=Path,
        default=None,
        help="Optional OpenAPI spec to cross-reference",
    )
    val.set_defaults(func=_cmd_validate)

    return parser


def main() -> None:
    """CLI entry point for mcp-builder."""
    parser = build_parser()
    args = parser.parse_args()

    configure_logging(verbose=args.verbose, level=args.log_level)

    try:
        args.func(args)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValidationError as exc:
        print(f"Validation error:\n{exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
