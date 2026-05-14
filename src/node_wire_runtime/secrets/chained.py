#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import logging
from typing import Sequence

from node_wire_runtime.secrets.base import (
    SecretNotFoundError,
    SecretProvider,
    SecretProviderError,
)

logger = logging.getLogger("node_wire_runtime.secrets.chained")


class ChainedSecretProvider(SecretProvider):
    """Try providers in order.

    Falls through ONLY on SecretNotFoundError / KeyError (key absent).
    Propagates SecretProviderError immediately — never mask a broken provider.
    """

    def __init__(self, *providers: SecretProvider) -> None:
        if not providers:
            raise ValueError("ChainedSecretProvider requires at least one provider")
        self._providers: Sequence[SecretProvider] = providers

    def get_secret(self, key: str) -> str:
        last_not_found: Exception | None = None
        for provider in self._providers:
            try:
                return provider.get_secret(key)
            except SecretProviderError:
                # Provider is broken (IAM, network, config). Fail hard.
                raise
            except (SecretNotFoundError, KeyError) as exc:
                last_not_found = exc
                continue  # Try next provider

        raise SecretNotFoundError(
            f"Secret '{key}' not found in any of {len(self._providers)} provider(s)"
        ) from last_not_found
