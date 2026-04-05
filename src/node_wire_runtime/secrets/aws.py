from __future__ import annotations

import json

from node_wire_runtime.secrets.base import (
    SecretNotFoundError,
    SecretProvider,
    SecretProviderError,
)

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError as _e:
    raise ImportError(
        "boto3 is required for AwsSecretsManagerProvider. "
        "Install it with: pip install 'node-wire-runtime[aws]'"
    ) from _e


class AwsSecretsManagerProvider(SecretProvider):
    """Fetches a JSON secret bundle from AWS Secrets Manager at init time.

    Keys in the JSON map to connector secret names (e.g. ``epic_client_id``).
    Raise SecretProviderError on auth / network / config failures so the caller
    knows the provider itself is broken rather than a single key being absent.
    """

    def __init__(self, secret_name: str, region: str = "us-east-1") -> None:
        try:
            client = boto3.client("secretsmanager", region_name=region)
            raw = client.get_secret_value(SecretId=secret_name)["SecretString"]
            self._data: dict = json.loads(raw)
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "ResourceNotFoundException":
                raise SecretNotFoundError(secret_name) from exc
            raise SecretProviderError(f"AWS Secrets Manager error ({code}): {secret_name}") from exc
        except BotoCoreError as exc:
            raise SecretProviderError(f"AWS connection error for secret {secret_name!r}") from exc

    def get_secret(self, key: str) -> str:
        try:
            return self._data[key]
        except KeyError:
            raise SecretNotFoundError(key)
