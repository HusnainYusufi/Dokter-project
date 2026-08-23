"""Job creation carries the rule configuration, and caching respects it."""
from __future__ import annotations

import pytest

from app.schemas.rules import RuleConfigCreate

PDF = b"%PDF-1.4 minimal source bytes"


@pytest.fixture()
def service(sqlite_db):
    from app.deps import get_extraction_service, get_rule_config_store

    get_rule_config_store().seed_defaults()
    return get_extraction_service()


@pytest.mark.anyio
async def test_a_new_job_records_the_default_configuration(service):
    job = await service.create_job(filename="a.pdf", file_content=PDF)

    assert job.rule_config_id
    assert job.rule_config_version == 1

    detail = service.get_job(job.id)
    assert detail.rule_config is not None
    assert detail.rule_config.id == job.rule_config_id
    # The snapshot carries the rules themselves, not just a reference.
    assert detail.rule_config.rules


@pytest.mark.anyio
async def test_the_same_file_under_the_same_config_reuses_the_job(service):
    first = await service.create_job(filename="a.pdf", file_content=PDF)
    second = await service.create_job(filename="a.pdf", file_content=PDF)

    assert second.id == first.id


@pytest.mark.anyio
async def test_the_same_file_under_a_different_config_starts_a_new_job(service):
    from app.deps import get_rule_config_store

    first = await service.create_job(filename="a.pdf", file_content=PDF)
    other = get_rule_config_store().create_config(RuleConfigCreate(name="Other rules"))

    second = await service.create_job(filename="a.pdf", file_content=PDF, rule_config_id=other.id)

    assert second.id != first.id
    assert second.rule_config_name == "Other rules"


@pytest.mark.anyio
async def test_editing_a_config_invalidates_the_cached_job(service):
    from app.deps import get_rule_config_store
    from app.schemas.rules import RuleConfigUpdate

    store = get_rule_config_store()
    first = await service.create_job(filename="a.pdf", file_content=PDF)

    default = store.get_default()
    store.update_config(default.id, RuleConfigUpdate(name=default.name, rules=[]))

    second = await service.create_job(filename="a.pdf", file_content=PDF)

    assert second.id != first.id
    assert second.rule_config_version == 2


@pytest.mark.anyio
async def test_an_unknown_configuration_is_rejected(service):
    from app.core.exceptions import ProcessingError

    with pytest.raises(ProcessingError) as excinfo:
        await service.create_job(filename="a.pdf", file_content=PDF, rule_config_id="cfg_missing")
    assert excinfo.value.status_code == 404
