"""The saved document type registry."""
from __future__ import annotations

import pytest

from app.core.exceptions import ProcessingError
from app.schemas.rules import DocumentRuleInput, RuleConfigCreate, RuleConfigUpdate
from app.services.rules.document_types import (
    BUILTIN_DOCUMENT_TYPES,
    CATCH_ALL_DOCUMENT_TYPE,
    SUGGESTED_DOCUMENT_TYPES,
    DocumentTypeStore,
)


@pytest.fixture()
def type_store(sqlite_db):
    store = DocumentTypeStore()
    store.seed_defaults()
    return store


def test_seeding_inserts_every_shipped_type_once(sqlite_db):
    store = DocumentTypeStore()
    seeded = store.seed_defaults()
    assert seeded == len(BUILTIN_DOCUMENT_TYPES) + len(SUGGESTED_DOCUMENT_TYPES)
    # Idempotent: a second boot must not duplicate or overwrite.
    assert store.seed_defaults() == 0
    assert len(store.list_types()) == seeded


def test_parser_buckets_lead_the_list_and_are_marked_builtin(type_store):
    types = type_store.list_types()
    leading = types[: len(BUILTIN_DOCUMENT_TYPES)]

    assert [item.name for item in leading] == [name for name, _ in BUILTIN_DOCUMENT_TYPES]
    assert all(item.is_builtin for item in leading)
    assert not any(item.is_builtin for item in types[len(BUILTIN_DOCUMENT_TYPES):])


def test_every_shipped_type_carries_a_description(type_store):
    assert all(item.description.strip() for item in type_store.list_types())


def test_the_catch_all_type_is_shipped(type_store):
    names = [item.name for item in type_store.list_types()]
    assert CATCH_ALL_DOCUMENT_TYPE in names


def test_creating_a_type_and_rejecting_duplicates(type_store):
    from app.schemas.rules import DocumentTypeCreate

    created = type_store.create_type(DocumentTypeCreate(name="Bariatric assessment", description="x"))
    assert created.is_builtin is False
    assert created.usage_count == 0

    # Case-insensitive: near-duplicates are not allowed to pile up.
    with pytest.raises(ProcessingError) as excinfo:
        type_store.create_type(DocumentTypeCreate(name="bariatric ASSESSMENT"))
    assert excinfo.value.status_code == 409


def test_a_saved_type_can_be_permanently_removed(type_store):
    from app.schemas.rules import DocumentTypeCreate

    created = type_store.create_type(DocumentTypeCreate(name="Temporary kind"))
    type_store.delete_type(created.id)
    assert "Temporary kind" not in [item.name for item in type_store.list_types()]


def test_parser_buckets_cannot_be_removed(type_store):
    clinical = next(item for item in type_store.list_types() if item.name == "clinical")
    with pytest.raises(ProcessingError) as excinfo:
        type_store.delete_type(clinical.id)
    assert excinfo.value.status_code == 400


def test_deleting_an_unknown_type_is_not_found(type_store):
    with pytest.raises(ProcessingError) as excinfo:
        type_store.delete_type("dt_missing")
    assert excinfo.value.status_code == 404


def test_usage_count_reflects_rules_referencing_the_type(type_store, rule_store):
    rule_store.create_config(
        RuleConfigCreate(
            name="Uses imaging",
            rules=[DocumentRuleInput(document_type="imaging")],
        )
    )
    imaging = next(item for item in type_store.list_types() if item.name == "imaging")
    assert imaging.usage_count == 1


def test_saving_a_config_registers_a_brand_new_type(type_store, rule_store):
    """Typing a new type into a rule and saving keeps it selectable afterwards."""
    rule_store.create_config(
        RuleConfigCreate(
            name="Novel",
            rules=[DocumentRuleInput(document_type="SKU-4471 Intake Form")],
        )
    )
    assert "SKU-4471 Intake Form" in [item.name for item in type_store.list_types()]


def test_updating_a_config_registers_new_types_too(type_store, rule_store):
    config = rule_store.create_config(RuleConfigCreate(name="Grows"))
    rule_store.update_config(
        config.id,
        RuleConfigUpdate(
            name="Grows",
            rules=[DocumentRuleInput(document_type="Bespoke Panel Report")],
        ),
    )
    assert "Bespoke Panel Report" in [item.name for item in type_store.list_types()]


def test_a_removed_type_leaves_existing_rules_untouched(type_store, rule_store):
    """Deleting a type stops it being offered; it must not silently rewrite the
    behavior of configurations already using it."""
    from app.schemas.rules import DocumentTypeCreate

    created = type_store.create_type(DocumentTypeCreate(name="Doomed kind"))
    config = rule_store.create_config(
        RuleConfigCreate(name="Holds it", rules=[DocumentRuleInput(document_type="Doomed kind")])
    )

    type_store.delete_type(created.id)

    still = rule_store.get_config(config.id)
    assert [rule.document_type for rule in still.rules] == ["Doomed kind"]


def test_seeding_repairs_bare_rows_auto_registered_by_a_config(sqlite_db, rule_store):
    """Config seeding registers the types its rules use. If that happens first,
    those bare rows must not block the shipped catalogue or leave the parser
    buckets unmarked and undescribed."""
    rule_store.seed_defaults()
    store = DocumentTypeStore()

    store.seed_defaults()

    by_name = {item.name.lower(): item for item in store.list_types()}
    for name, _ in BUILTIN_DOCUMENT_TYPES:
        assert by_name[name.lower()].is_builtin is True
        assert by_name[name.lower()].description.strip()
    for name, _ in SUGGESTED_DOCUMENT_TYPES:
        assert name.lower() in by_name
        assert by_name[name.lower()].description.strip()
