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
from typing import Awaitable, Callable

from app.core.config import settings
from app.schemas.extraction import SummaryParagraph
from app.services.extraction.cost import CostTracker
from app.services.extraction.formatting import clean_title, format_author, strip_foreign_scripts
from app.services.extraction.header import is_placeholder_date, normalize_date
from app.services.extraction.llm import RunLogger, openai_json, opinion_model
from app.services.extraction.models import DocumentSegment, PatientBundle
from app.services.extraction.prompts import SUMMARY_SCHEMA, SUMMARY_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Documents per summarizer call. Small enough to report progress, large enough
# to keep the call count (and cost) reasonable on big files.
SUMMARY_CHUNK_SIZE = 5

ProgressCb = Callable[[str], Awaitable[None]]


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


def _is_lab(doc: DocumentSegment) -> bool:
    """Lab / pathology reports are surfaced as documents but not summarized.

    Per client direction, medical consultants do not want lab/pathology prose at
    all. We keep the document (with its number, color, badge, and page anchor)
    but render a short placeholder instead of spending a summary call on it.
    """
    return doc.bucket == "pathology"


def _lab_placeholder(doc: DocumentSegment) -> str:
    date = _document_date(doc)
    title = clean_title(doc.title)
    label = title or "lab report"
    if date:
        return f"{date}, {label}."
    return f"{label[:1].upper()}{label[1:]}."


def _document_context(doc: DocumentSegment) -> dict[str, object]:
    """Deterministic context handed to the summarizer for one document."""
    evidence: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in doc.all_evidence:
        text = item.text.strip()
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
        "author_is_doctor": bool(doc.author.name and doc.author.is_doctor),
        "recipient": format_author(doc.recipient) or "",
        "claimant_authored": doc.claimant_authored,
        # Layout-aware markdown of the document's pages (tables, forms, figure
        # descriptions) for fuller context. Capped to keep token cost bounded.
        "markdown": doc.markdown[:8000],
        "evidence": evidence,
    }


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
        if not doc_id:
            continue
        # Record the response even when empty: an empty summary is the model's
        # intentional signal to OMIT the document (e.g. consent/admin forms).
        # That must be distinguishable from a document the model never answered
        # for (chunk failure), which should fall back to the raw evidence.
        out[doc_id] = str(entry.get("summary") or "").strip()
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
        text = item.text.strip().rstrip(". ")
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
    # Lab/pathology documents are placeholders only (client direction), so they
    # never go to the (paid) summarizer.
    to_summarize = [doc for doc in documents if not _is_lab(doc)]
    summaries: dict[str, str] = {}

    if settings.OPENAI_API_KEY and to_summarize:
        chunks = [
            to_summarize[i : i + SUMMARY_CHUNK_SIZE]
            for i in range(0, len(to_summarize), SUMMARY_CHUNK_SIZE)
        ]
        total = len(to_summarize)
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
    document_number = 0
    for doc in documents:
        is_lab = _is_lab(doc)
        if is_lab:
            # Lab/pathology: short placeholder only, always kept.
            text: str | None = _lab_placeholder(doc)
        else:
            text = summaries.get(doc.id)
            if text is None:
                # The summarizer never answered for this document (chunk failed
                # or was skipped). Fall back to the raw evidence so it is not lost.
                text = _fallback_paragraph(doc)
            # An empty string here is the model's intentional omission (admin/
            # consent form) - drop it. A None/empty fallback likewise drops it.
            if not text:
                continue
        text = strip_foreign_scripts(text or "")
        if not text:
            continue
        document_number += 1
        paragraphs.append(
            SummaryParagraph(
                text=text,
                page_start=doc.page_start,
                page_end=doc.page_end,
                document_id=doc.id,
                document_type=_document_type_label(doc),
                document_number=document_number,
                is_lab=is_lab,
            )
        )

    summary_text = "\n\n".join(p.text for p in paragraphs)
    return paragraphs, summary_text or "No patient summary generated."
