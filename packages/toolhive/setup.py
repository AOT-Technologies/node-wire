#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Cython build for node-wire-toolhive (binary-only wheel, like node-wire-bindings)."""

from __future__ import annotations

import glob
import os
import sys

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


src_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src/node_wire_toolhive"))
py_files = sorted(glob.glob(os.path.join(src_root, "*.py")))

# Editable installs must stay pure Python (see packages/bindings/setup.py).
EDITABLE = "editable_wheel" in sys.argv

if __name__ == "__main__":
    if EDITABLE:
        setup()
    else:
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
