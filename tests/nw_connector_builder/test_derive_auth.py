# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from nw_connector_builder.derive.auth import (
    build_auth_plan,
    choose_connector_scheme,
    evaluate_operation_security,
)
from nw_connector_builder.derive.operations import derive_operations
from nw_connector_builder.load import load_openapi_document

FIXTURES = Path(__file__).parent / "fixtures"


def test_auth_anonymous_vs_optional() -> None:
    schemes = {
        "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
    }
    fp = "apiKey:header:X-API-Key"
    assert evaluate_operation_security([], None, schemes, fp).mode == "anonymous"
    assert evaluate_operation_security([{}], None, schemes, fp).mode == "optional"


def test_auth_and_multi_and_oauth_unmapped_flow_is_divergent_not_unsupported() -> None:
    """Host-supplied oauth2 (no usable flow) is presentable → "divergent", not
    "unsupported" (reserved for mutualTLS, apiKey-in-cookie, unknown types).
    """
    schemes = {
        "A": {"type": "apiKey", "in": "header", "name": "X"},
        "O": {"type": "oauth2", "flows": {}},
    }
    fp = "apiKey:header:X"
    d = evaluate_operation_security([{"A": [], "O": []}], None, schemes, fp)
    assert d.mode == "and_multi"
    d2 = evaluate_operation_security([{"O": []}], None, schemes, fp)
    assert d2.mode == "divergent"
    # Divergent carries the scheme name for per-action routing.
    assert d2.scheme_name == "O"


def test_mutual_tls_and_cookie_api_key_are_still_unsupported() -> None:
    """mutualTLS and apiKey-in-cookie remain soft-dropped as "unsupported"."""
    schemes = {
        "M": {"type": "mutualTLS"},
        "C": {"type": "apiKey", "in": "cookie", "name": "session"},
    }
    assert evaluate_operation_security([{"M": []}], None, schemes, None).mode == "unsupported"
    assert evaluate_operation_security([{"C": []}], None, schemes, None).mode == "unsupported"


def test_oauth2_implicit_and_password_are_host_supplied_not_unsupported() -> None:
    """oauth2 implicit/password are host-supplied (presentable), never acquired."""
    schemes = {
        "O": {
            "type": "oauth2",
            "flows": {
                "implicit": {"authorizationUrl": "https://idp.example.com/authorize"},
                "password": {"tokenUrl": "https://idp.example.com/token"},
            },
        },
    }
    # No connector-level scheme established yet: presentable + no mismatch -> optional.
    d = evaluate_operation_security([{"O": []}], None, schemes, None)
    assert d.mode == "optional"
    # This IS the connector's chosen (host-supplied) scheme -> required.
    fp = "oauth2:None:O"
    d2 = evaluate_operation_security([{"O": []}], None, schemes, fp)
    assert d2.mode == "required"
    # Connector uses a *different* scheme -> divergent, same as any other mismatch.
    d3 = evaluate_operation_security([{"O": []}], None, schemes, "apiKey:header:X")
    assert d3.mode == "divergent"
    assert d3.scheme_name == "O"


def test_oauth2_client_credentials_flow_is_supported() -> None:
    schemes = {
        "O": {
            "type": "oauth2",
            "flows": {
                "clientCredentials": {
                    "tokenUrl": "https://idp.example.com/token",
                    "scopes": {"read": "Read access"},
                }
            },
        },
    }
    fp = "oauth2:client_credentials:O"
    d = evaluate_operation_security([{"O": []}], None, schemes, fp)
    assert d.mode == "required"


def test_oauth2_authorization_code_flow_is_supported() -> None:
    schemes = {
        "O": {
            "type": "oauth2",
            "flows": {
                "authorizationCode": {
                    "authorizationUrl": "https://idp.example.com/authorize",
                    "tokenUrl": "https://idp.example.com/token",
                    "scopes": {"read": "Read access"},
                }
            },
        },
    }
    fp = "oauth2:authorization_code:O"
    d = evaluate_operation_security([{"O": []}], None, schemes, fp)
    assert d.mode == "required"


