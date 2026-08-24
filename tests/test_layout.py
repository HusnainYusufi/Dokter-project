"""Document-structure analysis.

Two failures motivate this, both from a vision model inferring structure it
could not see. An EMR results index on page 10 became two imaging reports with
impressions of their own, and a "Deliver To" name on page 12 was credited as the
report's author. A layout service returns tables as tables and labelled fields
with their labels, so neither has to be guessed.
"""
from __future__ import annotations

import pytest

from app.services.layout import get_layout_provider, layout_enabled
from app.services.layout.base import (
    LayoutField,
    LayoutTable,
    NoLayoutProvider,
    PageLayout,
)
from app.services.layout.providers import from_textract
from app.services.layout.render import index_evidence_rows, render_layout_block

# The table at the foot of page 10 of the reference file. Both rows became
# imaging reports, and the 28Apr22 "Normal" contradicted the real report on
# page 11, which found opacification and a calvarial lucency.
RESULTS_INDEX = LayoutTable(
    headers=["Date", "Status", "Provider", "Type", "Exam"],
    rows=[
        ["Date", "Status", "Provider", "Type", "Exam"],
        ["07May22", "Normal", "Pask , Leane", "X-Ray", "X-Ray, Chest"],
        ["28Apr22", "Normal", "Chao , Claire", "X-Ray", "X-Ray, Sinuses/Chest"],
    ],
    page_number=10,
)

# The header of page 12: the report's author is Adarsh Patel, who appears on a
# dictating line, not in any of these.
PAGE_12_FIELDS = [
    LayoutField("Deliver To", "Pask,Leane Norma", 12),
    LayoutField("Ordering Physician", "Beny,Majak Takpiny", 12),
    LayoutField("Family Physican", "Pask,Leane Norma", 12),
    LayoutField("Patient Type", "Emergency", 12),
    LayoutField("Accession Number", "DX-22-0148207", 12),
]


def test_layout_is_off_unless_a_provider_is_configured():
    """It sends page images to another processor. That is a decision to make
    deliberately where the pages are medical records."""
    assert layout_enabled() is False
    assert isinstance(get_layout_provider(), NoLayoutProvider)


@pytest.mark.anyio
async def test_the_no_op_provider_reports_that_nothing_ran():
    """Distinct from a service running and finding nothing."""
    layout = await NoLayoutProvider().analyze(1, b"")

    assert layout.analyzed is False
    assert layout.is_informative is False


def test_a_page_with_no_structure_renders_no_block():
    """A block saying "nothing found" spends tokens teaching the model to skip
    a section."""
    assert render_layout_block(PageLayout(page_number=1)) == ""
    assert render_layout_block(PageLayout(page_number=1, analyzed=False)) == ""


def test_a_results_table_is_named_an_index_and_its_rows_are_shown():
    block = render_layout_block(PageLayout(page_number=10, tables=[RESULTS_INDEX]))

    assert "TABLES" in block
    assert "INDEX of material held elsewhere" in block
    assert "never create a document from one" in block
    # The status word is called out by name, since reporting it as an
    # impression is what asserted a normal result for an abnormal study.
    assert "never read a status word" in block
    assert 'provenance "index"' in block
    assert "28Apr22 | Normal | Chao , Claire" in block


def test_routing_fields_are_separated_from_the_rest_and_disclaimed():
    block = render_layout_block(PageLayout(page_number=12, fields=PAGE_12_FIELDS))

    assert "ROUTING FIELDS" in block
    assert "NONE of them is the author" in block
    # Routing labels sit in the routing section...
    routing_section = block.split("ROUTING FIELDS")[1].split("LABELLED FIELDS")[0]
    assert "Deliver To" in routing_section
    assert "Ordering Physician" in routing_section
    assert "Family Physican" in routing_section  # as misspelled on the page
    # ...and ordinary fields do not.
    assert "Patient Type" not in routing_section


@pytest.mark.parametrize(
    "label",
    ["Ordering Physician", "Deliver To", "Referred by", "Copy to", "Attention", "Provider:"],
)
def test_the_routing_labels_are_matched_loosely(label):
    """Every EMR words them slightly differently."""
    block = render_layout_block(PageLayout(page_number=1, fields=[LayoutField(label, "A Name", 1)]))
    assert "ROUTING FIELDS" in block


def test_an_ordinary_field_is_not_treated_as_routing():
    block = render_layout_block(
        PageLayout(page_number=1, fields=[LayoutField("Exam Date", "28 Apr 2022", 1)])
    )
    assert "ROUTING FIELDS" not in block
    assert "LABELLED FIELDS" in block


