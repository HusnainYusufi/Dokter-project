from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    """Standard error response body."""

    detail: str = Field(description="Human-readable error description.")


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class PipelineStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ClinicalRelevance(str, Enum):
    CLINICAL = "clinical"
    FUNCTIONAL = "functional"
    ADMINISTRATIVE = "administrative"
    UNKNOWN = "unknown"


class PipelineStep(BaseModel):
    key: str
    label: str
    status: PipelineStepStatus
    detail: str | None = None


class Citation(BaseModel):
    page: int
    matching_text: str


class FieldCitation(BaseModel):
    field: str
    citations: list[Citation] = Field(default_factory=list)


class PageExtraction(BaseModel):
    page_number: int
    patient_name: str | None = None
    document_title: str | None = None
    document_type: str | None = None
    document_date: str | None = None
    author: str | None = None
    clinical_relevance: ClinicalRelevance = ClinicalRelevance.UNKNOWN
    starts_new_document: bool = False
    starts_new_patient: bool = False
    boundary_hint: str | None = None
    visible_text: str = ""
    citations: list[FieldCitation] = Field(default_factory=list)


class DocumentSummary(BaseModel):
    id: str
    title: str
    patient_name: str | None = None
    document_type: str | None = None
    document_date: str | None = None
    author: str | None = None
    page_numbers: list[int] = Field(default_factory=list)
    page_range: str
    classification: ClinicalRelevance = ClinicalRelevance.UNKNOWN
    include_in_output: bool = True
    capture_status: str = "captured"
    summary: str = ""


class ExportArtifact(BaseModel):
    filename: str
    content_type: str = "application/msword"
    ready: bool = False
    size_bytes: int | None = None


class ExtractionJobSummary(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "job_123",
                "filename": "Wanda Russell - Medical Consultant Request.pdf",
                "status": "processing",
                "created_at": "2026-04-01T12:34:56Z",
                "updated_at": "2026-04-01T12:35:12Z",
                "page_count": 338,
                "patient_count": 1,
                "document_count": 12,
                "capture_certification": "Indexed 338 pages before summary generation.",
                "pipeline": [
                    {"key": "upload", "label": "Upload", "status": "completed", "detail": None},
                    {"key": "extract", "label": "Extract", "status": "running", "detail": "Capturing page-wise text."},
                ],
                "export_artifact": {
                    "filename": "wanda-russell-medical-consultant-request.doc",
                    "content_type": "application/msword",
                    "ready": False,
                    "size_bytes": None,
                },
                "error": None,
            }
        }
    )

    id: str
    filename: str
    status: JobStatus
    created_at: str
    updated_at: str
    page_count: int = 0
    patient_count: int = 0
    document_count: int = 0
    capture_certification: str | None = None
    pipeline: list[PipelineStep] = Field(default_factory=list)
    export_artifact: ExportArtifact
    error: str | None = None


class ExtractionJobDetail(ExtractionJobSummary):
    source_available: bool = False
    pages: list[PageExtraction] = Field(default_factory=list)
    documents: list[DocumentSummary] = Field(default_factory=list)


class CreateJobResponse(BaseModel):
    job: ExtractionJobSummary


class JobListResponse(BaseModel):
    jobs: list[ExtractionJobSummary]
