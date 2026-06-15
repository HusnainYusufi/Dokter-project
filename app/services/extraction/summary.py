"""LLM summary builder.

One flowing prose paragraph per document, in the golden-rule house style,
composed by the summarizer LLM from deterministically-prepared context (full
date, document title, author, and the clinical facts). Documents are summarized
in small chunks so the UI can report progress. Page anchors are always assigned
deterministically (never trusted to the model).

Design goals: simple, faithful, and matching the reference reviews. There is no
aggressive post-processing that could destroy clinical context - the golden
rules live in the prompt, not in regex scrubbers. If the LLM is unavailable a
plain (still label-free) fallback paragraph is produced so the pipeline never
breaks.

Output: (list[SummaryParagraph], joined_summary_text)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Awaitable, Callable

from app.core.config import settings
from app.schemas.extraction import SummaryParagraph
from app.services.extraction.cost import CostTracker
from app.services.extraction.formatting import clean_title, format_author
from app.services.extraction.header import is_placeholder_date, normalize_date
from app.services.extraction.llm import RunLogger, openai_json, opinion_model
from app.services.extraction.models import DocumentSegment, PatientBundle
from app.services.extraction.prompts import SUMMARY_SCHEMA, SUMMARY_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Documents per summarizer call. Small enough to report progress, large enough
# to keep the call count (and cost) reasonable on big files.
SUMMARY_CHUNK_SIZE = 5

ProgressCb = Callable[[str], Awaitable[None]]

_SLASH_DATE_RE = re.compile(r"\b([A-Za-z]{3,9})\s+(\d{1,2})\s*/\s*(\d{2,4})\b")


def _normalize_inline_dates(text: str) -> str:
    """Rewrite inline raw-date tokens like 'Nov 23/22' into full golden form."""

    def repl(match: re.Match[str]) -> str:
        normalized = normalize_date(f"{match.group(1)} {match.group(2)}/{match.group(3)}")
        return normalized or match.group(0)

    return _SLASH_DATE_RE.sub(repl, text)


def _document_date(doc: DocumentSegment) -> str:
    date = normalize_date(doc.date)
    if not date and doc.date and not is_placeholder_date(doc.date):
        date = doc.date
    return date or ""


def _document_type_label(doc: DocumentSegment) -> str:
    """Internal bucket label used for SummaryParagraph.document_type (never printed)."""
    return {
        "imaging": "imaging",
        "pathology": "pathology",
        "functional": "functional",
        "administrative": "administrative",
    }.get(doc.bucket, "clinical")


def _kind_label(doc: DocumentSegment) -> str:
    """Human-readable label used only when a document has no printed title."""
    return {
        "imaging": "imaging report",
        "pathology": "pathology report",
        "functional": "functional report",
        "administrative": "document",
    }.get(doc.bucket, "clinical note")


def _included_documents(bundle: PatientBundle) -> list[DocumentSegment]:
    return [doc for doc in bundle.documents if doc.include_in_output]


def _document_context(doc: DocumentSegment) -> dict[str, object]:
    """Deterministic context handed to the summarizer for one document."""
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
    return {
        "document_id": doc.id,
        "date": _document_date(doc),
        "title": clean_title(doc.title) or "",
        "label": _kind_label(doc),
        "document_bucket": doc.bucket,
        "author": format_author(doc.author) or "",
        "author_raw": doc.author.name or "",
        "author_credentials": doc.author.credentials or "",
        "author_is_doctor": doc.author.is_doctor,
        "evidence": evidence,
    }


# Light, non-destructive cleanup: strip markdown markers and a dangling "Dr."
# that has no surname after it. Never removes clinical words.
_DANGLING_BY_DR_RE = re.compile(r"\bby\s+Dr\.?(?!\s+[A-Z][a-z])", re.IGNORECASE)
_STRAY_DR_RE = re.compile(r"\bDr\.?(?=\s*[.,;:])")


def _light_clean(text: str) -> str:
    if not text:
        return ""
    cleaned = text.replace("**", "").replace("__", "")
    cleaned = re.sub(r"^[*\-#>]+\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = _DANGLING_BY_DR_RE.sub("", cleaned)
    cleaned = _STRAY_DR_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{2,}", " ", cleaned)
    return cleaned.strip()


async def _summarize_chunk(
    bundle: PatientBundle,
    docs: list[DocumentSegment],
    *,
    run_logger: RunLogger | None,
    cost_tracker: CostTracker | None,
) -> dict[str, str]:
    payload = {
        "patient": {"name": bundle.name or ""},
        "documents": [_document_context(doc) for doc in docs],
    }
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
    out: dict[str, str] = {}
    for entry in response.get("summaries") or []:
        if not isinstance(entry, dict):
            continue
        doc_id = str(entry.get("document_id") or "").strip()
        text = _light_clean(str(entry.get("summary") or "").strip())
        if doc_id and text:
            out[doc_id] = text
    return out


def _prefix(doc: DocumentSegment) -> str:
    parts: list[str] = []
    date = _document_date(doc)
    if date:
        parts.append(date)
    title = clean_title(doc.title)
    parts.append(title or _kind_label(doc))
    author = format_author(doc.author)
    if author:
        parts.append(f"by {author}")
    return " ".join(parts).rstrip(".") + "."


def _fallback_paragraph(doc: DocumentSegment) -> str | None:
    """Plain, label-free paragraph used only when the LLM is unavailable."""
    texts: list[str] = []
    seen: set[str] = set()
    for item in doc.all_evidence:
        text = _normalize_inline_dates(item.text.strip()).rstrip(". ")
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        texts.append(text)
    if not texts:
        return None
    return f"{_prefix(doc)} " + ". ".join(texts) + "."


async def build_summary(
    bundle: PatientBundle,
    *,
    run_logger: RunLogger | None = None,
    cost_tracker: CostTracker | None = None,
    progress: ProgressCb | None = None,
    bundle_index: int = 1,
) -> tuple[list[SummaryParagraph], str]:
    documents = _included_documents(bundle)
    summaries: dict[str, str] = {}

    if settings.OPENAI_API_KEY and documents:
        chunks = [
            documents[i : i + SUMMARY_CHUNK_SIZE]
            for i in range(0, len(documents), SUMMARY_CHUNK_SIZE)
        ]
        total = len(documents)
        done = 0
        for chunk_index, chunk in enumerate(chunks, start=1):
            start, end = done + 1, done + len(chunk)
            if progress:
                await progress(
                    f"Bundle {bundle_index}: summarizing chunk {chunk_index}/{len(chunks)} "
                    f"documents {start}-{end} of {total}"
                )
            try:
                summaries.update(
                    await _summarize_chunk(
                        bundle, chunk, run_logger=run_logger, cost_tracker=cost_tracker
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Summary chunk %s failed for %s: %s - using fallback.",
                    chunk_index,
                    bundle.id,
                    exc,
                )
            done = end
        if progress:
            await progress(f"Bundle {bundle_index}: summarized {total} document(s)")

    paragraphs: list[SummaryParagraph] = []
    for doc in documents:
        text = summaries.get(doc.id) or _fallback_paragraph(doc)
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
