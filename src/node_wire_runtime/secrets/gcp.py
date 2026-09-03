#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import json

from node_wire_runtime.secrets.base import (
    SecretNotFoundError,
    SecretProvider,
    SecretProviderError,
)


def _import_gcp():
    """Import GCP SDKs lazily so stubs / Cython reloads resolve via ``sys.modules``."""
    try:
        from google.cloud import secretmanager
        from google.api_core.exceptions import NotFound, GoogleAPICallError
    except ImportError as exc:
        raise ImportError(
            "google-cloud-secret-manager is required for GcpSecretManagerProvider. "
            "Install with: pip install 'node-wire-runtime[gcp]'"
        ) from exc
    return secretmanager, NotFound, GoogleAPICallError


class GcpSecretManagerProvider(SecretProvider):
    """Reads the latest version of a GCP Secret Manager secret at init time.

    The secret should be a JSON object whose keys map to connector secret names.
    Uses Application Default Credentials (ADC) — works with workload identity,
    service account key files, and gcloud auth.
    """

    def __init__(self, project_id: str, secret_id: str, version: str = "latest") -> None:
        secretmanager, NotFound, GoogleAPICallError = _import_gcp()
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_id}/versions/{version}"
        try:
            response = client.access_secret_version(request={"name": name})
            payload = response.payload.data.decode("utf-8")
            self._data: dict = json.loads(payload)
        except NotFound:
            raise SecretNotFoundError(f"{project_id}/{secret_id}")
        except GoogleAPICallError as exc:
            raise SecretProviderError(
                f"GCP Secret Manager error for {project_id}/{secret_id}: {exc}"
            ) from exc

    def get_secret(self, key: str) -> str:
        try:
            return self._data[key]
        except KeyError:
            raise SecretNotFoundError(key)
