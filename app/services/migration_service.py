from __future__ import annotations

import logging

from app.core.config import settings
from app.services.job_store import EncryptedJobStore
from app.services.legacy_job_store import LegacyEncryptedJobStore

logger = logging.getLogger(__name__)


class LegacyJobMigrationService:
    def __init__(
        self,
        *,
        store: EncryptedJobStore,
        legacy_store: LegacyEncryptedJobStore | None = None,
    ) -> None:
        self.store = store
        self.legacy_store = legacy_store or LegacyEncryptedJobStore()

    def import_legacy_jobs(self) -> int:
        if not settings.ENABLE_LEGACY_JOB_IMPORT:
            return 0

        imported = 0
        for job in self.legacy_store.list_job_details():
            try:
                did_import = self.store.import_legacy_job(
                    job,
                    source_pdf=self.legacy_store.read_artifact(job.id, "source_pdf")
                    if self.legacy_store.artifact_exists(job.id, "source_pdf")
                    else None,
                    summary_doc=self.legacy_store.read_artifact(job.id, "summary_doc")
                    if self.legacy_store.artifact_exists(job.id, "summary_doc")
                    else None,
                    extraction_result=self.legacy_store.read_artifact(job.id, "extraction_result")
                    if self.legacy_store.artifact_exists(job.id, "extraction_result")
                    else None,
                )
                if did_import:
                    imported += 1
            except Exception:
                logger.exception("Legacy job import failed for %s", job.id)
        return imported
