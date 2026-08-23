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
from app.schemas.rules import RuleConfigSnapshot
from app.services.extraction.cost import CostTracker
from app.services.extraction.llm import RunLogger, openai_json, opinion_model
from app.services.extraction.models import PatientBundle
from app.services.extraction.prompts import OPINION_SCHEMA
from app.services.rules.prompt_builder import build_opinion_prompt, rule_for_document

logger = logging.getLogger(__name__)


def _build_evidence_list(bundle: PatientBundle) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for doc in bundle.documents:
        if not doc.include_in_output:
            continue
        author = doc.author.name or ""
        author_is_doctor = bool(doc.author.name and doc.author.is_doctor)
        author_credentials = doc.author.credentials or ""
        for page in doc.pages:
            for evidence in page.evidence:
                items.append(
                    {
                        "kind": evidence.kind,
                        "text": evidence.text,
                        "value": evidence.value or "",
                        "document_title": doc.title or "",
                        "document_date": doc.date or "",
                        "document_bucket": doc.bucket,
                        "author": author,
                        "author_credentials": author_credentials,
                        "author_is_doctor": author_is_doctor,
                        "page": page.page_number,
                    }
                )
                if len(items) >= 320:
                    return items
    return items


def _build_assignment_context(
    bundle: PatientBundle, rule_config: RuleConfigSnapshot | None = None
) -> str:
    """Referral questions/context, separate from clinical evidence.

    Administrative documents and coverage placeholders always contribute (the
    referral/question forms live there); a rule flagged `use_as_context` adds
    its matching documents too, so a custom document type can feed the opinion
    even when it is skipped in the summary."""
    blocks: list[str] = []
    for doc in bundle.documents:
        rule = rule_for_document(rule_config, custom_type=doc.custom_type, bucket=doc.bucket)
        rule_wants_context = bool(rule and rule.use_as_context)
        if not doc.is_placeholder and doc.bucket != "administrative" and not rule_wants_context:
            continue
        text = doc.markdown or " ".join(
            page.raw_text_excerpt for page in doc.pages if page.raw_text_excerpt
        )
        text = text.strip()
        if text:
            blocks.append(text)
        if sum(len(block) for block in blocks) >= 12000:
            break
    return "\n\n".join(blocks)[:12000]


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
    rule_config: RuleConfigSnapshot | None = None,
) -> tuple[str, str]:
    """Return (opinion_text, definition_text).

    `definition_text` is non-empty only for templates that require a separate
    Definition section (critical illness - golden rules 7.2); it carries the
    contractual application, never analysis.
    """
    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY missing - skipping opinion generation.")
        return "No patient opinion generated.", ""

    evidence = _build_evidence_list(bundle)
    if not evidence:
        return "No patient opinion generated.", ""

    user_payload = {
        "header": header.model_dump(),
        "patient": {
            "name": bundle.name or "",
            "dob": bundle.dob or "",
            "page_start": bundle.page_start,
            "page_end": bundle.page_end,
        },
        "assignment_context": _build_assignment_context(bundle, rule_config),
        "evidence": evidence,
    }

    try:
        response = await openai_json(
            model=opinion_model(),
            system_prompt=build_opinion_prompt(rule_config),
            user_prompt=json.dumps(user_payload, ensure_ascii=False),
            schema=OPINION_SCHEMA,
            task_label=f"Opinion {bundle.id}",
            run_logger=run_logger,
            stage="summarize",
            cost_tracker=cost_tracker,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Opinion generation failed for %s: %s", bundle.id, exc)
        return "No patient opinion generated.", ""

    validated_header = response.get("header")
    if isinstance(validated_header, dict):
        # The opinion model already sees the complete evidence context and is
        # asked to distinguish the generated review's author from the incoming
        # referral sender/recipient. Apply supported corrections instead of
        # discarding the validated header and preserving reversed source fields.
        for field in PatientHeader.model_fields:
            value = validated_header.get(field)
            if isinstance(value, str) and value.strip():
                setattr(header, field, value.strip())

    definition_text = _scrub_opinion(str(response.get("definition") or ""))
    opinion_text = _scrub_opinion(str(response.get("opinion") or ""))
    if not opinion_text:
        return "No patient opinion generated.", definition_text
    return opinion_text, definition_text
