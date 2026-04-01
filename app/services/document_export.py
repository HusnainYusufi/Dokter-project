from __future__ import annotations

from collections import defaultdict

from app.core.exceptions import ExportError
from app.schemas.extraction import ClinicalRelevance, ExtractionJobDetail


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
            included_documents = [document for document in job.documents if document.include_in_output]
            if not included_documents:
                included_documents = job.documents

            lines: list[str] = [
                r"{\rtf1\ansi\deff0",
                r"{\fonttbl{\f0 Arial;}{\f1 Times New Roman;}}",
                r"\paperw12240\paperh15840\margl1080\margr1080\margt900\margb900",
                r"\f0\fs48\b Medical Intelligence Extractive Summary\b0\fs22\par",
                rf"\fs22 Source file: {_rtf_escape(job.filename)}\par",
                rf"Generated: {_rtf_escape(job.updated_at)}\par",
            ]

            if job.capture_certification:
                lines.append(rf"Capture certification: {_rtf_escape(job.capture_certification)}\par")

            lines.append(r"\par\b Document Index\b0\par")
            for index, document in enumerate(job.documents, start=1):
                label = document.title or document.document_type or f"Document {index}"
                meta_bits = [document.document_date, document.author, document.patient_name]
                meta_text = " | ".join(bit for bit in meta_bits if bit)
                lines.append(
                    rf"{index}. {_rtf_escape(label)} "
                    rf"(pages {_rtf_escape(document.page_range)})"
                    + (rf" - {_rtf_escape(meta_text)}" if meta_text else "")
                    + r"\par"
                )

            patient_buckets: dict[str, list] = defaultdict(list)
            for document in included_documents:
                patient_buckets[document.patient_name or "Unassigned patient"].append(document)

            lines.append(r"\par\b Extractive Summaries\b0\par")
            for bucket_index, (patient_name, documents) in enumerate(patient_buckets.items(), start=1):
                lines.append(rf"\b {_rtf_escape(patient_name)}\b0\par")
                for document in documents:
                    summary = document.summary.strip() or "No extractive summary was generated for this document."
                    if document.classification == ClinicalRelevance.ADMINISTRATIVE:
                        summary = f"[Administrative/Excluded] {summary}"
                    lines.append(rf"{_rtf_escape(summary)}\par\par")
                if bucket_index < len(patient_buckets):
                    lines.append(r"\page")

            lines.append("}")
            return "".join(lines).encode("utf-8")
        except Exception as exc:
            raise ExportError("Failed to generate the Word-compatible summary artifact.") from exc