def test_build_auth_plan_oauth2_client_credentials() -> None:
    schemes = {
        "oauth2": {
            "type": "oauth2",
            "flows": {
                "clientCredentials": {
                    "tokenUrl": "https://idp.example.com/token",
                    "scopes": {"read": "Read access", "write": "Write access"},
                }
            },
        },
    }
    plan = build_auth_plan("microsoft_teams", schemes, "oauth2")
    assert plan.provider == "oauth2"
    assert plan.yaml_block["grant_method"] == "client_secret_post"
    assert plan.yaml_block["token_url_secret"] == "MICROSOFT_TEAMS_TOKEN_URL"
    assert plan.yaml_block["client_id_secret"] == "MICROSOFT_TEAMS_CLIENT_ID"
    assert plan.yaml_block["client_secret_secret"] == "MICROSOFT_TEAMS_CLIENT_SECRET"
    assert plan.yaml_block["scopes"] == ["read", "write"]
    assert "refresh_token_secret" not in plan.yaml_block
    assert plan.secret_defaults["MICROSOFT_TEAMS_TOKEN_URL"] == "https://idp.example.com/token"
    assert set(plan.secret_keys) == {
        "MICROSOFT_TEAMS_TOKEN_URL",
        "MICROSOFT_TEAMS_CLIENT_ID",
        "MICROSOFT_TEAMS_CLIENT_SECRET",
    }
    assert any("unattended" in n.lower() for n in plan.notes)