def test_table_rows_become_index_evidence_deterministically():
    """A row of a listing is a pointer, and that must not depend on a
    judgement call that has already been shown to go wrong."""
    rows = index_evidence_rows(PageLayout(page_number=10, tables=[RESULTS_INDEX]))

    assert "28Apr22 | Normal | Chao , Claire | X-Ray | X-Ray, Sinuses/Chest" in rows
    assert len(rows) == 3


def test_an_empty_table_yields_nothing():
    empty = LayoutTable(rows=[["", ""], ["", ""]], page_number=1)
    assert empty.is_empty
    assert index_evidence_rows(PageLayout(page_number=1, tables=[empty])) == []
    assert render_layout_block(PageLayout(page_number=1, tables=[empty])) == ""


def test_a_long_table_is_truncated_with_the_remainder_named():
    big = LayoutTable(rows=[[f"row {i}", "x"] for i in range(60)], page_number=1)
    block = render_layout_block(PageLayout(page_number=1, tables=[big]))

    assert "60 row(s)" in block
    assert "35 further row(s) not shown" in block


# --------------------------------------------------------------------------
# Response mapping, tested without a client.


def test_textract_tables_and_forms_are_mapped():
    response = {
        "Blocks": [
            {"Id": "t1", "BlockType": "TABLE", "Relationships": [{"Type": "CHILD", "Ids": ["c1", "c2"]}]},
            {
                "Id": "c1",
                "BlockType": "CELL",
                "RowIndex": 1,
                "ColumnIndex": 1,
                "EntityTypes": ["COLUMN_HEADER"],
                "Relationships": [{"Type": "CHILD", "Ids": ["w1"]}],
            },
            {
                "Id": "c2",
                "BlockType": "CELL",
                "RowIndex": 1,
                "ColumnIndex": 2,
                "Relationships": [{"Type": "CHILD", "Ids": ["w2"]}],
            },
            {"Id": "w1", "BlockType": "WORD", "Text": "Date"},
            {"Id": "w2", "BlockType": "WORD", "Text": "Status"},
            {
                "Id": "k1",
                "BlockType": "KEY_VALUE_SET",
                "EntityTypes": ["KEY"],
                "Relationships": [
                    {"Type": "CHILD", "Ids": ["w3"]},
                    {"Type": "VALUE", "Ids": ["v1"]},
                ],
            },
            {"Id": "w3", "BlockType": "WORD", "Text": "Ordering"},
            {
                "Id": "v1",
                "BlockType": "KEY_VALUE_SET",
                "EntityTypes": ["VALUE"],
                "Relationships": [{"Type": "CHILD", "Ids": ["w4"]}],
            },
            {"Id": "w4", "BlockType": "WORD", "Text": "Beny"},
        ]
    }
    layout = from_textract(response, page_number=12)

    assert layout.tables[0].rows == [["Date", "Status"]]
    assert layout.tables[0].headers == ["Date"]
    assert layout.fields[0].label == "Ordering"
    assert layout.fields[0].value == "Beny"


def test_textract_selected_checkboxes_are_captured():
    """A tick is often the entire content of a functional form field."""
    response = {
        "Blocks": [
            {"Id": "t1", "BlockType": "TABLE", "Relationships": [{"Type": "CHILD", "Ids": ["c1"]}]},
            {
                "Id": "c1",
                "BlockType": "CELL",
                "RowIndex": 1,
                "ColumnIndex": 1,
                "Relationships": [{"Type": "CHILD", "Ids": ["s1"]}],
            },
            {"Id": "s1", "BlockType": "SELECTION_ELEMENT", "SelectionStatus": "SELECTED"},
        ]
    }
    assert from_textract(response, page_number=1).tables[0].rows == [["[X]"]]


def test_an_empty_textract_response_maps_cleanly():
    layout = from_textract({"Blocks": []}, page_number=1)
    assert layout.tables == [] and layout.fields == []
    assert layout.is_informative is False


@pytest.mark.parametrize(
    ("label", "routing"),
    [
        # The reference page prints this misspelled; exact matching missed it.
        ("Family Physican", True),
        ("Family Physician", True),
        # "cc" is a routing label, but not inside another word. This was a real
        # false positive: "a-cc-ession" matched a bare substring check.
        ("Accession Number", False),
        ("CC:", True),
        # Close but not the same word.
        ("Provided care", False),
        ("Provider", True),
        ("Patient Type", False),
        ("Exam Date/Time", False),
        ("", False),
    ],
)
def test_routing_matching_is_whole_word_and_tolerates_one_ocr_slip(label, routing):
    from app.services.layout.render import _is_routing

    assert _is_routing(label) is routing
