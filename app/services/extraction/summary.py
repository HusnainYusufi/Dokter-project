"""Summary builder.

Primary path: an LLM composes one flowing prose paragraph per document from
deterministically-prepared context (date, type, author, verbatim evidence).
This keeps the golden-rule extractive discipline while reading like the
reference reviews instead of a field-labelled dump.

Fallback path: if the LLM is unavailable or fails, a deterministic
template-fill is used so the pipeline never breaks.

Output: (list[SummaryParagraph], joined_summary_text)
- Each SummaryParagraph carries page anchors so the UI can scroll the PDF
  viewer to the source page when the paragraph is hovered/clicked. Anchors are
  always assigned deterministically (never trusted to the LLM).
"""
from __future__ import annotations

import json
import logging
import re

from app.core.config import settings
from app.schemas.extraction import SummaryParagraph
from app.services.extraction.cost import CostTracker
from app.services.extraction.formatting import clean_title, format_author
from app.services.extraction.header import is_placeholder_date, normalize_date
from app.services.extraction.llm import RunLogger, openai_json, opinion_model
from app.services.extraction.models import (
    DocumentSegment,
    EvidenceItem,
    PatientBundle,
)
from app.services.extraction.prompts import SUMMARY_SCHEMA, SUMMARY_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


CLINICAL_BUCKETS = {"clinical", "functional"}
IMAGING_BUCKETS = {"imaging"}
PATHOLOGY_BUCKETS = {"pathology"}

CLINICAL_WORD_LIMIT = 200
IMAGING_WORD_LIMIT = 50
PATHOLOGY_WORD_LIMIT = 50

# Safety net for LLM prose so a runaway paragraph can never blow up the export.
LLM_CLINICAL_WORD_LIMIT = 240
LLM_IMAGING_WORD_LIMIT = 80

# Per-document evidence cap sent to the LLM (keeps token use bounded on big files).
MAX_EVIDENCE_PER_DOC = 90


_SLASH_DATE_RE = re.compile(
    r"\b([A-Za-z]{3,9})\s+(\d{1,2})\s*/\s*(\d{2,4})\b"
)


def _normalize_inline_dates(text: str) -> str:
    """Rewrite inline raw-date tokens like 'Nov 23/22' or 'Feb 3/23' into golden form."""

    def repl(match: re.Match[str]) -> str:
        candidate = f"{match.group(1)} {match.group(2)}/{match.group(3)}"
        normalized = normalize_date(candidate)
        return normalized or match.group(0)

    return _SLASH_DATE_RE.sub(repl, text)


def _document_date(doc: DocumentSegment) -> str:
    date = normalize_date(doc.date)
    if not date and doc.date and not is_placeholder_date(doc.date):
        date = doc.date
    return date or ""


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


# --------------------------------------------------------------------------- #
# Deterministic fallback                                                        #
# --------------------------------------------------------------------------- #


def _evidence_by_kind(evidence: list[EvidenceItem], *kinds: str) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for item in evidence:
        if item.kind in kinds:
            text = _normalize_inline_dates(item.text.strip()).rstrip(".")
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


_BOUNDARY_CHARS = ";.!?"


def _enforce_word_limit(text: str, limit: int) -> str:
    """Trim to ``limit`` words but never end mid-clause."""
    words = text.split()
    if len(words) <= limit:
        return text
    truncated = " ".join(words[:limit])
    last_boundary = max(truncated.rfind(c) for c in _BOUNDARY_CHARS)
    if last_boundary > 0:
        truncated = truncated[: last_boundary + 1]
    truncated = truncated.rstrip(",;: ")
    if not truncated.endswith(("?", "!", ".")):
        truncated = truncated + "."
    return truncated


def _document_prefix(doc: DocumentSegment) -> str:
    parts: list[str] = []
    date = _document_date(doc)
    if date:
        parts.append(date)
    title = clean_title(doc.title)
    if title:
        parts.append(title)
    author = format_author(doc.author)
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


def _has_body_segments(text: str, prefix: str) -> bool:
    """True if ``text`` contains content beyond just the prefix sentence."""
    if not text:
        return False
    if prefix and text.strip() == prefix.strip():
        return False
    body = text[len(prefix):].strip() if prefix and text.startswith(prefix) else text.strip()
    return bool(body)


def _deterministic_paragraph(doc: DocumentSegment) -> str | None:
    bucket = doc.bucket or "unknown"
    prefix = _document_prefix(doc)
    if bucket in IMAGING_BUCKETS:
        text = _build_imaging_paragraph(doc)
        if not _has_body_segments(text, prefix):
            return None
        return _enforce_word_limit(text, IMAGING_WORD_LIMIT)
    if bucket in PATHOLOGY_BUCKETS:
        text = _build_pathology_paragraph(doc)
        if not _has_body_segments(text, prefix):
            return None
        return _enforce_word_limit(text, PATHOLOGY_WORD_LIMIT)
    text = _build_clinical_paragraph(doc)
    if not _has_body_segments(text, prefix):
        return None
    return _enforce_word_limit(text, CLINICAL_WORD_LIMIT)


