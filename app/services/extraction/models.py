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
    # User-defined document type from the active rule configuration (Rule
    # Studio), tagged by the parser when the page matches a rule's
    # match_prompt. Empty/None when no custom type applies.
    custom_type: str | None = None


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


class PageMarker(BaseModel):
    """A document's own printed pagination, e.g. "Page 3 of 5".

    The document stating where its own boundaries are. Every other boundary
    signal is inference; this one is printed on the page.
    """

    index: int = 0
    total: int = 0

    @property
    def is_usable(self) -> bool:
        """A marker only means something when it is internally coherent."""
        return 0 < self.index <= self.total and self.total > 1

    @property
    def is_first(self) -> bool:
        return self.is_usable and self.index == 1

    @property
    def is_continuation(self) -> bool:
        return self.is_usable and self.index > 1


class ParsedPage(BaseModel):
    page_number: int
    page_marker: PageMarker = Field(default_factory=PageMarker)
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
    custom_type: str | None = None
    title: str | None = None
    date: str | None = None
    author: AuthorFingerprint = Field(default_factory=AuthorFingerprint)
    recipient: AuthorFingerprint = Field(default_factory=AuthorFingerprint)
    patient_key: str | None = None
    patient_name: str | None = None
    patient_dob: str | None = None
    claimant_authored: bool = False
    include_in_output: bool = True
    # A synthetic coverage segment for physical pages no real document claimed
    # (admin/blank/unparseable). Rendered as a deterministic placeholder line -
    # never sent to the summarizer - so every source page is visibly accounted
    # for and a reviewer can tell "nothing clinical here" from "silently lost".
    is_placeholder: bool = False

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


class DocumentSubsection(BaseModel):
    """A contiguous run of pages within one DocumentSegment sharing one
    date/author - one distinct dated encounter or letter inside a larger
    multi-page chart binder that group_documents() kept as a single document."""

    id: str
    pages: list[ParsedPage]
    date: str | None = None
    author: AuthorFingerprint = Field(default_factory=AuthorFingerprint)
    bucket: DocumentBucket = "unknown"

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
