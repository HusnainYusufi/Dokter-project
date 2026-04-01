from __future__ import annotations

import asyncio
import io
import json
from collections import Counter
from typing import Any

from llama_cloud import AsyncLlamaCloud

from app.core.config import settings
from app.core.exceptions import ExportError, ExtractionError, ProcessingError
from app.schemas.extraction import (
    Citation,
    ClinicalRelevance,
    DocumentSummary,
    ExtractionJobDetail,
    ExtractionJobSummary,
    FieldCitation,
    JobStatus,
    PageExtraction,
    PipelineStepStatus,
)
from app.services.document_export import DocumentExportService
from app.services.job_store import EncryptedJobStore, job_to_summary

PAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "patient_name": {
            "type": "string",
            "description": "Primary patient name on this page. Copy the spelling exactly if visible.",
        },
        "document_title": {
            "type": "string",
            "description": "Exact title or heading visible on the page, such as a form or report title.",
        },
        "document_type": {
            "type": "string",
            "description": "Specific document type, such as consultation note, imaging report, referral, pathology, form, administrative cover sheet, or other record type.",
        },
        "document_date": {
            "type": "string",
            "description": "Primary document date visible on the page, copied exactly when possible.",
        },
        "author": {
            "type": "string",
            "description": "Author, clinician, or sender shown on the page.",
        },
        "clinical_relevance": {
            "type": "string",
            "enum": ["clinical", "functional", "administrative", "unknown"],
            "description": "Classify whether this page is clinically relevant, a functional/work-capacity exception, administrative, or unknown.",
        },
        "starts_new_document": {
            "type": "boolean",
            "description": "True when this page clearly starts a new document or report boundary.",
        },
        "starts_new_patient": {
            "type": "boolean",
            "description": "True when this page starts a new patient boundary.",
        },
        "boundary_hint": {
            "type": "string",
            "description": "Short reason for the detected page, document, or patient boundary.",
        },
        "visible_text": {
            "type": "string",
            "description": "Copy the clinically relevant visible page text as faithfully as possible, preserving wording and order instead of summarizing.",
        },
    },
    "required": [
        "clinical_relevance",
        "starts_new_document",
        "starts_new_patient",
        "visible_text",
    ],
}

PAGE_PROMPT = """You are indexing a medico-legal PDF for a secure medical review portal.

Work in extractive capture mode.
- Copy visible page text faithfully instead of summarizing.
- Detect document and patient boundaries conservatively.
- Mark obvious administrative-only pages as administrative.
- Functional/work-capacity records should be marked functional, not administrative.
- If a field is not visible, leave it empty rather than guessing.
"""

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "Produce exactly one paragraph using extractive compression only. Use only words and phrases grounded in the source text, preserve source order, and avoid headings, bullets, or interpretation.",
        },
        "include_in_output": {
            "type": "boolean",
            "description": "True if this document should remain in the medico-legal output and false if it is purely administrative or non-clinical.",
        },
    },
    "required": ["summary", "include_in_output"],
}

SUMMARY_PROMPT = """You are preparing extractive medico-legal summaries.

Rules:
- Extractive mode only. Compress by deletion only.
- Do not infer, reorganize, or blend facts from outside the source.
- Preserve source order.
- Start with the document date, document type, and author when present in the source context.
- Exclude administrative, billing, consent, and fax-only pages unless the document is clearly a functional/work-capacity exception.
- Return a single paragraph with no headings or bullet points.
"""


