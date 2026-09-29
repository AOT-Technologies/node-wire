# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""ToolHive deploy manifests for node-wire mode: one backend, one ToolHive proxy per tenant.

Stacklok emits a single ``MCPServer``. A node-wire server is multi-tenant: one backend
Deployment serves every tenant, and each tenant reaches it through its own ``MCPRemoteProxy``
whose ``headerForward`` sets ``X-Tenant-ID``. ToolHive's header-forward middleware sets that
header with ``Header.Set`` on every request, overriding anything the client sent. Two layers
keep other pods from claiming a tenant: a NetworkPolicy admits only the proxies, and each proxy
also sends a shared secret (``X-NW-Proxy-Secret``) that the backend checks against
``NW_PROXY_SECRET``, which still holds on a cluster whose CNI ignores NetworkPolicy. Stacklok's own auth
manifests (external auth config, OIDC config, secret) are kept as rendered by stacklok.
"""

from __future__ import annotations

from typing import Any, Dict

import yaml

from mcp_builder.generate.plan import ServerPlan
from mcp_builder.generate.renderers.manifests import (
    DEFAULT_NAMESPACE,
    TOOLHIVE_API_VERSION,
    render_manifests,
)
from mcp_builder.schema.models import OAuth2Auth, OIDCAuth

BACKEND_PORT = 8080
TENANT_PLACEHOLDER = "REPLACE_ME_TENANT"
PROXY_LABEL = "node-wire.aot-technologies.com/proxy-for"
TENANT_HEADER = "X-Tenant-ID"
PROXY_SECRET_HEADER = "X-NW-Proxy-Secret"
PROXY_SECRET_ENV = "NW_PROXY_SECRET"
PROXY_SECRET_KEY = "token"
# The container listens on all interfaces inside its pod; the NetworkPolicy limits who reaches it.
_BIND_ALL = "0.0.0.0"  # nosec B104
# emptyDir scratch space for the read-only root filesystem.
_TMP_MOUNT = "/tmp"  # nosec B108


def _dump(*docs: Dict[str, Any]) -> str:
    return "".join("---\n" + yaml.safe_dump(d, sort_keys=False) for d in docs)


def render(plan: ServerPlan) -> Dict[str, str]:
    """``deploy/`` files for node-wire mode."""
    server = plan.server_name
    stacklok = render_manifests(plan)
    stacklok.pop("mcpserver.yaml")
    stacklok.pop("README.md")
    manifests = {
        "backend.yaml": _backend(server),
        "networkpolicy.yaml": _network_policy(server),
        "proxy-secret.yaml": _proxy_secret(server),
        "tenant-proxy.yaml": _tenant_proxy(plan),
        "tenants-secret.yaml": _tenants_secret(server),
        **stacklok,
    }
    manifests["README.md"] = _readme(plan, sorted(manifests))
    return manifests


def _labels(server: str) -> Dict[str, str]:
    return {"app.kubernetes.io/name": f"{server}-backend", "app.kubernetes.io/part-of": server}


def _backend(server: str) -> str:
    labels = _labels(server)
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": f"{server}-backend", "namespace": DEFAULT_NAMESPACE, "labels": labels},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "securityContext": {"runAsNonRoot": True},
                    "containers": [
                        {
                            "name": "mcp",
                            "image": f"{server}-mcp:latest",
                            "ports": [{"name": "mcp", "containerPort": BACKEND_PORT}],
                            "env": [
                                {"name": "MCP_HOST", "value": _BIND_ALL},
                                {"name": "MCP_PORT", "value": str(BACKEND_PORT)},
                                {"name": "REQUIRE_BEARER_TOKEN", "value": "true"},
                                {
                                    "name": PROXY_SECRET_ENV,
                                    "valueFrom": {
                                        "secretKeyRef": {
                                            "name": _proxy_secret_name(server),
                                            "key": PROXY_SECRET_KEY,
                                        }
                                    },
                                },
                            ],
                            # node-wire runtime + OTel need more than stacklok's 128Mi default.
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "192Mi"},
                                "limits": {"cpu": "500m", "memory": "512Mi"},
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "volumeMounts": [
                                {"name": "tenants", "mountPath": "/app/tenants", "readOnly": True},
                                {"name": "tmp", "mountPath": _TMP_MOUNT},
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "tenants", "secret": {"secretName": f"{server}-tenants"}},
                        {"name": "tmp", "emptyDir": {}},
                    ],
                },
            },
        },
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": f"{server}-backend", "namespace": DEFAULT_NAMESPACE, "labels": labels},
        "spec": {
            "type": "ClusterIP",
            "selector": labels,
            "ports": [{"name": "mcp", "port": BACKEND_PORT, "targetPort": "mcp"}],
        },
    }
    return (
        "# node-wire backend for "
        f"{server}: one Deployment serving every tenant. Reachable only through the\n"
        "# per-tenant ToolHive proxies (networkpolicy.yaml, proxy-secret.yaml); never expose this\n"
        "# Service directly.\n" + _dump(deployment, service)
    )


def _network_policy(server: str) -> str:
    policy = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": f"{server}-backend", "namespace": DEFAULT_NAMESPACE},
        "spec": {
            "podSelector": {"matchLabels": _labels(server)},
            "policyTypes": ["Ingress"],
            "ingress": [
                {
                    "from": [{"podSelector": {"matchLabels": {PROXY_LABEL: server}}}],
                    "ports": [{"protocol": "TCP", "port": BACKEND_PORT}],
                }
            ],
        },
    }
    return (
        "# Only ToolHive tenant proxies may reach the backend. Needs a CNI that enforces\n"
        "# NetworkPolicy; the proxy secret (proxy-secret.yaml) is the second layer.\n"
        + _dump(policy)
    )


def _proxy_secret_name(server: str) -> str:
    return f"{server}-proxy-secret"


def _proxy_secret(server: str) -> str:
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": _proxy_secret_name(server), "namespace": DEFAULT_NAMESPACE},
        "type": "Opaque",
        "stringData": {PROXY_SECRET_KEY: "REPLACE_ME_PROXY_SECRET"},
    }
    return (
        f"# Shared by every tenant proxy ({PROXY_SECRET_HEADER}) and the backend ({PROXY_SECRET_ENV}).\n"
        f"# The backend rejects {TENANT_HEADER} without it. Use a long random value, e.g.\n"
        "# `openssl rand -hex 32`.\n" + _dump(secret)
    )


def _tenant_proxy(plan: ServerPlan) -> str:
    server = plan.server_name
    spec: Dict[str, Any] = {
        "remoteUrl": (
            f"http://{server}-backend.{DEFAULT_NAMESPACE}.svc.cluster.local:{BACKEND_PORT}/mcp"
        ),
        "allowPrivateEndpoint": True,
        "transport": "streamable-http",
        "proxyPort": 8080,
        "headerForward": {
            "addPlaintextHeaders": {TENANT_HEADER: TENANT_PLACEHOLDER},
            "addHeadersFromSecret": [
                {
                    "headerName": PROXY_SECRET_HEADER,
                    "valueSecretRef": {
                        "name": _proxy_secret_name(server),
                        "key": PROXY_SECRET_KEY,
                    },
                }
            ],
        },
    }
    if isinstance(plan.auth, (OAuth2Auth, OIDCAuth)):
        spec["authServerRef"] = {"kind": "MCPExternalAuthConfig", "name": f"{server}-auth"}
        spec["oidcConfigRef"] = {
            "name": f"{server}-oidc",
            "audience": f"https://mcp.REPLACE_ME_DOMAIN/{server}/{TENANT_PLACEHOLDER}",
            "resourceUrl": f"https://mcp.REPLACE_ME_DOMAIN/{server}/{TENANT_PLACEHOLDER}",
        }
    elif plan.auth.type != "none":
        spec["externalAuthConfigRef"] = {"name": f"{server}-auth"}
    spec["podTemplateSpec"] = {"metadata": {"labels": {PROXY_LABEL: server}}}
    spec["audit"] = {"enabled": True}
    proxy = {
        "apiVersion": TOOLHIVE_API_VERSION,
        "kind": "MCPRemoteProxy",
        "metadata": {"name": f"{server}-{TENANT_PLACEHOLDER}", "namespace": DEFAULT_NAMESPACE},
        "spec": spec,
    }
    return (
        f"# One MCPRemoteProxy per tenant: copy this file per tenant and replace\n"
        f"# {TENANT_PLACEHOLDER} with the tenant id (a key of the tenants file). The proxy sets\n"
        f"# {TENANT_HEADER} on every request, overriding any client value. Tenants with their own\n"
        "# upstream credential need their own copy of the auth manifests (rename <server>-auth).\n"
        + _dump(proxy)
    )


def _tenants_secret(server: str) -> str:
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": f"{server}-tenants", "namespace": DEFAULT_NAMESPACE},
        "type": "Opaque",
        "stringData": {"tenants.yaml": "tenants: {}  # see config/tenants.example.yaml\n"},
    }
    return (
        "# node-wire tenant configs (base URL, auth placement, named configs), mounted at\n"
        "# /app/tenants/tenants.yaml. No upstream credentials go here: ToolHive forwards them.\n"
        + _dump(secret)
    )


def _readme(plan: ServerPlan, files: list[str]) -> str:
    server = plan.server_name
    rows = {
        "backend.yaml": "Deployment + ClusterIP Service running the node-wire MCP server",
        "networkpolicy.yaml": "Lets only the tenant proxies reach the backend",
        "proxy-secret.yaml": f"Shared secret the proxies send as `{PROXY_SECRET_HEADER}`",
        "tenant-proxy.yaml": f"`MCPRemoteProxy` template, one per tenant (sets `{TENANT_HEADER}`)",
        "tenants-secret.yaml": "Tenant configs mounted at `NW_TENANTS_PATH`",
        "mcpexternalauthconfig.yaml": "Upstream API authentication (stacklok)",
        "mcpoidcconfig.yaml": "OIDC issuer metadata for the embedded auth server (stacklok)",
        "secret.yaml": "Upstream bearer token for API-key auth (stacklok)",
        "README.md": "This file",
    }
    table = "\n".join(f"| `{f}` | {rows.get(f, '')} |" for f in files if f != "README.md")
    return f"""# Deployment manifests for `{server}` (node-wire runtime)

