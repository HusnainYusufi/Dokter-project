"""Layout structure a document-AI service can see and a vision model cannot.

A vision model reads a page as a picture and infers everything - that a block of
rows is a table, that "Ordering Physician" labels the name beside it. Both
inferences failed on real pages: an EMR results index became two imaging reports
with impressions of their own, and a "Deliver To" name was credited as a report's
author.

A layout service returns those as facts. Tables come back as tables, with rows
and cells. Labelled fields come back as key/value pairs with the label attached.
Neither has to be guessed.

This module is the shape the rest of the pipeline sees, so the choice of service
- Azure, Textract, or something self-hosted where no data may leave - is one
adapter and not a rewrite.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class LayoutTable:
    """A table exactly as printed, without interpretation.

    The critical fact this carries is that the content IS a table. A results
    index looks like a set of findings to a model reading pixels; knowing it is
    a grid of rows under column headers is what makes it an index.
    """

    rows: list[list[str]] = field(default_factory=list)
    # First row when the service marks it as a header, otherwise empty.
    headers: list[str] = field(default_factory=list)
    page_number: int = 0

    @property
    def is_empty(self) -> bool:
        return not any(cell.strip() for row in self.rows for cell in row)

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class LayoutField:
    """A labelled field: the printed label and the value beside it."""

    label: str
    value: str
    page_number: int = 0

    @property
    def is_usable(self) -> bool:
        return bool(self.label.strip() and self.value.strip())


@dataclass(frozen=True)
class PageLayout:
    """Everything a layout service found on one page."""

    page_number: int
    tables: list[LayoutTable] = field(default_factory=list)
    fields: list[LayoutField] = field(default_factory=list)
    # True when a service ran and found nothing, as opposed to no service
    # running at all - the two mean very different things to a reader.
    analyzed: bool = True

    @property
    def is_informative(self) -> bool:
        return bool(
            [t for t in self.tables if not t.is_empty] or [f for f in self.fields if f.is_usable]
        )


class LayoutProvider(Protocol):
    """A service that returns structure for a rendered page image."""

    name: str

    async def analyze(self, page_number: int, image: bytes) -> PageLayout:
        """Structure for one page. Must never raise: layout is an enrichment,
        and a page still parses without it."""
        ...


class NoLayoutProvider:
    """The default. Returns nothing, so the pipeline behaves exactly as it did
    before layout analysis existed."""

    name = "none"

    async def analyze(self, page_number: int, image: bytes) -> PageLayout:
        return PageLayout(page_number=page_number, analyzed=False)
