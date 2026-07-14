from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ErrorDetail(BaseModel):
    """Standard error response body."""

    detail: str = Field(description="Human-readable error description.")


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


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
    cost_usd: float = 0
    input_tokens: int = 0
    output_tokens: int = 0


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


class DocumentManifest(BaseModel):
    """Stable document segment resolved from page OCR text before patient summarization."""

    page_start: int
    page_end: int
    title: str | None = None
    document_type: str | None = None
    document_date: str | None = None
    author: str | None = None
    summary_kind: SummaryKind = SummaryKind.UNKNOWN
    classification: ClinicalRelevance = ClinicalRelevance.UNKNOWN
    patient_name: str | None = None
    patient_dob: str | None = None
    patient_identifier: str | None = None
    include_in_output: bool = True


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


class PatientHeader(BaseModel):
    to_name: str | None = None
    claim_number: str | None = None
    from_name: str | None = None
    age_dob: str | None = None
    review_date: str | None = None
    occupation: str | None = None
    claimant: str | None = None
    diagnosis_dod: str | None = None


class SubSummaryParagraph(BaseModel):
    """Legacy shape: one dated/authored entry nested within a larger
    multi-encounter document. The pipeline no longer produces nested entries
    (each dated entry is its own SummaryParagraph now - "one document, one
    card"); this model exists only so jobs persisted before that change still
    parse, and PatientSummary flattens them on load."""

    text: str
    page_start: int
    page_end: int
    date: str | None = None
    author: str | None = None


class SummaryParagraph(BaseModel):
    text: str
    page_start: int
    page_end: int
    document_id: str | None = None
    document_type: str | None = None
    document_number: int = 0
    is_lab: bool = False
    # Coverage placeholder for pages no real document claimed (admin/blank/
    # unparseable) - rendered muted, without a Document number.
    is_placeholder: bool = False
    # Legacy field: accepted on input (old persisted jobs) but flattened away
    # by PatientSummary below; never populated by the current pipeline.
    sub_summaries: list[SubSummaryParagraph] = Field(default_factory=list)


class PatientSummary(BaseModel):
    id: str
    name: str | None = None
    header: PatientHeader = Field(default_factory=PatientHeader)
    summary: str = ""
    summary_paragraphs: list[SummaryParagraph] = Field(default_factory=list)
    page_start: int = 0
    page_end: int = 0
    opinion: str = ""

    @model_validator(mode="after")
    def _flatten_legacy_sub_summaries(self) -> "PatientSummary":
        """Jobs saved before the "one document, one card" change carry
        multi-encounter records as ONE paragraph (a deterministic header) with
        the real entries nested in `sub_summaries`. Flatten those on load so
        every dated entry displays and exports as its own separately numbered
        document, exactly like output produced by the current pipeline."""
        if not any(len(p.sub_summaries) > 1 for p in self.summary_paragraphs):
            return self
        flat: list[SummaryParagraph] = []
        number = 0
        for para in self.summary_paragraphs:
            if len(para.sub_summaries) > 1:
                # The parent paragraph's text is only a header line for the
                # nested entries - drop it and promote each entry.
                for sub in para.sub_summaries:
                    number += 1
                    flat.append(
                        SummaryParagraph(
                            text=sub.text,
                            page_start=sub.page_start,
                            page_end=sub.page_end,
                            document_id=para.document_id,
                            document_type=para.document_type,
                            document_number=number,
                            is_lab=False,
                        )
                    )
                continue
            copy = para.model_copy(update={"sub_summaries": []})
            if not copy.is_placeholder and copy.document_number:
                number += 1
                copy.document_number = number
            flat.append(copy)
        self.summary_paragraphs = flat
        return self


class ExportArtifact(BaseModel):
    filename: str
    content_type: str = "application/msword"
    ready: bool = False
    size_bytes: int | None = None


class CostStageBreakdown(BaseModel):
    stage: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: float = 0.0


class CostModelBreakdown(BaseModel):
    provider: str
    model: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: float = 0.0


class CostSummary(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: float = 0.0
    by_stage: list[CostStageBreakdown] = Field(default_factory=list)
    by_model: list[CostModelBreakdown] = Field(default_factory=list)


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
    source_file_id: str | None = None
    filename: str
    source_digest: str | None = None
    status: JobStatus
    created_at: str
    updated_at: str
    processing_started_at: str | None = None
    page_count: int = 0
    patient_count: int = 0
    document_count: int = 0
    capture_certification: str | None = None
    pipeline: list[PipelineStep] = Field(default_factory=list)
    export_artifact: ExportArtifact
    cost_summary: CostSummary = Field(default_factory=CostSummary)
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
