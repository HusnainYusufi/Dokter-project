"""Test fixtures.

Every test runs against a throwaway SQLite database and the local object-store
fallback, so the suite needs no MySQL, MinIO, or AI provider keys.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Settings are built at import time and `app.deps` creates store singletons at
# import time, so the environment must be prepared before anything under `app.`
# is imported - including the storage root, so tests never write into the repo.
_STORAGE_ROOT = Path(tempfile.mkdtemp(prefix="dokter-tests-"))
os.environ.setdefault("LLAMA_CLOUD_API_KEY", "test-key")
os.environ.setdefault("ALLOW_LOCAL_FALLBACK", "true")
os.environ["ENABLE_LEGACY_JOB_IMPORT"] = "false"
os.environ["LEGACY_JOB_STORAGE_DIR"] = str(_STORAGE_ROOT / "jobs")
# No MinIO in tests: fail the bucket probe on the first attempt so the object
# store drops to its local fallback quickly instead of retrying for seconds.
os.environ.setdefault("AWS_MAX_ATTEMPTS", "1")
os.environ.setdefault("AWS_RETRY_MODE", "standard")
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("API_AUTH_TOKEN", None)


@pytest.fixture()
def sqlite_db(tmp_path, monkeypatch):
    """Bind the app to an empty SQLite database for one test."""
    from app.core.config import settings
    from app.db import session as db_session

    from app.deps import get_job_store

    database_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", database_url)
    db_session._rebind_engine(database_url)
    db_session.init_database_schema()
    # Arms the object store's local fallback (there is no MinIO here). Without
    # this a test that stores an artifact would depend on some earlier test
    # having started the app and initialized the singleton for it.
    get_job_store().initialize()
    yield database_url


@pytest.fixture()
def rule_store(sqlite_db):
    from app.services.rules import RuleConfigStore

    return RuleConfigStore()


@pytest.fixture()
def seeded_store(rule_store):
    rule_store.seed_defaults()
    return rule_store


@pytest.fixture()
def client(sqlite_db):
    """TestClient with the schema created and the default config seeded."""
    from fastapi.testclient import TestClient

    from app.deps import get_rule_config_store
    from app.main import app

    get_rule_config_store().seed_defaults()
    with TestClient(app) as test_client:
        yield test_client
