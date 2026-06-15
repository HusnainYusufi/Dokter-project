from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import mimetypes
from pathlib import Path
from uuid import uuid4

from fastapi import status
from sqlalchemy import select

from app.core.exceptions import JobNotFoundError, ProcessingError, StorageError
from app.db.models import ExtractionJobRecord, JobArtifactRecord, VaultFileRecord, VaultFolderRecord
from app.db.session import SessionLocal
from app.schemas.extraction import (
    ExportArtifact,
    ExtractionJobDetail,
    ExtractionJobSummary,
    JobStatus,
    PipelineStep,
    PipelineStepStatus,
)
from app.schemas.vault import (
    VaultBrowseResponse,
    VaultFileResponse,
    VaultFileSourceKind,
    VaultFileSummary,
    VaultFolderResponse,
    VaultFolderSummary,
    VaultPreviewKind,
    VaultRecentResponse,
    VaultUploadResponse,
)
from app.services.encryption import VaultEncryptionService
from app.services.object_store import EncryptedObjectStore

PIPELINE_BLUEPRINT: list[tuple[str, str]] = [
    ("upload", "Upload"),
    ("extract", "Parse"),
    ("boundary", "Boundary"),
    ("summary", "Summarize"),
    ("export", "Export"),
]

SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".flac"}
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
SUPPORTED_DOC_EXTENSIONS = {".docx", ".doc"}
SUPPORTED_DOC_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)


