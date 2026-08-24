"""Credentials must not reach stored records or the portal.

The failure this exists for: a production job failed with
"Incorrect API key provided: sk-proj-..." and that text was written to
job.error, persisted, and rendered on screen - where it outlives any rotation
and is visible to anyone who can see the job.
"""
from __future__ import annotations

import pytest

from app.core.redaction import REDACTED, redact_secrets


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
        "sk-abcdefghijklmnopqrstuvwxyz012345",
        "sk-svcacct-abcdefghijklmnopqrstuvwxyz01",
        "sk-ant-api03-abcdefghijklmnopqrstuvwx",
        "AIzaSyA1234567890abcdefghijklmnopqrstu",
        "llx-abcdefghijklmnopqrstuvwxyz012345",
        "AKIAIOSFODNN7EXAMPLE",
        "org-abcdefghijklmnopqrstuvwx",
    ],
)
def test_a_provider_key_never_survives(secret):
    message = f"Incorrect API key provided: {secret}. Check your key."
    cleaned = redact_secrets(message)

    assert secret not in cleaned
    assert REDACTED in cleaned
    # The operator still learns what went wrong.
    assert "Incorrect API key provided" in cleaned


def test_a_bearer_token_is_removed():
    cleaned = redact_secrets("401 Unauthorized: Bearer abcdefghijklmnopqrstuvwxyz012345")
    assert "abcdefghijklmnopqrstuvwxyz012345" not in cleaned


def test_a_jwt_is_removed():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r"
    assert jwt not in redact_secrets(f"token rejected: {jwt}")


def test_a_named_key_is_removed_whatever_its_shape():
    cleaned = redact_secrets("request failed api_key=zzz123nothinglikeakey")
    assert "zzz123nothinglikeakey" not in cleaned


def test_credentials_in_a_url_are_removed_but_the_host_survives():
    cleaned = redact_secrets("could not reach mysql://admin:hunter2@db.internal:3306/medical")
    assert "hunter2" not in cleaned
    assert "db.internal" in cleaned


def test_an_ordinary_message_is_left_alone():
    message = "Rendering page 12 failed: the page is not a valid PDF stream."
    assert redact_secrets(message) == message


def test_a_clinical_string_is_not_mistaken_for_a_secret():
    """Over-redaction would hide the real error from an operator."""
    message = "Document DX-22-0148207 on page 12 has no impression."
    assert redact_secrets(message) == message


@pytest.mark.parametrize("value", [None, ""])
def test_empty_input_is_safe(value):
    assert redact_secrets(value) == ""


def test_more_than_one_secret_in_one_message():
    cleaned = redact_secrets(
        "tried sk-proj-aaaaaaaaaaaaaaaaaaaaaaaa then AIzaSyB1234567890abcdefghijklmnopqrst"
    )
    assert "sk-proj-aaaa" not in cleaned
    assert "AIzaSyB" not in cleaned