Generated by `nw gen-stacklok`. Requires the
[ToolHive operator](https://docs.stacklok.com/toolhive/guides-k8s/deploy-operator) and a CNI
that enforces NetworkPolicy (Calico, Cilium, ...).

One backend serves every tenant; each tenant gets its own ToolHive `MCPRemoteProxy`, which
authenticates the MCP client, forwards the upstream credential as `Authorization: Bearer`,
and sets `{TENANT_HEADER}` to that tenant.

The backend trusts `{TENANT_HEADER}`, so only the proxies may reach it. `networkpolicy.yaml`
admits only the proxy pods, and every proxy also sends `{PROXY_SECRET_HEADER}`
(`proxy-secret.yaml`), which the backend checks against `{PROXY_SECRET_ENV}`. The secret still
protects the backend if the NetworkPolicy isn't enforced, but keep both.

| File | Purpose |
| --- | --- |
{table}

## Steps

1. Build and push the image (`docker build -t <registry>/{server}-mcp:<tag> .` from the
   project root), then set `image:` in `backend.yaml`.
2. Fill `tenants-secret.yaml` with your tenants (format: `config/tenants.example.yaml`), set
   `proxy-secret.yaml` to a long random value (`openssl rand -hex 32`), and apply both
   together with `backend.yaml` and `networkpolicy.yaml`.
3. For each tenant, copy `tenant-proxy.yaml`, replace `{TENANT_PLACEHOLDER}`, and apply it
   with that tenant's auth manifests. Replace the other `REPLACE_ME_*` placeholders as in
   stacklok's flow (external domain, OAuth client id, API key).
4. Expose each proxy's service (`mcp-{server}-<tenant>-remote-proxy`) through your ingress.

## Verify

- `kubectl get pods -l {PROXY_LABEL}={server}` lists the proxy pods the NetworkPolicy
  admits; if it is empty, the operator did not apply `podTemplateSpec` labels and the policy
  must select the proxies another way.
- Call a tool through one tenant's proxy with a real upstream credential before rollout.
"""