# --------------------------------------------------------------------------- #
# LLM context preparation                                                       #
# --------------------------------------------------------------------------- #


def _included_documents(bundle: PatientBundle) -> list[DocumentSegment]:
    return [doc for doc in bundle.documents if doc.include_in_output]


def _document_context(doc: DocumentSegment) -> dict[str, object]:
    """Deterministic, scrubbed context handed to the summarizer for one document."""
    evidence: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in doc.all_evidence:
        text = _normalize_inline_dates(item.text.strip())
        if not text:
            continue
        key = (item.kind, text.lower())
        if key in seen:
            continue
        seen.add(key)
        entry = {"kind": item.kind, "text": text}
        if item.value:
            entry["value"] = item.value
        evidence.append(entry)
        if len(evidence) >= MAX_EVIDENCE_PER_DOC:
            break
    return {
        "document_id": doc.id,
        "date": _document_date(doc),
        "type": _document_type_label(doc),
        "title": clean_title(doc.title) or "",
        "author": format_author(doc.author) or "",
        "evidence": evidence,
    }


_LABEL_PREFIX_RE = re.compile(
    r"^(?:Reported|History|Examination|Investigations?|Assessment|Findings?|"
    r"Impression|Medications?|Plan|Restrictions?(?:/Limitations?)?|Limitations?)\s*:\s*",
    re.IGNORECASE,
)


def _scrub_llm_text(text: str) -> str:
    if not text:
        return ""
    cleaned = text.replace("**", "").replace("__", "")
    cleaned = re.sub(r"^[*\-#>]+\s*", "", cleaned, flags=re.MULTILINE)
    # Strip any field labels the model may have echoed despite instructions.
    cleaned = _LABEL_PREFIX_RE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{2,}", " ", cleaned)
    return cleaned.strip()


def _llm_word_limit(doc: DocumentSegment) -> int:
    if doc.bucket in IMAGING_BUCKETS or doc.bucket in PATHOLOGY_BUCKETS:
        return LLM_IMAGING_WORD_LIMIT
    return LLM_CLINICAL_WORD_LIMIT


def _paragraph_for(
    doc: DocumentSegment,
    llm_text: str | None,
) -> str | None:
    """Pick the LLM paragraph when usable, else the deterministic fallback."""
    if llm_text:
        cleaned = _scrub_llm_text(llm_text)
        if cleaned:
            return _enforce_word_limit(cleaned, _llm_word_limit(doc))
    return _deterministic_paragraph(doc)


async def _llm_summaries(
    bundle: PatientBundle,
    documents: list[DocumentSegment],
    *,
    run_logger: RunLogger | None,
    cost_tracker: CostTracker | None,
) -> dict[str, str]:
    """Return {document_id: summary_text} from the summarizer LLM, or {} on failure."""
    if not settings.OPENAI_API_KEY:
        return {}

    payload = {
        "patient": {"name": bundle.name or ""},
        "documents": [_document_context(doc) for doc in documents],
    }
    try:
        response = await openai_json(
            model=opinion_model(),
            system_prompt=SUMMARY_SYSTEM_PROMPT,
            user_prompt=json.dumps(payload, ensure_ascii=False),
            schema=SUMMARY_SCHEMA,
            task_label=f"Summary {bundle.id}",
            run_logger=run_logger,
            stage="summarize",
            cost_tracker=cost_tracker,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Summary generation failed for %s: %s - using fallback.", bundle.id, exc)
        return {}

    out: dict[str, str] = {}
    for entry in response.get("summaries") or []:
        if not isinstance(entry, dict):
            continue
        doc_id = str(entry.get("document_id") or "").strip()
        text = str(entry.get("summary") or "").strip()
        if doc_id and text:
            out[doc_id] = text
    return out


# --------------------------------------------------------------------------- #
# Public entry point                                                            #
# --------------------------------------------------------------------------- #


async def build_summary(
    bundle: PatientBundle,
    *,
    run_logger: RunLogger | None = None,
    cost_tracker: CostTracker | None = None,
) -> tuple[list[SummaryParagraph], str]:
    documents = _included_documents(bundle)
    llm_map = await _llm_summaries(
        bundle,
        documents,
        run_logger=run_logger,
        cost_tracker=cost_tracker,
    )

    paragraphs: list[SummaryParagraph] = []
    for doc in documents:
        text = _paragraph_for(doc, llm_map.get(doc.id))
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
