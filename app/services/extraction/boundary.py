"""Whole-file document-boundary resolution.

The per-page parser reads every page in strict isolation (that is what keeps
page numbers exact), which means no single call ever sees enough context to
judge where one source document ends and the next begins. This module
restores that judgment WITHOUT giving up page-exact parsing: after all pages
are parsed, ONE text-only LLM call reads a compact, page-ordered digest of
the entire file - like a person flipping through the binder - and returns
the document ranges. The ranges drive `group_documents_with_plan`, which
stays deterministic and enforces the invariants the plan may never break
(page numbers from parsing only, patients never share a document, full
coverage). Any failure falls back to the heuristic grouping, so the
pipeline never breaks and nothing is ever silently lost.
"""
from __future__ import annotations

import json
import logging

from app.core.config import settings
from app.services.extraction.cost import CostTracker
from app.services.extraction.grouping import group_documents, group_documents_with_plan
from app.services.extraction.llm import RunLogger, gemini_json, openai_json, opinion_model
from app.services.extraction.models import DocumentSegment, ParsedPage
from app.services.extraction.prompts import BOUNDARY_SCHEMA, BOUNDARY_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Opening words per digest entry. Enough to recognize a letterhead, a form
# template, or a date line; small enough that a 1000-page file still fits
# one call comfortably.
_OPENING_CHARS = 160


def _digest(page: ParsedPage) -> dict:
    opening = (page.markdown or page.raw_text_excerpt or "").strip()
    opening = " ".join(opening.split())[:_OPENING_CHARS]
    return {
        "page": page.page_number,
        "kind": page.page_kind,
        "starts_new_document": page.starts_new_document,
        "title": page.document.title or "",
        "date": page.document.date or "",
        "author": page.author.name or "",
        "recipient": page.recipient.name or "",
        "patient": page.patient.name or "",
        "dob": page.patient.dob or "",
        "opening": opening,
    }


async def _call_boundary_model(
    payload: str,
    *,
    run_logger: RunLogger | None,
    cost_tracker: CostTracker | None,
) -> dict:
    if settings.AI_PROVIDER == "openai" or settings.OPENAI_API_KEY:
        return await openai_json(
            model=opinion_model(),
            system_prompt=BOUNDARY_SYSTEM_PROMPT,
            user_prompt=payload,
            schema=BOUNDARY_SCHEMA,
            task_label="Document boundary resolution",
            run_logger=run_logger,
            stage="parse",
            cost_tracker=cost_tracker,
        )
    return await gemini_json(
        model=settings.GEMINI_BUNDLE_MODEL,
        system_prompt=BOUNDARY_SYSTEM_PROMPT,
        user_text=payload,
        schema=BOUNDARY_SCHEMA,
        task_label="Document boundary resolution",
        run_logger=run_logger,
        stage="parse",
        cost_tracker=cost_tracker,
    )


async def resolve_documents(
    pages: list[ParsedPage],
    *,
    run_logger: RunLogger | None = None,
    cost_tracker: CostTracker | None = None,
) -> list[DocumentSegment]:
    """Group parsed pages into documents using the whole-file boundary plan,
    falling back to the heuristic grouping on any failure."""
    if not pages:
        return []
    page_count = max(p.page_number for p in pages)
    payload = json.dumps(
        {
            "page_count": page_count,
            "entries": [_digest(p) for p in pages],
        },
        ensure_ascii=False,
    )
    try:
        plan = await _call_boundary_model(
            payload, run_logger=run_logger, cost_tracker=cost_tracker
        )
        ranges = plan.get("documents") if isinstance(plan, dict) else None
        if not isinstance(ranges, list) or not ranges:
            raise ValueError("boundary plan returned no ranges")
        documents = group_documents_with_plan(pages, [r for r in ranges if isinstance(r, dict)])
        if not documents:
            raise ValueError("boundary plan produced no documents")
        logger.info(
            "Boundary plan applied: %d range(s) -> %d document(s).", len(ranges), len(documents)
        )
        return documents
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Whole-file boundary resolution failed (%s); using heuristic grouping.",
            str(exc)[:200],
        )
        return group_documents(pages)