class ExtractionPipelineService:
    """End-to-end extraction workflow with encrypted persistence."""

    def __init__(
        self,
        store: EncryptedJobStore | None = None,
        exporter: DocumentExportService | None = None,
    ) -> None:
        self.store = store or EncryptedJobStore()
        self.exporter = exporter or DocumentExportService()

    async def create_job(self, filename: str, file_content: bytes) -> ExtractionJobSummary:
        job = self.store.create_job(filename)
        self.store.save_artifact(job.id, "source_pdf", file_content)
        return job_to_summary(job)

    def list_jobs(self) -> list[ExtractionJobSummary]:
        return self.store.list_jobs()

    def get_job(self, job_id: str) -> ExtractionJobDetail:
        return self.store.get_job(job_id)

    def get_source_document(self, job_id: str) -> tuple[str, bytes]:
        job = self.store.get_job(job_id)
        return job.filename, self.store.read_artifact(job_id, "source_pdf")

    def get_export_document(self, job_id: str) -> tuple[str, bytes]:
        job = self.store.get_job(job_id)
        if not job.export_artifact.ready:
            raise ExportError("The export artifact is not ready yet.")
        return job.export_artifact.filename, self.store.read_artifact(job_id, "summary_doc")

    def delete_job(self, job_id: str) -> None:
        self.store.delete_job(job_id)

    async def process_job(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        source_bytes = self.store.read_artifact(job_id, "source_pdf")

        try:
            job.status = JobStatus.PROCESSING
            self._set_step(job, "extract", PipelineStepStatus.RUNNING, "Capturing page-wise extraction data.")
            self.store.save_job(job)

            pages = await self._extract_pages(source_bytes, job.filename)
            job.pages = pages
            job.page_count = len(pages)
            self._set_step(job, "extract", PipelineStepStatus.COMPLETED, f"Captured {len(pages)} pages.")
            self._set_step(job, "boundary", PipelineStepStatus.RUNNING, "Separating patients and documents.")
            self.store.save_job(job)

            documents = self._build_document_groups(pages)
            job.documents = documents
            job.document_count = len(documents)
            patient_names = {self._normalize_key(document.patient_name) for document in documents if document.patient_name}
            job.patient_count = len(patient_names) or (1 if documents else 0)
            job.capture_certification = (
                f"Indexed {job.page_count} pages across {job.document_count} documents and "
                f"{job.patient_count} patient boundary group(s) before summary generation."
            )
            self._set_step(
                job,
                "boundary",
                PipelineStepStatus.COMPLETED,
                f"Indexed {job.document_count} documents across {job.patient_count} patient boundary group(s).",
            )
            self._set_step(job, "summary", PipelineStepStatus.RUNNING, "Generating extractive medico-legal summaries.")
            self.store.save_job(job)

            job.documents = await self._summarize_documents(job.documents, job.pages)
            self._set_step(job, "summary", PipelineStepStatus.COMPLETED, "Prepared extractive summaries.")
            self._set_step(job, "export", PipelineStepStatus.RUNNING, "Generating Word-compatible .doc export.")
            self.store.save_job(job)

            self._refresh_export_filename(job)
            export_bytes = self.exporter.render(job)
            self.store.save_artifact(job.id, "summary_doc", export_bytes)
            self.store.save_artifact(
                job.id,
                "extraction_result",
                json.dumps(
                    {
                        "pages": [page.model_dump(mode="json") for page in job.pages],
                        "documents": [document.model_dump(mode="json") for document in job.documents],
                    },
                    indent=2,
                ).encode(),
            )
            job.export_artifact.ready = True
            job.export_artifact.size_bytes = len(export_bytes)
            self._set_step(job, "export", PipelineStepStatus.COMPLETED, f"Prepared {job.export_artifact.filename}.")
            job.status = JobStatus.COMPLETED
            job.error = None
            self.store.save_job(job)
        except Exception as exc:
            self._mark_job_failed(job, exc)

    async def _extract_pages(self, file_content: bytes, filename: str) -> list[PageExtraction]:
        config: dict[str, Any] = {
            "extraction_target": "PER_PAGE",
            "extraction_mode": settings.EXTRACTION_MODE,
            "chunk_mode": settings.EXTRACTION_CHUNK_MODE,
            "high_resolution_mode": settings.HIGH_RESOLUTION_MODE,
            "cite_sources": True,
            "system_prompt": PAGE_PROMPT,
        }
        if settings.EXTRACTION_MODE != "FAST" and settings.ENABLE_CONFIDENCE_SCORES:
            config["confidence_scores"] = True

        try:
            async with AsyncLlamaCloud(
                api_key=settings.LLAMA_CLOUD_API_KEY,
                timeout=900.0,
            ) as client:
                upload_stream = io.BytesIO(file_content)
                upload_stream.name = filename
                uploaded = await client.files.create(file=upload_stream, purpose="extract")
                result = await client.extraction.extract(
                    file_id=uploaded.id,
                    data_schema=PAGE_SCHEMA,
                    config=config,
                )
        except Exception as exc:
            raise ExtractionError(str(exc)) from exc

        payload = result.model_dump(mode="json")
        data_rows = payload.get("data") or []
        if isinstance(data_rows, dict):
            data_rows = [data_rows]

        field_metadata = payload.get("extraction_metadata", {}).get("field_metadata") or []
        pages: list[PageExtraction] = []
        for index, row in enumerate(data_rows, start=1):
            metadata_row = field_metadata[index - 1] if index - 1 < len(field_metadata) else {}
            page_number = int(metadata_row.get("page_number") or index)
            pages.append(
                PageExtraction(
                    page_number=page_number,
                    patient_name=self._clean_text(row.get("patient_name")),
                    document_title=self._clean_text(row.get("document_title")),
                    document_type=self._clean_text(row.get("document_type")),
                    document_date=self._clean_text(row.get("document_date")),
                    author=self._clean_text(row.get("author")),
                    clinical_relevance=self._parse_relevance(row.get("clinical_relevance")),
                    starts_new_document=bool(row.get("starts_new_document")),
                    starts_new_patient=bool(row.get("starts_new_patient")),
                    boundary_hint=self._clean_text(row.get("boundary_hint")),
                    visible_text=self._clean_text(row.get("visible_text")) or "",
                    citations=self._parse_citations(metadata_row),
                )
            )

        return pages

    def _build_document_groups(self, pages: list[PageExtraction]) -> list[DocumentSummary]:
        documents: list[DocumentSummary] = []
        current_pages: list[PageExtraction] = []
        for page in pages:
            if self._starts_new_group(current_pages, page):
                if current_pages:
                    documents.append(self._finalize_document(documents, current_pages))
                current_pages = [page]
            else:
                current_pages.append(page)

        if current_pages:
            documents.append(self._finalize_document(documents, current_pages))

        return documents

    async def _summarize_documents(
        self,
        documents: list[DocumentSummary],
        pages: list[PageExtraction],
    ) -> list[DocumentSummary]:
        page_map = {page.page_number: page for page in pages}
        semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_SUMMARIES)

        async with AsyncLlamaCloud(
            api_key=settings.LLAMA_CLOUD_API_KEY,
            timeout=600.0,
        ) as client:
            async def summarize_one(document: DocumentSummary) -> DocumentSummary:
                text = "\n\n".join(
                    page_map[page_number].visible_text.strip()
                    for page_number in document.page_numbers
                    if page_number in page_map and page_map[page_number].visible_text.strip()
                ).strip()

                if not document.include_in_output:
                    document.summary = "Excluded from the combined summary because the captured document appears administrative."
                    return document

                if not text:
                    document.summary = "No extractable text was captured for this document."
                    return document

                metadata_header = "\n".join(
                    [
                        f"Date: {document.document_date or 'Unknown'}",
                        f"Document Type: {document.document_type or document.title}",
                        f"Author: {document.author or 'Unknown'}",
                        f"Patient: {document.patient_name or 'Unknown'}",
                        "",
                        text,
                    ]
                )

                async with semaphore:
                    try:
                        result = await client.extraction.extract(
                            data_schema=SUMMARY_SCHEMA,
                            text=metadata_header,
                            config={
                                "extraction_target": "PER_DOC",
                                "extraction_mode": settings.SUMMARY_MODE,
                                "system_prompt": SUMMARY_PROMPT,
                            },
                        )
                        payload = result.model_dump(mode="json").get("data") or {}
                        document.summary = self._clean_text(payload.get("summary")) or self._fallback_summary(document, text)
                        document.include_in_output = bool(payload.get("include_in_output", document.include_in_output))
                    except Exception:
                        document.summary = self._fallback_summary(document, text)

                return document

            return await asyncio.gather(*(summarize_one(document) for document in documents))

    def _starts_new_group(self, current_pages: list[PageExtraction], page: PageExtraction) -> bool:
        if not current_pages:
            return True

        previous = current_pages[-1]
        if page.starts_new_patient or page.starts_new_document:
            return True

        if self._normalize_key(page.patient_name) and self._normalize_key(previous.patient_name):
            if self._normalize_key(page.patient_name) != self._normalize_key(previous.patient_name):
                return True

        same_title = self._normalize_key(page.document_title) == self._normalize_key(previous.document_title)
        same_type = self._normalize_key(page.document_type) == self._normalize_key(previous.document_type)
        same_date = self._normalize_key(page.document_date) == self._normalize_key(previous.document_date)

        if not same_title and not same_type and self._normalize_key(page.document_title):
            return True
        if not same_type and not same_date and self._normalize_key(page.document_type):
            return True

        return False

    def _finalize_document(self, existing_documents: list[DocumentSummary], pages: list[PageExtraction]) -> DocumentSummary:
        document_index = len(existing_documents) + 1
        patient_name = self._first_value(page.patient_name for page in pages)
        document_title = self._first_value(page.document_title for page in pages)
        document_type = self._most_common(page.document_type for page in pages)
        document_date = self._first_value(page.document_date for page in pages)
        author = self._first_value(page.author for page in pages)
        page_numbers = [page.page_number for page in pages]
        classification = self._most_common_relevance(page.clinical_relevance for page in pages)
        include_in_output = classification != ClinicalRelevance.ADMINISTRATIVE
        title = document_title or document_type or f"Document {document_index}"

        return DocumentSummary(
            id=f"doc_{document_index:03d}",
            title=title,
            patient_name=patient_name,
            document_type=document_type,
            document_date=document_date,
            author=author,
            page_numbers=page_numbers,
            page_range=self._page_range(page_numbers),
            classification=classification,
            include_in_output=include_in_output,
            capture_status="captured" if include_in_output else "excluded",
        )

    def _refresh_export_filename(self, job: ExtractionJobDetail) -> None:
        preferred_patient = next((document.patient_name for document in job.documents if document.patient_name), None)
        job.export_artifact.filename = self.store.build_export_filename(job.filename, preferred_patient)

    def _mark_job_failed(self, job: ExtractionJobDetail, exc: Exception) -> None:
        detail = exc.detail if isinstance(exc, ProcessingError) else str(exc)
        for step in job.pipeline:
            if step.status == PipelineStepStatus.RUNNING:
                step.status = PipelineStepStatus.FAILED
                step.detail = detail
                break

        job.status = JobStatus.FAILED
        job.error = detail
        self.store.save_job(job)

    def _set_step(
        self,
        job: ExtractionJobDetail,
        step_key: str,
        status: PipelineStepStatus,
        detail: str | None = None,
    ) -> None:
        for step in job.pipeline:
            if step.key == step_key:
                step.status = status
                step.detail = detail
                break

    def _parse_citations(self, metadata_row: dict[str, Any]) -> list[FieldCitation]:
        citations: list[FieldCitation] = []
        for field_name, field_meta in metadata_row.items():
            if field_name == "page_number" or not isinstance(field_meta, dict):
                continue
            field_citations = [
                Citation(
                    page=int(item.get("page", metadata_row.get("page_number", 0)) or 0),
                    matching_text=self._clean_text(item.get("matching_text")) or "",
                )
                for item in field_meta.get("citation", [])
                if self._clean_text(item.get("matching_text"))
            ]
            if field_citations:
                citations.append(FieldCitation(field=field_name, citations=field_citations))
        return citations

    def _fallback_summary(self, document: DocumentSummary, text: str) -> str:
        prefix = " ".join(
            piece
            for piece in (document.document_date, document.document_type or document.title, document.author)
            if piece
        ).strip()
        cleaned = " ".join(segment.strip() for segment in text.splitlines() if segment.strip())
        excerpt = cleaned[:1200].rsplit(" ", 1)[0] if len(cleaned) > 1200 else cleaned
        return " ".join(piece for piece in (prefix, excerpt) if piece).strip()

    def _parse_relevance(self, value: Any) -> ClinicalRelevance:
        normalized = self._normalize_key(value)
        if normalized == ClinicalRelevance.CLINICAL.value:
            return ClinicalRelevance.CLINICAL
        if normalized == ClinicalRelevance.FUNCTIONAL.value:
            return ClinicalRelevance.FUNCTIONAL
        if normalized == ClinicalRelevance.ADMINISTRATIVE.value:
            return ClinicalRelevance.ADMINISTRATIVE
        return ClinicalRelevance.UNKNOWN

    def _page_range(self, page_numbers: list[int]) -> str:
        if not page_numbers:
            return "0"
        if len(page_numbers) == 1:
            return str(page_numbers[0])
        return f"{page_numbers[0]}-{page_numbers[-1]}"

    def _most_common_relevance(self, values: list[ClinicalRelevance] | Any) -> ClinicalRelevance:
        normalized_values = [value for value in values if value]
        if not normalized_values:
            return ClinicalRelevance.UNKNOWN
        return Counter(normalized_values).most_common(1)[0][0]

    def _most_common(self, values: Any) -> str | None:
        candidates = [self._clean_text(value) for value in values]
        candidates = [candidate for candidate in candidates if candidate]
        if not candidates:
            return None
        return Counter(candidates).most_common(1)[0][0]

    def _first_value(self, values: Any) -> str | None:
        for value in values:
            cleaned = self._clean_text(value)
            if cleaned:
                return cleaned
        return None

    def _clean_text(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).replace("\u00a0", " ").strip()
        return text or None

    def _normalize_key(self, value: Any) -> str:
        cleaned = self._clean_text(value)
        return " ".join(cleaned.lower().split()) if cleaned else ""
