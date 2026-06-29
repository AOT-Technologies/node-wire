#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Type

from .models import ErrorCategory


@dataclass
class MappedError:
    code: str
    category: ErrorCategory


class ErrorMapper:
    """
    Global registry mapping exception classes to a standardized error taxonomy.

    Connector-specific registration is performed in Layer B (registration modules).
    """

    _registry: Dict[Type[BaseException], MappedError] = {}

    @classmethod
    def register(
        cls, exc_type: Type[BaseException], category: ErrorCategory, code: Optional[str] = None
    ) -> None:
        """
        Register an exception type with a category and optional stable error code.
        """
        error_code = code or exc_type.__name__
        cls._registry[exc_type] = MappedError(code=error_code, category=category)

    @classmethod
    def resolve(cls, exc: BaseException) -> MappedError:
        """
        Resolve an exception instance to a mapped error.
        Defaults to FATAL when no explicit mapping exists.
        """
        for exc_type, mapped in cls._registry.items():
            if isinstance(exc, exc_type):
                return mapped
        return MappedError(code=type(exc).__name__, category=ErrorCategory.FATAL)
