"""The user message sent with each page batch.

This exists because a NameError reached production. The layout block was added
to a function that had no `layouts` in scope, and nothing caught it: the prompt
was assembled inside the same function that called the model, so exercising it
needed API keys and no test ever did.

Assembling the message is now pure and asserted here. Every branch of it - the
text layer, the layout block, both, neither - is reachable without a network.
"""
from __future__ import annotations

import pytest

from app.services.extraction.parser import build_parse_user_text
from app.services.layout.base import LayoutField, LayoutTable, PageLayout

RESULTS_INDEX = PageLayout(
    page_number=10,
    tables=[
        LayoutTable(
            rows=[["28Apr22", "Normal", "Chao , Claire", "X-Ray", "X-Ray, Sinuses/Chest"]],
            page_number=10,
        )
    ],
)


def test_the_batch_is_described_and_the_page_numbers_named():
    text = build_parse_user_text([4, 5, 6], image_count=3)

    assert "3 PDF page image(s)" in text
    assert "[4, 5, 6]" in text
    assert "EXACTLY ONE entry per page" in text


def test_with_neither_extra_the_message_is_just_the_instructions():
    text = build_parse_user_text([1], image_count=1)

    assert "EMBEDDED TEXT LAYER" not in text
    assert "DETECTED LAYOUT" not in text


def test_an_embedded_text_layer_is_attached_to_its_own_page():
    text = build_parse_user_text([7, 8], image_count=2, texts=["typed seven", "typed eight"])

    assert "EMBEDDED TEXT LAYER of PDF page 7" in text
    assert "typed seven" in text
    assert "EMBEDDED TEXT LAYER of PDF page 8" in text


def test_a_page_with_no_text_layer_is_skipped_without_disturbing_the_others():
    text = build_parse_user_text([1, 2, 3], image_count=3, texts=["", "middle", ""])

    assert "EMBEDDED TEXT LAYER of PDF page 2" in text
    assert "EMBEDDED TEXT LAYER of PDF page 1" not in text
    assert "EMBEDDED TEXT LAYER of PDF page 3" not in text


def test_a_very_long_text_layer_is_clipped():
    text = build_parse_user_text([1], image_count=1, texts=["x" * 9000])
    assert "x" * 6000 in text
    assert "x" * 6001 not in text


def test_the_layout_block_reaches_the_message():
    """The regression: this raised NameError in production because `layouts`
    was not in scope where the block was assembled."""
    text = build_parse_user_text([10], image_count=1, layouts=[RESULTS_INDEX])

    assert "DETECTED LAYOUT for PDF page 10" in text
    assert "INDEX of material held elsewhere" in text
    assert "28Apr22 | Normal | Chao , Claire" in text


def test_layouts_and_texts_can_both_be_present():
    text = build_parse_user_text([10], image_count=1, texts=["typed"], layouts=[RESULTS_INDEX])

    assert "EMBEDDED TEXT LAYER" in text
    assert "DETECTED LAYOUT" in text


def test_a_layout_that_found_nothing_adds_no_block():
    text = build_parse_user_text([1], image_count=1, layouts=[PageLayout(page_number=1)])
    assert "DETECTED LAYOUT" not in text


def test_a_layout_that_never_ran_adds_no_block():
    text = build_parse_user_text(
        [1], image_count=1, layouts=[PageLayout(page_number=1, analyzed=False)]
    )
    assert "DETECTED LAYOUT" not in text


def test_routing_fields_carry_their_disclaimer_into_the_message():
    layout = PageLayout(
        page_number=12, fields=[LayoutField("Deliver To", "Pask,Leane Norma", 12)]
    )
    text = build_parse_user_text([12], image_count=1, layouts=[layout])

    assert "NONE of them is the author" in text


@pytest.mark.parametrize("layouts", [None, []])
def test_layout_absent_entirely_is_safe(layouts):
    """The default path, and the one that must never raise."""
    assert "DETECTED LAYOUT" not in build_parse_user_text([1], image_count=1, layouts=layouts)


def test_fewer_layouts_than_pages_is_safe():
    """A provider can fail on one page of a batch and succeed on another."""
    text = build_parse_user_text([10, 11], image_count=2, layouts=[RESULTS_INDEX])
    assert "DETECTED LAYOUT for PDF page 10" in text


@pytest.mark.anyio
async def test_the_parser_entry_points_accept_layouts():
    """Guards the shape of the call chain the NameError slipped through: the
    parameter has to exist on every function that passes it along."""
    import inspect

    from app.services.extraction import parser

    assert "layouts" in inspect.signature(parser._invoke_parser).parameters
    assert "layouts" in inspect.signature(parser._parse_batch).parameters
