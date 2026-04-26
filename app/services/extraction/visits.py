"""Office-visits builder.

Office visits are the file-order index of clinical documents (per golden rule
3.8 "Preserve original file order"). Each entry mirrors a document with the
date / author / page range surfaced for navigation.
"""
from __future__ import annotations

from app.schemas.extraction import OfficeVisitItem
from app.services.extraction.formatting import format_author
from app.services.extraction.header import normalize_date
from app.services.extraction.models import DocumentSegment, PatientBundle


def _format_title(doc: DocumentSegment) -> str:
    if doc.title:
        return doc.title
    return {
        "clinical": "Clinical Note",
        "imaging": "Imaging Report",
        "pathology": "Pathology Report",
        "functional": "Functional Document",
        "administrative": "Administrative Document",
        "unknown": "Document",
    }.get(doc.bucket, "Document")


def _has_clinical_evidence(doc: DocumentSegment) -> bool:
    return any(item.text.strip() for item in doc.all_evidence)


def build_office_visits(bundle: PatientBundle) -> list[OfficeVisitItem]:
    visits: list[OfficeVisitItem] = []
    for doc in bundle.documents:
        if not doc.include_in_output:
            continue
        if doc.bucket == "administrative":
            continue
        if not _has_clinical_evidence(doc):
            continue
        title = _format_title(doc)
        date = normalize_date(doc.date) or doc.date
        author = format_author(doc.author)
        visits.append(
            OfficeVisitItem(
                title=title,
                date=date,
                author=author,
                page_start=doc.page_start,
                page_end=doc.page_end,
            )
        )
    return visits
