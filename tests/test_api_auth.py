"""Optional shared-secret API authentication."""
from __future__ import annotations

import pytest


@pytest.fixture()
def token(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "API_AUTH_TOKEN", "s3cret-token")
    return "s3cret-token"


def test_the_api_is_open_when_no_token_is_configured(client):
    assert client.get("/api/v1/rule-configs").status_code == 200


def test_requests_without_the_token_are_rejected(client, token):
    response = client.get("/api/v1/rule-configs")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing or invalid API credentials."


def test_a_wrong_token_is_rejected(client, token):
    response = client.get(
        "/api/v1/rule-configs", headers={"Authorization": "Bearer wrong-token"}
    )
    assert response.status_code == 401


def test_the_correct_token_is_accepted(client, token):
    response = client.get("/api/v1/rule-configs", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


def test_health_and_docs_stay_reachable_without_a_token(client, token):
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200
