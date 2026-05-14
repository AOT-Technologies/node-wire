#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
import glob
import os
from Cython.Build import cythonize
from setuptools import setup
from setuptools.command.build_py import build_py as _BuildPy


class NoPyBuild(_BuildPy):
    def find_package_modules(self, package, package_dir):
        return []


src_root = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../../src/node_wire_google_drive")
)
py_files = glob.glob(os.path.join(src_root, "**", "*.py"), recursive=True)

setup(
    cmdclass={"build_py": NoPyBuild},
    ext_modules=cythonize(py_files, compiler_directives={"language_level": "3"}, build_dir="build"),
)
