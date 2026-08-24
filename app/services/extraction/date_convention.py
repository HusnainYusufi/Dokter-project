"""Which way round a numeric date is written.

"05/04/2022" is May 4th to a North American insurer and the 4th of May to most
of the rest of the world, and a medico-legal file orders its clinical narrative
by date. Guessing a region is not good enough: bundles cross borders, and a
Canadian claim routinely encloses a report from a clinic that writes dates the
other way.

So the convention is read from the file rather than assumed. Most bundles
contain at least one date that can only be read one way - any day above the
twelfth settles it - and that resolves every ambiguous date alongside it. When a
file offers no such evidence the ambiguity is reported rather than silently
decided.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# A bare numeric date: three groups separated by / - or .
_NUMERIC = re.compile(r"^\s*(\d{1,4})[-/.](\d{1,2})[-/.](\d{1,4})\s*$")


@dataclass(frozen=True)
class DateConvention:
    """How this file writes an all-numeric date."""

    day_first: bool | None = None
    # What settled it, for the audit trail. Empty when nothing did.
    evidence: str = ""

    @property
    def is_resolved(self) -> bool:
        return self.day_first is not None


UNRESOLVED = DateConvention()


def _components(raw: str) -> tuple[int, int, int] | None:
    """(first, second, third) of a bare numeric date, or None."""
    match = _NUMERIC.match(raw or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def is_ambiguous(raw: str) -> bool:
    """True when the date is numeric and could be read either way round.

    A year-first date (2022-05-04) is unambiguous by ISO convention, and any
    component above twelve cannot be a month.
    """
    parts = _components(raw)
    if not parts:
        return False
    first, second, _ = parts
    if first > 31:  # year-first, ISO
        return False
    return 1 <= first <= 12 and 1 <= second <= 12


def _votes(raw: str) -> tuple[bool, bool]:
    """(supports_day_first, supports_month_first) for one date string."""
    parts = _components(raw)
    if not parts:
        return False, False
    first, second, _ = parts
    if first > 31:  # year-first tells us nothing about the other two
        return False, False
    # A first component above twelve cannot be a month, so the file writes the
    # day first. A second component above twelve cannot be a month either, so
    # the file writes the month first.
    return first > 12 and second <= 12, second > 12 and first <= 12


def infer_convention(samples: list[str | None]) -> DateConvention:
    """Read the file's convention from the dates it contains.

    Only dates that can be read exactly one way vote. When the file contradicts
    itself - some dates clearly day-first, others clearly month-first, which
    happens in a bundle merged from several sources - nothing is resolved,
    because applying either convention to the ambiguous remainder would be
    wrong for half of them.
    """
    day_first = month_first = 0
    for sample in samples:
        if not sample:
            continue
        supports_day, supports_month = _votes(sample)
        day_first += supports_day
        month_first += supports_month

    if day_first and not month_first:
        return DateConvention(True, f"{day_first} date(s) with a day above the twelfth")
    if month_first and not day_first:
        return DateConvention(False, f"{month_first} date(s) with a month position above the twelfth")
    if day_first and month_first:
        return DateConvention(
            None,
            f"the file contradicts itself: {day_first} date(s) read day-first and "
            f"{month_first} month-first",
        )
    return UNRESOLVED
