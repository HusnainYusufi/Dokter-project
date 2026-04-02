from __future__ import annotations

import textwrap

from app.core.exceptions import ExportError
from app.schemas.extraction import ExtractionJobDetail, PatientHeader


def _rtf_escape(text: str) -> str:
    chunks: list[str] = []
    for char in text:
        codepoint = ord(char)
        if char == "\\":
            chunks.append(r"\\")
        elif char == "{":
            chunks.append(r"\{")
        elif char == "}":
            chunks.append(r"\}")
        elif char == "\n":
            chunks.append(r"\line ")
        elif 32 <= codepoint <= 126:
            chunks.append(char)
        else:
            chunks.append(rf"\u{codepoint}?")
    return "".join(chunks)


class DocumentExportService:
    """Generate a Word-compatible legacy .doc artifact using RTF content."""

    def render(self, job: ExtractionJobDetail) -> bytes:
        try:
            lines: list[str] = [
                r"{\rtf1\ansi\deff0",
                r"{\fonttbl{\f0 Arial;}{\f1 Times New Roman;}{\f2 Courier New;}}",
                r"\paperw12240\paperh15840\margl1080\margr1080\margt900\margb900",
                r"\viewkind4\uc1",
                r"\f0\fs48\b Medical File Review\b0\par",
            ]

            self._append_document_meta(lines, job)

            for bucket_index, patient in enumerate(job.patients, start=1):
                patient_label = patient.name or f"Patient {bucket_index}"
                if bucket_index > 1:
                    lines.append(r"\page")
                    lines.append(r"\f0\fs48\b Medical File Review\b0\par")
                    self._append_document_meta(lines, job)

                lines.append(r"\par")
                lines.append(rf"\f0\fs32\b {_rtf_escape(patient_label)}\b0\par")
                self._append_patient_header(lines, patient.header, patient_label)
                if patient.page_start > 0 and patient.page_end > 0:
                    page_text = str(patient.page_start) if patient.page_start == patient.page_end else f"{patient.page_start}-{patient.page_end}"
                    lines.append(rf"\f0\fs22\i Pages reviewed: {_rtf_escape(page_text)}\i0\par")

                self._append_section_heading(lines, "Summary")
                summary = patient.summary.strip() or "No patient summary was generated."
                for paragraph in [piece for piece in summary.split("\n\n") if piece.strip()]:
                    lines.append(rf"\f1\fs24 {_rtf_escape(paragraph.strip())}\par\par")

                self._append_section_heading(lines, "Opinion")
                opinion = patient.opinion.strip() or "No patient opinion was generated."
                for paragraph in [piece for piece in opinion.split("\n\n") if piece.strip()]:
                    lines.append(rf"\f1\fs24 {_rtf_escape(paragraph.strip())}\par\par")

                if patient.office_visits:
                    self._append_section_heading(lines, "Office Visits")
                    for visit_index, visit in enumerate(patient.office_visits, start=1):
                        page_text = str(visit.page_start) if visit.page_start == visit.page_end else f"{visit.page_start}-{visit.page_end}"
                        lines.append(rf"\f0\fs24\b {visit_index}. {_rtf_escape(visit.title)}\b0\par")
                        if visit.date or visit.author:
                            meta = " | ".join(bit for bit in [visit.date, visit.author] if bit)
                            lines.append(rf"\f1\fs22 {_rtf_escape(meta)}\par")
                        lines.append(rf"\f1\fs22 Pages: {_rtf_escape(page_text)}\par\par")

            lines.append("}")
            return "".join(lines).encode("utf-8")
        except Exception as exc:
            raise ExportError("Failed to generate the Word-compatible summary artifact.") from exc

    def _append_document_meta(self, lines: list[str], job: ExtractionJobDetail) -> None:
        lines.append(r"\f0\fs20\cf0")
        if job.capture_certification:
            lines.append(rf"{_rtf_escape(job.capture_certification)}\par")
        lines.append(rf"Source file: {_rtf_escape(job.filename)}\par")
        lines.append(rf"Generated: {_rtf_escape(job.updated_at)}\par")
        lines.append(r"\par")

    def _append_section_heading(self, lines: list[str], title: str) -> None:
        lines.append(r"\par")
        lines.append(rf"\f0\fs28\b {_rtf_escape(title)}\b0\par")
        lines.append(r"\f0\fs20\par")

    def _append_patient_header(self, lines: list[str], header: PatientHeader, fallback_name: str) -> None:
        claimant = header.claimant or fallback_name
        rows = [
            (("To", header.to_name), ("Claim", header.claim_number)),
            (("From", header.from_name), ("Age / DOB", header.age_dob)),
            (("Date", header.review_date), ("Occupation", header.occupation)),
            (("Claimant", claimant), ("Diagnosis / DOD", header.diagnosis_dod)),
        ]

        if not any(value for row in rows for _, value in row):
            return

        left_label_width = 10
        left_value_width = 19
        right_label_width = 16
        right_value_width = 21

        def wrap_value(value: str | None, width: int) -> list[str]:
            text = (value or "").strip()
            if not text:
                return [""]
            wrapped = textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False)
            return wrapped or [text]

        def build_row(left_label: str, left_value: str | None, right_label: str, right_value: str | None) -> list[str]:
            left_lines = wrap_value(left_value, left_value_width)
            right_lines = wrap_value(right_value, right_value_width)
            line_count = max(len(left_lines), len(right_lines))
            output: list[str] = []
            for index in range(line_count):
                output.append(
                    "| "
                    + (f"{left_label}:" if index == 0 else "").ljust(left_label_width)
                    + " | "
                    + (left_lines[index] if index < len(left_lines) else "").ljust(left_value_width)
                    + " | "
                    + (f"{right_label}:" if index == 0 else "").ljust(right_label_width)
                    + " | "
                    + (right_lines[index] if index < len(right_lines) else "").ljust(right_value_width)
                    + " |"
                )
            return output

        top_border = "+------------+---------------------+------------------+-----------------------+"
        divider = "+============+=====================+==================+=======================+"
        row_divider = "+------------+---------------------+------------------+-----------------------+"

        table_lines = [top_border]
        first_row = True
        for left, right in rows:
            left_label, left_value = left
            right_label, right_value = right
            if not left_value and not right_value:
                continue
            table_lines.extend(build_row(left_label, left_value, right_label, right_value))
            table_lines.append(divider if first_row else row_divider)
            first_row = False

        if table_lines[-1] in {divider, row_divider}:
            table_lines[-1] = row_divider

        lines.append(r"\par")
        for table_line in table_lines:
            lines.append(rf"\f2\fs20 {_rtf_escape(table_line)}\par")
