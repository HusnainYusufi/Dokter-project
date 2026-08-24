"""Prompt assembly and rule matching."""
from __future__ import annotations

from app.schemas.rules import (
    DocumentRule,
    OpinionTemplate,
    RuleAction,
    RuleConfigSnapshot,
)
from app.services.extraction.prompts import (
    OPINION_SYSTEM_PROMPT,
    PAGE_PARSE_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
)
from app.services.rules.prompt_builder import (
    build_opinion_prompt,
    build_page_parse_prompt,
    build_summary_prompt,
    rule_for_document,
)

GOLDEN = "Plain text only. Never infer."


def snapshot(**overrides) -> RuleConfigSnapshot:
    payload = {
        "id": "cfg_test",
        "name": "Test",
        "version": 3,
        "golden_rule_prompt": GOLDEN,
        "rules": [],
    }
    payload.update(overrides)
    return RuleConfigSnapshot(**payload)


def rule(document_type: str, **overrides) -> DocumentRule:
    payload = {"document_type": document_type}
    payload.update(overrides)
    return DocumentRule(**payload)


def test_without_a_snapshot_the_builtin_prompts_are_unchanged():
    assert build_page_parse_prompt(None) == PAGE_PARSE_SYSTEM_PROMPT
    assert build_summary_prompt(None) == SUMMARY_SYSTEM_PROMPT
    assert build_opinion_prompt(None) == OPINION_SYSTEM_PROMPT


def test_the_golden_rule_prompt_prefixes_every_stage():
    config = snapshot()
    for prompt in (
        build_page_parse_prompt(config),
        build_summary_prompt(config),
        build_opinion_prompt(config),
    ):
        assert prompt.startswith(GOLDEN)


def test_builtin_bucket_rules_do_not_create_a_custom_types_block():
    config = snapshot(rules=[rule("imaging"), rule("pathology", action=RuleAction.SKIP)])
    assert "CUSTOM DOCUMENT TYPES" not in build_page_parse_prompt(config)


def test_custom_types_are_taught_to_the_page_parser():
    config = snapshot(
        rules=[rule("Referral Form", match_prompt="Forms addressed to the consultant.")]
    )
    prompt = build_page_parse_prompt(config)

    assert "CUSTOM DOCUMENT TYPES" in prompt
    assert '"Referral Form": Forms addressed to the consultant.' in prompt


def test_custom_type_without_a_description_still_lists_the_label():
    config = snapshot(rules=[rule("Widget Sheet")])
    assert '"Widget Sheet": No description provided.' in build_page_parse_prompt(config)


def test_summary_prompt_lists_instructions_for_non_skipped_rules():
    config = snapshot(
        rules=[
            rule("imaging", instruction_prompt="Impression only."),
            rule("pathology", action=RuleAction.SKIP, instruction_prompt="Never summarize."),
        ]
    )
    prompt = build_summary_prompt(config)

    assert "DOCUMENT-TYPE RULES" in prompt
    assert '"imaging": Impression only.' in prompt
    # A skipped type never reaches the summarizer, so its text is not sent.
    assert "Never summarize." not in prompt


def test_full_data_rules_explain_the_full_text_field():
    """summary.py puts a `full_text` field on entries governed by a full_data
    rule; the prompt has to say what it is or the action under-delivers."""
    config = snapshot(rules=[rule("Operative report", action=RuleAction.FULL_DATA)])
    prompt = build_summary_prompt(config)

    assert "FULL TEXT ENTRIES" in prompt
    assert "`full_text`" in prompt


def test_without_a_full_data_rule_the_full_text_note_is_omitted():
    config = snapshot(rules=[rule("imaging", instruction_prompt="Impression only.")])
    assert "FULL TEXT ENTRIES" not in build_summary_prompt(config)


def test_presentation_is_additive_and_keeps_the_builtin_rules():
    """Presentation steers how the summary reads without discarding the
    extraction rules - that is the whole point of it existing next to the
    all-or-nothing summary override."""
    config = snapshot(summary_presentation="One paragraph per document, oldest first.")
    prompt = build_summary_prompt(config)

    assert "SUMMARY PRESENTATION" in prompt
    assert "One paragraph per document, oldest first." in prompt
    # The built-in extraction discipline is still present.
    assert "Summarize, do not transcribe." in prompt


def test_presentation_also_applies_on_top_of_an_override():
    config = snapshot(
        summary_prompt="Only summarize imaging.",
        summary_presentation="Newest entry first.",
    )
    prompt = build_summary_prompt(config)

    assert "Only summarize imaging." in prompt
    assert "Newest entry first." in prompt


def test_an_empty_presentation_field_falls_back_to_the_shipped_wording():
    """Presentation lives in exactly one place. The built-in body carries none
    of its own, so a configuration that leaves the field empty still has to be
    told how the summary reads."""
    prompt = build_summary_prompt(snapshot())

    assert "SUMMARY PRESENTATION" in prompt
    assert "HOW EACH PARAGRAPH OPENS" in prompt


