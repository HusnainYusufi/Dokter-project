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
    ("extract", "Parse"),
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

    def _index_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "job.index.json"

    def _job_metadata_paths(self) -> list[Path]:
        return sorted(self.root.glob("*/job.json.enc"), key=lambda candidate: candidate.stat().st_mtime, reverse=True)

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

    def _build_index_payload(self, job: ExtractionJobDetail) -> dict:
        summary = job_to_summary(job)
        return summary.model_dump(mode="json")

    def _write_index(self, job: ExtractionJobDetail) -> None:
        index_path = self._index_path(job.id)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(self._build_index_payload(job), indent=2), encoding="utf-8")

    def _read_index(self, job_id: str) -> ExtractionJobSummary | None:
        index_path = self._index_path(job_id)
        if not index_path.exists():
            return None
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            return ExtractionJobSummary.model_validate(payload)
        except Exception:
            return None

    def _build_placeholder_summary(self, job_id: str) -> ExtractionJobSummary:
        job_dir = self._job_dir(job_id)
        timestamp = datetime.fromtimestamp(job_dir.stat().st_mtime, UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        has_source = self.artifact_exists(job_id, "source_pdf")
        has_export = self.artifact_exists(job_id, "summary_doc")
        has_result = self.artifact_exists(job_id, "extraction_result")

        pipeline = default_pipeline()
        if has_source:
            pipeline[0].status = PipelineStepStatus.COMPLETED
            pipeline[0].detail = "Stored source artifact detected."
        if has_export or has_result:
            for step in pipeline[1:]:
                step.status = PipelineStepStatus.COMPLETED
            pipeline[-1].detail = "Recovered from storage manifest fallback."
            status = JobStatus.COMPLETED
        elif has_source:
            pipeline[1].status = PipelineStepStatus.RUNNING
            pipeline[1].detail = "Stored job exists but encrypted metadata could not be read."
            status = JobStatus.PROCESSING
        else:
            status = JobStatus.FAILED

        return ExtractionJobSummary(
            id=job_id,
            filename=f"Recovered document {job_id[-6:]}",
            source_digest=None,
            status=status,
            created_at=timestamp,
            updated_at=timestamp,
            page_count=0,
            patient_count=0,
            document_count=0,
            capture_certification="Recovered from storage folder after metadata read failure.",
            pipeline=pipeline,
            export_artifact=ExportArtifact(
                filename=f"{job_id}.doc",
                ready=has_export,
                size_bytes=None,
            ),
            error="Stored job metadata could not be decrypted with current server key.",
        )

    def build_export_filename(self, source_filename: str, patient_name: str | None = None) -> str:
        stem = Path(source_filename).stem
        if patient_name:
            safe_patient = "".join(char if char.isalnum() or char in {" ", "-"} else "" for char in patient_name).strip()
            if safe_patient:
                stem = f"{safe_patient} - {stem}"
        safe_stem = "".join(char if char.isalnum() or char in {" ", "-", "_"} else "-" for char in stem).strip(" -_")
        return f"{safe_stem or 'extractive-summary'}.doc"

    def create_job(self, filename: str, source_digest: str | None = None) -> ExtractionJobDetail:
        self.cleanup_expired_jobs()
        job_id = f"job_{uuid4().hex[:12]}"
        now = utc_now_iso()
        pipeline = default_pipeline()
        pipeline[0].status = PipelineStepStatus.COMPLETED
        pipeline[0].detail = "Source document encrypted and stored."
        job = ExtractionJobDetail(
            id=job_id,
            filename=filename,
            source_digest=source_digest,
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
        self._write_index(job)

    def get_job(self, job_id: str) -> ExtractionJobDetail:
        path = self._metadata_path(job_id)
        if not path.exists():
            raise JobNotFoundError(job_id)
        return self._read_job_from_path(path)

    def list_jobs(self) -> list[ExtractionJobSummary]:
        self.cleanup_expired_jobs()
        jobs: list[ExtractionJobSummary] = []
        seen_job_ids: set[str] = set()
        for path in self._job_metadata_paths():
            job_id = path.parent.name
            seen_job_ids.add(job_id)
            try:
                jobs.append(job_to_summary(self._read_job_from_path(path)))
            except Exception:
                fallback = self._read_index(job_id) or self._build_placeholder_summary(job_id)
                jobs.append(fallback)

        for job_dir in sorted(self.root.glob("job_*"), key=lambda candidate: candidate.stat().st_mtime, reverse=True):
            if job_dir.name in seen_job_ids:
                continue
            fallback = self._read_index(job_dir.name) or self._build_placeholder_summary(job_dir.name)
            jobs.append(fallback)
        return jobs

    def list_job_details(self) -> list[ExtractionJobDetail]:
        self.cleanup_expired_jobs()
        jobs: list[ExtractionJobDetail] = []
        for path in self._job_metadata_paths():
            try:
                jobs.append(self._read_job_from_path(path))
            except Exception:
                continue
        return jobs

    def find_job_by_source_digest(self, source_digest: str) -> ExtractionJobDetail | None:
        normalized_digest = source_digest.strip().lower()
        if not normalized_digest:
            return None

        for job in self.list_job_details():
            if (job.source_digest or "").strip().lower() == normalized_digest:
                return job
        return None

    def clone_job_from_existing(
        self,
        job: ExtractionJobDetail,
        *,
        filename: str,
        source_digest: str | None = None,
        source_bytes: bytes | None = None,
    ) -> ExtractionJobDetail:
        cloned = self.create_job(filename=filename, source_digest=source_digest or job.source_digest)
        cloned = job.model_copy(
            deep=True,
            update={
                "id": cloned.id,
                "filename": filename,
                "source_digest": source_digest or job.source_digest,
                "created_at": cloned.created_at,
                "updated_at": cloned.updated_at,
                "source_available": True,
            },
        )
        self.save_job(cloned)

        self.save_artifact(cloned.id, "source_pdf", source_bytes or self.read_artifact(job.id, "source_pdf"))
        if self.artifact_exists(job.id, "summary_doc"):
            self.save_artifact(cloned.id, "summary_doc", self.read_artifact(job.id, "summary_doc"))
        if self.artifact_exists(job.id, "extraction_result"):
            self.save_artifact(cloned.id, "extraction_result", self.read_artifact(job.id, "extraction_result"))
        return cloned

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
