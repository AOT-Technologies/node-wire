#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from .models import ConnectorResponse, ErrorCategory
from .errors import ErrorMapper
from .secrets import (
    SecretProvider,
    EnvSecretProvider,
    SecretNotFoundError,
    SecretProviderError,
    TenantSecretNotFoundError,
    TenantSecretProvider,
)
from .policy import PolicyHook, PolicyDenied
from .caller_identity import CallerIdentity, build_caller_identity
from .config_store import (
    ConnectorConfigStore,
    ConfigRecord,
    ConfigNotFoundError,
    ConfigNameConflictError,
    DefaultDeletionError,
    DEFAULT_TENANT,
)
from .identity import (
    MissingTenantError,
    TenantIdentityMismatchError,
    TenantMismatchError,
    effective_run_tenant_id,
    normalize_tenant_id,
    resolve_tenant_id,
    tenant_from_headers,
    tenants_equivalent,
)
from .auth import (
    AuthProvider,
    ApiKeyQueryAuthProvider,
    NoAuthProvider,
    StaticTokenAuthProvider,
    OAuth2AuthProvider,
    ServiceAccountAuthProvider,
)
from .base_connector import (
    BaseConnector,
    NestedConnectorActionError,
    get_connector_registry,
    nw_action,
    sdk_action,
)
from .rest import RestConnector, RestResponseOutput
from .sdk_action_spec import (
    SdkActionSpec,
    default_build_kwargs,
    default_invoke,
    default_resolve_method,
    execute_spec_async,
    execute_spec_in_thread,
    navigate_resource,
)
from .streaming import (
    StreamSignal,
    stream_completion_log,
    resolve_stream_buffer_ms,
    BufferedStreamIterator,
)


def _resolve_version() -> str:
    from importlib.metadata import PackageNotFoundError, version as pkg_version

    for dist_name in ("node-wire-runtime", "node-wire"):
        try:
            return pkg_version(dist_name)
        except PackageNotFoundError:
            pass

    # PYTHONPATH / src-layout imports without an installed distribution.
    try:
        import tomllib
        from pathlib import Path

        root_pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        return tomllib.loads(root_pyproject.read_text(encoding="utf-8"))["project"]["version"]
    except Exception:
        return "0.0.0"


__version__ = _resolve_version()

__all__ = [
    "ConnectorResponse",
    "ErrorCategory",
    "ErrorMapper",
    "SecretProvider",
    "EnvSecretProvider",
    "SecretNotFoundError",
    "SecretProviderError",
    "TenantSecretNotFoundError",
    "TenantSecretProvider",
    "PolicyHook",
    "PolicyDenied",
    "CallerIdentity",
    "build_caller_identity",
    "ConnectorConfigStore",
    "ConfigRecord",
    "ConfigNotFoundError",
    "ConfigNameConflictError",
    "DefaultDeletionError",
    "DEFAULT_TENANT",
    "MissingTenantError",
    "TenantIdentityMismatchError",
    "TenantMismatchError",
    "effective_run_tenant_id",
    "normalize_tenant_id",
    "resolve_tenant_id",
    "tenant_from_headers",
    "tenants_equivalent",
    "AuthProvider",
    "ApiKeyQueryAuthProvider",
    "NoAuthProvider",
    "StaticTokenAuthProvider",
    "OAuth2AuthProvider",
    "ServiceAccountAuthProvider",
    "BaseConnector",
    "RestConnector",
    "RestResponseOutput",
    "NestedConnectorActionError",
    "sdk_action",
    "nw_action",
    "get_connector_registry",
    "SdkActionSpec",
    "default_build_kwargs",
    "default_invoke",
    "default_resolve_method",
    "execute_spec_async",
    "execute_spec_in_thread",
    "navigate_resource",
    "StreamSignal",
    "stream_completion_log",
    "resolve_stream_buffer_ms",
    "BufferedStreamIterator",
]
