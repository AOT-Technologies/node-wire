#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""STRIPE_BASIC_AUTH is the Stripe key plus a colon. A key without the colon, or the base64 form,
would pass an opaque check and then fail every Stripe call with a 401, so it is refused when stored."""

from __future__ import annotations

import pytest

import node_wire_stripe.logic  # noqa: F401  (declares the shape on import)
from node_wire_runtime.tenant_persistence import SECRET_FORMAT_BY_CONNECTOR, validate_secret_formats


@pytest.mark.parametrize(
    "value",
    ["sk_test_51AbCdEf:", "sk_live_abc123:", "rk_test_abc:", "rk_live_Abc_9:", "  sk_test_abc:  "],
)
def test_a_key_with_its_colon_is_accepted(value: str) -> None:
    validate_secret_formats("stripe", {"STRIPE_BASIC_AUTH": value})


@pytest.mark.parametrize(
    "value",
    [
        "sk_test_abc",  # no colon
        "sk_test_abc:extra",  # something after the colon
        "pk_test_abc:",  # a publishable key
        "c2tfdGVzdF9hYmM6",  # already base64
        "sk_prod_abc:",  # not test or live
        "sk_test_:",  # no key body
        ":",
        "Bearer sk_test_abc:",
        "sk_test_abc:\nsk_test_def:",
    ],
)
def test_anything_else_is_refused(value: str) -> None:
    with pytest.raises(ValueError, match="STRIPE_BASIC_AUTH"):
        validate_secret_formats("stripe", {"STRIPE_BASIC_AUTH": value})


def test_the_message_does_not_echo_the_value() -> None:
    with pytest.raises(ValueError) as caught:
        validate_secret_formats("stripe", {"STRIPE_BASIC_AUTH": "sk_live_topsecretvalue"})
    assert "topsecretvalue" not in str(caught.value)


def test_the_generated_connector_declares_the_same_format() -> None:
    # Both places must agree, or the later declaration replaces the stricter one.
    assert SECRET_FORMAT_BY_CONNECTOR["stripe"] == {"STRIPE_BASIC_AUTH": "stripe_basic_auth"}
