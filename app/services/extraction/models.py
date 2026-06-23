"""Internal pydantic models for the extraction package.

These wrap the raw evidence-based parser output before it is folded back into
the public schemas in `app.schemas.extraction`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


EvidenceKind = Literal[
    "diagnosis",
    "symptom",
    "finding",
    "measurement",
    "medication",
    "history",
    "exam",
    "impression",
    "imaging_finding",
    "imaging_impression",
    "recommendation",
    "restriction",
    "limitation",
    "return_to_work",
    "hospitalization",
    "onset",
    "mechanism",
    "investigation",
    "score",
    "checklist",
]

PageKind = Literal[
    "clinical",
    "imaging",
    "pathology",
    "functional",
    "admin",
    "signature_only",
    "empty",
]

DocumentBucket = Literal["clinical", "imaging", "pathology", "functional", "administrative", "unknown"]


class EvidenceItem(BaseModel):
    kind: EvidenceKind
    text: str
    value: str | None = None


class PatientFingerprint(BaseModel):
    name: str | None = None
    dob: str | None = None
    identifier: str | None = None


class DocumentFingerprint(BaseModel):
    title: str | None = None
    bucket: DocumentBucket = "unknown"
    date: str | None = None


class AuthorFingerprint(BaseModel):
    name: str | None = None
    credentials: str | None = None
    is_doctor: bool = False
    is_signing: bool = False


class HeaderFields(BaseModel):
    to: str | None = None
    from_: str | None = Field(default=None, alias="from")
    claim_number: str | None = None
    occupation: str | None = None
    review_date: str | None = None
    diagnosis_dod: str | None = None

    model_config = {"populate_by_name": True}


class ParsedPage(BaseModel):
    page_number: int
    starts_new_document: bool = False
    include_in_output: bool = True
    page_kind: PageKind = "clinical"
    patient: PatientFingerprint = Field(default_factory=PatientFingerprint)
    document: DocumentFingerprint = Field(default_factory=DocumentFingerprint)
    author: AuthorFingerprint = Field(default_factory=AuthorFingerprint)
    recipient: AuthorFingerprint = Field(default_factory=AuthorFingerprint)
    header_fields: HeaderFields = Field(default_factory=HeaderFields)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    raw_text_excerpt: str = ""
    markdown: str = ""


class DocumentSegment(BaseModel):
    """A contiguous range of pages forming one document."""

    id: str
    pages: list[ParsedPage]
    bucket: DocumentBucket = "unknown"
    title: str | None = None
    date: str | None = None
    author: AuthorFingerprint = Field(default_factory=AuthorFingerprint)
    recipient: AuthorFingerprint = Field(default_factory=AuthorFingerprint)
    patient_key: str | None = None
    patient_name: str | None = None
    patient_dob: str | None = None
    claimant_authored: bool = False
    include_in_output: bool = True

    @property
    def page_start(self) -> int:
        return self.pages[0].page_number if self.pages else 0

    @property
    def page_end(self) -> int:
        return self.pages[-1].page_number if self.pages else 0

    @property
    def all_evidence(self) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        for page in self.pages:
            items.extend(page.evidence)
        return items

    @property
    def markdown(self) -> str:
        """The document's pages reconstructed as markdown, in page order."""
        blocks = [p.markdown.strip() for p in self.pages if p.markdown.strip()]
        return "\n\n".join(blocks)


class PatientBundle(BaseModel):
    id: str
    key: str
    name: str | None = None
    dob: str | None = None
    documents: list[DocumentSegment]

    @property
    def page_start(self) -> int:
        starts = [doc.page_start for doc in self.documents if doc.page_start]
        return min(starts) if starts else 0

    @property
    def page_end(self) -> int:
        ends = [doc.page_end for doc in self.documents if doc.page_end]
        return max(ends) if ends else 0
