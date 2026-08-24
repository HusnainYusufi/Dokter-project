"""Provenance: what a document states versus what it merely mentions.

A medical record constantly refers to findings it did not produce. Treating a
mention as a finding is what let a row of an EMR results index become an
imaging report with an impression of its own. This types the distinction so it
survives into the summarizer instead of being lost at the parse boundary.
"""
from __future__ import annotations

import pytest

from app.services.extraction.models import EvidenceItem
from app.services.extraction.parser import _provenance
from app.services.extraction.summary import _dedupe_evidence


def item(text: str, provenance: str = "primary", kind: str = "finding") -> EvidenceItem:
    return EvidenceItem(kind=kind, text=text, provenance=provenance)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("primary", "primary"),
        ("historical", "historical"),
        ("referenced", "referenced"),
        ("index", "index"),
        ("INDEX", "index"),
        ("  referenced  ", "referenced"),
    ],
)
def test_the_parser_accepts_every_provenance(raw, expected):
    assert _provenance(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "nonsense", 7, {"a": 1}])
def test_an_unrecognized_provenance_falls_back_to_primary(raw):
    """An unknown value must never silently demote real content out of the
    summary - that would lose findings rather than mislabel them."""
    assert _provenance(raw) == "primary"


def test_only_a_primary_item_is_first_hand():
    assert item("Chest is clear.").is_first_hand
    for provenance in ("historical", "referenced", "index"):
        assert not item("x", provenance).is_first_hand


def test_evidence_captured_before_the_field_existed_stays_primary():
    assert EvidenceItem(kind="finding", text="x").provenance == "primary"


def test_the_payload_marks_only_the_items_that_are_not_first_hand():
    """Saying `primary` on every ordinary item is noise the model reads past;
    the exception is what has to be visible."""
    payload = _dedupe_evidence(
        [
            item("Lungs are hyperinflated but clear."),
            item("Tested positive for COVID-19 on April 11.", "historical"),
            item("See attached screening form.", "referenced"),
            item("28Apr22 Normal Chao , Claire X-Ray Sinuses/Chest", "index"),
        ]
    )

    assert "provenance" not in payload[0]
    assert payload[1]["provenance"] == "historical"
    assert payload[2]["provenance"] == "referenced"
    assert payload[3]["provenance"] == "index"


def test_the_verbatim_text_of_a_pointer_is_still_carried():
    """A pointer is demoted, never dropped: the reviewer still needs to know
    the record referred to something that is not in the file."""
    payload = _dedupe_evidence([item("COMPARISON: April 20, 2022", "referenced")])

    assert payload[0]["text"] == "COMPARISON: April 20, 2022"


def test_deduplication_still_collapses_repeats():
    payload = _dedupe_evidence([item("Chest is clear."), item("chest is clear.")])
    assert len(payload) == 1
