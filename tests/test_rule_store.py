"""Rule configuration storage: seeding, CRUD, versioning, and guards."""
from __future__ import annotations

import pytest

from app.core.exceptions import ProcessingError
from app.schemas.rules import (
    DocumentRuleInput,
    RuleAction,
    RuleConfigCreate,
    RuleConfigUpdate,
)
from app.services.rules.defaults import DEFAULT_CONFIG_NAME


def test_seeding_creates_the_default_config_once(rule_store):
    assert rule_store.seed_defaults() is True
    assert rule_store.seed_defaults() is False

    configs = rule_store.list_configs()
    assert len(configs) == 1
    default = configs[0]
    assert default.name == DEFAULT_CONFIG_NAME
    assert default.is_default and default.is_seeded
    assert {rule.document_type for rule in default.rules} == {
        "clinical",
        "imaging",
        "pathology",
        "functional",
        "administrative",
        # Fallback so nothing escapes the configuration unhandled.
        "Other",
    }


def test_seeded_default_encodes_the_previous_hardcoded_behavior(seeded_store):
    rules = {rule.document_type: rule for rule in seeded_store.get_default().rules}

    assert rules["pathology"].action == RuleAction.SKIP
    assert rules["administrative"].action == RuleAction.SKIP
    assert rules["administrative"].use_as_context is True
    assert rules["imaging"].action == RuleAction.EXTRACT
    assert rules["imaging"].max_words == 50
    assert rules["clinical"].max_words == 200


def test_user_edits_to_the_seed_survive_reseeding(seeded_store):
    default = seeded_store.get_default()
    seeded_store.update_config(
        default.id,
        RuleConfigUpdate(name=default.name, golden_rule_prompt="Edited by the user.", rules=[]),
    )

    assert seeded_store.seed_defaults() is False
    assert seeded_store.get_default().golden_rule_prompt == "Edited by the user."


def test_restore_defaults_pulls_the_shipped_prompts_back_in(seeded_store):
    """Seeding only ever runs once, so a database created before a shipped
    prompt changed keeps the old text forever. Restoring is the way back."""
    from app.services.rules.defaults import default_rule_config

    default = seeded_store.get_default()
    seeded_store.update_config(
        default.id,
        RuleConfigUpdate(
            name=default.name,
            golden_rule_prompt="Stale text from an older release.",
            summary_presentation="Stale presentation.",
            rules=[DocumentRuleInput(document_type="imaging", instruction_prompt="Stale.")],
        ),
    )

    restored = seeded_store.restore_defaults(default.id)
    shipped = default_rule_config()

    assert restored.golden_rule_prompt == shipped.golden_rule_prompt
    assert restored.summary_presentation == shipped.summary_presentation
    assert {rule.document_type for rule in restored.rules} == {
        rule.document_type for rule in shipped.rules
    }
    # The operator's own name for the configuration is theirs, not the seed's.
    assert restored.name == default.name
    # Completed extractions keep the rules they actually ran with.
    assert restored.version > default.version


def test_update_bumps_the_version(seeded_store):
    config = seeded_store.get_default()
    assert config.version == 1

    updated = seeded_store.update_config(
        config.id, RuleConfigUpdate(name=config.name, rules=[])
    )
    assert updated.version == 2


def test_first_config_becomes_default_and_set_default_is_exclusive(rule_store):
    first = rule_store.create_config(RuleConfigCreate(name="First"))
    assert first.is_default is True

    second = rule_store.create_config(RuleConfigCreate(name="Second"))
    assert second.is_default is False

    rule_store.set_default(second.id)
    by_id = {config.id: config for config in rule_store.list_configs()}
    assert by_id[second.id].is_default is True
    assert by_id[first.id].is_default is False


def test_duplicate_names_are_rejected(rule_store):
    rule_store.create_config(RuleConfigCreate(name="Shared name"))
    with pytest.raises(ProcessingError) as excinfo:
        rule_store.create_config(RuleConfigCreate(name="Shared name"))
    assert excinfo.value.status_code == 409


def test_duplicating_a_config_copies_rules_under_a_free_name(seeded_store):
    source = seeded_store.get_default()
    copy = seeded_store.duplicate_config(source.id)

    assert copy.id != source.id
    assert copy.name == f"{source.name} (copy)"
    assert copy.is_default is False
    assert [rule.document_type for rule in copy.rules] == [
        rule.document_type for rule in source.rules
    ]

    second_copy = seeded_store.duplicate_config(source.id)
    assert second_copy.name == f"{source.name} (copy) 2"


def test_the_last_config_cannot_be_deleted(seeded_store):
    only = seeded_store.get_default()
    with pytest.raises(ProcessingError) as excinfo:
        seeded_store.delete_config(only.id)
    assert excinfo.value.status_code == 400


def test_deleting_the_default_promotes_another_config(seeded_store):
    seeded = seeded_store.get_default()
    other = seeded_store.create_config(RuleConfigCreate(name="Other", is_default=True))
    assert seeded_store.get_default().id == other.id

    seeded_store.delete_config(other.id)

    remaining = seeded_store.list_configs()
    assert len(remaining) == 1
    assert remaining[0].id == seeded.id
    assert remaining[0].is_default is True


def test_resolve_snapshot_defaults_and_orders_rules(seeded_store):
    explicit = seeded_store.create_config(
        RuleConfigCreate(
            name="Ordered",
            rules=[
                DocumentRuleInput(document_type="second"),
                DocumentRuleInput(document_type="first"),
            ],
        )
    )

    default_snapshot = seeded_store.resolve_snapshot(None)
    assert default_snapshot.name == DEFAULT_CONFIG_NAME

    snapshot = seeded_store.resolve_snapshot(explicit.id)
    assert [rule.document_type for rule in snapshot.rules] == ["second", "first"]
    assert snapshot.version == explicit.version


def test_resolve_snapshot_without_any_config_returns_none(rule_store):
    assert rule_store.resolve_snapshot(None) is None


def test_presentation_round_trips_through_create_update_and_snapshot(rule_store):
    created = rule_store.create_config(
        RuleConfigCreate(name="Presented", summary_presentation="Oldest first.")
    )
    assert created.summary_presentation == "Oldest first."

    updated = rule_store.update_config(
        created.id,
        RuleConfigUpdate(name="Presented", summary_presentation="Newest first."),
    )
    assert updated.summary_presentation == "Newest first."

    snapshot = rule_store.resolve_snapshot(created.id)
    assert snapshot.summary_presentation == "Newest first."

    copy = rule_store.duplicate_config(created.id)
    assert copy.summary_presentation == "Newest first."
