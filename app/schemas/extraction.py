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


class PageRole(str, Enum):
    DOCUMENT_START = "document_start"
    DOCUMENT_BODY = "document_body"
    SEPARATOR = "separator"
    INDEX_ONLY = "index_only"
    COVER = "cover"
    OTHER = "other"


class SummaryKind(str, Enum):
    CLINICAL = "clinical"
    IMAGING = "imaging"
    PATHOLOGY = "pathology"
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
    patient_dob: str | None = None
    patient_identifier: str | None = None
    patient_key: str | None = None
    mentioned_patient_names: list[str] = Field(default_factory=list)
    document_title: str | None = None
    document_type: str | None = None
    document_bucket: SummaryKind = SummaryKind.UNKNOWN
    document_date: str | None = None
    author: str | None = None
    author_role: str | None = None
    page_role: PageRole = PageRole.OTHER
    clinical_relevance: ClinicalRelevance = ClinicalRelevance.UNKNOWN
    starts_new_document: bool = False
    starts_new_patient: bool = False
    boundary_hint: str | None = None
    document_boundary_reason: str | None = None
    patient_boundary_reason: str | None = None
    accession_number: str | None = None
    exam_title: str | None = None
    radiologist_name: str | None = None
    narrative_report_available: bool | None = None
    visible_text: str = ""
    citations: list[FieldCitation] = Field(default_factory=list)


class DocumentSummary(BaseModel):
    id: str
    title: str
    patient_name: str | None = None
    patient_dob: str | None = None
    patient_identifier: str | None = None
    patient_key: str | None = None
    patient_group_id: str | None = None
    mentioned_patient_names: list[str] = Field(default_factory=list)
    document_type: str | None = None
    summary_kind: SummaryKind = SummaryKind.UNKNOWN
    document_date: str | None = None
    author: str | None = None
    author_role: str | None = None
    page_numbers: list[int] = Field(default_factory=list)
    page_range: str
    classification: ClinicalRelevance = ClinicalRelevance.UNKNOWN
    include_in_output: bool = True
    capture_status: str = "captured"
    accession_number: str | None = None
    exam_title: str | None = None
    radiologist_name: str | None = None
    narrative_report_available: bool | None = None
    summary: str = ""


class OfficeVisitItem(BaseModel):
    title: str
    date: str | None = None
    author: str | None = None
    page_start: int
    page_end: int


class PatientHeader(BaseModel):
    to_name: str | None = None
    claim_number: str | None = None
    from_name: str | None = None
    age_dob: str | None = None
    review_date: str | None = None
    occupation: str | None = None
    claimant: str | None = None
    diagnosis_dod: str | None = None


class PatientSummary(BaseModel):
    id: str
    name: str | None = None
    header: PatientHeader = Field(default_factory=PatientHeader)
    summary: str = ""
    page_start: int = 0
    page_end: int = 0
    opinion: str = ""
    office_visits: list[OfficeVisitItem] = Field(default_factory=list)


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
                    {"key": "extract", "label": "Parse", "status": "running", "detail": "Parsing rendered page images."},
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
    source_digest: str | None = None
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
    patients: list[PatientSummary] = Field(default_factory=list)


class CreateJobResponse(BaseModel):
    job: ExtractionJobSummary


class JobListResponse(BaseModel):
    jobs: list[ExtractionJobSummary]
