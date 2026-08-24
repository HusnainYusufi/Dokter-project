"""Cross-entry contradiction detection.

The case this exists for: an EMR results-index row became an imaging report
asserting "an impression of Normal" for a 28 Apr 2022 study, shipped alongside
the real report for the same study, which found opacification, a calvarial
lucency, and a T12 compression. Nothing in the pipeline could see both at once.
"""
from __future__ import annotations

import pytest

from app.schemas.extraction import SummaryParagraph
from app.services.extraction.consistency import find_contradictions


def entry(text: str, number: int = 1, page: int = 1, kind: str = "imaging") -> SummaryParagraph:
    return SummaryParagraph(
        text=text,
        page_start=page,
        page_end=page,
        document_number=number,
        registered_type=kind,
    )


REAL_28APR = (
    "April 28, 2022 imaging report by Dr. Waslen for sinuses/chest states partial "
    "opacification of the inferior third of the left maxillary sinus with an apparent "
    "fluid level, and a focal lucency at the superior calvarium measuring 1.5 cm."
)
INDEX_ROW_28APR = (
    "April 28, 2022 imaging report by Claire Chao documents an X-ray of the "
    "sinuses/chest with an impression of Normal."
)


def test_the_false_normal_is_caught_against_the_real_report():
    warnings = find_contradictions([entry(INDEX_ROW_28APR, 6, 10), entry(REAL_28APR, 7, 11)])

    assert len(warnings) == 1
    warning = warnings[0]
    assert warning.kind == "contradictory_imaging"
    assert warning.document_numbers == [6, 7]
    assert warning.page_ranges == ["10-10", "11-11"]
    assert "normal" in warning.detail.lower()


def test_the_order_of_the_entries_does_not_matter():
    forward = find_contradictions([entry(INDEX_ROW_28APR, 6, 10), entry(REAL_28APR, 7, 11)])
    backward = find_contradictions([entry(REAL_28APR, 7, 11), entry(INDEX_ROW_28APR, 6, 10)])

    assert len(forward) == len(backward) == 1


def test_studies_on_different_dates_are_not_compared():
    other_day = INDEX_ROW_28APR.replace("April 28, 2022", "May 07, 2022")
    assert find_contradictions([entry(other_day, 5, 10), entry(REAL_28APR, 7, 11)]) == []


def test_different_body_regions_on_one_date_are_not_a_contradiction():
    """A normal knee and an abnormal chest on the same day is ordinary."""
    knee = "April 28, 2022 imaging report of the knee reports the impression as normal."
    assert find_contradictions([entry(knee, 5, 10), entry(REAL_28APR, 7, 11)]) == []


def test_two_normal_studies_are_not_a_contradiction():
    second = INDEX_ROW_28APR.replace("Claire Chao", "Dr. Someone")
    assert find_contradictions([entry(INDEX_ROW_28APR, 5, 10), entry(second, 6, 11)]) == []


def test_a_normal_incidental_finding_inside_an_abnormal_report_is_not_a_contradiction():
    """Real reports routinely say a structure is within normal limits while
    reporting a finding elsewhere. Flagging those would bury the real signal."""
    mixed = (
        "April 28, 2022 imaging report of the chest reports cardiac and mediastinal "
        "contours within normal limits, with moderate anterior compression of T12."
    )
    assert find_contradictions([entry(mixed, 5, 11), entry(REAL_28APR, 7, 12)]) == []


def test_non_imaging_entries_are_left_alone():
    clinical = INDEX_ROW_28APR
    assert (
        find_contradictions(
            [entry(clinical, 5, 10, kind="clinical"), entry(REAL_28APR, 7, 11, kind="clinical")]
        )
        == []
    )


def test_placeholders_are_never_compared():
    placeholder = SummaryParagraph(
        text=INDEX_ROW_28APR,
        page_start=10,
        page_end=10,
        registered_type="imaging",
        is_placeholder=True,
    )
    assert find_contradictions([placeholder, entry(REAL_28APR, 7, 11)]) == []


def test_an_entry_that_does_not_open_with_a_date_is_skipped():
    undated = "Imaging report of the sinuses/chest with an impression of Normal."
    assert find_contradictions([entry(undated, 5, 10), entry(REAL_28APR, 7, 11)]) == []


@pytest.mark.parametrize(
    "negative",
    [
        "April 28, 2022 chest imaging reports no acute abnormality.",
        "April 28, 2022 chest imaging is unremarkable.",
        "April 28, 2022 chest imaging shows no acute process.",
    ],
)
def test_the_common_ways_of_reporting_nothing_found_are_recognized(negative):
    abnormal = "April 28, 2022 chest imaging shows a subacute ninth rib fracture."
    assert len(find_contradictions([entry(negative, 5, 10), entry(abnormal, 7, 11)])) == 1
