"""Assembles the pipeline's stage prompts from a RuleConfigSnapshot.

Every function degrades gracefully: with no snapshot (no configuration in
the database, or a job persisted before the rule engine existed) the
original built-in prompts are returned unchanged.
"""
from __future__ import annotations

from app.schemas.rules import DocumentRule, OpinionTemplate, RuleAction, RuleConfigSnapshot
from app.services.extraction.prompts import (
    OPINION_SYSTEM_PROMPT,
    PAGE_PARSE_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
)

# The parser's fixed bucket taxonomy. Custom document types are recognized IN
# ADDITION to these - buckets keep the grouping/boundary mechanics stable, the
# custom type carries the user's classification for rule matching.
_BUCKET_ALIASES = {
    "clinical": "clinical",
    "imaging": "imaging",
    "pathology": "pathology",
    "functional": "functional",
    "administrative": "administrative",
    "admin": "administrative",
}

_OPINION_TEMPLATE_INSTRUCTIONS: dict[OpinionTemplate, str] = {
    OpinionTemplate.DISABILITY: (
        "This is a disability file review: the output carries Summary and Opinion "
        "sections only. Follow the evidence-based, functional, insurer-focused "
        "model: state the work-capacity conclusion early and support it with the "
        "strongest objective and functional evidence."
    ),
    OpinionTemplate.CRITICAL_ILLNESS: (
        "This is a critical illness review. Populate the `definition` field: state "
        "whether the documented condition meets the policy definition, as a policy "
        "or contractual application only, with no medical or adjudicative opinion. "
        "Keep the analysis itself in `opinion`. Address tiered benefit analyses "
        "separately where applicable."
    ),
    OpinionTemplate.ACCOMMODATION: (
        "This is an accommodation opinion. Provide evidence-based accommodation "
        "reasoning, applying the hierarchy of hazard control where relevant, with "
        "Summary and Opinion sections only."
    ),
    OpinionTemplate.UNDERWRITING: (
        "This is an underwriting review. Focus the analysis on underwriting risk. "
        "Use underwriting manuals only when their content is provided in the "
        "evidence. Summary and Opinion sections only."
    ),
}


def _golden_block(snapshot: RuleConfigSnapshot | None) -> str:
    if not snapshot or not snapshot.golden_rule_prompt.strip():
        return ""
    return snapshot.golden_rule_prompt.strip() + "\n\n"


def _custom_type_rules(snapshot: RuleConfigSnapshot) -> list[DocumentRule]:
    """Rules whose document_type is NOT one of the built-in buckets - these
    define genuinely custom document kinds the parser must learn to tag."""
    return [
        rule
        for rule in snapshot.rules
        if rule.document_type.strip().lower() not in _BUCKET_ALIASES
    ]


def build_page_parse_prompt(snapshot: RuleConfigSnapshot | None) -> str:
    prompt = _golden_block(snapshot) + PAGE_PARSE_SYSTEM_PROMPT
    if not snapshot:
        return prompt
    custom_rules = _custom_type_rules(snapshot)
    if not custom_rules:
        return prompt
    lines = [
        "",
        "CUSTOM DOCUMENT TYPES:",
        "In addition to the standard bucket classification, tag each document's "
        "`custom_type` field with EXACTLY one of the labels below when the document "
        "matches its description; otherwise leave `custom_type` as an empty string. "
        "The bucket is still required and follows the standard rules.",
    ]
    for rule in custom_rules:
        description = rule.match_prompt.strip() or "No description provided."
        lines.append(f'- "{rule.document_type}": {description}')
    return prompt + "\n" + "\n".join(lines)


def build_summary_prompt(snapshot: RuleConfigSnapshot | None) -> str:
    base = SUMMARY_SYSTEM_PROMPT
    if snapshot and snapshot.summary_prompt and snapshot.summary_prompt.strip():
        base = snapshot.summary_prompt.strip()
    prompt = _golden_block(snapshot) + base
    if not snapshot:
        return prompt
    instruction_rules = [
        rule
        for rule in snapshot.rules
        if rule.action != RuleAction.SKIP and rule.instruction_prompt.strip()
    ]
    if instruction_rules:
        lines = [
            "",
            "DOCUMENT-TYPE RULES:",
            "Each input entry carries a `rule_document_type`. When it matches a rule "
            "below, follow that rule's instructions for the entry (the entry's "
            "`maximum_words` ceiling still applies):",
        ]
        for rule in instruction_rules:
            lines.append(f'- "{rule.document_type}": {rule.instruction_prompt.strip()}')
        prompt += "\n" + "\n".join(lines)
    return prompt


def build_opinion_prompt(snapshot: RuleConfigSnapshot | None) -> str:
    base = OPINION_SYSTEM_PROMPT
    if snapshot and snapshot.opinion_prompt and snapshot.opinion_prompt.strip():
        base = snapshot.opinion_prompt.strip()
    prompt = _golden_block(snapshot) + base
    if snapshot:
        template_note = _OPINION_TEMPLATE_INSTRUCTIONS.get(snapshot.opinion_template)
        if template_note:
            prompt += "\n\n" + template_note
    return prompt


def rule_for_document(
    snapshot: RuleConfigSnapshot | None,
    *,
    custom_type: str | None,
    bucket: str,
) -> DocumentRule | None:
    """Find the rule governing one document.

    A custom type tagged by the parser wins over the bucket, so a user-defined
    kind (e.g. "Referral Form") can carve documents out of a broad bucket.
    """
    if not snapshot:
        return None
    if custom_type:
        wanted = custom_type.strip().lower()
        for rule in snapshot.rules:
            if rule.document_type.strip().lower() == wanted:
                return rule
    normalized_bucket = _BUCKET_ALIASES.get(bucket.strip().lower())
    if normalized_bucket:
        for rule in snapshot.rules:
            if _BUCKET_ALIASES.get(rule.document_type.strip().lower()) == normalized_bucket:
                return rule
    return None
