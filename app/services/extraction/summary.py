"""LLM summary builder.

One flowing prose paragraph per document, in the golden-rule house style,
composed by the summarizer LLM from deterministically-prepared context (full
date, document title, author, and the clinical facts). A document that is
itself a large multi-page record holding several distinct dated encounters
(e.g. a hospital chart binder) is first split into per-encounter sub-sections
(`split_subsections`); each sub-section is summarized independently so a
lengthy multi-encounter document can no longer have all but one of its entries
silently dropped by the per-unit length cap. A document with a single
date/author (the common case) yields exactly one sub-section and renders
identically to a simple document. Units (sub-sections) are summarized in small
chunks so the UI can report progress. Page anchors are always assigned
deterministically (never trusted to the model).

Design goals: simple, faithful, and matching the reference reviews. There is no
aggressive post-processing that could destroy clinical context - the golden
rules live in the prompt, not in regex scrubbers. If the LLM is unavailable, or
omits/fails a unit, a plain (still label-free) fallback paragraph built from the
raw evidence is produced so the pipeline never breaks and no unit is ever
silently lost.

Output: (list[SummaryParagraph], joined_summary_text)
"""
from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable

from app.core.config import settings
from app.schemas.extraction import SubSummaryParagraph, SummaryParagraph
from app.services.extraction.cost import CostTracker
from app.services.extraction.formatting import clean_title, format_author
from app.services.extraction.grouping import split_subsections
from app.services.extraction.header import is_placeholder_date, normalize_date
from app.services.extraction.llm import RunLogger, openai_json, opinion_model
from app.services.extraction.models import (
    DocumentSegment,
    DocumentSubsection,
    EvidenceItem,
    PatientBundle,
)
from app.services.extraction.prompts import SUMMARY_SCHEMA, SUMMARY_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Documents per summarizer call. Small enough to report progress, large enough
# to keep the call count (and cost) reasonable on big files.
SUMMARY_CHUNK_SIZE = 5

ProgressCb = Callable[[str], Awaitable[None]]


def _normalize_display_date(raw: str | None) -> str:
    date = normalize_date(raw)
    if not date and raw and not is_placeholder_date(raw):
        date = raw
    return date or ""


def _document_date(doc: DocumentSegment) -> str:
    return _normalize_display_date(doc.date)


def _subsection_date(sub: DocumentSubsection) -> str:
    return _normalize_display_date(sub.date)


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


def _looks_like_image_caption(text: str) -> bool:
    t = text.strip().lower()
    return t.startswith("![") or t.startswith("medical image on page")


def _is_image_only_doc(doc: DocumentSegment) -> bool:
    """A document that is just a captured image (X-ray/photo) with no report text.

    The summarizer treats such a document as having no clinical value and returns
    an empty string, which drops it. We give it a deterministic one-line summary
    instead so the image always appears as its own document.
    """
    ev = [item for item in doc.all_evidence if item.text.strip()]
    return bool(ev) and all(_looks_like_image_caption(item.text) for item in ev)


def _image_summary(doc: DocumentSegment) -> str:
    caption = "Medical image"
    for item in doc.all_evidence:
        text = item.text.strip()
        if not text:
            continue
        if text.startswith("![") and text.endswith("]"):
            text = text[2:-1].strip()
        caption = text or caption
        break
    date = _document_date(doc)
    body = f"{date}, {caption[:1].lower()}{caption[1:]}" if date else caption
    return body.rstrip(".") + "."


def _dedupe_evidence(items: list[EvidenceItem]) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
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
    return evidence