def test_the_builtin_body_no_longer_dictates_presentation():
    """The built-in prompt used to hardcode the opening format and per-type
    word budgets, which meant the Presentation tab and the per-type opt-in
    could never actually change them."""
    from app.services.extraction.prompts import SUMMARY_SYSTEM_PROMPT

    assert "March 01, 2023 attending physician statement" not in SUMMARY_SYSTEM_PROMPT
    assert "25 to 50 words" not in SUMMARY_SYSTEM_PROMPT
    assert "varied connector verbs" not in SUMMARY_SYSTEM_PROMPT
    # The extraction discipline it does own is untouched.
    assert "Summarize, do not transcribe." in SUMMARY_SYSTEM_PROMPT


def test_a_configured_presentation_replaces_the_fallback_entirely():
    """Otherwise the shipped wording would compete with the operator's."""
    prompt = build_summary_prompt(snapshot(summary_presentation="Newest entry first."))

    assert "Newest entry first." in prompt
    assert "HOW EACH PARAGRAPH OPENS" not in prompt


def test_an_opted_in_type_replaces_rather_than_supplements_the_presentation():
    config = snapshot(
        summary_presentation="One paragraph per document.",
        rules=[rule("imaging", override_presentation=True, presentation_prompt="One line.")],
    )
    prompt = build_summary_prompt(config)

    assert "REPLACES" in prompt
    assert "not as well" in prompt


def test_prompt_overrides_replace_the_builtin_body_but_keep_golden_rules():
    config = snapshot(summary_prompt="Only summarize imaging.", opinion_prompt="Only opine.")

    summary = build_summary_prompt(config)
    assert summary.startswith(GOLDEN)
    assert "Only summarize imaging." in summary
    assert "Summarize, do not transcribe." not in summary

    opinion = build_opinion_prompt(config)
    assert "Only opine." in opinion


def test_each_opinion_template_adds_its_own_instructions():
    critical = build_opinion_prompt(snapshot(opinion_template=OpinionTemplate.CRITICAL_ILLNESS))
    assert "`definition` field" in critical

    underwriting = build_opinion_prompt(snapshot(opinion_template=OpinionTemplate.UNDERWRITING))
    assert "underwriting risk" in underwriting


def test_a_custom_type_match_wins_over_the_bucket():
    config = snapshot(
        rules=[
            rule("clinical", instruction_prompt="Clinical handling."),
            rule("Referral Form", action=RuleAction.SKIP, use_as_context=True),
        ]
    )

    matched = rule_for_document(config, custom_type="Referral Form", bucket="clinical")
    assert matched.document_type == "Referral Form"
    assert matched.action == RuleAction.SKIP


def test_matching_is_case_insensitive_and_falls_back_to_the_bucket():
    config = snapshot(rules=[rule("IMAGING", max_words=40)])

    assert rule_for_document(config, custom_type="imaging", bucket="unknown").max_words == 40
    assert rule_for_document(config, custom_type=None, bucket="imaging").max_words == 40


def test_the_admin_bucket_alias_matches_the_administrative_rule():
    config = snapshot(rules=[rule("administrative", action=RuleAction.SKIP)])
    assert rule_for_document(config, custom_type=None, bucket="admin").action == RuleAction.SKIP


def test_an_unmatched_document_has_no_rule():
    config = snapshot(rules=[rule("imaging")])
    assert rule_for_document(config, custom_type=None, bucket="clinical") is None
    assert rule_for_document(None, custom_type="imaging", bucket="imaging") is None


def test_the_catch_all_rule_governs_anything_unmatched():
    """Nothing should escape a configuration: a rule on the catch-all type
    applies to whatever matched no other rule."""
    config = snapshot(
        rules=[
            rule("imaging", max_words=50),
            rule("Other", instruction_prompt="Get the date and the document kind."),
        ]
    )

    assert rule_for_document(config, custom_type=None, bucket="imaging").document_type == "imaging"
    assert rule_for_document(config, custom_type=None, bucket="unknown").document_type == "Other"
    assert rule_for_document(config, custom_type="Unheard Of", bucket="unknown").document_type == "Other"


def test_the_catch_all_is_not_offered_to_the_parser_as_a_detectable_type():
    """It is resolved downstream, so asking the model to tag it would be both
    redundant and a way for documents to be mis-tagged into it."""
    config = snapshot(rules=[rule("Other", match_prompt="Anything else.")])
    assert "CUSTOM DOCUMENT TYPES" not in build_page_parse_prompt(config)


def test_without_a_catch_all_rule_an_unmatched_document_still_has_no_rule():
    config = snapshot(rules=[rule("imaging")])
    assert rule_for_document(config, custom_type=None, bucket="unknown") is None


def test_per_type_presentation_only_applies_when_the_type_opts_in():
    """max_words and the presentation prompt are inert unless the rule opted
    into presenting that type differently."""
    config = snapshot(
        summary_presentation="One paragraph per document.",
        rules=[
            rule("imaging", override_presentation=True, presentation_prompt="Impression only, one line."),
            rule("clinical", presentation_prompt="Ignored, did not opt in."),
        ],
    )
    prompt = build_summary_prompt(config)

    assert "PER-TYPE PRESENTATION" in prompt
    assert '"imaging": Impression only, one line.' in prompt
    assert "Ignored, did not opt in." not in prompt
    # The configuration presentation still governs everything else.
    assert "One paragraph per document." in prompt


def test_no_per_type_presentation_block_without_an_opted_in_rule():
    config = snapshot(rules=[rule("imaging", presentation_prompt="Not opted in.")])
    assert "PER-TYPE PRESENTATION" not in build_summary_prompt(config)