def test_build_auth_plan_oauth2_authorization_code() -> None:
    schemes = {
        "oauth2": {
            "type": "oauth2",
            "flows": {
                "authorizationCode": {
                    "authorizationUrl": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                    "tokenUrl": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                    "scopes": {"Team.ReadBasic.All": "Read teams basic info"},
                }
            },
        },
    }
    plan = build_auth_plan("microsoft_teams", schemes, "oauth2")
    assert plan.provider == "oauth2"
    assert plan.yaml_block["grant_method"] == "refresh_token"
    assert plan.yaml_block["refresh_token_secret"] == "MICROSOFT_TEAMS_REFRESH_TOKEN"
    # offline_access is force-added: Entra (and most OIDC providers) won't issue a refresh
    # token during interactive consent without it, even though the spec doesn't declare it.
    assert plan.yaml_block["scopes"] == ["Team.ReadBasic.All", "offline_access"]
    assert plan.secret_key == "MICROSOFT_TEAMS_REFRESH_TOKEN"
    assert set(plan.secret_keys) == {
        "MICROSOFT_TEAMS_TOKEN_URL",
        "MICROSOFT_TEAMS_CLIENT_ID",
        "MICROSOFT_TEAMS_CLIENT_SECRET",
        "MICROSOFT_TEAMS_REFRESH_TOKEN",
    }
    assert (
        plan.secret_defaults["MICROSOFT_TEAMS_TOKEN_URL"]
        == "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    )
    assert any("one-time" in n.lower() or "interactive" in n.lower() for n in plan.notes)
    assert any("on_refresh_token_rotated" in n for n in plan.notes)
    assert any("offline_access" in n for n in plan.notes)


def test_build_auth_plan_oauth2_authorization_code_offline_access_not_duplicated() -> None:
    schemes = {
        "oauth2": {
            "type": "oauth2",
            "flows": {
                "authorizationCode": {
                    "authorizationUrl": "https://idp.example.com/authorize",
                    "tokenUrl": "https://idp.example.com/token",
                    "scopes": {"read": "Read access", "offline_access": "Get a refresh token"},
                }
            },
        },
    }
    plan = build_auth_plan("acme", schemes, "oauth2")
    assert plan.yaml_block["scopes"] == ["read", "offline_access"]
    assert not any("Added 'offline_access'" in n for n in plan.notes)


def test_derive_microsoft_teams_style_spec_no_longer_soft_dropped() -> None:
    doc = {
        "openapi": "3.0.3",
        "info": {"title": "t", "version": "1"},
        "servers": [{"url": "https://graph.microsoft.com/v1.0"}],
        "security": [{"oauth2": ["Team.ReadBasic.All"]}],
        "paths": {
            "/teams/{team-id}/installedApps": {
                "get": {
                    "operationId": "listInstalledApps",
                    "parameters": [
                        {
                            "name": "team-id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
        "components": {
            "securitySchemes": {
                "oauth2": {
                    "type": "oauth2",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                            "tokenUrl": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                            "scopes": {"Team.ReadBasic.All": "Read teams basic info"},
                        }
                    },
                }
            }
        },
    }
    result = derive_operations(doc, connector_id="microsoft_teams")
    assert result.drops == []
    assert {a.name for a in result.actions} == {"list_installed_apps"}
    assert result.auth_plan.provider == "oauth2"
    assert result.auth_plan.yaml_block["grant_method"] == "refresh_token"
    # The spec's declared scopes don't include offline_access; without it Entra never
    # issues a refresh token, so the generator adds it — see derive/auth.py.
    assert "offline_access" in result.auth_plan.yaml_block["scopes"]


def test_derive_demo_pets() -> None:
    doc, _ = load_openapi_document(str(FIXTURES / "demo_pets.openapi.yaml"))
    result = derive_operations(doc, connector_id="demo_pets")
    names = {a.name for a in result.actions}
    assert "get_pet" in names
    assert "create_pet" in names
    assert "health_check" in names
    health = next(a for a in result.actions if a.name == "health_check")
    assert health.auth is False
    assert result.auth_plan.provider == "static_token"
    assert result.default_base_url == "https://api.example.com/v1"


@pytest.mark.parametrize(
    ("schemes", "chosen", "provider", "secret_key"),
    [
        (
            {"K": {"type": "apiKey", "in": "header", "name": "X-API-Key"}},
            "K",
            "static_token",
            "PET_STORE_API_KEY",
        ),
        (
            {"K": {"type": "apiKey", "in": "query", "name": "api_key"}},
            "K",
            "apikey_query",
            "PET_STORE_API_KEY",
        ),
        (
            {"B": {"type": "http", "scheme": "bearer"}},
            "B",
            "static_token",
            "PET_STORE_TOKEN",
        ),
        (
            {"B": {"type": "http", "scheme": "basic"}},
            "B",
            "static_token",
            "PET_STORE_BASIC_AUTH",
        ),
    ],
)
def test_build_auth_plan_supported_schemes(
    schemes: dict,
    chosen: str,
    provider: str,
    secret_key: str,
) -> None:
    plan = build_auth_plan("pet_store", schemes, chosen)
    assert plan.provider == provider
    assert plan.secret_key == secret_key
    assert plan.scheme_name == chosen
    assert plan.yaml_block["provider"] == provider
    assert plan.yaml_block["secret_key"] == secret_key


def test_build_auth_plan_oauth2_implicit_is_host_supplied() -> None:
    schemes = {
        "petstore_auth": {
            "type": "oauth2",
            "flows": {
                "implicit": {"authorizationUrl": "https://petstore.swagger.io/oauth/authorize"}
            },
        },
    }
    plan = build_auth_plan("pet_store", schemes, "petstore_auth")
    assert plan.provider == "static_token"
    assert plan.tier == "host_supplied"
    assert plan.secret_key == "PET_STORE_ACCESS_TOKEN"
    assert plan.yaml_block["provider"] == "static_token"
    assert plan.yaml_block["header_name"] == "Authorization"
    assert plan.yaml_block["prefix"] == "Bearer"
    assert plan.yaml_block["host_supplied"] is True
    # Framing must never claim Node Wire supports implicit acquisition.
    assert any("HOST-SUPPLIED" in n for n in plan.notes)
    assert any("never acquires" in n for n in plan.notes)


def test_build_auth_plan_openid_connect_is_host_supplied() -> None:
    schemes = {
        "oidc": {"type": "openIdConnect", "openIdConnectUrl": "https://idp.example.com/.well-known"}
    }
    plan = build_auth_plan("acme", schemes, "oidc")
    assert plan.provider == "static_token"
    assert plan.tier == "host_supplied"
    assert plan.secret_key == "ACME_ACCESS_TOKEN"
    assert plan.yaml_block["host_supplied"] is True


def test_build_auth_plan_anonymous_when_unmapped() -> None:
    plan = build_auth_plan("pet_store", {}, None)
    assert plan.provider == "none"
    assert plan.secret_key == ""
    assert plan.yaml_block == {}
    assert any("anonymous" in n.lower() for n in plan.notes)


def test_choose_connector_scheme_from_document_security() -> None:
    schemes = {
        "ApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
        "Bearer": {"type": "http", "scheme": "bearer"},
    }
    doc = {"security": [{"ApiKey": []}], "paths": {}}
    assert choose_connector_scheme(doc, schemes) == "ApiKey"


def test_choose_connector_scheme_majority_vote() -> None:
    schemes = {
        "A": {"type": "apiKey", "in": "header", "name": "X"},
        "B": {"type": "http", "scheme": "bearer"},
    }
    doc = {
        "paths": {
            "/a": {"get": {"security": [{"A": []}]}},
            "/b": {"get": {"security": [{"B": []}]}},
            "/c": {"post": {"security": [{"B": []}]}},
            "/d": {"get": {"security": [{"B": []}]}},
        }
    }
    assert choose_connector_scheme(doc, schemes) == "B"


def test_choose_connector_scheme_mapped_wins_over_host_supplied_petstore_shape() -> None:
    """Mapped schemes beat host-supplied by priority, not raw usage count.

    Petstore shape: api_key (2 ops) vs oauth2-implicit petstore_auth (7 ops) must
    still choose api_key as the connector default.
    """
    schemes = {
        "api_key": {"type": "apiKey", "in": "header", "name": "api_key"},
        "petstore_auth": {
            "type": "oauth2",
            "flows": {
                "implicit": {"authorizationUrl": "https://petstore.swagger.io/oauth/authorize"}
            },
        },
    }
    doc = {
        "paths": {
            "/pet/{petId}": {
                "get": {"security": [{"api_key": []}]},
                "post": {"security": [{"petstore_auth": ["write:pets"]}]},
            },
            "/store/inventory": {"get": {"security": [{"api_key": []}]}},
            "/pet": {
                "post": {"security": [{"petstore_auth": ["write:pets"]}]},
                "put": {"security": [{"petstore_auth": ["write:pets"]}]},
            },
            "/pet/findByStatus": {"get": {"security": [{"petstore_auth": ["read:pets"]}]}},
            "/pet/findByTags": {"get": {"security": [{"petstore_auth": ["read:pets"]}]}},
            "/pet/{petId}/uploadImage": {"post": {"security": [{"petstore_auth": ["write:pets"]}]}},
        }
    }
    # Sanity check on the fixture itself: 2 api_key uses, 7 petstore_auth uses —
    # matching the real spec's ratio, so petstore_auth would win on raw count alone.
    assert choose_connector_scheme(doc, schemes) == "api_key"


def test_choose_connector_scheme_falls_back_to_host_supplied_when_nothing_else_exists() -> None:
    """Implicit-only specs fall back to a host-supplied connector scheme."""
    schemes = {
        "only_auth": {
            "type": "oauth2",
            "flows": {"implicit": {"authorizationUrl": "https://idp.example.com/authorize"}},
        },
    }
    doc = {"paths": {"/a": {"get": {"security": [{"only_auth": []}]}}}}
    assert choose_connector_scheme(doc, schemes) == "only_auth"


def test_derive_oauth2_implicit_only_spec_no_longer_hard_fails() -> None:
    """End-to-end version of the fallback above through derive_operations: an
    implicit-only spec used to raise DeriveError; it must now build with a
    host-supplied bearer credential.
    """
    doc = {
        "openapi": "3.0.3",
        "info": {"title": "t", "version": "1"},
        "servers": [{"url": "https://api.example.com"}],
        "security": [{"only_auth": []}],
        "paths": {
            "/widgets": {
                "get": {
                    "operationId": "listWidgets",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
        "components": {
            "securitySchemes": {
                "only_auth": {
                    "type": "oauth2",
                    "flows": {
                        "implicit": {"authorizationUrl": "https://idp.example.com/authorize"}
                    },
                }
            }
        },
    }
    result = derive_operations(doc, connector_id="widgets")
    assert result.drops == []
    assert {a.name for a in result.actions} == {"list_widgets"}
    assert result.auth_plan.tier == "host_supplied"
    assert result.auth_plan.yaml_block["host_supplied"] is True


def test_derive_petstore_shape_generates_all_ops_zero_drops() -> None:
    """Petstore-shaped multi-scheme doc: all ops generate; divergent ops are named."""
    doc = {
        "openapi": "3.0.3",
        "info": {"title": "t", "version": "1"},
        "servers": [{"url": "https://petstore.swagger.io/v2"}],
        "paths": {
            "/pet/{petId}": {
                "get": {
                    "operationId": "getPetById",
                    "security": [{"api_key": []}],
                    "parameters": [
                        {
                            "name": "petId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"200": {"description": "ok"}},
                },
                "post": {
                    "operationId": "updatePetWithForm",
                    "security": [{"petstore_auth": ["write:pets"]}],
                    "parameters": [
                        {
                            "name": "petId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"200": {"description": "ok"}},
                },
                "delete": {
                    "operationId": "deletePet",
                    "security": [{"petstore_auth": ["write:pets"]}],
                    "parameters": [
                        {
                            "name": "petId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"200": {"description": "ok"}},
                },
            },
            "/pet/{petId}/uploadImage": {
                "post": {
                    "operationId": "uploadFile",
                    "security": [{"petstore_auth": ["write:pets"]}],
                    "parameters": [
                        {
                            "name": "petId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            },
            "/store/inventory": {
                "get": {
                    "operationId": "getInventory",
                    "security": [{"api_key": []}],
                    "responses": {"200": {"description": "ok"}},
                }
            },
            "/pet": {
                "post": {
                    "operationId": "addPet",
                    "security": [{"petstore_auth": ["write:pets"]}],
                    "responses": {"200": {"description": "ok"}},
                },
                "put": {
                    "operationId": "updatePet",
                    "security": [{"petstore_auth": ["write:pets"]}],
                    "responses": {"200": {"description": "ok"}},
                },
            },
            "/pet/findByStatus": {
                "get": {
                    "operationId": "findPetsByStatus",
                    "security": [{"petstore_auth": ["read:pets"]}],
                    "responses": {"200": {"description": "ok"}},
                }
            },
            "/store/order": {
                "post": {
                    "operationId": "placeOrder",
                    "responses": {"200": {"description": "ok"}},
                }
            },
            "/user": {
                "post": {
                    "operationId": "createUser",
                    "responses": {"200": {"description": "ok"}},
                }
            },
        },
        "components": {
            "securitySchemes": {
                "api_key": {"type": "apiKey", "in": "header", "name": "api_key"},
                "petstore_auth": {
                    "type": "oauth2",
                    "flows": {
                        "implicit": {
                            "authorizationUrl": "https://petstore.swagger.io/oauth/authorize"
                        }
                    },
                },
            }
        },
    }
    result = derive_operations(doc, connector_id="pet_store")
    assert result.drops == []
    assert len(result.actions) == 10
    by_name = {a.name: a for a in result.actions}

    # Connector default stays api_key.
    assert result.auth_plan.provider == "static_token"
    assert result.auth_plan.scheme_name == "api_key"

    # api_key-required ops: default scheme (auth_scheme_name=None).
    assert by_name["get_pet_by_id"].auth_scheme_name is None
    assert by_name["get_inventory"].auth_scheme_name is None

    # petstore_auth-required ops: routed to the named scheme.
    petstore_auth_ops = {
        "update_pet_with_form",
        "delete_pet",
        "upload_file",
        "add_pet",
        "update_pet",
        "find_pets_by_status",
    }
    for op_name in petstore_auth_ops:
        assert by_name[op_name].auth_scheme_name == "petstore_auth", op_name

    # No security declared at all: still "optional" against the default scheme.
    assert by_name["place_order"].auth_scheme_name is None
    assert by_name["create_user"].auth_scheme_name is None

    # Exactly one extra auth plan, for petstore_auth, host-supplied bearer.
    assert set(result.extra_auth_plans) == {"petstore_auth"}
    extra = result.extra_auth_plans["petstore_auth"]
    assert extra.tier == "host_supplied"
    assert extra.provider == "static_token"
    assert extra.yaml_block["host_supplied"] is True