def datetime_to_iso(value: datetime) -> str:
    return value.replace(tzinfo=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_pipeline() -> list[PipelineStep]:
    return [
        PipelineStep(key=key, label=label, status=PipelineStepStatus.PENDING)
        for key, label in PIPELINE_BLUEPRINT
    ]


def job_to_summary(job: ExtractionJobDetail) -> ExtractionJobSummary:
    return ExtractionJobSummary.model_validate(job.model_dump())


def normalize_filename(name: str | None, fallback: str = "upload") -> str:
    candidate = Path(name or fallback).name.strip()
    return candidate or fallback


def normalize_folder_name(name: str) -> str:
    candidate = name.replace("/", " ").replace("\\", " ").strip()
    if not candidate:
        raise ProcessingError("Folder name cannot be empty.", status_code=status.HTTP_400_BAD_REQUEST)
    return candidate


def normalize_extension(filename: str) -> str | None:
    suffix = Path(filename).suffix.strip().lower()
    return suffix or None


def normalize_content_type(filename: str, content_type: str | None) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    normalized = (content_type or "").strip().lower()
    if normalized and normalized != "application/octet-stream":
        return normalized
    if guessed:
        return guessed.lower()
    extension = normalize_extension(filename)
    if extension == ".pdf":
        return "application/pdf"
    if extension in SUPPORTED_DOC_EXTENSIONS:
        if extension == ".doc":
            return "application/msword"
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "application/octet-stream"


def classify_preview_kind(filename: str, content_type: str) -> VaultPreviewKind:
    extension = normalize_extension(filename)
    if content_type == "application/pdf" or extension == ".pdf":
        return VaultPreviewKind.PDF
    if content_type.startswith("image/") or extension in SUPPORTED_IMAGE_EXTENSIONS:
        return VaultPreviewKind.IMAGE
    if content_type.startswith("audio/") or extension in SUPPORTED_AUDIO_EXTENSIONS:
        return VaultPreviewKind.AUDIO
    if content_type.startswith("video/") or extension in SUPPORTED_VIDEO_EXTENSIONS:
        return VaultPreviewKind.VIDEO
    if extension in SUPPORTED_DOC_EXTENSIONS or content_type in SUPPORTED_DOC_CONTENT_TYPES:
        return VaultPreviewKind.DOC
    return VaultPreviewKind.DOWNLOAD


def can_extract_content(filename: str, content_type: str) -> bool:
    return classify_preview_kind(filename, content_type) == VaultPreviewKind.PDF


def is_allowed_vault_type(filename: str, content_type: str) -> bool:
    if classify_preview_kind(filename, content_type) != VaultPreviewKind.DOWNLOAD:
        return True
    return False


class EncryptedJobStore:
    """MySQL metadata store plus encrypted S3-compatible object storage."""

    def __init__(
        self,
        *,
        object_store: EncryptedObjectStore | None = None,
        encryption: VaultEncryptionService | None = None,
    ) -> None:
        self.encryption = encryption or VaultEncryptionService()
        self.object_store = object_store or EncryptedObjectStore(encryption=self.encryption)

    def initialize(self) -> None:
        self.object_store.ensure_bucket()

    def _job_artifact_object_key(self, job_id: str, name: str) -> str:
        return f"jobs/{job_id}/{name}"

    def _vault_object_key(self, file_id: str) -> str:
        return f"vault/files/{file_id}/content"

    def _build_job_payload(self, job: ExtractionJobDetail) -> str:
        return self.encryption.encrypt_text(job.model_dump_json(indent=2))

    def _read_job_payload(self, payload_encrypted: str) -> ExtractionJobDetail:
        payload = self.encryption.decrypt_text(payload_encrypted)
        return ExtractionJobDetail.model_validate_json(payload)

    def _folder_to_summary(self, folder: VaultFolderRecord) -> VaultFolderSummary:
        return VaultFolderSummary(
            id=folder.id,
            name=folder.name,
            parent_id=folder.parent_id,
            created_at=datetime_to_iso(folder.created_at),
            updated_at=datetime_to_iso(folder.updated_at),
        )

    def _latest_linked_job(self, session, file_id: str) -> tuple[str | None, JobStatus | None]:
        record = session.execute(
            select(ExtractionJobRecord)
            .where(ExtractionJobRecord.source_file_id == file_id)
            .order_by(ExtractionJobRecord.updated_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if not record:
            return None, None
        return record.id, JobStatus(record.status)

    def _file_to_summary(
        self,
        session,
        file: VaultFileRecord,
        linked_job: tuple[str | None, JobStatus | None] | None = None,
    ) -> VaultFileSummary:
        linked_job_id, linked_job_status = linked_job if linked_job is not None else self._latest_linked_job(session, file.id)
        return VaultFileSummary(
            id=file.id,
            name=file.name,
            folder_id=file.folder_id,
            content_type=file.content_type,
            size_bytes=file.size_bytes,
            extension=file.extension,
            preview_kind=VaultPreviewKind(file.preview_kind),
            can_extract=file.can_extract,
            source_kind=VaultFileSourceKind(file.source_kind),
            checksum_sha256=file.checksum_sha256,
            linked_job_id=linked_job_id,
            linked_job_status=linked_job_status,
            created_at=datetime_to_iso(file.created_at),
            updated_at=datetime_to_iso(file.updated_at),
        )

    def _latest_linked_jobs_for_files(self, session, file_ids: list[str]) -> dict[str, tuple[str | None, JobStatus | None]]:
        if not file_ids:
            return {}
        records = session.execute(
            select(ExtractionJobRecord)
            .where(ExtractionJobRecord.source_file_id.in_(file_ids))
            .order_by(ExtractionJobRecord.source_file_id.asc(), ExtractionJobRecord.updated_at.desc())
        ).scalars().all()
        linked: dict[str, tuple[str | None, JobStatus | None]] = {}
        for record in records:
            if not record.source_file_id or record.source_file_id in linked:
                continue
            linked[record.source_file_id] = (record.id, JobStatus(record.status))
        return linked

    def _build_placeholder_summary(self, record: ExtractionJobRecord) -> ExtractionJobSummary:
        pipeline = [
            PipelineStep.model_validate(step)
            for step in (record.pipeline_json or [step.model_dump(mode="json") for step in default_pipeline()])
        ]
        return ExtractionJobSummary(
            id=record.id,
            source_file_id=record.source_file_id,
            filename=record.filename,
            source_digest=record.source_digest,
            status=JobStatus(record.status),
            created_at=datetime_to_iso(record.created_at),
            updated_at=datetime_to_iso(record.updated_at),
            processing_started_at=utc_now_iso() if record.status == JobStatus.PROCESSING.value else None,
            page_count=record.page_count,
            patient_count=record.patient_count,
            document_count=record.document_count,
            capture_certification=record.capture_certification,
            pipeline=pipeline,
            export_artifact=ExportArtifact(
                filename=record.export_filename,
                content_type=record.export_content_type,
                ready=record.export_ready,
                size_bytes=record.export_size_bytes,
            ),
            error=record.error,
        )

    def _record_to_job(self, record: ExtractionJobRecord) -> ExtractionJobDetail:
        job = self._read_job_payload(record.payload_encrypted)
        if job.source_file_id != record.source_file_id:
            job.source_file_id = record.source_file_id
        if job.status == JobStatus.PROCESSING and not job.processing_started_at:
            # Backfill active jobs created before processing_started_at existed.
            # Future jobs get an exact value in process_job().
            job.processing_started_at = utc_now_iso()
        return job

    def _ensure_folder(self, session, folder_id: str | None) -> VaultFolderRecord | None:
        if not folder_id:
            return None
        folder = session.get(VaultFolderRecord, folder_id)
        if not folder:
            raise ProcessingError(
                f"Vault folder '{folder_id}' was not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return folder

    def _get_vault_file_record(self, session, file_id: str) -> VaultFileRecord:
        file = session.get(VaultFileRecord, file_id)
        if not file:
            raise ProcessingError(
                f"Vault file '{file_id}' was not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return file

    def _get_artifact_record(self, session, job_id: str, name: str) -> JobArtifactRecord | None:
        return session.execute(
            select(JobArtifactRecord).where(
                JobArtifactRecord.job_id == job_id,
                JobArtifactRecord.name == name,
            )
        ).scalar_one_or_none()

    def _upsert_job_artifact(
        self,
        session,
        *,
        job_id: str,
        name: str,
        object_key: str,
        content_type: str,
        size_bytes: int,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> JobArtifactRecord:
        artifact = self._get_artifact_record(session, job_id, name)
        if not artifact:
            artifact = JobArtifactRecord(
                job_id=job_id,
                name=name,
                object_key=object_key,
                content_type=content_type,
                size_bytes=size_bytes,
            )
            if created_at:
                artifact.created_at = created_at
            if updated_at:
                artifact.updated_at = updated_at
            session.add(artifact)
            return artifact

        artifact.object_key = object_key
        artifact.content_type = content_type
        artifact.size_bytes = size_bytes
        if created_at:
            artifact.created_at = created_at
        if updated_at:
            artifact.updated_at = updated_at
        return artifact

    def _store_new_vault_file(
        self,
        session,
        *,
        name: str,
        payload: bytes,
        content_type: str,
        folder_id: str | None,
        source_kind: VaultFileSourceKind,
        checksum_sha256: str | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> VaultFileRecord:
        normalized_name = normalize_filename(name)
        normalized_content_type = normalize_content_type(normalized_name, content_type)
        if not is_allowed_vault_type(normalized_name, normalized_content_type):
            raise ProcessingError(
                "Unsupported file type. Upload PDF, audio, video, DOC, DOCX, or image files.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        self._ensure_folder(session, folder_id)

        file_id = f"file_{uuid4().hex[:12]}"
        object_key = self._vault_object_key(file_id)
        size_bytes = self.object_store.put_bytes(object_key, payload, content_type=normalized_content_type)
        checksum = checksum_sha256 or hashlib.sha256(payload).hexdigest()
        preview_kind = classify_preview_kind(normalized_name, normalized_content_type)

        record = VaultFileRecord(
            id=file_id,
            folder_id=folder_id,
            name=normalized_name,
            content_type=normalized_content_type,
            size_bytes=size_bytes,
            extension=normalize_extension(normalized_name),
            preview_kind=preview_kind.value,
            can_extract=can_extract_content(normalized_name, normalized_content_type),
            source_kind=source_kind.value,
            checksum_sha256=checksum,
            object_key=object_key,
        )
        if created_at:
            record.created_at = created_at
        if updated_at:
            record.updated_at = updated_at
        session.add(record)
        session.flush()
        return record

    def create_folder(self, name: str, parent_id: str | None = None) -> VaultFolderResponse:
        normalized_name = normalize_folder_name(name)
        with SessionLocal() as session:
            self._ensure_folder(session, parent_id)
            folder = VaultFolderRecord(
                id=f"folder_{uuid4().hex[:12]}",
                name=normalized_name,
                parent_id=parent_id,
            )
            session.add(folder)
            session.commit()
            session.refresh(folder)
            return VaultFolderResponse(folder=self._folder_to_summary(folder))

    def rename_folder(self, folder_id: str, name: str) -> VaultFolderResponse:
        normalized_name = normalize_folder_name(name)
        with SessionLocal() as session:
            folder = self._ensure_folder(session, folder_id)
            if not folder:
                raise ProcessingError(
                    f"Vault folder '{folder_id}' was not found.",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            folder.name = normalized_name
            session.commit()
            session.refresh(folder)
            return VaultFolderResponse(folder=self._folder_to_summary(folder))

    def delete_folder(self, folder_id: str) -> None:
        with SessionLocal() as session:
            folder = self._ensure_folder(session, folder_id)
            if not folder:
                raise ProcessingError(
                    f"Vault folder '{folder_id}' was not found.",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            session.delete(folder)
            session.commit()

    def _build_breadcrumbs(self, session, current_folder: VaultFolderRecord | None) -> list[VaultFolderSummary]:
        breadcrumbs: list[VaultFolderSummary] = []
        cursor = current_folder
        while cursor:
            breadcrumbs.append(self._folder_to_summary(cursor))
            cursor = session.get(VaultFolderRecord, cursor.parent_id) if cursor.parent_id else None
        breadcrumbs.reverse()
        return breadcrumbs

    def browse_vault(self, folder_id: str | None = None) -> VaultBrowseResponse:
        with SessionLocal() as session:
            current_folder = self._ensure_folder(session, folder_id)
            folders = session.execute(
                select(VaultFolderRecord)
                .where(VaultFolderRecord.parent_id == folder_id)
                .order_by(VaultFolderRecord.name.asc())
            ).scalars().all()
            files = session.execute(
                select(VaultFileRecord)
                .where(
                    VaultFileRecord.folder_id == folder_id,
                    VaultFileRecord.source_kind == VaultFileSourceKind.UPLOAD.value,
                )
                .order_by(VaultFileRecord.updated_at.desc(), VaultFileRecord.name.asc())
            ).scalars().all()
            linked_jobs = self._latest_linked_jobs_for_files(session, [file.id for file in files])

            return VaultBrowseResponse(
                current_folder=self._folder_to_summary(current_folder) if current_folder else None,
                breadcrumbs=self._build_breadcrumbs(session, current_folder),
                folders=[self._folder_to_summary(folder) for folder in folders],
                files=[self._file_to_summary(session, file, linked_jobs.get(file.id, (None, None))) for file in files],
            )

    def list_recent_vault_files(self, limit: int = 12) -> VaultRecentResponse:
        with SessionLocal() as session:
            files = session.execute(
                select(VaultFileRecord)
                .where(VaultFileRecord.source_kind == VaultFileSourceKind.UPLOAD.value)
                .order_by(VaultFileRecord.updated_at.desc(), VaultFileRecord.name.asc())
                .limit(limit)
            ).scalars().all()
            return VaultRecentResponse(files=[self._file_to_summary(session, file) for file in files])

    def upload_vault_files(
        self,
        files: list[tuple[str, str | None, bytes]],
        *,
        folder_id: str | None = None,
        source_kind: VaultFileSourceKind = VaultFileSourceKind.UPLOAD,
    ) -> VaultUploadResponse:
        uploaded: list[VaultFileSummary] = []
        with SessionLocal() as session:
            self._ensure_folder(session, folder_id)
            for name, content_type, payload in files:
                file = self._store_new_vault_file(
                    session,
                    name=name,
                    payload=payload,
                    content_type=content_type or "application/octet-stream",
                    folder_id=folder_id,
                    source_kind=source_kind,
                )
                uploaded.append(self._file_to_summary(session, file))
            session.commit()
        return VaultUploadResponse(files=uploaded)

    def get_vault_file(self, file_id: str) -> VaultFileResponse:
        with SessionLocal() as session:
            file = self._get_vault_file_record(session, file_id)
            return VaultFileResponse(file=self._file_to_summary(session, file))

    def get_vault_file_bytes(self, file_id: str) -> tuple[VaultFileSummary, bytes]:
        with SessionLocal() as session:
            file = self._get_vault_file_record(session, file_id)
            summary = self._file_to_summary(session, file)
            return summary, self.object_store.get_bytes(file.object_key)

    def rename_vault_file(self, file_id: str, name: str) -> VaultFileResponse:
        normalized_name = normalize_filename(name)
        with SessionLocal() as session:
            file = self._get_vault_file_record(session, file_id)
            file.name = normalized_name
            file.extension = normalize_extension(normalized_name)
            file.preview_kind = classify_preview_kind(normalized_name, file.content_type).value
            file.can_extract = can_extract_content(normalized_name, file.content_type)
            session.commit()
            session.refresh(file)
            return VaultFileResponse(file=self._file_to_summary(session, file))

    def delete_vault_file(self, file_id: str) -> None:
        with SessionLocal() as session:
            file = self._get_vault_file_record(session, file_id)
            linked_job = session.execute(
                select(ExtractionJobRecord.id).where(ExtractionJobRecord.source_file_id == file_id).limit(1)
            ).scalar_one_or_none()
            if linked_job:
                raise ProcessingError(
                    "Delete linked extraction jobs first, then remove the vault file.",
                    status_code=status.HTTP_409_CONFLICT,
                )

            self.object_store.delete_object(file.object_key)
            session.delete(file)
            session.commit()

    def build_export_filename(self, source_filename: str, patient_name: str | None = None) -> str:
        stem = Path(source_filename).stem
        if patient_name:
            safe_patient = "".join(char if char.isalnum() or char in {" ", "-"} else "" for char in patient_name).strip()
            if safe_patient:
                stem = f"{safe_patient} - {stem}"
        safe_stem = "".join(char if char.isalnum() or char in {" ", "-", "_"} else "-" for char in stem).strip(" -_")
        return f"{safe_stem or 'extractive-summary'}.doc"

    def create_job(
        self,
        filename: str,
        source_digest: str | None = None,
        *,
        source_file_id: str | None = None,
    ) -> ExtractionJobDetail:
        now = utc_now_iso()
        pipeline = default_pipeline()
        pipeline[0].status = PipelineStepStatus.COMPLETED
        pipeline[0].detail = "Source document encrypted and stored."
        job = ExtractionJobDetail(
            id=f"job_{uuid4().hex[:12]}",
            source_file_id=source_file_id,
            filename=normalize_filename(filename),
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
        with SessionLocal() as session:
            record = session.get(ExtractionJobRecord, job.id)
            effective_source_file_id = job.source_file_id or (record.source_file_id if record else None)
            persisted_job = job if effective_source_file_id == job.source_file_id else job.model_copy(update={"source_file_id": effective_source_file_id})
            summary = job_to_summary(persisted_job)
            payload_encrypted = self._build_job_payload(persisted_job)
            if not record:
                record = ExtractionJobRecord(
                    id=persisted_job.id,
                    source_file_id=persisted_job.source_file_id,
                    filename=persisted_job.filename,
                    source_digest=persisted_job.source_digest,
                    status=persisted_job.status.value,
                    created_at=parse_iso_datetime(persisted_job.created_at),
                    updated_at=parse_iso_datetime(persisted_job.updated_at),
                    page_count=persisted_job.page_count,
                    patient_count=persisted_job.patient_count,
                    document_count=persisted_job.document_count,
                    capture_certification=persisted_job.capture_certification,
                    pipeline_json=[step.model_dump(mode="json") for step in persisted_job.pipeline],
                    export_filename=persisted_job.export_artifact.filename,
                    export_content_type=persisted_job.export_artifact.content_type,
                    export_ready=persisted_job.export_artifact.ready,
                    export_size_bytes=persisted_job.export_artifact.size_bytes,
                    error=persisted_job.error,
                    payload_encrypted=payload_encrypted,
                )
                session.add(record)
            else:
                record.source_file_id = persisted_job.source_file_id
                record.filename = summary.filename
                record.source_digest = summary.source_digest
                record.status = summary.status.value
                record.created_at = parse_iso_datetime(persisted_job.created_at)
                record.updated_at = parse_iso_datetime(persisted_job.updated_at)
                record.page_count = summary.page_count
                record.patient_count = summary.patient_count
                record.document_count = summary.document_count
                record.capture_certification = summary.capture_certification
                record.pipeline_json = [step.model_dump(mode="json") for step in summary.pipeline]
                record.export_filename = summary.export_artifact.filename
                record.export_content_type = summary.export_artifact.content_type
                record.export_ready = summary.export_artifact.ready
                record.export_size_bytes = summary.export_artifact.size_bytes
                record.error = summary.error
                record.payload_encrypted = payload_encrypted
            session.commit()

    def get_job(self, job_id: str) -> ExtractionJobDetail:
        with SessionLocal() as session:
            record = session.get(ExtractionJobRecord, job_id)
            if not record:
                raise JobNotFoundError(job_id)
            return self._record_to_job(record)

    def list_jobs(self) -> list[ExtractionJobSummary]:
        jobs: list[ExtractionJobSummary] = []
        with SessionLocal() as session:
            records = session.execute(
                select(ExtractionJobRecord).order_by(ExtractionJobRecord.updated_at.desc())
            ).scalars().all()
            for record in records:
                try:
                    jobs.append(job_to_summary(self._record_to_job(record)))
                except Exception:
                    jobs.append(self._build_placeholder_summary(record))
        return jobs

    def list_job_details(self) -> list[ExtractionJobDetail]:
        jobs: list[ExtractionJobDetail] = []
        with SessionLocal() as session:
            records = session.execute(
                select(ExtractionJobRecord).order_by(ExtractionJobRecord.updated_at.desc())
            ).scalars().all()
            for record in records:
                try:
                    jobs.append(self._record_to_job(record))
                except Exception:
                    continue
        return jobs

    def find_job_by_source_digest(self, source_digest: str) -> ExtractionJobDetail | None:
        normalized = source_digest.strip().lower()
        if not normalized:
            return None

        with SessionLocal() as session:
            record = session.execute(
                select(ExtractionJobRecord)
                .where(ExtractionJobRecord.source_digest == normalized)
                .order_by(ExtractionJobRecord.updated_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if not record:
                return None
            return self._record_to_job(record)

    def clone_job_from_existing(
        self,
        job: ExtractionJobDetail,
        *,
        filename: str,
        source_digest: str | None = None,
        source_bytes: bytes | None = None,
        source_file_id: str | None = None,
    ) -> ExtractionJobDetail:
        cloned = self.create_job(filename=filename, source_digest=source_digest or job.source_digest, source_file_id=source_file_id)
        cloned = job.model_copy(
            deep=True,
            update={
                "id": cloned.id,
                "source_file_id": source_file_id,
                "filename": filename,
                "source_digest": source_digest or job.source_digest,
                "created_at": cloned.created_at,
                "updated_at": cloned.updated_at,
                "source_available": True,
            },
        )
        self.save_job(cloned)

        source_payload = source_bytes or self.read_artifact(job.id, "source_pdf")
        self.save_artifact(cloned.id, "source_pdf", source_payload)
        if self.artifact_exists(job.id, "summary_doc"):
            self.save_artifact(cloned.id, "summary_doc", self.read_artifact(job.id, "summary_doc"))
        if self.artifact_exists(job.id, "extraction_result"):
            self.save_artifact(cloned.id, "extraction_result", self.read_artifact(job.id, "extraction_result"))
        return cloned

    def save_artifact(self, job_id: str, name: str, data: bytes) -> None:
        with SessionLocal() as session:
            record = session.get(ExtractionJobRecord, job_id)
            if not record:
                raise JobNotFoundError(job_id)

            if name == "source_pdf":
                content_type = "application/pdf"
            elif name == "summary_doc":
                content_type = record.export_content_type
            else:
                content_type = "application/json"

            object_key = self._job_artifact_object_key(job_id, name)
            size_bytes = self.object_store.put_bytes(object_key, data, content_type=content_type)
            self._upsert_job_artifact(
                session,
                job_id=job_id,
                name=name,
                object_key=object_key,
                content_type=content_type,
                size_bytes=size_bytes,
            )

            if name == "source_pdf" and not record.source_file_id:
                source_file = self._store_new_vault_file(
                    session,
                    name=record.filename,
                    payload=data,
                    content_type=content_type,
                    folder_id=None,
                    source_kind=VaultFileSourceKind.JOB_SOURCE,
                    checksum_sha256=record.source_digest or hashlib.sha256(data).hexdigest(),
                )
                record.source_file_id = source_file.id

            session.commit()

    def read_artifact(self, job_id: str, name: str) -> bytes:
        with SessionLocal() as session:
            record = session.get(ExtractionJobRecord, job_id)
            if not record:
                raise JobNotFoundError(job_id)
            artifact = self._get_artifact_record(session, job_id, name)
            if artifact:
                return self.object_store.get_bytes(artifact.object_key)
            if name == "source_pdf" and record.source_file_id:
                file = self._get_vault_file_record(session, record.source_file_id)
                return self.object_store.get_bytes(file.object_key)
            raise StorageError(f"Failed to read encrypted artifact '{name}'.")

    def artifact_exists(self, job_id: str, name: str) -> bool:
        with SessionLocal() as session:
            record = session.get(ExtractionJobRecord, job_id)
            if not record:
                return False
            artifact = self._get_artifact_record(session, job_id, name)
            if artifact:
                return self.object_store.object_exists(artifact.object_key)
            if name == "source_pdf" and record.source_file_id:
                file = session.get(VaultFileRecord, record.source_file_id)
                return bool(file and self.object_store.object_exists(file.object_key))
            return False

    def delete_job(self, job_id: str) -> None:
        with SessionLocal() as session:
            record = session.get(ExtractionJobRecord, job_id)
            if not record:
                raise JobNotFoundError(job_id)

            # Re-uploading a byte-identical file content-addresses by source_digest
            # and clones a prior COMPLETED job's results (see create_job_from_source +
            # clone_job_from_existing). Deleting only the selected job therefore leaves
            # identical cached siblings behind, so the "deleted" document reappears on
            # the next upload. Deleting the document purges all of its TERMINAL cache
            # jobs for that digest. Active (queued/processing) siblings are left alone.
            targets = [record]
            digest = (record.source_digest or "").strip().lower()
            if digest:
                siblings = session.execute(
                    select(ExtractionJobRecord).where(
                        ExtractionJobRecord.source_digest == digest,
                        ExtractionJobRecord.id != record.id,
                        ~ExtractionJobRecord.status.in_(
                            [JobStatus.QUEUED.value, JobStatus.PROCESSING.value]
                        ),
                    )
                ).scalars().all()
                targets.extend(siblings)

            source_file_ids = {rec.source_file_id for rec in targets if rec.source_file_id}

            for rec in targets:
                artifacts = session.execute(
                    select(JobArtifactRecord).where(JobArtifactRecord.job_id == rec.id)
                ).scalars().all()
                for artifact in artifacts:
                    self.object_store.delete_object(artifact.object_key)
                    session.delete(artifact)
                session.delete(rec)

            # Records must be gone before we check whether any surviving job still
            # references a source vault file.
            session.flush()

            # Remove the auto-created "job_source" vault files (the encrypted source
            # PDF) that no remaining job references. User-managed vault files
            # (upload / legacy_import) are never deleted here.
            for source_file_id in source_file_ids:
                vault_file = session.get(VaultFileRecord, source_file_id)
                if not vault_file or vault_file.source_kind != VaultFileSourceKind.JOB_SOURCE.value:
                    continue
                still_referenced = session.execute(
                    select(ExtractionJobRecord.id)
                    .where(ExtractionJobRecord.source_file_id == source_file_id)
                    .limit(1)
                ).scalar_one_or_none()
                if still_referenced:
                    continue
                self.object_store.delete_object(vault_file.object_key)
                session.delete(vault_file)

            session.commit()

    def import_legacy_job(
        self,
        job: ExtractionJobDetail,
        *,
        source_pdf: bytes | None,
        summary_doc: bytes | None,
        extraction_result: bytes | None,
    ) -> bool:
        with SessionLocal() as session:
            if session.get(ExtractionJobRecord, job.id):
                return False

            source_file_id: str | None = None
            created_at = parse_iso_datetime(job.created_at)
            updated_at = parse_iso_datetime(job.updated_at)

            if source_pdf:
                source_file = self._store_new_vault_file(
                    session,
                    name=job.filename,
                    payload=source_pdf,
                    content_type="application/pdf",
                    folder_id=None,
                    source_kind=VaultFileSourceKind.LEGACY_IMPORT,
                    checksum_sha256=job.source_digest or hashlib.sha256(source_pdf).hexdigest(),
                    created_at=created_at,
                    updated_at=updated_at,
                )
                source_file_id = source_file.id

            record = ExtractionJobRecord(
                id=job.id,
                source_file_id=source_file_id,
                filename=job.filename,
                source_digest=job.source_digest,
                status=job.status.value,
                created_at=created_at,
                updated_at=updated_at,
                page_count=job.page_count,
                patient_count=job.patient_count,
                document_count=job.document_count,
                capture_certification=job.capture_certification,
                pipeline_json=[step.model_dump(mode="json") for step in job.pipeline],
                export_filename=job.export_artifact.filename,
                export_content_type=job.export_artifact.content_type,
                export_ready=job.export_artifact.ready,
                export_size_bytes=job.export_artifact.size_bytes,
                error=job.error,
                payload_encrypted=self._build_job_payload(job.model_copy(update={"source_file_id": source_file_id})),
            )
            session.add(record)
            session.flush()

            if source_pdf:
                object_key = self._job_artifact_object_key(job.id, "source_pdf")
                size_bytes = self.object_store.put_bytes(object_key, source_pdf, content_type="application/pdf")
                self._upsert_job_artifact(
                    session,
                    job_id=job.id,
                    name="source_pdf",
                    object_key=object_key,
                    content_type="application/pdf",
                    size_bytes=size_bytes,
                    created_at=created_at,
                    updated_at=updated_at,
                )

            if summary_doc:
                object_key = self._job_artifact_object_key(job.id, "summary_doc")
                size_bytes = self.object_store.put_bytes(object_key, summary_doc, content_type=job.export_artifact.content_type)
                self._upsert_job_artifact(
                    session,
                    job_id=job.id,
                    name="summary_doc",
                    object_key=object_key,
                    content_type=job.export_artifact.content_type,
                    size_bytes=size_bytes,
                    created_at=created_at,
                    updated_at=updated_at,
                )

            if extraction_result:
                object_key = self._job_artifact_object_key(job.id, "extraction_result")
                size_bytes = self.object_store.put_bytes(object_key, extraction_result, content_type="application/json")
                self._upsert_job_artifact(
                    session,
                    job_id=job.id,
                    name="extraction_result",
                    object_key=object_key,
                    content_type="application/json",
                    size_bytes=size_bytes,
                    created_at=created_at,
                    updated_at=updated_at,
                )

            session.commit()
            return True
