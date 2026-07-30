#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
node_wire_runtime.secrets
=========================

Pluggable secret resolution for Node Wire connectors.

Baseline (always available):
    SecretProvider          — abstract base class
    EnvSecretProvider       — reads from os.environ (default for all deployments)
    SecretNotFoundError     — key absent in this provider
    SecretProviderError     — provider itself is broken (auth / network / config)
    ChainedSecretProvider   — tries providers in order; only falls through on NotFound

Cloud backends (installed as extras):
    AwsSecretsManagerProvider   pip install node-wire-runtime[aws]
    HashiCorpVaultProvider      pip install node-wire-runtime[vault]
    AzureKeyVaultProvider       pip install node-wire-runtime[azure]
    GcpSecretManagerProvider    pip install node-wire-runtime[gcp]
"""

from node_wire_runtime.secrets.base import (
    EnvSecretProvider,
    SecretNotFoundError,
    SecretProvider,
    SecretProviderError,
    TenantSecretNotFoundError,
    TenantSecretProvider,
)
from node_wire_runtime.secrets.chained import ChainedSecretProvider

__all__ = [
    "SecretProvider",
    "EnvSecretProvider",
    "SecretNotFoundError",
    "SecretProviderError",
    "TenantSecretNotFoundError",
    "TenantSecretProvider",
    "ChainedSecretProvider",
]
