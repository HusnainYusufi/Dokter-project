"""Score a completed extraction against a hand-built answer key.

Every prompt change so far was verified structurally - the prompt assembles,
the tests pass, the types compile - and never against ground truth. That is how
a change that removed every word ceiling shipped. This scores real output
against pages a human read, so a change can be measured instead of guessed.

Usage:
    python -m evals.score evals/cases/lilian_30_pages.json job.json

`job.json` is the body of GET /api/v1/extract/jobs/{id}. The scorer reads the
summary paragraphs out of it and needs no API keys, so it runs anywhere.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass
class Finding:
    severity: str  # "critical" | "error" | "warning"
    check: str
    detail: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    checks_run: int = 0

    def add(self, severity: str, check: str, detail: str) -> None:
        self.findings.append(Finding(severity, check, detail))

    @property
    def critical(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "critical"]

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def score(self) -> float:
        """Fraction of checks that produced no finding, 0.0 to 1.0."""
        if not self.checks_run:
            return 0.0
        return max(0.0, (self.checks_run - len(self.findings)) / self.checks_run)


# --------------------------------------------------------------------------
# Reading a job


@dataclass
class Entry:
    """One summary entry as the portal renders it."""

    text: str
    page_start: int
    page_end: int
    registered_type: str | None
    is_placeholder: bool

    @property
    def pages(self) -> set[int]:
        return set(range(self.page_start, self.page_end + 1))

    @property
    def opening(self) -> str:
        """The first clause, where the date, type, and author belong."""
        return self.text[:200]


def load_entries(job: dict[str, Any]) -> list[Entry]:
    """Pull summary entries out of a job detail body.

    A job carries one or more patient bundles; every bundle's paragraphs are
    scored together, because the answer key describes the file, not a bundle.
    """
    entries: list[Entry] = []
    patients = job.get("patients") or job.get("result", {}).get("patients") or []
    for patient in patients:
        for para in patient.get("summary_paragraphs") or []:
            entries.append(
                Entry(
                    text=str(para.get("text") or ""),
                    page_start=int(para.get("page_start") or 0),
                    page_end=int(para.get("page_end") or 0),
                    registered_type=para.get("registered_type"),
                    is_placeholder=bool(para.get("is_placeholder")),
                )
            )
    return entries


# --------------------------------------------------------------------------
# Checks


def _overlaps(entry: Entry, page_range: list[int]) -> bool:
    start, end = page_range
    return bool(entry.pages & set(range(start, end + 1)))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def check_page_coverage(entries: Iterable[Entry], key: dict, report: Report) -> None:
    """Every page must be accounted for by some entry.

    A page nothing claims is a page silently dropped from a medico-legal file.
    """
    report.checks_run += 1
    covered: set[int] = set()
    for entry in entries:
        covered |= entry.pages
    expected = set(range(1, int(key["page_count"]) + 1))
    missing = sorted(expected - covered)
    if missing:
        report.add(
            "critical",
            "page_coverage",
            f"pages never appear in any entry: {missing}",
        )


def check_documents(entries: list[Entry], key: dict, report: Report) -> None:
    """Each expected document should have an entry covering its pages, with the
    right date, registered type, and author."""
    for doc in key["documents"]:
        pages = doc["pages"]
        matches = [e for e in entries if _overlaps(e, pages)]

        report.checks_run += 1
        if not matches:
            report.add("error", f"document:{doc['id']}", f"no entry covers pages {pages}")
            continue

        # Date: the key's date should appear in some matching entry's opening.
        if doc.get("date"):
            report.checks_run += 1
            if not any(doc["date"].lower() in _norm(e.opening) for e in matches):
                seen = " | ".join(e.opening[:70] for e in matches[:3])
                report.add(
                    "error",
                    f"date:{doc['id']}",
                    f"expected {doc['date']!r} in the opening; got: {seen}",
                )

        # Author, when the source names one.
        if doc.get("author"):
            report.checks_run += 1
            if not any(doc["author"].lower() in _norm(e.text) for e in matches):
                report.add(
                    "error",
                    f"author:{doc['id']}",
                    f"expected author {doc['author']!r} on pages {pages}",
                )

        # Registered type, for entries the configuration should summarize.
        if doc.get("summarized") and doc.get("bucket") not in {"empty", None}:
            report.checks_run += 1
            types = {(e.registered_type or "").lower() for e in matches}
            if doc["bucket"] not in types:
                report.add(
                    "warning",
                    f"type:{doc['id']}",
                    f"expected registered type {doc['bucket']!r} on pages {pages}, got {sorted(types)}",
                )


def check_traps(entries: list[Entry], key: dict, report: Report) -> None:
    """The specific mistakes this file is known to provoke."""
    for trap in key["traps"]:
        report.checks_run += 1
        kind = trap["kind"]

        if kind == "forbidden_document":
            page_entries = [
                e for e in entries if _overlaps(e, trap["pages"]) and not e.is_placeholder
            ]
            limit = int(trap["max_documents_on_page"])
            if len(page_entries) > limit:
                report.add(
                    "critical",
                    f"trap:{trap['id']}",
                    f"{len(page_entries)} entries on pages {trap['pages']}, expected at most "
                    f"{limit}. {trap['why']}",
                )

        elif kind == "forbidden_phrase_near_date":
            date = trap["date"].lower()
            for entry in entries:
                if date not in _norm(entry.text):
                    continue
                for phrase in trap["forbidden"]:
                    if phrase.lower() in _norm(entry.text):
                        report.add(
                            "critical",
                            f"trap:{trap['id']}",
                            f"entry dated {trap['date']} says {phrase!r}. {trap['why']}",
                        )
                        break

        elif kind == "forbidden_author":
            for entry in entries:
                if not any(_overlaps(entry, [p, p]) for p in trap["scope_pages"]):
                    continue
                for name in trap["forbidden"]:
                    if f"by {name.lower()}" in _norm(entry.text):
                        report.add(
                            "critical",
                            f"trap:{trap['id']}",
                            f"pages {entry.page_start}-{entry.page_end} credit {name!r} as author. "
                            f"{trap['why']}",
                        )
                        break

        elif kind == "pages_in_same_document":
            start, end = trap["pages"]
            # Placeholders count. Demoting a form's signature page to an
            # administrative placeholder is exactly the split being guarded
            # against - it is how the form's author and date got discarded.
            owners = {
                (e.page_start, e.page_end) for e in entries if _overlaps(e, trap["pages"])
            }
            if len(owners) > 1:
                report.add(
                    "error",
                    f"trap:{trap['id']}",
                    f"pages {start}-{end} split across {sorted(owners)}. {trap['why']}",
                )

        elif kind == "separate_documents":
            for entry in entries:
                if _overlaps(entry, trap["left_pages"]) and _overlaps(entry, trap["right_pages"]):
                    report.add(
                        "error",
                        f"trap:{trap['id']}",
                        f"one entry spans pages {entry.page_start}-{entry.page_end}, merging "
                        f"{trap['left_pages']} with {trap['right_pages']}. {trap['why']}",
                    )
                    break

        elif kind == "forbidden_phrase_on_page":
            for entry in entries:
                if not _overlaps(entry, trap["pages"]):
                    continue
                for phrase in trap["forbidden"]:
                    if phrase.lower() in _norm(entry.text):
                        report.add(
                            "error",
                            f"trap:{trap['id']}",
                            f"pages {trap['pages']} say {phrase!r}. {trap['why']}",
                        )
                        break

        else:  # pragma: no cover - guards a malformed key
            raise ValueError(f"unknown trap kind: {kind}")


def check_ceilings(entries: list[Entry], ceilings: dict[str, int], report: Report) -> None:
    """Entries must respect the ceiling for their registered type.

    Overrunning is what turned a four-page form into a 500-word wall, so it is
    scored rather than eyeballed.
    """
    for entry in entries:
        if entry.is_placeholder or not entry.registered_type:
            continue
        limit = ceilings.get(entry.registered_type.lower())
        if not limit:
            continue
        report.checks_run += 1
        words = len(entry.text.split())
        if words > limit:
            report.add(
                "warning" if words <= limit * 1.2 else "error",
                f"ceiling:{entry.registered_type}",
                f"pages {entry.page_start}-{entry.page_end}: {words} words against a "
                f"{limit}-word ceiling",
            )


def score(job: dict[str, Any], key: dict, ceilings: dict[str, int] | None = None) -> Report:
    report = Report()
    entries = load_entries(job)
    if not entries:
        report.checks_run += 1
        report.add("critical", "load", "the job carries no summary paragraphs")
        return report
    check_page_coverage(entries, key, report)
    check_documents(entries, key, report)
    check_traps(entries, key, report)
    if ceilings:
        check_ceilings(entries, ceilings, report)
    return report


def render(report: Report, case_id: str) -> str:
    lines = [f"eval: {case_id}", ""]
    for label, group in (
        ("CRITICAL", report.critical),
        ("ERROR", report.errors),
        ("WARNING", report.warnings),
    ):
        for finding in group:
            lines.append(f"[{label}] {finding.check}")
            lines.append(f"    {finding.detail}")
    if not report.findings:
        lines.append("no findings")
    lines.append("")
    lines.append(
        f"{report.checks_run - len(report.findings)}/{report.checks_run} checks clean "
        f"({report.score:.0%}) - "
        f"{len(report.critical)} critical, {len(report.errors)} error, "
        f"{len(report.warnings)} warning"
    )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    key = json.loads(Path(argv[1]).read_text())
    job = json.loads(Path(argv[2]).read_text())
    # The ceilings the shipped default configuration applies.
    ceilings = {"clinical": 150, "imaging": 50, "functional": 200, "other": 90}
    report = score(job, key, ceilings)
    print(render(report, key["case_id"]))
    return 1 if (report.critical or report.errors) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
