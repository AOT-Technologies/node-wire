# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Which compiled wheels a generated server's image can install, read from its Dockerfile.

node-wire ships binary-only (Cython) wheels, so they must match the image's Python ABI and C
library exactly: stacklok's ``dhi.io/python:3.13-alpine…`` needs ``cp313`` + ``musllinux``; a
``python:3.13-slim`` base would need ``cp313`` + ``manylinux``. Deriving the target from the
image keeps the wheels in step if the base image changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

_FROM = re.compile(r"^\s*FROM\s+(?P<image>\S+)", re.IGNORECASE | re.MULTILINE)
_PYTHON_TAG = re.compile(r"python:(?P<major>\d+)\.(?P<minor>\d+)")


class UnknownImageError(ValueError):
    """The Dockerfile's runtime base image does not say which Python / libc it has."""


@dataclass(frozen=True)
class WheelTarget:
    python: str  # CPython tag, e.g. cp313
    libc: str  # musllinux | manylinux
    image: str  # the runtime base image it was read from

    @classmethod
    def from_dockerfile(cls, dockerfile: Path) -> "WheelTarget":
        """From the Dockerfile's last ``FROM`` (the runtime stage)."""
        images = _FROM.findall(dockerfile.read_text(encoding="utf-8"))
        if not images:
            raise UnknownImageError(f"No FROM line in {dockerfile}")
        image = images[-1]
        match = _PYTHON_TAG.search(image)
        if not match:
            raise UnknownImageError(
                f"Cannot tell the Python version of {image} ({dockerfile}); expected a "
                "python:<major>.<minor>… base image"
            )
        libc = "musllinux" if "alpine" in image.lower() else "manylinux"
        return cls(f"cp{match['major']}{match['minor']}", libc, image)

    @property
    def cibw_build(self) -> str:
        """cibuildwheel ``CIBW_BUILD`` selector, e.g. ``cp313-musllinux_*``."""
        return f"{self.python}-{self.libc}_*"

    @property
    def description(self) -> str:
        return f"{self.python} {self.libc} (for {self.image.split('@', 1)[0]})"

    def wheels_by_arch(self, dist_dir: Path) -> Dict[str, Path]:
        """Newest matching wheel per architecture in ``dist_dir``."""
        pattern = re.compile(
            rf"-{self.python}-{self.python}-{self.libc}_\d+_\d+_(?P<arch>[a-z0-9_]+)\.whl$"
        )
        by_arch: Dict[str, Path] = {}
        for wheel in sorted(dist_dir.glob("*.whl"), key=lambda p: p.stat().st_mtime):
            found = pattern.search(wheel.name)
            if found:
                by_arch[found["arch"]] = wheel
        return by_arch
