"""
Cython build for node-wire-runtime.

Compiles all .py files to .so/.pyd extensions and overrides build_py
so that source .py files are NOT copied into the wheel — only the compiled
binary extensions are included.

Build with:
    python -m build --wheel --no-isolation

Verify no .py files leaked:
    unzip -l dist/node_wire_runtime-*.whl | grep '\.py$'
"""

import glob
import os

from Cython.Build import cythonize
from setuptools import setup
from setuptools.command.build_py import build_py as _BuildPy


class NoPyBuild(_BuildPy):
    """Override that skips copying .py source files into the build tree.

    Setuptools would normally copy every .py file into the wheel alongside
    the compiled extension. Returning [] here ensures the wheel contains
    only .so/.pyd binaries.
    """

    def find_package_modules(self, package, package_dir):
        return []


src_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src/node_wire_runtime"))
py_files = glob.glob(os.path.join(src_root, "**", "*.py"), recursive=True)

setup(
    cmdclass={"build_py": NoPyBuild},
    ext_modules=cythonize(
        py_files,
        compiler_directives={"language_level": "3"},
        build_dir="build",
        annotate=False,
    ),
)
