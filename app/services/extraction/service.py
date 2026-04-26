"""Public extraction service.

Mirrors the surface area of the old `ExtractionPipelineService` so existing
endpoints, deps, and runner code keep working.
"""
from __future__ import annotations

import logging

from app.schemas.extraction import (
    ExtractionJobDetail,
    ExtractionJobSummary,
)
from app.services.document_export import DocumentExportService
from app.services.extraction.persistence import JobPersistence
from app.services.job_store import EncryptedJobStore

logger = logging.getLogger(__name__)


class ExtractionService:
    """End-to-end extraction workflow with encrypted persistence."""

    def __init__(
        self,
        store: EncryptedJobStore | None = None,
        exporter: DocumentExportService | None = None,
    ) -> None:
        self.store = store or EncryptedJobStore()
        self.exporter = exporter or DocumentExportService()
        self.persistence = JobPersistence(self.store)

    async def create_job(self, filename: str, file_content: bytes) -> ExtractionJobSummary:
        return await self.persistence.create_job_from_source(
            filename=filename,
            file_content=file_content,
        )

    async def create_job_from_vault_file(self, file_id: str) -> ExtractionJobSummary:
        return await self.persistence.create_job_from_vault_file(file_id)

    def list_jobs(self) -> list[ExtractionJobSummary]:
        return self.persistence.list_jobs()

    def get_job(self, job_id: str) -> ExtractionJobDetail:
        return self.persistence.get_job(job_id)

    def get_source_document(self, job_id: str) -> tuple[str, bytes]:
        return self.persistence.get_source_document(job_id)

    def get_export_document(self, job_id: str) -> tuple[str, bytes]:
        return self.persistence.get_export_document(job_id)

    def cancel_job(self, job_id: str) -> ExtractionJobSummary:
        return self.persistence.cancel_job(job_id)

    def retry_job(self, job_id: str) -> ExtractionJobSummary:
        return self.persistence.retry_job(job_id)

    def delete_job(self, job_id: str) -> None:
        self.persistence.delete_job(job_id)

    def recover_incomplete_jobs(self) -> list[str]:
        return self.persistence.recover_incomplete_jobs()

    async def process_job(self, job_id: str) -> None:
        from app.services.extraction.pipeline import process_job

        await process_job(self, job_id)
