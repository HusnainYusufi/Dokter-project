"""Which way round a numeric date is written.

"05/04/2022" is May 4th to a North American insurer and the 4th of May to most
of the rest of the world. Before this, the same date written two ways parsed to
two different days: "05/04/66" came back May 04 and "05-04-2022" April 05,
because the separator decided which format string was tried first. A file that
orders its clinical narrative by date cannot have that.
"""
from __future__ import annotations

import pytest

from app.services.extraction.date_convention import (
    infer_convention,
    is_ambiguous,
)
from app.services.extraction.header import canonical_date_iso, normalize_date


@pytest.mark.parametrize(
    ("raw", "month_first", "day_first"),
    [
        ("05/04/66", "May 04, 1966", "April 05, 1966"),
        ("05-04-2022", "May 04, 2022", "April 05, 2022"),
        ("11/12/2022", "November 12, 2022", "December 11, 2022"),
        ("3/4/2022", "March 04, 2022", "April 03, 2022"),
    ],
)
def test_the_separator_no_longer_decides_the_day(raw, month_first, day_first):
    assert normalize_date(raw) == month_first
    assert normalize_date(raw, day_first=True) == day_first


@pytest.mark.parametrize(
    "raw",
    [
        "13/04/2022",  # 13 cannot be a month
        "2023-03-01",  # ISO, year first
        "26-May-22 15:32 CST",  # named month
        "29Jul22",
        "Nov 23/22",
    ],
)
def test_an_unambiguous_date_reads_the_same_either_way(raw):
    assert normalize_date(raw) == normalize_date(raw, day_first=True)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("05/04/2022", True),
        ("11/12/22", True),
        ("13/04/2022", False),  # 13 settles it
        ("04/13/2022", False),
        ("2022-05-04", False),  # ISO
        ("May 04, 2022", False),  # not numeric
        ("", False),
    ],
)
def test_ambiguity_is_recognized(raw, expected):
    assert is_ambiguous(raw) is expected


def test_a_day_above_the_twelfth_settles_the_whole_file():
    convention = infer_convention(["05/04/2022", "13/04/2022", "11/12/2022"])

    assert convention.day_first is True
    assert "above the twelfth" in convention.evidence


def test_a_month_position_above_the_twelfth_settles_it_the_other_way():
    convention = infer_convention(["05/04/2022", "04/13/2022"])

    assert convention.day_first is False


def test_a_file_that_contradicts_itself_resolves_nothing():
    """A merged bundle can carry both conventions. Applying either to the
    ambiguous remainder would be wrong for half of them."""
    convention = infer_convention(["13/04/2022", "04/13/2022"])

    assert convention.day_first is None
    assert "contradicts itself" in convention.evidence


def test_a_file_with_no_evidence_resolves_nothing():
    assert infer_convention(["05/04/2022", "11/12/2022"]).day_first is None
    assert infer_convention([]).day_first is None
    assert infer_convention([None, ""]).day_first is None


def test_named_months_and_iso_dates_cast_no_vote():
    """They are unambiguous themselves and say nothing about how the file
    writes its numeric dates."""
    assert infer_convention(["May 04, 2022", "2022-05-04", "29Jul22"]).day_first is None


def test_the_convention_reaches_the_canonical_form_too():
    """Grouping keys on canonical_date_iso, so it must agree with the display
    form or a document splits from itself."""
    assert canonical_date_iso("05/04/2022") == "2022-05-04"
    assert canonical_date_iso("05/04/2022", day_first=True) == "2022-04-05"


def test_a_year_first_date_is_never_treated_as_ambiguous():
    assert normalize_date("2022-05-04", day_first=True) == "May 04, 2022"
