from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.extraction import JobStatus


class VaultPreviewKind(str, Enum):
    PDF = "pdf"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOC = "doc"
    DOWNLOAD = "download"


class VaultFileSourceKind(str, Enum):
    UPLOAD = "upload"
    JOB_SOURCE = "job_source"
    LEGACY_IMPORT = "legacy_import"


class VaultFolderSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    parent_id: str | None = None
    created_at: str
    updated_at: str


class VaultFileSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    folder_id: str | None = None
    content_type: str
    size_bytes: int
    extension: str | None = None
    preview_kind: VaultPreviewKind
    can_extract: bool = False
    source_kind: VaultFileSourceKind = VaultFileSourceKind.UPLOAD
    checksum_sha256: str
    linked_job_id: str | None = None
    linked_job_status: JobStatus | None = None
    created_at: str
    updated_at: str


class VaultBrowseResponse(BaseModel):
    current_folder: VaultFolderSummary | None = None
    breadcrumbs: list[VaultFolderSummary] = Field(default_factory=list)
    folders: list[VaultFolderSummary] = Field(default_factory=list)
    files: list[VaultFileSummary] = Field(default_factory=list)


class VaultRecentResponse(BaseModel):
    files: list[VaultFileSummary] = Field(default_factory=list)


class VaultUploadResponse(BaseModel):
    files: list[VaultFileSummary] = Field(default_factory=list)


class VaultFolderResponse(BaseModel):
    folder: VaultFolderSummary


class VaultFileResponse(BaseModel):
    file: VaultFileSummary


class CreateVaultFolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = None


class RenameVaultFolderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class RenameVaultFileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
