"""Office-visits builder.

Office visits are the file-order index of clinical documents (per golden rule
3.8 "Preserve original file order"). Each entry mirrors a document with the
date / author / page range surfaced for navigation.
"""
from __future__ import annotations

import re

from app.schemas.extraction import OfficeVisitItem
from app.services.extraction.header import normalize_date
from app.services.extraction.models import AuthorFingerprint, DocumentSegment, PatientBundle


def _format_author(author: AuthorFingerprint) -> str | None:
    if not author.name:
        return None
    name = author.name.strip().rstrip(",")
    if author.is_doctor and not re.match(r"^dr\.?\b", name, flags=re.IGNORECASE):
        last = name.split()[-1]
        return f"Dr. {last}"
    return name


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


def build_office_visits(bundle: PatientBundle) -> list[OfficeVisitItem]:
    visits: list[OfficeVisitItem] = []
    for doc in bundle.documents:
        if not doc.include_in_output:
            continue
        if doc.bucket == "administrative":
            continue
        title = _format_title(doc)
        date = normalize_date(doc.date) or doc.date
        author = _format_author(doc.author)
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
