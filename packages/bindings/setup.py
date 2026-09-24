#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
Cython build for node-wire-bindings (MCP host surface only).

Compiles bindings/__init__.py, factory.py, invoke.py, and bindings/mcp_server/*
to binary extensions. REST/gRPC modules are excluded — they are not needed by
generated MCP Docker images.
"""

from __future__ import annotations

import glob
import os

from Cython.Build import cythonize
from setuptools import setup
from setuptools.command.build_ext import build_ext as _BuildExt
from setuptools.command.build_py import build_py as _BuildPy


class NoPyBuild(_BuildPy):
    """Skip copying .py sources into the wheel — binaries only."""

    def find_package_modules(self, package, package_dir):
        return []


class ParallelBuildExt(_BuildExt):
    """Compile extension modules with one compiler job per core."""

    def finalize_options(self):
        super().finalize_options()
        if self.parallel is None:
            self.parallel = os.cpu_count() or 1


src_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src/bindings"))
mcp_root = os.path.join(src_root, "mcp_server")

py_files = [
    os.path.join(src_root, "__init__.py"),
    os.path.join(src_root, "factory.py"),
    os.path.join(src_root, "invoke.py"),
    *glob.glob(os.path.join(mcp_root, "**", "*.py"), recursive=True),
]
py_files = [p for p in py_files if os.path.isfile(p)]

# Guarded so Cython's process pool can re-import this file on macOS and Windows.
if __name__ == "__main__":
    setup(
        cmdclass={"build_py": NoPyBuild, "build_ext": ParallelBuildExt},
        ext_modules=cythonize(
            py_files,
            nthreads=os.cpu_count() or 1,
            compiler_directives={"language_level": "3"},
            build_dir="build",
            annotate=False,
        ),
    )
