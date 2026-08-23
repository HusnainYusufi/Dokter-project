"""Rule-driven summary behavior and the capture certification."""
from __future__ import annotations

import pytest

from app.schemas.rules import DocumentRuleInput, RuleAction, RuleConfigCreate
from app.services.extraction.formatting import spell_number
from app.services.extraction.header import normalize_date
from app.services.extraction.models import (
    DocumentFingerprint,
    DocumentSegment,
    EvidenceItem,
    ParsedPage,
    PatientBundle,
)
from app.services.extraction.opinion import _build_assignment_context
from app.services.extraction.summary import build_capture_statement, build_summary


def segment(page_number: int, bucket: str = "clinical", custom_type: str | None = None):
    page = ParsedPage(
        page_number=page_number,
        page_kind=bucket if bucket != "administrative" else "admin",
        evidence=[EvidenceItem(kind="finding", text=f"finding {page_number}")],
        document=DocumentFingerprint(bucket=bucket, date="May 1, 2024", custom_type=custom_type),
        markdown=f"Full text of page {page_number}",
    )
    return DocumentSegment(
        id=f"doc-{page_number}",
        pages=[page],
        bucket=bucket,
        custom_type=custom_type,
        date="May 1, 2024",
        include_in_output=True,
    )


def bundle(*segments) -> PatientBundle:
    return PatientBundle(id="p1", key="key", name="Claimant", documents=list(segments))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, "one"),
        (21, "twenty-one"),
        (100, "one hundred"),
        (338, "three hundred and thirty-eight"),
        (1024, "one thousand and twenty-four"),
        (12000, "12000"),
    ],
)
def test_page_counts_are_spelled_out(value, expected):
    assert spell_number(value) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2023-03-01", "March 01, 2023"),
        ("Sep 8 2022", "September 08, 2022"),
        ("29Jul22", "July 29, 2022"),
    ],
)
def test_dates_render_with_a_zero_padded_day(raw, expected):
    assert normalize_date(raw) == expected


def test_capture_statement_certifies_the_page_count():
    statement = build_capture_statement(bundle(segment(1)), file_page_count=338)
    assert statement.startswith("Three hundred and thirty-eight pages have been provided.")
    assert statement.endswith("All information has been reviewed and summarized below as necessary.")


def test_capture_statement_names_the_claimant_pages_when_the_file_holds_several():
    statement = build_capture_statement(
        bundle(segment(4), segment(9)), file_page_count=50, patient_count=3
    )
    assert "Pages 4 to 9 relate to this claimant." in statement


def test_capture_statement_is_empty_without_a_page_count():
    assert build_capture_statement(bundle(segment(1)), file_page_count=0) == ""


@pytest.mark.anyio
async def test_default_rules_skip_pathology_and_summarize_the_rest(seeded_store):
    snapshot = seeded_store.resolve_snapshot(None)
    paragraphs, _ = await build_summary(
        bundle(segment(1, "clinical"), segment(2, "pathology")), rule_config=snapshot
    )

    by_type = {paragraph.document_type: paragraph for paragraph in paragraphs}
    assert by_type["pathology"].is_lab is True
    assert by_type["clinical"].is_lab is False
    # Both documents still appear as numbered cards.
    assert {paragraph.document_number for paragraph in paragraphs} == {1, 2}


@pytest.mark.anyio
async def test_a_skip_rule_turns_a_summarized_type_into_a_placeholder(rule_store):
    config = rule_store.create_config(
        RuleConfigCreate(
            name="Skip imaging",
            rules=[DocumentRuleInput(document_type="imaging", action=RuleAction.SKIP)],
        )
    )
    snapshot = rule_store.resolve_snapshot(config.id)

    paragraphs, _ = await build_summary(bundle(segment(1, "imaging")), rule_config=snapshot)

    assert len(paragraphs) == 1
    assert paragraphs[0].is_lab is True


@pytest.mark.anyio
async def test_a_custom_type_rule_can_feed_the_opinion_as_context(rule_store):
    config = rule_store.create_config(
        RuleConfigCreate(
            name="Referral context",
            rules=[
                DocumentRuleInput(
                    document_type="Referral Form",
                    action=RuleAction.SKIP,
                    use_as_context=True,
                )
            ],
        )
    )
    snapshot = rule_store.resolve_snapshot(config.id)
    patient = bundle(segment(2, "clinical", custom_type="Referral Form"))

    context = _build_assignment_context(patient, snapshot)
    assert "Full text of page 2" in context

    # Without the rule the same clinical document is not assignment context.
    assert _build_assignment_context(patient, None) == ""
