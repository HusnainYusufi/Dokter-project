"""Rule configuration HTTP API."""
from __future__ import annotations

BASE = "/api/v1/rule-configs"


def valid_payload(**overrides) -> dict:
    payload = {
        "name": "Imaging only",
        "description": "Skip everything but imaging.",
        "golden_rule_prompt": "Plain text only.",
        "opinion_template": "disability",
        "rules": [
            {
                "document_type": "imaging",
                "match_prompt": "Radiology reports.",
                "action": "extract",
                "instruction_prompt": "Impression only.",
                "max_words": 50,
                "use_as_context": False,
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_the_seeded_default_is_listed(client):
    response = client.get(BASE)
    assert response.status_code == 200

    configs = response.json()["configs"]
    assert len(configs) == 1
    assert configs[0]["is_default"] is True
    assert configs[0]["is_seeded"] is True


def test_create_read_update_cycle(client):
    created = client.post(BASE, json=valid_payload())
    assert created.status_code == 201
    config = created.json()["config"]
    assert config["version"] == 1
    assert config["is_default"] is False

    fetched = client.get(f"{BASE}/{config['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["config"]["rules"][0]["document_type"] == "imaging"

    updated = client.put(f"{BASE}/{config['id']}", json=valid_payload(description="Changed."))
    assert updated.status_code == 200
    assert updated.json()["config"]["version"] == 2
    assert updated.json()["config"]["description"] == "Changed."


def test_duplicate_name_conflicts(client):
    assert client.post(BASE, json=valid_payload()).status_code == 201
    assert client.post(BASE, json=valid_payload()).status_code == 409


def test_unknown_config_is_not_found(client):
    assert client.get(f"{BASE}/cfg_missing").status_code == 404


def test_invalid_payloads_are_rejected(client):
    assert client.post(BASE, json=valid_payload(name="   ")).status_code == 422

    bad_action = valid_payload()
    bad_action["rules"][0]["action"] = "destroy"
    assert client.post(BASE, json=bad_action).status_code == 422

    bad_words = valid_payload()
    bad_words["rules"][0]["max_words"] = 5
    assert client.post(BASE, json=bad_words).status_code == 422

    blank_type = valid_payload()
    blank_type["rules"][0]["document_type"] = "  "
    assert client.post(BASE, json=blank_type).status_code == 422


def test_set_default_moves_the_flag(client):
    config = client.post(BASE, json=valid_payload()).json()["config"]

    response = client.post(f"{BASE}/{config['id']}/set-default")
    assert response.status_code == 200
    assert response.json()["config"]["is_default"] is True

    defaults = [item for item in client.get(BASE).json()["configs"] if item["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == config["id"]


def test_duplicate_and_delete(client):
    config = client.post(BASE, json=valid_payload()).json()["config"]

    duplicated = client.post(f"{BASE}/{config['id']}/duplicate")
    assert duplicated.status_code == 201
    assert duplicated.json()["config"]["name"] == "Imaging only (copy)"

    assert client.delete(f"{BASE}/{duplicated.json()['config']['id']}").status_code == 204
    assert client.delete(f"{BASE}/{config['id']}").status_code == 204
    assert len(client.get(BASE).json()["configs"]) == 1


def test_the_last_config_cannot_be_deleted(client):
    only = client.get(BASE).json()["configs"][0]
    response = client.delete(f"{BASE}/{only['id']}")
    assert response.status_code == 400
    assert "detail" in response.json()


def test_document_types_endpoint_includes_custom_types(client):
    client.post(
        BASE,
        json=valid_payload(
            name="With custom",
            rules=[
                {
                    "document_type": "Referral Form",
                    "match_prompt": "",
                    "action": "skip",
                    "instruction_prompt": "",
                    "max_words": None,
                    "use_as_context": True,
                }
            ],
        ),
    )

    types = client.get(f"{BASE}/document-types").json()["document_types"]
    assert "clinical" in types
    assert "Referral Form" in types
