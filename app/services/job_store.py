from __future__ import annotations

import base64
import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.exceptions import JobNotFoundError, StorageError
from app.schemas.extraction import (
    ExportArtifact,
    ExtractionJobDetail,
    ExtractionJobSummary,
    JobStatus,
    PipelineStep,
    PipelineStepStatus,
)

PIPELINE_BLUEPRINT: list[tuple[str, str]] = [
    ("upload", "Upload"),
    ("extract", "Extract"),
    ("boundary", "Boundary"),
    ("summary", "Summarize"),
    ("export", "Export"),
]


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_pipeline() -> list[PipelineStep]:
    return [
        PipelineStep(key=key, label=label, status=PipelineStepStatus.PENDING)
        for key, label in PIPELINE_BLUEPRINT
    ]


def job_to_summary(job: ExtractionJobDetail) -> ExtractionJobSummary:
    return ExtractionJobSummary.model_validate(job.model_dump())


class EncryptedJobStore:
    """Encrypted on-disk store for uploaded PDFs, results, and exports."""

    def __init__(self) -> None:
        self.root = Path(settings.JOB_STORAGE_DIR)
        self.root.mkdir(parents=True, exist_ok=True)
        self.fernet = Fernet(self._build_key())

    def _build_key(self) -> bytes:
        if settings.ARTIFACT_ENCRYPTION_KEY:
            raw = settings.ARTIFACT_ENCRYPTION_KEY.strip().encode()
            if len(raw) == 44:
                return raw
            digest = hashlib.sha256(raw).digest()
            return base64.urlsafe_b64encode(digest)

        material = "|".join(
            value
            for value in (
                settings.NEXTAUTH_SECRET,
                settings.LLAMA_CLOUD_API_KEY,
                settings.PROJECT_NAME,
            )
            if value
        )
        digest = hashlib.sha256(material.encode()).digest()
        return base64.urlsafe_b64encode(digest)

    def _job_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def _metadata_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "job.json.enc"

    def _artifact_path(self, job_id: str, name: str) -> Path:
        return self._job_dir(job_id) / f"{name}.enc"

    def _encrypt_bytes(self, data: bytes) -> bytes:
        return self.fernet.encrypt(data)

    def _decrypt_bytes(self, data: bytes) -> bytes:
        try:
            return self.fernet.decrypt(data)
        except InvalidToken as exc:
            raise StorageError("Artifact decryption failed. Check the encryption key configuration.") from exc

    def _read_job_from_path(self, path: Path) -> ExtractionJobDetail:
        payload = self._decrypt_bytes(path.read_bytes())
        return ExtractionJobDetail.model_validate_json(payload.decode())

    def _write_job_to_path(self, path: Path, job: ExtractionJobDetail) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = job.model_dump_json(indent=2).encode()
        path.write_bytes(self._encrypt_bytes(payload))

    def build_export_filename(self, source_filename: str, patient_name: str | None = None) -> str:
        stem = Path(source_filename).stem
        if patient_name:
            safe_patient = "".join(char if char.isalnum() or char in {" ", "-"} else "" for char in patient_name).strip()
            if safe_patient:
                stem = f"{safe_patient} - {stem}"
        safe_stem = "".join(char if char.isalnum() or char in {" ", "-", "_"} else "-" for char in stem).strip(" -_")
        return f"{safe_stem or 'extractive-summary'}.doc"

    def create_job(self, filename: str) -> ExtractionJobDetail:
        self.cleanup_expired_jobs()
        job_id = f"job_{uuid4().hex[:12]}"
        now = utc_now_iso()
        pipeline = default_pipeline()
        pipeline[0].status = PipelineStepStatus.COMPLETED
        pipeline[0].detail = "Source document encrypted and stored."
        job = ExtractionJobDetail(
            id=job_id,
            filename=filename,
            status=JobStatus.QUEUED,
            created_at=now,
            updated_at=now,
            source_available=True,
            pipeline=pipeline,
            export_artifact=ExportArtifact(filename=self.build_export_filename(filename)),
        )
        self.save_job(job)
        return job

    def save_job(self, job: ExtractionJobDetail) -> None:
        job.updated_at = utc_now_iso()
        self._write_job_to_path(self._metadata_path(job.id), job)

    def get_job(self, job_id: str) -> ExtractionJobDetail:
        path = self._metadata_path(job_id)
        if not path.exists():
            raise JobNotFoundError(job_id)
        return self._read_job_from_path(path)

    def list_jobs(self) -> list[ExtractionJobSummary]:
        self.cleanup_expired_jobs()
        jobs: list[ExtractionJobSummary] = []
        for path in sorted(self.root.glob("*/job.json.enc"), key=lambda candidate: candidate.stat().st_mtime, reverse=True):
            try:
                jobs.append(job_to_summary(self._read_job_from_path(path)))
            except Exception:
                continue
        return jobs

    def save_artifact(self, job_id: str, name: str, data: bytes) -> None:
        try:
            path = self._artifact_path(job_id, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self._encrypt_bytes(data))
        except Exception as exc:
            raise StorageError(f"Failed to store encrypted artifact '{name}'.") from exc

    def read_artifact(self, job_id: str, name: str) -> bytes:
        path = self._artifact_path(job_id, name)
        if not path.exists():
            raise JobNotFoundError(job_id)
        try:
            return self._decrypt_bytes(path.read_bytes())
        except JobNotFoundError:
            raise
        except Exception as exc:
            raise StorageError(f"Failed to read encrypted artifact '{name}'.") from exc

    def artifact_exists(self, job_id: str, name: str) -> bool:
        return self._artifact_path(job_id, name).exists()

    def delete_job(self, job_id: str) -> None:
        job_dir = self._job_dir(job_id)
        if not job_dir.exists():
            raise JobNotFoundError(job_id)
        shutil.rmtree(job_dir, ignore_errors=True)

    def cleanup_expired_jobs(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(hours=settings.JOB_RETENTION_HOURS)
        for path in self.root.glob("*/job.json.enc"):
            try:
                job = self._read_job_from_path(path)
                updated_at = datetime.fromisoformat(job.updated_at.replace("Z", "+00:00"))
                if updated_at < cutoff:
                    shutil.rmtree(path.parent, ignore_errors=True)
            except Exception:
                continue
