"""Adapter between the new extraction package and the existing EncryptedJobStore.

Keeps job CRUD, artifact storage, and vault wiring identical to the old service.
"""
from __future__ import annotations

import hashlib
import logging

from app.core.exceptions import ExportError, ProcessingError
from app.schemas.extraction import (
    ExtractionJobDetail,
    ExtractionJobSummary,
    JobStatus,
    PipelineStepStatus,
)
from app.services.job_store import EncryptedJobStore, job_to_summary

logger = logging.getLogger(__name__)


class JobPersistence:
    def __init__(self, store: EncryptedJobStore) -> None:
        self.store = store

    async def create_job_from_source(
        self,
        *,
        filename: str,
        file_content: bytes,
        source_file_id: str | None = None,
    ) -> ExtractionJobSummary:
        source_digest = hashlib.sha256(file_content).hexdigest()
        existing_job = self.store.find_job_by_source_digest(source_digest)

        if existing_job and existing_job.status in {JobStatus.QUEUED, JobStatus.PROCESSING}:
            return job_to_summary(existing_job)

        if (
            existing_job
            and existing_job.status == JobStatus.COMPLETED
            and self.store.artifact_exists(existing_job.id, "summary_doc")
            and self.store.artifact_exists(existing_job.id, "extraction_result")
        ):
            cloned_job = self.store.clone_job_from_existing(
                existing_job,
                filename=filename,
                source_digest=source_digest,
                source_bytes=file_content,
                source_file_id=source_file_id,
            )
            return job_to_summary(cloned_job)

        if (
            existing_job
            and existing_job.status == JobStatus.FAILED
            and self.store.artifact_exists(existing_job.id, "source_pdf")
        ):
            existing_job.status = JobStatus.QUEUED
            existing_job.error = None
            existing_job.filename = filename
            existing_job.source_file_id = source_file_id or existing_job.source_file_id
            existing_job.export_artifact.ready = False
            existing_job.export_artifact.size_bytes = None
            for step in existing_job.pipeline:
                if step.key == "upload":
                    step.status = PipelineStepStatus.COMPLETED
                    step.detail = "Source document encrypted and stored."
                else:
                    step.status = PipelineStepStatus.PENDING
                    step.detail = "Queued to retry."
            self.store.save_job(existing_job)
            return job_to_summary(existing_job)

        job = self.store.create_job(filename, source_digest=source_digest, source_file_id=source_file_id)
        self.store.save_artifact(job.id, "source_pdf", file_content)
        return job_to_summary(job)

    async def create_job_from_vault_file(self, file_id: str) -> ExtractionJobSummary:
        source = self.store.get_vault_file(file_id).file
        if not source.can_extract:
            raise ProcessingError("Only PDF vault files can start extraction jobs.", status_code=400)
        _, payload = self.store.get_vault_file_bytes(file_id)
        return await self.create_job_from_source(
            filename=source.name,
            file_content=payload,
            source_file_id=file_id,
        )

    def list_jobs(self) -> list[ExtractionJobSummary]:
        return self.store.list_jobs()

    def get_job(self, job_id: str) -> ExtractionJobDetail:
        return self.store.get_job(job_id)

    def get_source_document(self, job_id: str) -> tuple[str, bytes]:
        job = self.store.get_job(job_id)
        return job.filename, self.store.read_artifact(job_id, "source_pdf")

    def get_export_document(self, job_id: str) -> tuple[str, bytes]:
        job = self.store.get_job(job_id)
        if not job.export_artifact.ready:
            raise ExportError("The export artifact is not ready yet.")
        return job.export_artifact.filename, self.store.read_artifact(job_id, "summary_doc")

    def cancel_job(self, job_id: str) -> ExtractionJobSummary:
        job = self.store.get_job(job_id)
        if job.status not in {JobStatus.QUEUED, JobStatus.PROCESSING}:
            return job_to_summary(job)
        job.status = JobStatus.CANCELLED
        job.error = "Job cancelled by user."
        for step in job.pipeline:
            if step.status == PipelineStepStatus.RUNNING:
                step.status = PipelineStepStatus.FAILED
                step.detail = "Cancelled by user."
        self.store.save_job(job)
        return job_to_summary(job)

    def retry_job(self, job_id: str) -> ExtractionJobSummary:
        job = self.store.get_job(job_id)
        if not self.store.artifact_exists(job.id, "source_pdf"):
            raise ProcessingError("Source PDF is not available for retry.", status_code=400)

        job.status = JobStatus.QUEUED
        job.processing_started_at = None
        job.error = None
        job.export_artifact.ready = False
        job.export_artifact.size_bytes = None
        for step in job.pipeline:
            if step.key == "upload":
                step.status = PipelineStepStatus.COMPLETED
                step.detail = "Source document encrypted and stored."
            else:
                step.status = PipelineStepStatus.PENDING
                step.detail = "Waiting for retry."
        self.store.save_job(job)
        return job_to_summary(job)

    def delete_job(self, job_id: str) -> None:
        self.store.delete_job(job_id)

    def recover_incomplete_jobs(self) -> list[str]:
        recovered: list[str] = []
        for job in self.store.list_job_details():
            if job.status not in {JobStatus.QUEUED, JobStatus.PROCESSING}:
                continue
            job.status = JobStatus.QUEUED
            job.processing_started_at = None
            for step in job.pipeline:
                if step.status == PipelineStepStatus.RUNNING:
                    step.status = PipelineStepStatus.PENDING
                    step.detail = "Queued to resume after server restart."
            self.store.save_job(job)
            recovered.append(job.id)
        return recovered

    def read_source_pdf(self, job_id: str) -> bytes:
        return self.store.read_artifact(job_id, "source_pdf")

    def save_artifact(self, job_id: str, name: str, data: bytes) -> None:
        self.store.save_artifact(job_id, name, data)

    def artifact_exists(self, job_id: str, name: str) -> bool:
        return self.store.artifact_exists(job_id, name)

    def save_job(self, job: ExtractionJobDetail) -> None:
        self.store.save_job(job)
