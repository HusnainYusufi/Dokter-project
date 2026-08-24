"""Derived extraction quality.

Self-reported model confidence is poorly calibrated and tells a reviewer
nothing about what to check. These signals are observable facts about the
parse, and each one names the page and the problem.
"""
from __future__ import annotations

from app.services.extraction.models import (
    AuthorFingerprint,
    DocumentFingerprint,
    DocumentSegment,
    EvidenceItem,
    PageMarker,
    ParsedPage,
)
from app.services.extraction.quality import REVIEW_THRESHOLD, assess


def page(
    number: int,
    *,
    evidence: int = 3,
    index: int = 0,
    total: int = 0,
    provenance: str = "primary",
) -> ParsedPage:
    return ParsedPage(
        page_number=number,
        page_marker=PageMarker(index=index, total=total),
        page_kind="imaging",
        evidence=[
            EvidenceItem(kind="finding", text=f"finding {number}-{i}", provenance=provenance)  # type: ignore[arg-type]
            for i in range(evidence)
        ],
        document=DocumentFingerprint(bucket="imaging"),
    )


def doc(
    *pages: ParsedPage,
    date: str | None = "April 28, 2022",
    author: str | None = "Waslen",
    title: str | None = "SINUSES",
) -> DocumentSegment:
    return DocumentSegment(
        id="d",
        pages=list(pages) or [page(1)],
        bucket="imaging",
        date=date,
        title=title,
        author=AuthorFingerprint(name=author),
        include_in_output=True,
    )


def test_a_clean_document_scores_full_marks_and_needs_no_review():
    quality = assess(doc())

    assert quality.score == 1.0
    assert quality.reasons == []
    assert not quality.needs_review


def test_a_missing_author_is_named_rather_than_just_scored():
    quality = assess(doc(author=None))

    assert "no author was identified" in quality.reasons
    assert quality.score < 1.0


def test_an_unparsable_date_reports_the_string_it_could_not_read():
    quality = assess(doc(date="Rev. 2021-09"))

    assert any("did not parse" in reason for reason in quality.reasons)


def test_a_missing_date_and_author_together_push_it_under_review():
    quality = assess(doc(date=None, author=None))

    assert quality.needs_review
    assert len(quality.reasons) == 2


def test_pages_that_yielded_nothing_are_the_heaviest_single_signal():
    quality = assess(doc(page(1, evidence=0), page(2, evidence=0)))

    assert "no evidence was captured from these pages" in quality.reasons


def test_thin_evidence_relative_to_page_count_is_flagged():
    quality = assess(doc(page(1, evidence=1), page(2, evidence=0), page(3, evidence=0)))

    assert any("evidence items across" in reason for reason in quality.reasons)


def test_an_entry_with_nothing_first_hand_is_flagged():
    """The shape of an index row read as a report: it holds text, but every
    item of it is a recital or a pointer."""
    quality = assess(doc(page(1, evidence=3, provenance="index")))

    assert "nothing here is first-hand; it recites or points at other records" in quality.reasons


def test_a_document_missing_pages_of_its_own_printed_run_is_flagged():
    """Printed 'of 5' but only three pages were gathered - two went elsewhere."""
    quality = assess(doc(page(1, index=1, total=5), page(2, index=2, total=5), page(3, index=3, total=5)))

    assert "its printed pagination does not match the pages gathered here" in quality.reasons


def test_a_complete_printed_run_is_not_flagged():
    quality = assess(doc(page(1, index=1, total=2), page(2, index=2, total=2)))

    assert quality.reasons == []


def test_pages_without_markers_raise_no_pagination_concern():
    quality = assess(doc(page(1), page(2), page(3)))

    assert not any("pagination" in reason for reason in quality.reasons)


def test_the_score_never_goes_below_zero():
    quality = assess(
        doc(page(1, evidence=0), date=None, author=None, title=None)
    )

    assert quality.score >= 0.0
    assert quality.needs_review


def test_one_ordinary_gap_does_not_trip_the_review_threshold():
    """Plenty of legitimate forms are unsigned. A single missing signal should
    inform a reviewer, not bury them in flags."""
    quality = assess(doc(author=None))

    assert quality.score >= REVIEW_THRESHOLD
    assert not quality.needs_review
