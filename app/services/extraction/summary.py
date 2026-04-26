"""Template-fill summary builder.

One paragraph per included document. Pure deterministic concatenation of
verbatim evidence-item text. No LLM rewriting.

Output: (list[SummaryParagraph], joined_summary_text)
- SummaryParagraph carries page anchors so the UI can scroll the PDF viewer
  to the source page when the paragraph is hovered/clicked.
"""
from __future__ import annotations

import re

from app.schemas.extraction import SummaryParagraph
from app.services.extraction.header import normalize_date
from app.services.extraction.models import (
    AuthorFingerprint,
    DocumentSegment,
    EvidenceItem,
    PatientBundle,
)


CLINICAL_BUCKETS = {"clinical", "functional"}
IMAGING_BUCKETS = {"imaging"}
PATHOLOGY_BUCKETS = {"pathology"}

CLINICAL_WORD_LIMIT = 200
IMAGING_WORD_LIMIT = 50
PATHOLOGY_WORD_LIMIT = 50


def _format_author(author: AuthorFingerprint) -> str:
    if not author.name:
        return ""
    name = author.name.strip().rstrip(",")
    if author.is_doctor and not re.match(r"^dr\.?\b", name, flags=re.IGNORECASE):
        last = name.split()[-1]
        return f"Dr. {last}"
    return name


def _evidence_by_kind(evidence: list[EvidenceItem], *kinds: str) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for item in evidence:
        if item.kind in kinds:
            text = item.text.strip().rstrip(".")
            key = text.lower()
            if key in seen or not text:
                continue
            seen.add(key)
            selected.append(text)
    return selected


def _join_phrases(phrases: list[str]) -> str:
    if not phrases:
        return ""
    cleaned = [p.rstrip(".") for p in phrases if p.strip()]
    return "; ".join(cleaned)


def _enforce_word_limit(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return text
    truncated = " ".join(words[:limit])
    return truncated.rstrip(",;:") + "."


def _document_prefix(doc: DocumentSegment) -> str:
    parts: list[str] = []
    date = normalize_date(doc.date) or doc.date
    if date:
        parts.append(date)
    title = doc.title
    if title:
        parts.append(title)
    author = _format_author(doc.author)
    if author:
        parts.append(author)
    if not parts:
        return ""
    return " ".join(parts).rstrip(".") + "."


def _build_imaging_paragraph(doc: DocumentSegment) -> str:
    evidence = doc.all_evidence
    findings = _evidence_by_kind(evidence, "imaging_finding", "finding")
    impressions = _evidence_by_kind(evidence, "imaging_impression", "impression")
    parts: list[str] = []
    prefix = _document_prefix(doc)
    if prefix:
        parts.append(prefix)
    if findings:
        parts.append("Findings: " + _join_phrases(findings) + ".")
    if impressions:
        parts.append("Impression: " + _join_phrases(impressions) + ".")
    return " ".join(parts).strip()


def _build_pathology_paragraph(doc: DocumentSegment) -> str:
    evidence = doc.all_evidence
    findings = _evidence_by_kind(evidence, "finding", "diagnosis")
    impressions = _evidence_by_kind(evidence, "impression")
    parts: list[str] = []
    prefix = _document_prefix(doc)
    if prefix:
        parts.append(prefix)
    if findings:
        parts.append("Findings: " + _join_phrases(findings) + ".")
    if impressions:
        parts.append("Impression: " + _join_phrases(impressions) + ".")
    return " ".join(parts).strip()


def _build_clinical_paragraph(doc: DocumentSegment) -> str:
    evidence = doc.all_evidence
    history = _evidence_by_kind(evidence, "history", "onset", "mechanism", "hospitalization")
    symptoms = _evidence_by_kind(evidence, "symptom")
    exam = _evidence_by_kind(evidence, "exam", "measurement", "score")
    diagnosis = _evidence_by_kind(evidence, "diagnosis", "impression")
    investigations = _evidence_by_kind(evidence, "investigation")
    medications = _evidence_by_kind(evidence, "medication")
    plan = _evidence_by_kind(evidence, "recommendation", "return_to_work")
    restrictions = _evidence_by_kind(evidence, "restriction", "limitation")

    parts: list[str] = []
    prefix = _document_prefix(doc)
    if prefix:
        parts.append(prefix)
    if symptoms:
        parts.append("Reported: " + _join_phrases(symptoms) + ".")
    if history:
        parts.append("History: " + _join_phrases(history) + ".")
    if exam:
        parts.append("Examination: " + _join_phrases(exam) + ".")
    if investigations:
        parts.append("Investigations: " + _join_phrases(investigations) + ".")
    if diagnosis:
        parts.append("Assessment: " + _join_phrases(diagnosis) + ".")
    if medications:
        parts.append("Medications: " + _join_phrases(medications) + ".")
    if plan:
        parts.append("Plan: " + _join_phrases(plan) + ".")
    if restrictions:
        parts.append("Restrictions/Limitations: " + _join_phrases(restrictions) + ".")
    return " ".join(parts).strip()


def _document_paragraph(doc: DocumentSegment) -> str | None:
    bucket = doc.bucket or "unknown"
    if bucket in IMAGING_BUCKETS:
        text = _build_imaging_paragraph(doc)
        return _enforce_word_limit(text, IMAGING_WORD_LIMIT) if text else None
    if bucket in PATHOLOGY_BUCKETS:
        text = _build_pathology_paragraph(doc)
        return _enforce_word_limit(text, PATHOLOGY_WORD_LIMIT) if text else None
    text = _build_clinical_paragraph(doc)
    if not text:
        return None
    return _enforce_word_limit(text, CLINICAL_WORD_LIMIT)


def _document_type_label(doc: DocumentSegment) -> str:
    if doc.bucket == "imaging":
        return "imaging"
    if doc.bucket == "pathology":
        return "pathology"
    if doc.bucket == "functional":
        return "functional"
    if doc.bucket == "administrative":
        return "administrative"
    return "clinical"


def build_summary(bundle: PatientBundle) -> tuple[list[SummaryParagraph], str]:
    paragraphs: list[SummaryParagraph] = []

    for doc in bundle.documents:
        if not doc.include_in_output:
            continue
        text = _document_paragraph(doc)
        if not text:
            continue
        paragraphs.append(
            SummaryParagraph(
                text=text,
                page_start=doc.page_start,
                page_end=doc.page_end,
                document_id=doc.id,
                document_type=_document_type_label(doc),
            )
        )

    summary_text = "\n\n".join(p.text for p in paragraphs)
    return paragraphs, summary_text or "No patient summary generated."
