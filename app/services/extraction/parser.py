"""AI-driven page parser producing ParsedPage with evidence arrays."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from app.core.config import settings
from app.services.extraction.cost import CostTracker
from app.services.extraction.llm import RunLogger, gemini_json, openai_multimodal_json, page_model
from app.services.layout import (
    PageLayout,
    get_layout_provider,
    layout_enabled,
    render_layout_block,
)
from app.services.extraction.models import (
    PageMarker,
    AuthorFingerprint,
    DocumentBucket,
    DocumentFingerprint,
    EvidenceItem,
    HeaderFields,
    PageKind,
    ParsedPage,
    PatientFingerprint,
)
from app.schemas.rules import RuleConfigSnapshot
from app.services.extraction.pdf import ink_ratio, render_page_batches
from app.services.extraction.prompts import PARSED_PAGES_SCHEMA
from app.services.rules.prompt_builder import build_page_parse_prompt

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


async def _analyze_layout(batch: list[tuple[int, bytes, str]]) -> list[PageLayout]:
    """Structure for each page of a batch, or nothing when layout is disabled.

    Runs alongside the vision call rather than replacing it: the service is
    better at tables and labelled fields, the model is better at reading a
    messy scan for meaning, and each is used for what it is good at.
    """
    if not layout_enabled():
        return []
    provider = get_layout_provider()
    return list(
        await asyncio.gather(*(provider.analyze(page_no, image) for page_no, image, _ in batch))
    )


async def parse_pdf(
    file_content: bytes,
    *,
    page_count: int,
    check_cancel: Callable[[], None] | None = None,
    progress: Callable[[str], Awaitable[None]] | None = None,
    run_logger: RunLogger | None = None,
    cost_tracker: CostTracker | None = None,
    rule_config: RuleConfigSnapshot | None = None,
) -> list[ParsedPage]:
    """Render the PDF in batches, send each batch to the configured AI provider, return parsed pages."""
    semaphore = asyncio.Semaphore(max(1, int(settings.AI_PAGE_CONCURRENCY)))
    batches = list(render_page_batches(file_content))
    system_prompt = build_page_parse_prompt(rule_config)

    async def _process(batch: list[tuple[int, bytes]]) -> list[ParsedPage]:
        async with semaphore:
            if check_cancel:
                check_cancel()
            layouts = await _analyze_layout(batch)
            return await _parse_batch(
                batch,
                system_prompt=system_prompt,
                layouts=layouts,
                run_logger=run_logger,
                cost_tracker=cost_tracker,
            )

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
            # Count distinct physical pages, not returned ParsedPage entries: a
            # page with extra_documents expands into multiple entries sharing
            # one page_number, which would otherwise inflate the count past the
            # true page total (e.g. "78/75").
            completed += len({p.page_number for p in pages})
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
    texts: list[str] | None = None,
    *,
    system_prompt: str,
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
    # When the PDF page carries an embedded text layer, hand it to the model
    # as a SPELLING REFERENCE next to the image: typed names/values then come
    # out character-exact instead of re-OCR'd from pixels. The image remains
    # authoritative - many pages are pure scans with no text layer, and pages
    # that have one still hold their decisive content in handwriting, ticks,
    # stamps, and signatures the text layer cannot see.
    if texts:
        for page_no, text in zip(page_numbers, texts):
            if not text:
                continue
            clipped = text[:6000]
            user_text += (
                f"\n\nEMBEDDED TEXT LAYER of PDF page {page_no} (machine-extracted; may be incomplete "
                "and NEVER contains handwriting, checkbox states, stamps, or signatures - read those "
                "from the image; use this text only to copy typed content with exact spelling):\n"
                f"<<<\n{clipped}\n>>>"
            )
    # Structure from a document-AI service, when one is configured. It says
    # what SHAPE things are - this block is a table, that name is a routing
    # label - which is exactly what a model reading pixels has to guess at.
    for page_no, layout in zip(page_numbers, layouts or []):
        block = render_layout_block(layout)
        if block:
            user_text += f"\n\n{block}"

    task_label = f"page-parse pages {page_numbers[0]}-{page_numbers[-1]}"
    call_kwargs = dict(
        model=page_model(),
        system_prompt=system_prompt,
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
    batch: list[tuple[int, bytes, str]],
    *,
    system_prompt: str,
    layouts: list[PageLayout] | None = None,
    run_logger: RunLogger | None,
    cost_tracker: CostTracker | None = None,
) -> list[ParsedPage]:
    page_numbers = [item[0] for item in batch]
    images = [item[1] for item in batch]
    texts = [item[2] for item in batch]

    payload = await _invoke_parser(
        page_numbers,
        images,
        texts,
        system_prompt=system_prompt,
        run_logger=run_logger,
        cost_tracker=cost_tracker,
    )

    raw_pages = payload.get("pages") if isinstance(payload, dict) else None
    if not isinstance(raw_pages, list):
        logger.warning("%s did not return pages array for batch %s", settings.AI_PROVIDER, page_numbers)
        return [_empty_page(n, "no pages array returned") for n in page_numbers]

    # When the model does not return exactly one entry per requested page, the
    # positional mapping below would silently shift page numbers and duplicate a
    # neighbouring page's content onto the wrong page (this is what mislabels a
    # report's page in the UI). Re-parse each page on its own so the page number
    # for every document stays exact. Single-page batches instead fold the
    # surplus entries back into the one physical page below - the model
    # sometimes reports a page's second/third dated entry as an additional
    # `pages` element (numbered with the NEXT page number, or a page label
    # printed inside the document) instead of using `extra_documents`, and
    # discarding those entries silently loses real dated entries.
    if len(raw_pages) != len(page_numbers) and len(page_numbers) > 1:
        logger.info(
            "Batch %s returned %d page(s) for %d image(s); re-parsing individually.",
            page_numbers,
            len(raw_pages),
            len(page_numbers),
        )
        out: list[ParsedPage] = []
        for page_no, image, text in batch:
            out.extend(
                await _parse_batch(
                    [(page_no, image, text)],
                    system_prompt=system_prompt,
                    run_logger=run_logger,
                    cost_tracker=cost_tracker,
                )
            )
        return out

    # Map entries to pages purely by POSITION. The model's own `page_number`
    # claims are never trusted: vision models routinely echo a page label
    # printed inside the document ("Page 19/76"), drift by one after a dense
    # page, or continue a sequence - trusting the claim is exactly what showed
    # page-535 content under page 537 in the UI. The images were sent in
    # `page_numbers` order and (after the count guard above) the entries come
    # back one per page in the order received, so position IS ground truth.
    out: list[ParsedPage] = []
    entries = [entry if isinstance(entry, dict) else None for entry in raw_pages]

    for idx, page_no in enumerate(page_numbers):
        entry = entries[idx] if idx < len(entries) else None
        if entry is None:
            out.append(_rescue_image_page(_empty_page(page_no, "missing in response"), images[idx]))
            continue
        out.append(_rescue_image_page(_normalize_page(entry, page_no), images[idx]))
        out.extend(_expand_extra_documents(entry, page_no))

    # Single-page batch that came back with SURPLUS page entries: every entry
    # beyond the first describes another document found on that same physical
    # page (whatever page number the model invented for it). Keep each one as
    # a same-page extra document instead of dropping it.
    if len(page_numbers) == 1 and len(entries) > 1:
        page_no = page_numbers[0]
        for entry in entries[1:]:
            if entry is None:
                continue
            surplus: dict[str, Any] = dict(entry)
            surplus.setdefault("starts_new_document", True)
            out.append(_normalize_page(surplus, page_no))
            out.extend(_expand_extra_documents(surplus, page_no))
    return out


def _expand_extra_documents(entry: dict[str, Any], page_no: int) -> list[ParsedPage]:
    """Expand `extra_documents` into synthetic same-page ParsedPage entries."""
    extra = entry.get("extra_documents")
    if not isinstance(extra, list):
        return []
    out: list[ParsedPage] = []
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
            evidence.append(
                EvidenceItem(  # type: ignore[arg-type]
                    kind=kind,
                    text=text,
                    value=value,
                    provenance=_provenance(item.get("provenance")),
                )
            )

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
        # `page_no` is the physical page whose image produced this entry -
        # always authoritative. The model's own page_number claim is ignored
        # (it echoes page labels printed inside documents, e.g. "Page 19/76").
        page_number=page_no,
        page_marker=_page_marker(entry.get("page_marker")),
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
            custom_type=_clean_text(document_data.get("custom_type")) or None,
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


_VALID_PROVENANCE = {"primary", "historical", "referenced", "index"}


def _provenance(raw: object) -> str:
    """How this document came by the finding.

    Anything unrecognized falls back to `primary`, which is how every item
    behaved before the field existed - an unknown value must not silently
    demote real content out of the summary.
    """
    value = str(raw or "").strip().lower()
    return value if value in _VALID_PROVENANCE else "primary"


def _page_marker(raw: object) -> PageMarker:
    """The page's printed 'Page N of M', defaulting to an unusable marker.

    Anything malformed is dropped rather than guessed at: an incoherent marker
    must not out-vote the boundary heuristics.
    """
    if not isinstance(raw, dict):
        return PageMarker()
    try:
        return PageMarker(
            index=int(raw.get("index") or 0), total=int(raw.get("total") or 0)
        )
    except (TypeError, ValueError):
        return PageMarker()


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = " ".join(text.split())
    return text
