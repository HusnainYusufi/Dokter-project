"""Turn layout structure into the block the page parser reads.

The wording matters more than the data. Handing the model a table and leaving it
to draw conclusions repeats the original failure; the block has to say what a
table IS - a listing of things held elsewhere, not a set of findings - and what
a labelled field is - a role, which for routing labels means the named person is
not the author.

Only informative structure is rendered. A page with no tables and no labelled
fields adds nothing, and a block saying so would be tokens spent teaching the
model to ignore a section.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.services.layout.base import LayoutField, LayoutTable, PageLayout

# Labels naming someone who requested, receives, or is copied on a document
# rather than someone who wrote it. Held as token sequences, because these are
# read off scans by OCR and printed by EMRs that each word them differently: the
# reference page prints "Family Physican", missing a letter, and an exact match
# would sail straight past it.
_ROUTING_PHRASES: tuple[tuple[str, ...], ...] = (
    ("ordering",),
    ("deliver", "to"),
    ("delivered", "to"),
    ("family", "physician"),
    ("admitting",),
    ("consulting",),
    ("referred", "by"),
    ("referring",),
    ("provider",),
    ("copy", "to"),
    ("copies", "to"),
    ("cc",),
    ("attention",),
    ("attn",),
    ("addressed", "to"),
    ("requested", "by"),
)

# How close a token must be to count as the same word. Tight enough that
# "accession" is not "cc" and "provider" is not "provided", loose enough to
# absorb a dropped or doubled letter.
# "physician"/"physican" is 0.94; "provider"/"provided" is 0.875 and must not
# match, so the bar sits between them.
_TOKEN_SIMILARITY = 0.9

_MAX_TABLE_ROWS = 25
_MAX_CELL = 120


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z]+", text.strip().lower()) if token]


def _same_word(a: str, b: str) -> bool:
    """Whole-token comparison, tolerant of one OCR slip.

    Whole-token is what stops "cc" matching inside "accession"; the tolerance is
    what catches "physican".
    """
    if a == b:
        return True
    # A short marker must match exactly. At two or three letters almost
    # anything is one edit away from anything else.
    if min(len(a), len(b)) <= 4:
        return False
    return SequenceMatcher(None, a, b).ratio() >= _TOKEN_SIMILARITY


def _is_routing(label: str) -> bool:
    """True when the label names a recipient or requester, not an author."""
    tokens = _tokens(label)
    if not tokens:
        return False
    for phrase in _ROUTING_PHRASES:
        span = len(phrase)
        for start in range(len(tokens) - span + 1):
            window = tokens[start : start + span]
            if all(_same_word(word, marker) for word, marker in zip(window, phrase)):
                return True
    return False


def _cell(text: str) -> str:
    cleaned = " ".join(text.split())
    return cleaned[:_MAX_CELL]


def _render_table(table: LayoutTable, index: int) -> list[str]:
    lines = [f"TABLE {index} ({table.row_count} row(s)):"]
    if table.headers:
        lines.append("  columns: " + " | ".join(_cell(h) for h in table.headers if h.strip()))
    for row in table.rows[:_MAX_TABLE_ROWS]:
        if not any(cell.strip() for cell in row):
            continue
        lines.append("  row: " + " | ".join(_cell(cell) for cell in row))
    remaining = table.row_count - _MAX_TABLE_ROWS
    if remaining > 0:
        lines.append(f"  ... {remaining} further row(s) not shown")
    return lines


def render_layout_block(layout: PageLayout) -> str:
    """The prompt block for one page, or empty when there is nothing to say."""
    if not layout.analyzed or not layout.is_informative:
        return ""

    tables = [t for t in layout.tables if not t.is_empty]
    fields = [f for f in layout.fields if f.is_usable]
    routing = [f for f in fields if _is_routing(f.label)]
    other = [f for f in fields if not _is_routing(f.label)]

    lines = [
        f"DETECTED LAYOUT for PDF page {layout.page_number} (from a document-structure "
        "service, not from your reading of the image). This is structure, not content: "
        "it tells you what SHAPE things are, and you still read the values from the image."
    ]

    if tables:
        lines.append("")
        lines.append(
            "The following are TABLES - grids of rows under columns. A table that lists "
            "studies, results, documents, or appointments is an INDEX of material held "
            "elsewhere in the chart, not a set of reports: never create a document from one "
            "of its rows, never read a status word in a row (\"Normal\", \"Final\", "
            "\"Complete\") as a clinical impression, and never treat a name in a row as an "
            "author. Capture the rows verbatim as evidence with provenance \"index\"."
        )
        for position, table in enumerate(tables, start=1):
            lines.extend(_render_table(table, position))

    if routing:
        lines.append("")
        lines.append(
            "The following are ROUTING FIELDS - each names someone who requested, receives, "
            "or is copied on this document. NONE of them is the author. Take the author only "
            "from a dictating or signing line, and leave it empty if there is none:"
        )
        for item in routing:
            lines.append(f'  "{_cell(item.label)}": {_cell(item.value)}')

    if other:
        lines.append("")
        lines.append(
            "The following are LABELLED FIELDS printed on the page, given with their labels "
            "so you attach each value to the right field:"
        )
        for item in other[:40]:
            lines.append(f'  "{_cell(item.label)}": {_cell(item.value)}')

    return "\n".join(lines)


def index_evidence_rows(layout: PageLayout) -> list[str]:
    """Table rows as flat strings, for capture as `index`-provenance evidence.

    Deterministic rather than model-decided: a row of a listing is a pointer,
    and that classification should not depend on a judgement call that has
    already been shown to go wrong.
    """
    rows: list[str] = []
    for table in layout.tables:
        if table.is_empty:
            continue
        for row in table.rows[:_MAX_TABLE_ROWS]:
            cells = [_cell(cell) for cell in row if cell.strip()]
            if len(cells) >= 2:
                rows.append(" | ".join(cells))
    return rows
