"""Cross-file author/recipient name-spelling resolution.

Every page is parsed in total isolation (one page per vision call, no memory
of any other page), so the SAME person's handwritten signature routinely
comes back spelled differently on every visit - a clinician re-signs their
name slightly differently by hand each time, and the transcription reads
that cursive slightly differently each time too. Per-page isolation cannot
fix this on its own: nothing in one call knows what another call read.

This closes the gap the same way `boundary.py` does: ONE text-only call sees
every distinct name found anywhere in the file, together with where each was
seen, and clusters the ones that are almost certainly one real person -
preferring a clean typed/printed instance (an EMR discharge report, an
invoice letterhead) over noisy cursive when one exists. The chosen spelling
is NEVER invented: `canonical` is enforced (in code, not just prompted) to be
one of the strings this file's own parsing actually produced. On any
failure this changes nothing, so parsing output is used exactly as read.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from app.core.config import settings
from app.services.extraction.cost import CostTracker
from app.services.extraction.llm import RunLogger, gemini_json, openai_json, opinion_model
from app.services.extraction.models import ParsedPage
from app.services.extraction.prompts import IDENTITY_SCHEMA, IDENTITY_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Occurrences and excerpt length per name variant in the digest - enough
# context for the model to judge clinic/letterhead continuity without
# ballooning the call on a file with hundreds of distinct name strings.
_MAX_OCCURRENCES_PER_VARIANT = 6
_EXCERPT_CHARS = 200


def _collect_name_sites(pages: list[ParsedPage]) -> dict[str, list[ParsedPage]]:
    """Every distinct, non-empty author/recipient name string mapped to the
    pages it was read on (a name seen as both author and recipient somewhere
    still gets one entry - it is the same spelling problem either way)."""
    sites: dict[str, list[ParsedPage]] = defaultdict(list)
    for page in pages:
        for name in (page.author.name, page.recipient.name):
            if name and name.strip():
                sites[name].append(page)
    return sites


def build_variant_digest(pages: list[ParsedPage]) -> list[dict]:
    """Pure, testable: the per-variant evidence payload sent to the model."""
    sites = _collect_name_sites(pages)
    digest: list[dict] = []
    for name, occurrences in sites.items():
        sample = occurrences[0]
        excerpt = (sample.markdown or sample.raw_text_excerpt or "").strip()
        excerpt = " ".join(excerpt.split())[:_EXCERPT_CHARS]
        digest.append(
            {
                "name": name,
                "occurrences": [
                    {
                        "page": p.page_number,
                        "title": p.document.title or "",
                        "date": p.document.date or "",
                        "bucket": p.document.bucket,
                    }
                    for p in occurrences[:_MAX_OCCURRENCES_PER_VARIANT]
                ],
                "sample_context": excerpt,
            }
        )
    return digest


def build_rename_map(clusters: list[dict], known_names: set[str]) -> dict[str, str]:
    """Pure, testable: validate the model's clusters into a raw-name ->
    canonical-name map. A cluster is used ONLY when `canonical` is exactly
    one of this file's own parsed name strings; any member string the model
    invented or that never occurred in this file is silently dropped rather
    than trusted. This is what guarantees a displayed name is always
    something actually read from the document, never fabricated."""
    rename: dict[str, str] = {}
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        canonical = cluster.get("canonical")
        members = cluster.get("members")
        if not isinstance(canonical, str) or canonical not in known_names:
            continue
        if not isinstance(members, list):
            continue
        valid_members = [m for m in members if isinstance(m, str) and m in known_names]
        if len(valid_members) < 2:
            # Nothing to resolve - nothing to apply.
            continue
        for member in valid_members:
            rename[member] = canonical
    return rename


def apply_rename_map(pages: list[ParsedPage], rename: dict[str, str]) -> None:
    """Apply a validated raw-name -> canonical-name map in place. Every
    canonical value is itself one of the strings this file's own parsing
    produced (enforced by build_rename_map), so this only ever swaps one
    real reading for another equally real reading of the same signature -
    never introduces a name that wasn't actually parsed from the document."""
    if not rename:
        return
    for page in pages:
        if page.author.name in rename:
            page.author.name = rename[page.author.name]
        if page.recipient.name in rename:
            page.recipient.name = rename[page.recipient.name]


async def _call_identity_model(
    digest: list[dict],
    *,
    run_logger: RunLogger | None,
    cost_tracker: CostTracker | None,
) -> dict:
    import json

    payload = json.dumps({"names": digest}, ensure_ascii=False)
    if settings.AI_PROVIDER == "openai" or settings.OPENAI_API_KEY:
        return await openai_json(
            model=opinion_model(),
            system_prompt=IDENTITY_SYSTEM_PROMPT,
            user_prompt=payload,
            schema=IDENTITY_SCHEMA,
            task_label="Author identity resolution",
            run_logger=run_logger,
            stage="parse",
            cost_tracker=cost_tracker,
        )
    return await gemini_json(
        model=settings.GEMINI_BUNDLE_MODEL,
        system_prompt=IDENTITY_SYSTEM_PROMPT,
        user_text=payload,
        schema=IDENTITY_SCHEMA,
        task_label="Author identity resolution",
        run_logger=run_logger,
        stage="parse",
        cost_tracker=cost_tracker,
    )


async def resolve_author_identities(
    pages: list[ParsedPage],
    *,
    run_logger: RunLogger | None = None,
    cost_tracker: CostTracker | None = None,
) -> list[ParsedPage]:
    """Resolve handwritten-signature spelling drift across the whole file.
    Mutates and returns `pages`. Any failure leaves pages unchanged - parsing
    output is always the safe fallback."""
    digest = build_variant_digest(pages)
    if len(digest) < 2:
        return pages
    try:
        response = await _call_identity_model(
            digest, run_logger=run_logger, cost_tracker=cost_tracker
        )
        clusters = response.get("clusters") if isinstance(response, dict) else None
        if not isinstance(clusters, list) or not clusters:
            return pages
        known_names = {entry["name"] for entry in digest}
        rename = build_rename_map(clusters, known_names)
        if rename:
            logger.info("Author identity resolution merged %d name variant(s).", len(rename))
        apply_rename_map(pages, rename)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Author identity resolution failed (%s); names left as parsed.",
            str(exc)[:200],
        )
    return pages
