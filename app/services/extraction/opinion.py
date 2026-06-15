"""OpenAI opinion builder.

Sends the deterministic header + a flattened list of cited evidence (with
page anchors) so the model can synthesize a 3-5 paragraph medico-legal
opinion at Grade 11 reading level. Returns plain text only.
"""
from __future__ import annotations

import json
import logging
import re

from app.core.config import settings
from app.schemas.extraction import PatientHeader
from app.services.extraction.cost import CostTracker
from app.services.extraction.llm import RunLogger, openai_json, opinion_model
from app.services.extraction.models import PatientBundle
from app.services.extraction.prompts import OPINION_SCHEMA, OPINION_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def _build_evidence_list(bundle: PatientBundle) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for doc in bundle.documents:
        if not doc.include_in_output:
            continue
        author = doc.author.name or ""
        for page in doc.pages:
            for evidence in page.evidence:
                items.append(
                    {
                        "kind": evidence.kind,
                        "text": evidence.text,
                        "value": evidence.value or "",
                        "document_title": doc.title or "",
                        "document_date": doc.date or "",
                        "author": author,
                        "page": page.page_number,
                    }
                )
                if len(items) >= 320:
                    return items
    return items


def _scrub_opinion(text: str) -> str:
    """Light cleanup only: strip markdown markers. Never delete words/phrases,
    which corrupts grammar and destroys context."""
    if not text:
        return text
    cleaned = text.replace("**", "").replace("__", "")
    cleaned = re.sub(r"^[*\-#>]+\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


async def build_opinion(
    bundle: PatientBundle,
    header: PatientHeader,
    *,
    run_logger: RunLogger | None = None,
    cost_tracker: CostTracker | None = None,
) -> str:
    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY missing - skipping opinion generation.")
        return "No patient opinion generated."

    evidence = _build_evidence_list(bundle)
    if not evidence:
        return "No patient opinion generated."

    user_payload = {
        "header": header.model_dump(),
        "patient": {
            "name": bundle.name or "",
            "dob": bundle.dob or "",
            "page_start": bundle.page_start,
            "page_end": bundle.page_end,
        },
        "evidence": evidence,
    }

    try:
        response = await openai_json(
            model=opinion_model(),
            system_prompt=OPINION_SYSTEM_PROMPT,
            user_prompt=json.dumps(user_payload, ensure_ascii=False),
            schema=OPINION_SCHEMA,
            task_label=f"Opinion {bundle.id}",
            run_logger=run_logger,
            stage="summarize",
            cost_tracker=cost_tracker,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Opinion generation failed for %s: %s", bundle.id, exc)
        return "No patient opinion generated."

    opinion_text = _scrub_opinion(str(response.get("opinion") or ""))
    if not opinion_text:
        return "No patient opinion generated."
    return opinion_text
