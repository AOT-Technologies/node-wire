"""String escaping utilities for code-generation renderers."""

from __future__ import annotations


def escape_python_string(value: str) -> str:
    """Escape a value for safe embedding inside a Python double-quoted string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def escape_toml_string(value: str) -> str:
    """Escape a value for safe embedding inside a TOML basic (double-quoted) string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')