def _subsection_context(
    doc: DocumentSegment,
    sub: DocumentSubsection,
    is_multi_unit: bool,
) -> dict[str, object]:
    """Deterministic context handed to the summarizer for one unit (a whole
    simple document, or one dated sub-section of a larger multi-encounter
    document). `title`/`label`/`document_bucket`/`recipient`/`claimant_authored`
    are document-level concepts and always come from the parent `doc`; only the
    date/author and evidence are scoped to this specific sub-section (falling
    back to the parent document's when the sub-section itself carries none)."""
    author = sub.author if sub.author.name else doc.author
    return {
        "subsection_id": sub.id,
        "date": _subsection_date(sub) or _document_date(doc),
        "title": clean_title(doc.title) or "",
        "label": _kind_label(doc),
        "document_bucket": doc.bucket,
        "author": format_author(author) or "",
        "author_raw": author.name or "",
        "author_credentials": author.credentials or "",
        "author_is_doctor": bool(author.name and author.is_doctor),
        "recipient": format_author(doc.recipient) or "",
        "claimant_authored": doc.claimant_authored,
        "is_multi_unit_document": is_multi_unit,
        "evidence": _dedupe_evidence(sub.all_evidence),
    }


async def _summarize_chunk(
    bundle: PatientBundle,
    units: list[tuple[DocumentSegment, DocumentSubsection, bool]],
    *,
    run_logger: RunLogger | None,
    cost_tracker: CostTracker | None,
) -> dict[str, str]:
    payload = {
        "patient": {"name": bundle.name or ""},
        "documents": [
            _subsection_context(doc, sub, is_multi_unit) for doc, sub, is_multi_unit in units
        ],
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
        sub_id = str(entry.get("subsection_id") or "").strip()
        if not sub_id:
            continue
        # Record the response even when empty: for a sole/single unit an empty
        # summary is the model's intentional signal to OMIT it (e.g. a
        # consent/admin form). That must be distinguishable from a unit the
        # model never answered for (chunk failure), which should fall back to
        # the raw evidence - see build_summary() for how each case is handled.
        out[sub_id] = str(entry.get("summary") or "").strip()
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


def _subsection_prefix(doc: DocumentSegment, sub: DocumentSubsection) -> str:
    """Like `_prefix()`, but prefers the sub-section's own date/author over the
    parent document's, falling back to the parent's when the sub-section itself
    carries none."""
    parts: list[str] = []
    date = _subsection_date(sub) or _document_date(doc)
    if date:
        parts.append(date)
    title = clean_title(doc.title)
    parts.append(title or _kind_label(doc))
    author = format_author(sub.author) or format_author(doc.author)
    if author:
        parts.append(f"by {author}")
    return " ".join(parts).rstrip(".") + "."


def _fallback_subsection_paragraph(doc: DocumentSegment, sub: DocumentSubsection) -> str | None:
    """Plain, label-free paragraph built from one sub-section's own evidence,
    used when the LLM omits or fails that sub-section."""
    texts: list[str] = []
    seen: set[str] = set()
    for item in sub.all_evidence:
        text = item.text.strip().rstrip(". ")
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        texts.append(text)
    if not texts:
        return None
    return f"{_subsection_prefix(doc, sub)} " + ". ".join(texts) + "."


def _subsection_stub(doc: DocumentSegment, sub: DocumentSubsection) -> str:
    """Innermost fallback: a sub-section can carry pages but zero evidence text
    (should be rare - such pages are normally filtered upstream). Guarantees a
    visible line always exists for a distinct dated entry rather than it
    silently vanishing."""
    pages_text = (
        f"page {sub.page_start}"
        if sub.page_start == sub.page_end
        else f"pages {sub.page_start}-{sub.page_end}"
    )
    return f"{_subsection_prefix(doc, sub)} ({pages_text})."


async def build_summary(
    bundle: PatientBundle,
    *,
    run_logger: RunLogger | None = None,
    cost_tracker: CostTracker | None = None,
    progress: ProgressCb | None = None,
    bundle_index: int = 1,
) -> tuple[list[SummaryParagraph], str]:
    documents = _included_documents(bundle)
    # Lab/pathology documents are placeholders only (client direction) and never
    # go to the (paid) summarizer, and never need sub-splitting.
    to_summarize = [doc for doc in documents if not _is_lab(doc)]

    # Split each non-lab, non-image document into its dated/authored
    # sub-sections once, up front, so the chunking pass below and the assembly
    # pass further down reuse the exact same split. Image-only documents keep
    # their exact deterministic path (there is nothing to split - a captured
    # image is always a single entry) and are intentionally excluded here so
    # they cost zero extra CPU/LLM work, exactly like lab documents.
    subs_by_doc: dict[str, list[DocumentSubsection]] = {
        doc.id: split_subsections(doc) for doc in to_summarize if not _is_image_only_doc(doc)
    }

    # Flatten to (doc, sub, is_multi_unit) units so a bundle of simple
    # (single-subsection) documents is chunked and summarized exactly like
    # today, and only genuinely multi-encounter documents contribute more units.
    units: list[tuple[DocumentSegment, DocumentSubsection, bool]] = []
    for doc in to_summarize:
        subs = subs_by_doc.get(doc.id)
        if subs is None:
            continue
        is_multi_unit = len(subs) > 1
        units.extend((doc, sub, is_multi_unit) for sub in subs)

    summaries: dict[str, str] = {}

    if settings.OPENAI_API_KEY and units:
        chunks = [
            units[i : i + SUMMARY_CHUNK_SIZE] for i in range(0, len(units), SUMMARY_CHUNK_SIZE)
        ]
        total = len(units)
        done = 0
        for chunk_index, chunk in enumerate(chunks, start=1):
            start, end = done + 1, done + len(chunk)
            if progress:
                await progress(
                    f"Bundle {bundle_index}: summarizing chunk {chunk_index}/{len(chunks)} "
                    f"entries {start}-{end} of {total}"
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
            await progress(f"Bundle {bundle_index}: summarized {total} entries")

    paragraphs: list[SummaryParagraph] = []
    document_number = 0
    for doc in documents:
        is_lab = _is_lab(doc)
        sub_paragraphs: list[SubSummaryParagraph] = []
        if is_lab:
            # Lab/pathology: short placeholder only, always kept.
            text: str | None = _lab_placeholder(doc)
        elif _is_image_only_doc(doc):
            # Image-only (X-ray/photo): deterministic caption, always kept.
            text = _image_summary(doc)
        else:
            subs = subs_by_doc[doc.id]
            if len(subs) <= 1:
                # Single entry: behaves exactly like a plain document today -
                # one paragraph, no nested sub-summaries.
                sub = subs[0]
                raw = summaries.get(sub.id)
                if raw is None:
                    # Never answered (chunk failed or was skipped). Fall back to
                    # the raw evidence so it is not lost.
                    text = _fallback_subsection_paragraph(doc, sub)
                else:
                    # The model answered, possibly with an intentional empty
                    # string (this document has no real clinical value - e.g. a
                    # consent/release form that slipped past the page-kind
                    # gate). An empty answer here is a correct omission.
                    text = raw or None
                if not text:
                    continue
            else:
                # Multiple distinct dated/authored entries inside one document:
                # the document-level line is a deterministic header (no extra
                # LLM call), and every entry gets its own short sub-summary. A
                # sub-section can NEVER be "an admin form" on its own - that
                # classification is already resolved once for the whole
                # document above - so any falsy response here is always treated
                # as an omission and always falls back, guaranteeing this entry
                # is never silently lost (the actual fix for missing dates).
                sub_texts: list[str] = []
                for sub in subs:
                    sub_text = summaries.get(sub.id)
                    if not sub_text:
                        sub_text = _fallback_subsection_paragraph(doc, sub) or _subsection_stub(
                            doc, sub
                        )
                    sub_texts.append(sub_text)
                text = _prefix(doc)
                sub_paragraphs = [
                    SubSummaryParagraph(
                        text=sub_text,
                        page_start=sub.page_start,
                        page_end=sub.page_end,
                        date=_subsection_date(sub) or None,
                        author=format_author(sub.author),
                    )
                    for sub, sub_text in zip(subs, sub_texts)
                ]
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
                sub_summaries=sub_paragraphs,
            )
        )

    summary_text = "\n\n".join(p.text for p in paragraphs)
    return paragraphs, summary_text or "No patient summary generated."
