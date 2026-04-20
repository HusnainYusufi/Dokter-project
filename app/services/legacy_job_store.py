from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.core.exceptions import JobNotFoundError, StorageError
from app.schemas.extraction import ExtractionJobDetail
from app.services.encryption import VaultEncryptionService


class LegacyEncryptedJobStore:
    """Read-only adapter for importing legacy filesystem jobs."""

    def __init__(self) -> None:
        self.root = Path(settings.LEGACY_JOB_STORAGE_DIR)
        self.encryption = VaultEncryptionService()

    def _metadata_path(self, job_id: str) -> Path:
        return self.root / job_id / "job.json.enc"

    def _artifact_path(self, job_id: str, name: str) -> Path:
        return self.root / job_id / f"{name}.enc"

    def _job_metadata_paths(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(self.root.glob("*/job.json.enc"), key=lambda candidate: candidate.stat().st_mtime, reverse=True)

    def _read_job_from_path(self, path: Path) -> ExtractionJobDetail:
        try:
            payload = self.encryption.decrypt_bytes(path.read_bytes()).decode("utf-8")
            return ExtractionJobDetail.model_validate_json(payload)
        except Exception as exc:
            raise StorageError(f"Failed to read legacy job metadata from '{path}'.") from exc

    def list_job_details(self) -> list[ExtractionJobDetail]:
        jobs: list[ExtractionJobDetail] = []
        for path in self._job_metadata_paths():
            try:
                jobs.append(self._read_job_from_path(path))
            except Exception:
                continue
        return jobs

    def artifact_exists(self, job_id: str, name: str) -> bool:
        return self._artifact_path(job_id, name).exists()

    def read_artifact(self, job_id: str, name: str) -> bytes:
        path = self._artifact_path(job_id, name)
        if not path.exists():
            raise JobNotFoundError(job_id)
        try:
            return self.encryption.decrypt_bytes(path.read_bytes())
        except Exception as exc:
            raise StorageError(f"Failed to read legacy artifact '{name}'.") from exc
