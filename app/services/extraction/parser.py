"""AI-driven page parser producing ParsedPage with evidence arrays."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from app.core.config import settings
from app.services.extraction.cost import CostTracker
from app.services.extraction.llm import RunLogger, gemini_json, openai_multimodal_json, page_model
from app.services.extraction.models import (
    AuthorFingerprint,
    DocumentBucket,
    DocumentFingerprint,
    EvidenceItem,
    HeaderFields,
    PageKind,
    ParsedPage,
    PatientFingerprint,
)
from app.services.extraction.pdf import ink_ratio, render_page_batches
from app.services.extraction.prompts import (
    PAGE_PARSE_SYSTEM_PROMPT,
    PARSED_PAGES_SCHEMA,
)

logger = logging.getLogger(__name__)


_VALID_PAGE_KINDS: set[str] = {
    "clinical",
    "imaging",
    "pathology",
    "functional",
    "admin",
    "signature_only",
    "empty",
}

_VALID_BUCKETS: set[str] = {
    "clinical",
    "imaging",
    "pathology",
    "functional",
    "administrative",
    "unknown",
}

_VALID_EVIDENCE_KINDS: set[str] = {
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
}


async def parse_pdf(
    file_content: bytes,
    *,
    page_count: int,
    check_cancel: Callable[[], None] | None = None,
    progress: Callable[[str], Awaitable[None]] | None = None,
    run_logger: RunLogger | None = None,
    cost_tracker: CostTracker | None = None,
) -> list[ParsedPage]:
    """Render the PDF in batches, send each batch to the configured AI provider, return parsed pages."""
    semaphore = asyncio.Semaphore(max(1, int(settings.AI_PAGE_CONCURRENCY)))
    batches = list(render_page_batches(file_content))

    async def _process(batch: list[tuple[int, bytes]]) -> list[ParsedPage]:
        async with semaphore:
            if check_cancel:
                check_cancel()
            return await _parse_batch(batch, run_logger=run_logger, cost_tracker=cost_tracker)

    # Each page_number may expand into multiple ParsedPage entries when the AI
    # detects more than one distinct document on a single physical page.
    parsed: dict[int, list[ParsedPage]] = {}
    completed = 0
    total = page_count
    tasks = [asyncio.create_task(_process(batch)) for batch in batches]
    try:
        for fut in asyncio.as_completed(tasks):
            pages = await fut
            for parsed_page in pages:
                parsed.setdefault(parsed_page.page_number, []).append(parsed_page)
            completed += len(pages)
            if progress:
                await progress(f"Parsed {completed}/{total} page(s).")
    except Exception:
        for task in tasks:
            task.cancel()
        raise

    ordered: list[ParsedPage] = []
    for n in range(1, page_count + 1):
        if n in parsed:
            ordered.extend(parsed[n])
        else:
            ordered.append(_empty_page(n, "missing"))
    return ordered


async def _invoke_parser(
    page_numbers: list[int],
    images: list[bytes],
    *,
    run_logger: RunLogger | None,
    cost_tracker: CostTracker | None,
) -> Any:
    user_text = (
        f"Parse the following {len(images)} PDF page image(s).\n"
        f"They correspond to PDF pages: {page_numbers}.\n"
        "Return a JSON object with a `pages` array containing EXACTLY ONE entry per page in the order received, "
        "even if a page is blank, an image, or a signature page (use page_kind empty/imaging/signature_only accordingly).\n"
        "For EACH page, first reconstruct the page as faithful `markdown`, then extract the structured fields and evidence from it.\n"
        "If a page contains two or more distinct document headers (different titles, dates, or signatories), "
        "you MUST populate `extra_documents` with the additional documents. "
        "Companion forms on the same page (e.g. a member's LTD claim and a Physician's Initial Report with different dates) "
        "are separate documents and must each appear — the first as the primary, the rest in `extra_documents`."
    )
    task_label = f"page-parse pages {page_numbers[0]}-{page_numbers[-1]}"
    call_kwargs = dict(
        model=page_model(),
        system_prompt=PAGE_PARSE_SYSTEM_PROMPT,
        user_text=user_text,
        images=images,
        schema=PARSED_PAGES_SCHEMA,
        task_label=task_label,
        run_logger=run_logger,
        stage="parse",
        cost_tracker=cost_tracker,
    )
    if settings.AI_PROVIDER == "openai":
        return await openai_multimodal_json(**call_kwargs)
    return await gemini_json(**call_kwargs)


async def _parse_batch(
    batch: list[tuple[int, bytes]],
    *,
    run_logger: RunLogger | None,
    cost_tracker: CostTracker | None = None,
) -> list[ParsedPage]:
    page_numbers = [n for n, _ in batch]
    images = [data for _, data in batch]

    payload = await _invoke_parser(
        page_numbers,
        images,
        run_logger=run_logger,
        cost_tracker=cost_tracker,
    )

    raw_pages = payload.get("pages") if isinstance(payload, dict) else None
    if not isinstance(raw_pages, list):
        logger.warning("%s did not return pages array for batch %s", settings.AI_PROVIDER, page_numbers)
        return [_empty_page(n, "no pages array returned") for n in page_numbers]

    # When the model does not return exactly one entry per requested page, the
    # positional fallback below would silently shift page numbers and duplicate a
    # neighbouring page's content onto the wrong page (this is what mislabels a
    # report's page in the UI). Re-parse each page on its own so the page number
    # for every document stays exact. Single-page batches fall through to the
    # tolerant mapping below.
    if len(raw_pages) != len(page_numbers) and len(page_numbers) > 1:
        logger.info(
            "Batch %s returned %d page(s) for %d image(s); re-parsing individually.",
            page_numbers,
            len(raw_pages),
            len(page_numbers),
        )
        out: list[ParsedPage] = []
        for page_no, image in batch:
            out.extend(
                await _parse_batch(
                    [(page_no, image)],
                    run_logger=run_logger,
                    cost_tracker=cost_tracker,
                )
            )
        return out

    out: list[ParsedPage] = []
    by_index: dict[int, dict[str, Any]] = {}
    for entry in raw_pages:
        if not isinstance(entry, dict):
            continue
        try:
            num = int(entry.get("page_number") or 0)
        except (TypeError, ValueError):
            num = 0
        if num in page_numbers:
            by_index[num] = entry

    for idx, page_no in enumerate(page_numbers):
        entry = by_index.get(page_no)
        if entry is None and idx < len(raw_pages) and isinstance(raw_pages[idx], dict):
            entry = raw_pages[idx]
            entry["page_number"] = page_no
        if entry is None:
            out.append(_rescue_image_page(_empty_page(page_no, "missing in response"), images[idx]))
            continue
        out.append(_rescue_image_page(_normalize_page(entry, page_no), images[idx]))
        # Expand any additional documents found on the same physical page.
        extra = entry.get("extra_documents")
        if isinstance(extra, list):
            for extra_entry in extra:
                if not isinstance(extra_entry, dict):
                    continue
                # Synthesise a full page entry from the extra_document fields,
                # reusing the same page_number so PDF links stay correct.
                synthetic: dict[str, Any] = dict(extra_entry)
                synthetic["page_number"] = page_no
                # Extra documents always start a new document segment.
                synthetic.setdefault("starts_new_document", True)
                out.append(_normalize_page(synthetic, page_no))
    return out


def _empty_page(page_number: int, reason: str) -> ParsedPage:
    return ParsedPage(
        page_number=page_number,
        starts_new_document=False,
        include_in_output=False,
        page_kind="empty",
        evidence=[],
        raw_text_excerpt=f"[parse-error: {reason}]",
    )


def _normalize_page(entry: dict[str, Any], page_no: int) -> ParsedPage:
    page_kind_raw = (entry.get("page_kind") or "clinical").strip().lower()
    page_kind: PageKind = page_kind_raw if page_kind_raw in _VALID_PAGE_KINDS else "clinical"  # type: ignore[assignment]
    bucket_raw = ((entry.get("document") or {}).get("bucket") or "unknown").strip().lower()
    bucket: DocumentBucket = bucket_raw if bucket_raw in _VALID_BUCKETS else "unknown"  # type: ignore[assignment]

    patient_data = entry.get("patient") or {}
    document_data = entry.get("document") or {}
    author_data = entry.get("author") or {}
    recipient_data = entry.get("recipient") or {}
    header_fields_data = entry.get("header_fields") or {}
    raw_evidence = entry.get("evidence") or []

    evidence: list[EvidenceItem] = []
    if isinstance(raw_evidence, list):
        for item in raw_evidence:
            if not isinstance(item, dict):
                continue
            kind = (item.get("kind") or "").strip().lower()
            if kind not in _VALID_EVIDENCE_KINDS:
                continue
            text = _clean_text(item.get("text"))
            if not text:
                continue
            value = _clean_text(item.get("value")) or None
            evidence.append(EvidenceItem(kind=kind, text=text, value=value))  # type: ignore[arg-type]

    markdown = str(entry.get("markdown") or "").strip()

    # A page whose only real content is a medical image carries an image marker in
    # its markdown even when the model mislabeled the page empty/signature. Treat
    # it as imaging so the figure (X-ray/scan/clinical photo) is captured, not
    # silently dropped.
    rescued_image = not evidence and "![" in markdown and page_kind in {"empty", "signature_only"}
    if rescued_image:
        page_kind = "imaging"  # type: ignore[assignment]

    # Imaging page with no report text: capture the image as one evidence item
    # (using its markdown caption when present) so it becomes a document.
    if page_kind == "imaging" and not evidence:
        caption = _first_image_caption(markdown) or _clean_text(entry.get("raw_text_excerpt"))
        evidence.append(
            EvidenceItem(
                kind="imaging_finding",
                text=caption or "Medical image on page; no report text captured.",
            )
        )

    include_default = page_kind not in {"admin", "empty"}
    include_in_output = True if rescued_image else bool(entry.get("include_in_output", include_default))

    return ParsedPage(
        page_number=int(entry.get("page_number") or page_no),
        starts_new_document=bool(entry.get("starts_new_document", False)),
        include_in_output=include_in_output,
        page_kind=page_kind,
        patient=PatientFingerprint(
            name=_clean_text(patient_data.get("name")) or None,
            dob=_clean_text(patient_data.get("dob")) or None,
            identifier=_clean_text(patient_data.get("identifier")) or None,
        ),
        document=DocumentFingerprint(
            title=_clean_text(document_data.get("title")) or None,
            bucket=bucket,
            date=_clean_text(document_data.get("date")) or None,
        ),
        author=AuthorFingerprint(
            name=_clean_text(author_data.get("name")) or None,
            credentials=_clean_text(author_data.get("credentials")) or None,
            is_doctor=bool(author_data.get("is_doctor", False)),
            is_signing=bool(author_data.get("is_signing", False)),
        ),
        recipient=AuthorFingerprint(
            name=_clean_text(recipient_data.get("name")) or None,
            credentials=_clean_text(recipient_data.get("credentials")) or None,
            is_doctor=bool(recipient_data.get("is_doctor", False)),
            is_signing=bool(recipient_data.get("is_signing", False)),
        ),
        header_fields=HeaderFields(
            to=_clean_text(header_fields_data.get("to")) or None,
            **{
                "from": _clean_text(header_fields_data.get("from")) or None,
            },
            claim_number=_clean_text(header_fields_data.get("claim_number")) or None,
            occupation=_clean_text(header_fields_data.get("occupation")) or None,
            review_date=_clean_text(header_fields_data.get("review_date")) or None,
            diagnosis_dod=_clean_text(header_fields_data.get("diagnosis_dod")) or None,
        ),
        evidence=evidence,
        raw_text_excerpt=_clean_text(entry.get("raw_text_excerpt")) or "",
        markdown=markdown,
    )


# A page the model calls empty but whose rendered image is substantially dark is
# almost always a photo / X-ray / scan, not a blank page. Reclassify it as an
# image so it is captured as a document instead of being dropped.
# Text pages render around 0.07-0.08 ink; a photo/X-ray is far higher (the
# clinical photo in our test set is ~0.69). 0.15 sits well clear of dense text.
_IMAGE_INK_THRESHOLD = 0.15


def _rescue_image_page(page: ParsedPage, image_bytes: bytes) -> ParsedPage:
    # Any page that produced no evidence would be dropped. If its rendered image
    # is substantially dark it is a photo/X-ray/scan, not a blank page - capture
    # it as an imaging document regardless of how the model labeled (or skipped)
    # it. The ink threshold keeps genuinely blank/text pages from triggering.
    if page.evidence:
        return page
    if ink_ratio(image_bytes) < _IMAGE_INK_THRESHOLD:
        return page
    caption = _first_image_caption(page.markdown)
    page.page_kind = "imaging"
    page.include_in_output = True
    page.evidence = [
        EvidenceItem(
            kind="imaging_finding",
            text=caption or "Medical image on page; no report text captured.",
        )
    ]
    return page


def _first_image_caption(markdown: str) -> str:
    """Pull the caption out of the first markdown image marker ``![caption]``."""
    start = markdown.find("![")
    if start == -1:
        return ""
    end = markdown.find("]", start + 2)
    if end == -1:
        return ""
    return markdown[start + 2 : end].strip()


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = " ".join(text.split())
    return text
