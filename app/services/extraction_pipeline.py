from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Awaitable, Callable

from llama_cloud import AsyncLlamaCloud
import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, AuthenticationError, RateLimitError
import pypdfium2 as pdfium

from app.core.config import settings
from app.core.exceptions import ExportError, ExtractionError, GeminiExtractionError, OpenAIExtractionError, ProcessingError
from app.schemas.extraction import (
    Citation,
    ClinicalRelevance,
    DocumentSummary,
    ExtractionJobDetail,
    ExtractionJobSummary,
    FieldCitation,
    JobStatus,
    OfficeVisitItem,
    PageExtraction,
    PageRole,
    PatientHeader,
    PatientSummary,
    PipelineStepStatus,
    SummaryKind,
)
from app.services.document_export import DocumentExportService
from app.services.job_store import EncryptedJobStore, job_to_summary

logger = logging.getLogger(__name__)

CLASSIFY_WINDOW_SIZE = 12
CLASSIFY_MAX_CANDIDATES = 6
CLASSIFY_POLL_INTERVAL_SECONDS = 1.0
CLASSIFY_POLL_TIMEOUT_SECONDS = 300.0
OPENAI_PAGE_IMAGE_MEDIA_TYPE = "image/png"
OPENAI_MAX_RETRIES = 5
OPENAI_RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
OPENAI_RETRY_MAX_DELAY_SECONDS = 60.0
GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
PAGE_PARSE_CACHE_VERSION = 2
PATIENT_BUNDLE_CACHE_VERSION = 1
DOCTOR_NAME_PATTERN = re.compile(r"\b(Dr\.?\s+)([A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*)+)")


class JobCancelled(Exception):
    pass


@dataclass
class PageParseBatchResult:
    batch_index: int
    page_numbers: list[int]
    pages: list[PageExtraction]
    error: str | None = None

PAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "patient_name": {
            "type": "string",
            "description": "Primary patient or claimant name shown on this page. Copy the spelling exactly if visible.",
        },
        "patient_dob": {
            "type": "string",
            "description": "Patient or claimant date of birth copied exactly from the page when visible.",
        },
        "patient_identifier": {
            "type": "string",
            "description": "Most useful patient or claim identifier on the page, such as claim number, contract ID, chart number, or member ID.",
        },
        "document_title": {
            "type": "string",
            "description": "Exact title or heading visible on the page, such as a form or report title.",
        },
        "mentioned_patient_names": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Every patient or claimant name visibly mentioned on the page, including the primary patient and any comparative or family references.",
        },
        "document_type": {
            "type": "string",
            "description": "Specific document type, such as consultation note, imaging report, referral, pathology, form, administrative cover sheet, or other record type.",
        },
        "document_bucket": {
            "type": "string",
            "enum": ["clinical", "imaging", "pathology", "functional", "administrative", "unknown"],
            "description": "Best-fit bucket for this page: clinical, imaging, pathology, functional, administrative, or unknown.",
        },
        "document_date": {
            "type": "string",
            "description": "Primary document date visible on the page, copied exactly when possible.",
        },
        "author": {
            "type": "string",
            "description": "Author, clinician, or sender shown on the page.",
        },
        "visible_text": {
            "type": "string",
            "description": "Copy the visible page text as faithfully as possible, preserving wording and order instead of summarizing. Exclude repeated scan garbage when possible, but do not paraphrase.",
        },
    },
    "required": ["visible_text"],
}

PAGE_PROMPT = """You are indexing a medico-legal PDF for a secure medical review portal.

Work in extractive capture mode.
- Index every page in order before any summarization occurs.
- Treat each page as local-only evidence.
- Copy visible page text faithfully instead of summarizing.
- Extract title, date, author, and patient details only from the current page.
- Do not borrow, merge, or infer title/date/author from other pages.
- If a title is on one page and the date is only visible on a different page, leave the missing field empty on the current page.
- Capture only the core page facts: patient identity, basic document metadata, bucket, and visible text.
- Use administrative for fax cover sheets, consent forms, billing, and routing pages.
- Use functional for work-capacity, disability, referral-for-review, or insurer review material.
- If a field is not visible, leave it empty rather than guessing.
"""

PATIENT_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Patient name exactly as shown in the supplied patient bundle. Leave empty if not visible.",
        },
        "header": {
            "type": "object",
            "properties": {
                "to_name": {"type": "string"},
                "claim_number": {"type": "string"},
                "from_name": {"type": "string"},
                "age_dob": {
                    "type": "string",
                    "description": "Age or DOB field as shown in the main claimant header. Prefer the explicit DOB/age line in the top header block.",
                },
                "review_date": {
                    "type": "string",
                    "description": "Primary report/review date from the top document header. Explicitly prefer the date shown next to a visible `Date:` label near the top/title/header. Do not use footer timestamps, fax times, print times, or page generation dates.",
                },
                "occupation": {"type": "string"},
                "claimant": {"type": "string"},
                "diagnosis_dod": {"type": "string"},
            },
            "description": "Claimant-style review header copied from the supplied file where visible, matching the reference review layout.",
        },
        "page_start": {
            "type": "integer",
            "description": "First page number belonging to this patient boundary group.",
        },
        "page_end": {
            "type": "integer",
            "description": "Last page number belonging to this patient boundary group.",
        },
        "summary": {
            "type": "string",
            "description": "Patient-level summary following the golden rules. It must be plain text, source-bounded, and written as a chronological series of paragraphs in original document order. One paragraph per materially distinct document, with each paragraph using the controlling clinical/report date for that document.",
        },
        "opinion": {
            "type": "string",
            "description": "Patient-level opinion based only on the supplied evidence. Keep it concise, professional, and evidence-based.",
        },
    },
    "required": ["header", "page_start", "page_end", "summary", "opinion"],
}

PATIENT_SCHEMA: dict[str, Any] = PATIENT_ITEM_SCHEMA

JOB_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "page_count": {
            "type": "integer",
            "description": "Total page count of the uploaded PDF.",
        },
        "patients": {
            "type": "array",
            "items": PATIENT_ITEM_SCHEMA,
            "description": "Patient sections identified across the whole file in original file order.",
        },
    },
    "required": ["patients"],
}

PATIENT_PROMPT = """You are preparing a patient-level medico-legal output from a whole uploaded PDF in one pass.

Rules:
- Use only the supplied PDF.
- Do not invent or infer missing facts.
- Identify patient boundaries yourself from the file content and page numbering.
- Return one patient section per patient boundary group in original file order.
- If the whole file belongs to one real-world patient, return exactly one patient section for the whole file.
- Do not split the same patient into multiple sections just because the file contains multiple document types, appendices, questionnaires, administrative pages, imaging, or office-visit records.
- Merge obvious name variants for the same person into one patient section, including casing differences, comma-swapped names, middle initials, and minor punctuation differences.
- When later pages omit the patient name but clearly still belong to the same patient file, keep them inside the same patient section rather than creating a new patient.
- Return a claimant-style header object for each patient section with these fields copied exactly if visible: `to_name`, `claim_number`, `from_name`, `age_dob`, `review_date`, `occupation`, `claimant`, `diagnosis_dod`.
- For all extracted dates, prefer the primary clinical/report/header date shown near the top of the document.
- Explicitly prefer the date written next to a visible `Date:` label near the top of the document whenever one is present.
- Do not use footer timestamps, fax transmission stamps, print timestamps, page generation dates, or scanner metadata when a real document date is visible near the top.
- For office visits and reports, prefer the signed date, visit date, consultation date, report date, or header date near the title.
- Format dates in full written form when visible, for example `January 11, 2026`.
- `page_start` and `page_end` are mandatory for every patient section.
- `page_start` must be the first page belonging to that patient section and `page_end` must be the last page belonging to that patient section.
- `summary` must follow the golden rules strictly:
  plain text only,
  extractive/source-bounded,
  professional and neutral medico-legal tone,
  preserve original document order,
  no synthesis,
  no inference,
  and reflect the concise medico-legal style of the reference output.
- `summary` must read like a selective chronological medico-legal file review rather than a page-by-page dump.
- `summary` must be a series of paragraphs, ideally one paragraph per materially distinct document.
- Each summary paragraph must start on the same line with:
  full date in written form,
  document type,
  author.
- Example structure: `March 01, 2023 APS Dr. Pask ...`
- When the author is a doctor, use `Dr.` plus surname only, never the full given name sequence. Example: write `Dr. Alex`, not `Dr. John Alex`.
- Do not write synthetic lead-ins such as `On March 01, 2023...` unless that exact phrasing is visible in the source.
- For each summary paragraph, use the controlling date for that specific document:
  visit date for office visits,
  consultation date for consultation letters,
  report date for reports,
  imaging date for imaging,
  specimen/procedure date for pathology,
  and signed letter date for correspondence.
- If a `Date:` label is present near the top header for that document, treat that `Date:` value as the controlling date unless the source explicitly indicates another controlling date for that document type.
- Do not use profile update dates, medication-profile dates, chart snapshot dates, `as of` dates, fax timestamps, print timestamps, or footer dates when a true document date is visible.
- Do not start a paragraph with demographic/profile metadata just because it appears first on the page if the actual clinical/report date is different.
- If the only visible date is an `as of` or profile snapshot date and it is not clearly the controlling document date, omit the date rather than using the wrong one.
- Include only materially distinct dated records, clinically relevant findings, functionally relevant findings, investigations, work-capacity information, and major treatment updates.
- Collapse repeated screening tools, repeated appendices, and repeated forms into one mention unless a later instance adds materially new findings.
- Exclude blank forms, references, licensing text, educational boilerplate, questionnaire instructions, and generic screening descriptions unless the patient-specific result itself is clinically relevant.
- If the exact date, document type, or author is not visible, omit the missing element rather than inventing it.
- `opinion` must follow the golden rules strictly:
  analytical only,
  evidence-based,
  concise,
  professional,
  and separate from the summary role.
- `opinion` must resemble the reference output: a short medico-legal analysis focused on prognosis, work capacity, rehabilitation value, and whether further intervention is justified by the supplied evidence.
- Do not return a structured record list in this pass. Record indexing is handled separately from page-local extraction.
- If a field is not visible, leave it empty rather than guessing.
"""

OPENAI_PAGE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "page_number": {"type": "integer"},
        "patient_name": {"type": "string"},
        "patient_dob": {"type": "string"},
        "patient_identifier": {"type": "string"},
        "mentioned_patient_names": {
            "type": "array",
            "items": {"type": "string"},
        },
        "document_title": {"type": "string"},
        "document_type": {"type": "string"},
        "document_bucket": {
            "type": "string",
            "enum": ["clinical", "imaging", "pathology", "functional", "administrative", "unknown"],
        },
        "document_date": {"type": "string"},
        "author": {"type": "string"},
        "visible_text": {
            "type": "string",
            "description": "Faithful transcription of the page text visible in the rendered image. Copy what is visible instead of summarizing.",
        },
    },
    "required": ["page_number", "visible_text"],
}

OPENAI_PAGE_BATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pages": {
            "type": "array",
            "items": OPENAI_PAGE_ITEM_SCHEMA,
        }
    },
    "required": ["pages"],
}

OPENAI_PAGE_PROMPT = """You are extracting structured page metadata from rendered PDF page images.

Rules:
- Each page image is preceded by a `Page N` label. Use only that image for the matching `page_number`.
- Treat each page as local-only evidence.
- Never borrow metadata from a different page.
- If a value is not visible on that page, leave it empty.
- Return one JSON object with a `pages` array.
- Each item must include the original `page_number`.
- `document_bucket` must be one of: clinical, imaging, pathology, functional, administrative, unknown.
- Use administrative for fax covers, consent forms, billing, routing pages, and generic insurer/admin material.
- Use functional for work-capacity, disability, insurer review, referral-for-review, and rehabilitation planning material.
- Preserve patient names exactly as visible where possible.
- Copy the page body faithfully into `visible_text` instead of summarizing or paraphrasing.
- If the page is scanned or image-only, transcribe the visible text as accurately as possible from the image.
"""

OPENAI_PATIENT_BUNDLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "header": PATIENT_ITEM_SCHEMA["properties"]["header"],
        "summary": {"type": "string"},
        "opinion": {"type": "string"},
        "office_visits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "date": {"type": "string"},
                    "author": {"type": "string"},
                    "page_start": {"type": "integer"},
                    "page_end": {"type": "integer"},
                },
                "required": ["title", "page_start", "page_end"],
            },
        },
    },
    "required": ["header", "summary", "opinion"],
}

OPENAI_PATIENT_BUNDLE_PROMPT = """You are preparing a single patient-level medico-legal output from a pre-parsed patient bundle text.

Rules:
- Use only the supplied patient bundle text.
- Do not invent or infer missing facts.
- Return one JSON object matching the requested schema.
- `header` should copy claimant-style fields exactly where visible: `to_name`, `claim_number`, `from_name`, `age_dob`, `review_date`, `occupation`, `claimant`, `diagnosis_dod`.
- Prefer the primary header/report date written near the top of a document.
- Do not use footer timestamps, fax times, print times, page generation times, or scanner metadata when a true document date is visible.
- Format dates in full written form when visible, for example `January 11, 2026`. No abbreviations.
- Plain text discipline inside `summary` and `opinion`:
  - plain text only
  - no bullets
  - no headings
  - hard paragraph returns only
  - no decorative punctuation separators (avoid en-dash `–`)
- Locked extractive mode for `summary`:
  - extractive only (compress by deletion only)
  - do not paraphrase
  - do not reorganize
  - do not synthesize
  - do not infer
  - if a phrase cannot be traced verbatim to the supplied bundle text, omit it
- `summary` structure:
  - one paragraph per document section present in the bundle text
  - preserve original bundle document order (Document 1, Document 2, ...)
  - each paragraph must start on the same line with: FullDate DocumentType Author
  - use the document's controlling date when visible
  - if date/type/author is not visible for that document, omit the missing element rather than invent it
  - when the author is a physician, use `Dr.` plus surname only (e.g. `Dr. Alex`)
- `opinion` must be analytical only and evidence-based, without restating the full summary content.
- `office_visits` must be chronological oldest-to-newest when enough evidence exists.
"""

REFERENCE_OUTPUT_STYLE = """Reference output style:
- Summary is a chronological sequence of plain-text paragraphs, one paragraph per included document.
- Each summary paragraph starts on the same line with: FullDate DocumentType Author.
- Summary paragraphs read like concise medico-legal review paragraphs, not raw form-field dumps.
- Opinion is a separate set of short analytical paragraphs, not a copied chronology or a field-by-field restatement.
"""

OPENAI_PATIENT_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "opinion": {"type": "string"},
        "office_visits": OPENAI_PATIENT_BUNDLE_SCHEMA["properties"]["office_visits"],
    },
    "required": ["summary", "opinion", "office_visits"],
}

OPENAI_PATIENT_REVIEW_PROMPT = """You are a locked medico-legal repair filter.

Role:
- This is a repair pass, not a restart of the extraction loop.
- Preserve compliant content.
- Repair only the parts that break the rules.
- If one field is uncertain, leave that field conservative and keep the rest.
- Use the source bundle text and document manifest as the final authority, not the draft.

Summary rules:
- factual only
- extractive/source-bounded
- no inference
- no synthesis
- one paragraph per included document in original file order
- include referrals, imaging, pathology, functional/work-capacity material, case-management notes, and telephone interviews when present
- exclude administrative, billing, consent, fax cover sheets, routing pages, and signature-only logistics pages unless explicitly instructed otherwise
- each paragraph must start on the same line with: FullDate DocumentType Author, omitting only elements that are not visible
- all dates must be written in full format such as January 27, 2023
- physician names must be Dr. Lastname only
- remove raw form scaffolding and direct identifier blocks such as Address, Postal Code, Social Insurance Number, email, checkbox markers, and member-identification boilerplate unless clinically or functionally necessary

Opinion rules:
- analytical only
- concise
- evidence-based
- do not repeat raw chronology line-by-line
- do not restate raw form fields

Office visit rules:
- keep oldest to newest when enough evidence exists
- include only clinically or functionally relevant captured documents
- exclude consent, billing, fax, routing, and signature-only pages

Return corrected JSON only.
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
        self._running_jobs: set[str] = set()
        self._openai_client: AsyncOpenAI | None = None

    async def _create_job_from_source(
        self,
        *,
        filename: str,
        file_content: bytes,
        source_file_id: str | None = None,
    ) -> ExtractionJobSummary:
        source_digest = hashlib.sha256(file_content).hexdigest()
        existing_job = self.store.find_job_by_source_digest(source_digest)

        if existing_job and existing_job.status in {JobStatus.QUEUED, JobStatus.PROCESSING}:
            return job_to_summary(existing_job)

        if (
            existing_job
            and existing_job.status == JobStatus.COMPLETED
            and self.store.artifact_exists(existing_job.id, "summary_doc")
            and self.store.artifact_exists(existing_job.id, "extraction_result")
        ):
            cloned_job = self.store.clone_job_from_existing(
                existing_job,
                filename=filename,
                source_digest=source_digest,
                source_bytes=file_content,
                source_file_id=source_file_id,
            )
            return job_to_summary(cloned_job)

        if existing_job and existing_job.status == JobStatus.FAILED and self.store.artifact_exists(existing_job.id, "source_pdf"):
            logger.info("Re-queueing failed job %s for resumable parsing", existing_job.id)
            existing_job.status = JobStatus.QUEUED
            existing_job.error = None
            existing_job.filename = filename
            existing_job.source_file_id = source_file_id or existing_job.source_file_id
            existing_job.export_artifact.ready = False
            existing_job.export_artifact.size_bytes = None
            for step in existing_job.pipeline:
                if step.key == "upload":
                    step.status = PipelineStepStatus.COMPLETED
                    step.detail = "Source document encrypted and stored."
                else:
                    step.status = PipelineStepStatus.PENDING
                    step.detail = "Queued to retry with saved parse checkpoints."
            self.store.save_job(existing_job)
            return job_to_summary(existing_job)

        job = self.store.create_job(filename, source_digest=source_digest, source_file_id=source_file_id)
        self.store.save_artifact(job.id, "source_pdf", file_content)
        return job_to_summary(job)

    async def create_job(self, filename: str, file_content: bytes) -> ExtractionJobSummary:
        return await self._create_job_from_source(filename=filename, file_content=file_content)

    async def create_job_from_vault_file(self, file_id: str) -> ExtractionJobSummary:
        source = self.store.get_vault_file(file_id).file
        if not source.can_extract:
            raise ProcessingError("Only PDF vault files can start extraction jobs.", status_code=400)
        _, payload = self.store.get_vault_file_bytes(file_id)
        return await self._create_job_from_source(
            filename=source.name,
            file_content=payload,
            source_file_id=file_id,
        )

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

    def cancel_job(self, job_id: str) -> ExtractionJobSummary:
        job = self.store.get_job(job_id)
        if job.status not in {JobStatus.QUEUED, JobStatus.PROCESSING}:
            return job_to_summary(job)
        job.status = JobStatus.CANCELLED
        job.error = "Job cancelled by user."
        for step in job.pipeline:
            if step.status == PipelineStepStatus.RUNNING:
                step.status = PipelineStepStatus.FAILED
                step.detail = "Cancelled by user."
        self.store.save_job(job)
        return job_to_summary(job)

    def retry_job(self, job_id: str) -> ExtractionJobSummary:
        job = self.store.get_job(job_id)
        if not self.store.artifact_exists(job.id, "source_pdf"):
            raise ProcessingError("Source PDF is not available for retry.", status_code=400)

        job.status = JobStatus.QUEUED
        job.error = None
        job.export_artifact.ready = False
        job.export_artifact.size_bytes = None
        for step in job.pipeline:
            if step.key == "upload":
                step.status = PipelineStepStatus.COMPLETED
                step.detail = "Source document encrypted and stored."
            elif step.key == "extract":
                step.status = PipelineStepStatus.PENDING
                step.detail = "Queued to retry failed parse batches."
            else:
                step.status = PipelineStepStatus.PENDING
                step.detail = "Waiting for retry."
        self.store.save_job(job)
        return job_to_summary(job)

    def delete_job(self, job_id: str) -> None:
        self.store.delete_job(job_id)

    def recover_incomplete_jobs(self) -> list[str]:
        recovered_job_ids: list[str] = []

        for job in self.store.list_job_details():
            if job.status not in {JobStatus.QUEUED, JobStatus.PROCESSING}:
                continue

            job.status = JobStatus.QUEUED
            for step in job.pipeline:
                if step.status == PipelineStepStatus.RUNNING:
                    step.status = PipelineStepStatus.PENDING
                    step.detail = "Queued to resume after server restart."

            self.store.save_job(job)
            recovered_job_ids.append(job.id)

        return recovered_job_ids

    async def process_job(self, job_id: str) -> None:
        if job_id in self._running_jobs:
            logger.info("Skipping duplicate process request for %s", job_id)
            return

        self._running_jobs.add(job_id)
        job = self.store.get_job(job_id)
        source_bytes = self.store.read_artifact(job_id, "source_pdf")

        try:
            logger.info("Starting extraction job %s for %s", job.id, job.filename)
            self._raise_if_cancelled(job)
            job.status = JobStatus.PROCESSING
            self._set_step(
                job,
                "extract",
                PipelineStepStatus.RUNNING,
                f"Rendering pages and parsing page-local signals with {self._ai_provider_label()}.",
            )
            self._set_step(job, "boundary", PipelineStepStatus.PENDING, "Waiting for parsed page signals.")
            self._set_step(job, "summary", PipelineStepStatus.PENDING, "Waiting for patient boundary resolution.")
            self.store.save_job(job)

            payload = await self._extract_job_payload(source_bytes, job.filename, job)
            job.pages = []
            job.documents = []
            job.patients = payload["patients"]
            job.page_count = payload["page_count"]
            job.patient_count = len(job.patients)
            job.document_count = sum(len(patient.office_visits) for patient in job.patients)
            job.capture_certification = (
                self._clean_text(payload.get("capture_certification"))
                or f"Parsed {job.page_count} page(s) and prepared {job.patient_count} patient section(s)."
            )
            self._set_step(
                job,
                "extract",
                PipelineStepStatus.COMPLETED,
                self._clean_text(payload.get("extract_detail")) or f"Parsed {job.page_count} page-local row(s).",
            )
            self._set_step(
                job,
                "boundary",
                PipelineStepStatus.COMPLETED,
                self._clean_text(payload.get("boundary_detail")) or "Resolved patient boundaries from parsed pages.",
            )
            self._set_step(
                job,
                "summary",
                PipelineStepStatus.COMPLETED,
                self._clean_text(payload.get("summary_detail")) or "Prepared patient summaries and opinions.",
            )
            self._set_step(job, "export", PipelineStepStatus.RUNNING, "Generating Word-compatible .doc export.")
            self.store.save_job(job)

            logger.info(
                "Extraction job %s produced %s patients across %s pages",
                job.id,
                job.patient_count,
                job.page_count,
            )
            self._refresh_export_filename(job)
            export_bytes = self.exporter.render(job)
            self.store.save_artifact(job.id, "summary_doc", export_bytes)
            self.store.save_artifact(
                job.id,
                "extraction_result",
                json.dumps(
                    {
                        "patients": [patient.model_dump(mode="json") for patient in job.patients],
                        "page_count": job.page_count,
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
            logger.info("Completed extraction job %s", job.id)
        except JobCancelled:
            self._mark_job_cancelled(job)
        except Exception as exc:
            self._mark_job_failed(job, exc)
        finally:
            self._running_jobs.discard(job_id)

    def _update_job_progress(
        self,
        job: ExtractionJobDetail | None,
        step_key: str,
        detail: str,
        *,
        status: PipelineStepStatus | None = None,
    ) -> None:
        if not job:
            logger.info(detail)
            return

        if status is not None:
            self._set_step(job, step_key, status, detail)
        else:
            for step in job.pipeline:
                if step.key == step_key:
                    step.detail = detail
                    break
        self.store.save_job(job)
        logger.info("Job %s: %s", job.id, detail)

    async def _extract_job_payload(
        self,
        file_content: bytes,
        filename: str,
        job: ExtractionJobDetail | None = None,
    ) -> dict[str, Any]:
        actual_page_count = self._count_pdf_pages(file_content)
        if actual_page_count <= 0:
            raise self._provider_extraction_error("Unable to determine PDF page count for image-based page parsing.")

        self._update_job_progress(job, "extract", f"{self._ai_provider_label()} page parsing in progress.")
        pages = await self._extract_pages_with_ai(file_content, actual_page_count, job=job)
        extract_detail = f"Parsed {len(pages)} page-local row(s) from {actual_page_count} rendered page(s)."
        self._update_job_progress(job, "extract", extract_detail, status=PipelineStepStatus.COMPLETED)

        self._update_job_progress(job, "boundary", "Deriving patient candidates from parsed pages.", status=PipelineStepStatus.RUNNING)
        pages, fallback_patients, boundary_detail, boundary_strategy = await self._assign_patient_coverage(
            file_content,
            filename,
            pages,
            job=job,
        )
        self._update_job_progress(job, "boundary", boundary_detail, status=PipelineStepStatus.COMPLETED)

        documents = self._build_document_groups(pages) if pages else []
        if documents:
            self._update_job_progress(job, "summary", "Preparing patient bundles.", status=PipelineStepStatus.RUNNING)
            patients = await self._build_patient_groups(documents, pages, job)
        else:
            patients = fallback_patients

        inferred_page_count = max(
            [patient.page_end for patient in patients if patient.page_end > 0] + [0]
        )
        patients = self._collapse_patient_sections(patients)
        if documents:
            self._attach_records_to_patients(patients, documents)

        return {
            "page_count": int(actual_page_count or inferred_page_count or 0),
            "patients": patients,
            "extract_detail": extract_detail,
            "boundary_detail": boundary_detail,
            "summary_detail": "Prepared patient summaries and opinions.",
            "capture_certification": (
                f"Parsed {actual_page_count} page(s) into page-local signals and resolved "
                f"{len(patients)} patient section(s) using {boundary_strategy}."
            ),
        }

    def _parse_page_extractions(self, result: Any) -> list[PageExtraction]:
        payload = result.model_dump(mode="json")
        rows = payload.get("data") or payload.get("extract_result") or []
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            return []

        pages: list[PageExtraction] = []
        for page_number, item in enumerate(rows, start=1):
            if not isinstance(item, dict):
                continue

            patient_name = self._clean_patient_label(item.get("patient_name"))
            patient_dob = self._clean_text(item.get("patient_dob"))
            patient_identifier = self._clean_text(item.get("patient_identifier"))
            pages.append(
                PageExtraction(
                    page_number=page_number,
                    patient_name=patient_name,
                    patient_dob=patient_dob,
                    patient_identifier=patient_identifier,
                    patient_key=self._build_patient_key(patient_name, patient_dob, patient_identifier),
                    mentioned_patient_names=self._clean_name_list(item.get("mentioned_patient_names")),
                    document_title=self._clean_text(item.get("document_title")),
                    document_type=self._clean_text(item.get("document_type")),
                    document_bucket=self._parse_summary_kind(item.get("document_bucket")),
                    document_date=self._normalize_document_date(item.get("document_date"), patient_dob),
                    author=self._clean_text(item.get("author")),
                    visible_text=self._clean_text(item.get("visible_text")) or "",
                )
            )

        logger.info("Built %s page-local extraction rows for record indexing", len(pages))
        return pages

    async def _extract_pages_with_ai(
        self,
        file_content: bytes,
        page_count: int,
        *,
        job: ExtractionJobDetail | None = None,
    ) -> list[PageExtraction]:
        if page_count <= 0:
            return []

        batch_size = max(1, settings.OPENAI_PAGE_BATCH_SIZE)
        total_batches = max(1, (page_count + batch_size - 1) // batch_size)
        concurrency = max(1, settings.AI_PAGE_CONCURRENCY)
        extracted_pages: list[PageExtraction] = []
        batches = [
            (batch_index, list(range(start + 1, min(page_count, start + batch_size) + 1)))
            for batch_index, start in enumerate(range(0, page_count, batch_size), start=1)
        ]
        batch_status: dict[int, str] = {}
        completed_batches: set[int] = set()
        failed_batches: dict[int, str] = {}
        status_lock = asyncio.Lock()
        semaphore = asyncio.Semaphore(concurrency)

        async def set_batch_status(batch_index: int, status: str) -> None:
            async with status_lock:
                batch_status[batch_index] = status
                self._update_job_progress(
                    job,
                    "extract",
                    self._format_parse_progress(
                        batch_status,
                        completed_count=len(completed_batches),
                        failed_count=len(failed_batches),
                        total_batches=total_batches,
                    ),
                )

        async def parse_batch(batch_index: int, batch_page_numbers: list[int]) -> PageParseBatchResult:
            async with semaphore:
                self._raise_if_cancelled(job)
                page_label = f"{batch_page_numbers[0]}-{batch_page_numbers[-1]}"
                checkpoint = self._load_page_parse_batch_checkpoint(job, batch_index, batch_page_numbers)
                if checkpoint is not None:
                    checkpoint_pages, checkpoint_cost = checkpoint
                    async with status_lock:
                        completed_batches.add(batch_index)
                    await set_batch_status(batch_index, f"Batch {batch_index}: cached pages {page_label} | saved cost {self._format_cost(checkpoint_cost)}")
                    return PageParseBatchResult(batch_index=batch_index, page_numbers=batch_page_numbers, pages=checkpoint_pages)

                await set_batch_status(batch_index, f"Batch {batch_index}: parsing pages {page_label}")
                try:
                    batch_pages = self._render_pdf_page_batch(pdf_document, batch_page_numbers)
                    payload = await self._request_ai_json(
                        model=self._page_model(),
                        system_prompt=OPENAI_PAGE_PROMPT,
                        user_prompt=self._build_openai_page_batch_content(batch_pages),
                        schema=OPENAI_PAGE_BATCH_SCHEMA,
                        task_label=f"{self._ai_provider_label()} page parsing batch {batch_index}/{total_batches}",
                    )
                    self._raise_if_cancelled(job)
                except JobCancelled:
                    raise
                except Exception as exc:
                    error = exc.detail if isinstance(exc, ProcessingError) else str(exc)
                    async with status_lock:
                        failed_batches[batch_index] = f"pages {page_label}: {error}"
                    await set_batch_status(batch_index, f"Batch {batch_index}: failed pages {page_label} - {self._short_error(error)}")
                    return PageParseBatchResult(batch_index=batch_index, page_numbers=batch_page_numbers, pages=[], error=error)

                rows = payload.get("pages") if isinstance(payload, dict) else []
                usage = self._pop_ai_usage(payload)
                cost = self._estimate_ai_cost(self._page_model(), usage)
                self._record_ai_usage(job, "extract", usage, cost)
                rows_by_page: dict[int, dict[str, Any]] = {}
                if isinstance(rows, list):
                    for item in rows:
                        if not isinstance(item, dict):
                            continue
                        page_number = int(item.get("page_number") or 0)
                        if page_number > 0:
                            rows_by_page[page_number] = item

                batch_extracted: list[PageExtraction] = []
                for page_number in batch_page_numbers:
                    item = rows_by_page.get(page_number, {})
                    patient_name = self._clean_patient_label(item.get("patient_name"))
                    patient_dob = self._clean_text(item.get("patient_dob"))
                    patient_identifier = self._clean_text(item.get("patient_identifier"))
                    batch_extracted.append(
                        PageExtraction(
                            page_number=page_number,
                            patient_name=patient_name,
                            patient_dob=patient_dob,
                            patient_identifier=patient_identifier,
                            patient_key=self._build_patient_key(patient_name, patient_dob, patient_identifier),
                            mentioned_patient_names=self._clean_name_list(item.get("mentioned_patient_names")),
                            document_title=self._clean_text(item.get("document_title")),
                            document_type=self._clean_text(item.get("document_type")),
                            document_bucket=self._parse_summary_kind(item.get("document_bucket")),
                            document_date=self._normalize_document_date(item.get("document_date"), patient_dob),
                            author=self._clean_text(item.get("author")),
                            visible_text=self._clean_text(item.get("visible_text")) or "",
                        )
                    )
                self._save_page_parse_batch_checkpoint(job, batch_index, batch_page_numbers, batch_extracted, cost)
                async with status_lock:
                    completed_batches.add(batch_index)
                await set_batch_status(batch_index, f"Batch {batch_index}: parsed pages {page_label} | cost {self._format_cost(cost)}")
                return PageParseBatchResult(batch_index=batch_index, page_numbers=batch_page_numbers, pages=batch_extracted)

        pdf_document = self._open_pdf_document(file_content, task_label="image-based page parsing")
        try:
            batch_results = await asyncio.gather(
                *(parse_batch(batch_index, page_numbers) for batch_index, page_numbers in batches)
            )
            failed_results = [result for result in batch_results if result.error]
            for result in batch_results:
                extracted_pages.extend(result.pages)
            if failed_results:
                failed_ranges = ", ".join(
                    f"{result.page_numbers[0]}-{result.page_numbers[-1]}" for result in failed_results
                )
                raise self._provider_extraction_error(
                    f"Page parse failed for batch page range(s): {failed_ranges}. "
                    "Completed batches were saved; retry this same file to resume only failed ranges."
                )
        finally:
            pdf_document.close()

        extracted_pages.sort(key=lambda page: page.page_number)
        logger.info("Built %s page-local extraction rows with %s parsing", len(extracted_pages), self._ai_provider_label())
        return extracted_pages

    def _format_parse_progress(
        self,
        batch_status: dict[int, str],
        *,
        completed_count: int,
        failed_count: int,
        total_batches: int,
    ) -> str:
        active_lines = [
            batch_status[index]
            for index in sorted(batch_status)
            if "parsing" in batch_status[index] or "failed" in batch_status[index]
        ]
        recent_done = [
            batch_status[index]
            for index in sorted(batch_status, reverse=True)
            if "parsed" in batch_status[index] or "cached" in batch_status[index]
        ][:6]
        return "\n".join(
            [
                f"Parsed {completed_count}/{total_batches} batches. Failed {failed_count}.",
                *active_lines,
                *reversed(recent_done),
            ]
        ).strip()

    def _short_error(self, error: str, limit: int = 180) -> str:
        text = self._clean_text(error) or "Unknown error"
        return text if len(text) <= limit else f"{text[: limit - 3]}..."

    def _provider_extraction_error(self, detail: str) -> ProcessingError:
        if settings.AI_PROVIDER == "gemini":
            return GeminiExtractionError(detail)
        return OpenAIExtractionError(detail)

    def _page_parse_batch_artifact_name(self, batch_index: int) -> str:
        return f"page_parse_batch_{batch_index:05d}"

    def _load_page_parse_batch_checkpoint(
        self,
        job: ExtractionJobDetail | None,
        batch_index: int,
        page_numbers: list[int],
    ) -> tuple[list[PageExtraction], float] | None:
        if not job:
            return None
        artifact_name = self._page_parse_batch_artifact_name(batch_index)
        if not self.store.artifact_exists(job.id, artifact_name):
            return None
        try:
            payload = json.loads(self.store.read_artifact(job.id, artifact_name).decode())
            if payload.get("cache_version") != PAGE_PARSE_CACHE_VERSION:
                return None
            if payload.get("page_numbers") != page_numbers:
                return None
            rows = payload.get("pages")
            if not isinstance(rows, list):
                return None
            pages = [PageExtraction.model_validate(row) for row in rows]
            if len(pages) != len(page_numbers):
                return None
            return pages, float(payload.get("cost_usd") or 0)
        except Exception:
            logger.warning("Ignoring corrupt page parse checkpoint %s for job %s", artifact_name, job.id, exc_info=True)
            return None

    def _save_page_parse_batch_checkpoint(
        self,
        job: ExtractionJobDetail | None,
        batch_index: int,
        page_numbers: list[int],
        pages: list[PageExtraction],
        cost_usd: float = 0,
    ) -> None:
        if not job:
            return
        payload = {
            "cache_version": PAGE_PARSE_CACHE_VERSION,
            "provider": self._ai_provider_label(),
            "model": self._page_model(),
            "cost_usd": cost_usd,
            "page_numbers": page_numbers,
            "pages": [page.model_dump(mode="json") for page in pages],
        }
        self.store.save_artifact(
            job.id,
            self._page_parse_batch_artifact_name(batch_index),
            json.dumps(payload, indent=2).encode(),
        )

    def _build_openai_page_batch_content(self, batch_pages: list[tuple[int, bytes]]) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Each page label below is followed by one rendered PDF page image. "
                    "Return one JSON row per page image and transcribe `visible_text` faithfully from the image."
                ),
            }
        ]
        for page_number, image_bytes in batch_pages:
            content.append({"type": "text", "text": f"Page {page_number}"})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": self._build_openai_image_data_url(image_bytes)},
                }
            )
        return content

    def _build_openai_image_data_url(self, image_bytes: bytes) -> str:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{OPENAI_PAGE_IMAGE_MEDIA_TYPE};base64,{encoded}"

    async def _assign_patient_coverage(
        self,
        file_content: bytes,
        filename: str,
        pages: list[PageExtraction],
        job: ExtractionJobDetail | None = None,
    ) -> tuple[list[PageExtraction], list[PatientSummary], str, str]:
        if not pages:
            return pages, [], "No parsed page signals were available for patient coverage.", "Gemini page-local signals only"

        candidates = self._discover_patient_candidates([], pages)
        if not candidates:
            unknown_candidate = self._unknown_patient_candidate(len(pages))
            detail = "No reliable patient candidates found in Gemini page signals; assigned all pages to Unknown patient."
            return self._apply_uniform_patient_coverage(pages, unknown_candidate), [], detail, "Gemini page-local signals only"

        if len(candidates) == 1:
            detail = f"Resolved patient coverage from Gemini page signals ({candidates[0]['name']})."
            logger.info(
                "Detected single-patient coverage candidate %s; assigning all %s pages to that patient",
                candidates[0]["name"],
                len(pages),
            )
            return self._apply_uniform_patient_coverage(pages, candidates[0]), [], detail, "Gemini page-local signals only"

        labels_by_page, ambiguous_pages = self._seed_patient_labels_from_pages(pages, candidates)
        labels_by_page = self._fill_stable_patient_label_gaps(len(pages), labels_by_page)
        page_windows = self._build_ambiguous_patient_windows(len(pages), labels_by_page, ambiguous_pages)

        if page_windows:
            self._update_job_progress(
                job,
                "boundary",
                f"Resolving {len(page_windows)} ambiguous patient boundary window(s) from Gemini page signals.",
            )
            labels_by_page = self._fill_ambiguous_labels_from_neighbors(len(pages), labels_by_page)
            if not labels_by_page:
                strongest_candidate = candidates[0]
                detail = (
                    "Ambiguous patient coverage could not be separated from Gemini page signals; "
                    f"assigned all pages to strongest candidate ({strongest_candidate['name']})."
                )
                return self._apply_uniform_patient_coverage(pages, strongest_candidate), [], detail, "Gemini page-local signals only"

        resolved_pages = self._apply_window_labels_to_pages(pages, labels_by_page, candidates)
        if not self._all_pages_have_patient_assignment(resolved_pages):
            labels_by_page = self._fill_ambiguous_labels_from_neighbors(len(pages), labels_by_page)
            resolved_pages = self._apply_window_labels_to_pages(pages, labels_by_page, candidates)

        if page_windows:
            detail = f"Resolved patient coverage from Gemini page signals across {len(page_windows)} ambiguous window(s)."
            strategy = "Gemini page-local signals only"
        else:
            detail = "Resolved patient coverage from Gemini page signals."
            strategy = "Gemini page-local signals only"
        return resolved_pages, [], detail, strategy

    async def _resolve_patient_coverage_with_llama_fallback(
        self,
        file_content: bytes,
        filename: str,
        pages: list[PageExtraction],
        *,
        job: ExtractionJobDetail | None = None,
        reason: str,
    ) -> tuple[list[PageExtraction], list[PatientSummary]]:
        self._update_job_progress(job, "boundary", f"{reason} Running whole-file Llama fallback.")
        async with AsyncLlamaCloud(
            api_key=settings.LLAMA_CLOUD_API_KEY,
            timeout=900.0,
        ) as client:
            uploaded_id = await self._upload_file_to_llama(client, file_content, filename)
            fallback_patients = await self._extract_patient_boundaries_with_llama(client, uploaded_id, job)
            fallback_candidates = self._discover_patient_candidates(fallback_patients, pages)
            if not fallback_candidates:
                return pages, fallback_patients
            if len(fallback_candidates) == 1:
                return self._apply_uniform_patient_coverage(pages, fallback_candidates[0]), fallback_patients
            page_windows = self._default_patient_classify_windows(len(pages))
            labels_by_page = await self._classify_patient_windows(
                client,
                uploaded_id,
                len(pages),
                fallback_candidates,
                job=job,
                page_windows=page_windows,
            )
            return self._apply_window_labels_to_pages(pages, labels_by_page, fallback_candidates), fallback_patients

    async def _classify_patient_windows_with_llama(
        self,
        file_content: bytes,
        filename: str,
        page_count: int,
        candidates: list[dict[str, Any]],
        page_windows: list[tuple[int, int]],
        *,
        job: ExtractionJobDetail | None = None,
    ) -> dict[int, str]:
        async with AsyncLlamaCloud(
            api_key=settings.LLAMA_CLOUD_API_KEY,
            timeout=900.0,
        ) as client:
            uploaded_id = await self._upload_file_to_llama(client, file_content, filename)
            return await self._classify_patient_windows(
                client,
                uploaded_id,
                page_count,
                candidates,
                job=job,
                page_windows=page_windows,
            )

    async def _upload_file_to_llama(
        self,
        client: AsyncLlamaCloud,
        file_content: bytes,
        filename: str,
    ) -> str:
        upload_stream = io.BytesIO(file_content)
        upload_stream.name = filename
        uploaded = await client.files.create(file=upload_stream, purpose="extract")
        return uploaded.id

    async def _extract_patient_boundaries_with_llama(
        self,
        client: AsyncLlamaCloud,
        uploaded_id: str,
        job: ExtractionJobDetail | None = None,
    ) -> list[PatientSummary]:
        patient_config: dict[str, Any] = {
            "data_schema": JOB_SCHEMA,
            "extraction_target": "per_doc",
            "tier": "agentic",
            "parse_tier": "agentic",
            "system_prompt": PATIENT_PROMPT,
            "cite_sources": True,
        }
        if settings.ENABLE_CONFIDENCE_SCORES:
            patient_config["confidence_scores"] = True
        self._update_job_progress(job, "boundary", "Running whole-file Llama fallback for patient sections.")
        patient_result = await self._run_extract(
            client,
            uploaded_id,
            patient_config,
            JOB_SCHEMA,
            progress_label="whole-file patient boundary fallback",
        )
        return self._parse_llama_patient_sections(patient_result)

    def _parse_llama_patient_sections(self, result: Any) -> list[PatientSummary]:
        payload = result.model_dump(mode="json")
        row = payload.get("data") or payload.get("extract_result") or {}
        if isinstance(row, list):
            row = row[0] if row else {}

        patients: list[PatientSummary] = []
        for index, item in enumerate(row.get("patients") or [], start=1):
            page_start = int(item.get("page_start") or 0)
            page_end = int(item.get("page_end") or page_start or 0)
            header_payload = item.get("header") or {}
            patients.append(
                PatientSummary(
                    id=f"patient_{index:03d}",
                    name=self._clean_patient_label(item.get("name"))
                    or self._clean_patient_label(header_payload.get("claimant"))
                    or None,
                    header=PatientHeader(
                        to_name=self._clean_text(header_payload.get("to_name")),
                        claim_number=self._clean_text(header_payload.get("claim_number")),
                        from_name=self._clean_text(header_payload.get("from_name")),
                        age_dob=self._clean_text(header_payload.get("age_dob")),
                        review_date=self._normalize_extracted_date(self._clean_text(header_payload.get("review_date"))),
                        occupation=self._clean_text(header_payload.get("occupation")),
                        claimant=self._clean_patient_label(header_payload.get("claimant"))
                        or self._clean_patient_label(item.get("name")),
                        diagnosis_dod=self._normalize_extracted_date(self._clean_text(header_payload.get("diagnosis_dod")))
                        or self._clean_text(header_payload.get("diagnosis_dod")),
                    ),
                    summary=self._clean_text(item.get("summary")) or "No patient summary generated.",
                    page_start=page_start,
                    page_end=page_end,
                    opinion=self._clean_text(item.get("opinion")) or "No patient opinion generated.",
                    office_visits=[],
                )
            )
        return patients

    def _discover_patient_candidates(
        self,
        initial_patients: list[PatientSummary],
        pages: list[PageExtraction],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []

        def upsert_candidate(
            raw_name: Any,
            *,
            claim_number: str | None = None,
            age_dob: str | None = None,
            weight: int = 1,
            first_page: int | None = None,
        ) -> None:
            clean_name = self._clean_patient_label(raw_name)
            canonical_name = self._canonical_patient_name(clean_name)
            if not clean_name or not canonical_name:
                return

            matched = next(
                (
                    candidate
                    for candidate in candidates
                    if self._canonical_names_match(candidate["canonical_name"], canonical_name)
                ),
                None,
            )
            if matched:
                matched["count"] += weight
                if first_page and (matched["first_page"] == 0 or first_page < matched["first_page"]):
                    matched["first_page"] = first_page
                if claim_number and not matched["claim_number"]:
                    matched["claim_number"] = claim_number
                if age_dob and not matched["age_dob"]:
                    matched["age_dob"] = age_dob
                if len(clean_name) > len(matched["name"]):
                    matched["name"] = clean_name
                return

            candidates.append(
                {
                    "name": clean_name,
                    "canonical_name": canonical_name,
                    "claim_number": self._clean_text(claim_number),
                    "age_dob": self._clean_text(age_dob),
                    "count": weight,
                    "first_page": first_page or 0,
                }
            )

        for patient in initial_patients:
            upsert_candidate(
                patient.header.claimant or patient.name,
                claim_number=patient.header.claim_number,
                age_dob=patient.header.age_dob,
                weight=max(5, len(patient.office_visits) + 3),
                first_page=patient.page_start or 0,
            )

        for page in pages:
            upsert_candidate(
                page.patient_name,
                claim_number=page.patient_identifier,
                age_dob=page.patient_dob,
                weight=2,
                first_page=page.page_number,
            )
            for value in page.mentioned_patient_names:
                upsert_candidate(value, weight=1, first_page=page.page_number)

        candidates.sort(
            key=lambda candidate: (
                -candidate["count"],
                candidate["first_page"] if candidate["first_page"] > 0 else 10**9,
                candidate["name"],
            )
        )
        candidates = candidates[:CLASSIFY_MAX_CANDIDATES]

        if len(candidates) > 1 and candidates[0]["count"] >= max(6, candidates[1]["count"] * 4):
            candidates = [candidates[0]]

        for index, candidate in enumerate(candidates, start=1):
            candidate["rule_type"] = f"patient_{index:03d}"

        logger.info(
            "Discovered %s patient coverage candidate(s): %s",
            len(candidates),
            ", ".join(candidate["name"] for candidate in candidates),
        )
        return candidates

    def _unknown_patient_candidate(self, page_count: int) -> dict[str, Any]:
        name = "Unknown patient"
        return {
            "name": name,
            "canonical_name": self._canonical_patient_name(name),
            "claim_number": None,
            "age_dob": None,
            "count": page_count,
            "first_page": 1,
            "rule_type": "patient_001",
        }

    def _apply_uniform_patient_coverage(
        self,
        pages: list[PageExtraction],
        candidate: dict[str, Any],
    ) -> list[PageExtraction]:
        for page in pages:
            self._apply_candidate_to_page(page, candidate)
        return pages

    def _seed_patient_labels_from_pages(
        self,
        pages: list[PageExtraction],
        candidates: list[dict[str, Any]],
    ) -> tuple[dict[int, str], list[int]]:
        labels_by_page: dict[int, str] = {}
        ambiguous_pages: list[int] = []
        for page in pages:
            matching_types = self._matching_candidate_types_for_page(page, candidates)
            if len(matching_types) == 1:
                labels_by_page[page.page_number] = matching_types[0]
            elif len(matching_types) > 1:
                ambiguous_pages.append(page.page_number)
        return labels_by_page, ambiguous_pages

    def _matching_candidate_types_for_page(
        self,
        page: PageExtraction,
        candidates: list[dict[str, Any]],
    ) -> list[str]:
        scored_matches: list[tuple[str, int]] = []
        for candidate in candidates:
            score = self._candidate_match_score(page, candidate)
            if score > 0:
                scored_matches.append((candidate["rule_type"], score))
        if not scored_matches:
            return []
        best_score = max(score for _, score in scored_matches)
        return [rule_type for rule_type, score in scored_matches if score == best_score]

    def _candidate_match_score(self, page: PageExtraction, candidate: dict[str, Any]) -> int:
        score = 0
        candidate_name = candidate.get("canonical_name") or self._canonical_patient_name(candidate.get("name"))
        page_name = self._canonical_patient_name(page.patient_name)
        if page_name and candidate_name and self._canonical_names_match(page_name, candidate_name):
            score += 6

        candidate_identifier = self._normalize_patient_identifier(candidate.get("claim_number"))
        page_identifier = self._normalize_patient_identifier(page.patient_identifier)
        if candidate_identifier and page_identifier and candidate_identifier == page_identifier:
            score += 5

        candidate_dob = self._normalize_patient_dob(candidate.get("age_dob"))
        page_dob = self._normalize_patient_dob(page.patient_dob)
        if candidate_dob and page_dob and candidate_dob == page_dob:
            score += 3

        for value in page.mentioned_patient_names:
            mentioned_name = self._canonical_patient_name(value)
            if mentioned_name and candidate_name and self._canonical_names_match(mentioned_name, candidate_name):
                score += 2
                break

        return score

    def _fill_stable_patient_label_gaps(self, page_count: int, labels_by_page: dict[int, str]) -> dict[int, str]:
        filled = dict(labels_by_page)
        page_number = 1
        while page_number <= page_count:
            if page_number in filled:
                page_number += 1
                continue

            gap_start = page_number
            while page_number <= page_count and page_number not in filled:
                page_number += 1
            gap_end = page_number - 1

            left_label = filled.get(gap_start - 1)
            right_label = filled.get(page_number)
            if left_label and right_label and left_label == right_label:
                for missing_page in range(gap_start, gap_end + 1):
                    filled[missing_page] = left_label

        return filled

    def _fill_ambiguous_labels_from_neighbors(self, page_count: int, labels_by_page: dict[int, str]) -> dict[int, str]:
        filled = dict(labels_by_page)
        known_pages = sorted(filled)
        if not known_pages:
            return filled

        for page_number in range(1, page_count + 1):
            if page_number in filled:
                continue
            previous_known = next((known for known in reversed(known_pages) if known < page_number), None)
            next_known = next((known for known in known_pages if known > page_number), None)
            if previous_known is None and next_known is None:
                continue
            if previous_known is None:
                filled[page_number] = filled[next_known]
                continue
            if next_known is None:
                filled[page_number] = filled[previous_known]
                continue
            previous_distance = page_number - previous_known
            next_distance = next_known - page_number
            filled[page_number] = filled[previous_known] if previous_distance <= next_distance else filled[next_known]

        return filled

    def _build_ambiguous_patient_windows(
        self,
        page_count: int,
        labels_by_page: dict[int, str],
        ambiguous_pages: list[int],
    ) -> list[tuple[int, int]]:
        target_pages = sorted(
            set(ambiguous_pages)
            | {page_number for page_number in range(1, page_count + 1) if page_number not in labels_by_page}
        )
        if not target_pages:
            return []

        merged_ranges: list[tuple[int, int]] = []
        range_start = target_pages[0]
        range_end = target_pages[0]
        for page_number in target_pages[1:]:
            if page_number <= range_end + 1:
                range_end = page_number
                continue
            merged_ranges.append((range_start, range_end))
            range_start = page_number
            range_end = page_number
        merged_ranges.append((range_start, range_end))

        expanded: list[tuple[int, int]] = []
        for range_start, range_end in merged_ranges:
            buffered_start = max(1, range_start - 1)
            buffered_end = min(page_count, range_end + 1)
            expanded.extend(self._split_page_window(buffered_start, buffered_end))
        return expanded

    def _default_patient_classify_windows(self, page_count: int) -> list[tuple[int, int]]:
        windows: list[tuple[int, int]] = []
        for start_page in range(1, page_count + 1, CLASSIFY_WINDOW_SIZE):
            end_page = min(page_count, start_page + CLASSIFY_WINDOW_SIZE - 1)
            windows.append((start_page, end_page))
        return windows

    def _split_page_window(self, start_page: int, end_page: int) -> list[tuple[int, int]]:
        windows: list[tuple[int, int]] = []
        page_number = start_page
        while page_number <= end_page:
            chunk_end = min(end_page, page_number + CLASSIFY_WINDOW_SIZE - 1)
            windows.append((page_number, chunk_end))
            page_number = chunk_end + 1
        return windows

    def _all_pages_have_patient_assignment(self, pages: list[PageExtraction]) -> bool:
        return all(self._normalize_key(page.patient_key or page.patient_name) for page in pages)

    async def _classify_patient_windows(
        self,
        client: AsyncLlamaCloud,
        file_id: str,
        page_count: int,
        candidates: list[dict[str, Any]],
        job: ExtractionJobDetail | None = None,
        page_windows: list[tuple[int, int]] | None = None,
    ) -> dict[int, str]:
        labels_by_page: dict[int, str] = {}
        rules = self._build_patient_classify_rules(candidates)
        windows = page_windows or self._default_patient_classify_windows(page_count)
        total_windows = len(windows)

        for window_index, (start_page, end_page) in enumerate(windows, start=1):
            page_spec = str(start_page) if start_page == end_page else f"{start_page}-{end_page}"
            self._update_job_progress(
                job,
                "boundary",
                f"Classifying patient coverage window {window_index}/{total_windows} (pages {page_spec}).",
            )
            classify_job = await client.classify.create(
                file_input=file_id,
                configuration={
                    "rules": rules,
                    "mode": "FAST",
                    "parsing_configuration": {
                        "target_pages": page_spec,
                    },
                },
            )
            logger.info(
                "Started classify window %s/%s for pages %s as job %s",
                window_index,
                total_windows,
                page_spec,
                classify_job.id,
            )
            completed = await self._poll_classify_job(client, classify_job.id, job_ref=job)
            predicted_type = self._clean_text(completed.result.type if completed.result else None)
            if not predicted_type:
                continue
            for page_number in range(start_page, end_page + 1):
                labels_by_page[page_number] = predicted_type

        logger.info("Classified %s targeted patient coverage window(s)", total_windows)
        return labels_by_page

    async def _poll_classify_job(
        self,
        client: AsyncLlamaCloud,
        job_id: str,
        *,
        job_ref: ExtractionJobDetail | None = None,
    ) -> Any:
        deadline = asyncio.get_running_loop().time() + CLASSIFY_POLL_TIMEOUT_SECONDS
        poll_count = 0
        while True:
            job = await client.classify.get(job_id)
            poll_count += 1
            if job.status == "COMPLETED":
                return job
            if job.status == "FAILED":
                raise ExtractionError(job.error_message or f"Patient coverage classify job {job_id} failed.")
            if asyncio.get_running_loop().time() >= deadline:
                raise ExtractionError(f"Timed out waiting for patient coverage classify job {job_id}.")
            if poll_count == 1 or poll_count % 10 == 0:
                self._update_job_progress(job_ref, "boundary", f"Waiting on classify job {job_id} ({job.status}).")
            await asyncio.sleep(CLASSIFY_POLL_INTERVAL_SECONDS)

    def _build_patient_classify_rules(self, candidates: list[dict[str, Any]]) -> list[dict[str, str]]:
        rules = [
            {
                "type": candidate["rule_type"],
                "description": self._patient_classify_description(candidate),
            }
            for candidate in candidates
        ]
        rules.extend(
            [
                {
                    "type": "administrative_reference",
                    "description": "Pages that are fax covers, privacy sheets, billing records, blank forms, reference instructions, generic questionnaires without patient-specific completed content, or routing/cover material.",
                },
                {
                    "type": "unclear_continuation",
                    "description": "Pages that appear to continue a patient file but do not clearly identify which patient from the candidate list they belong to.",
                },
            ]
        )
        return rules

    def _patient_classify_description(self, candidate: dict[str, Any]) -> str:
        qualifiers = [f"patient or claimant {candidate['name']}"]
        if candidate.get("claim_number"):
            qualifiers.append(f"claim/member/file number {candidate['claim_number']}")
        if candidate.get("age_dob"):
            qualifiers.append(f"age or DOB {candidate['age_dob']}")
        qualifiers_text = ", ".join(qualifiers)
        return (
            f"Pages belonging to {qualifiers_text}. Include continuation pages where the patient name is omitted but the "
            "clinical, insurer, rehabilitation, or functional context clearly continues the same patient's file."
        )

    def _apply_window_labels_to_pages(
        self,
        pages: list[PageExtraction],
        labels_by_page: dict[int, str],
        candidates: list[dict[str, Any]],
    ) -> list[PageExtraction]:
        candidate_by_type = {candidate["rule_type"]: candidate for candidate in candidates}
        patient_types = set(candidate_by_type)

        for page in pages:
            label = labels_by_page.get(page.page_number)
            if label in patient_types:
                self._apply_candidate_to_page(page, candidate_by_type[label])
                continue

            if len(candidates) == 1:
                self._apply_candidate_to_page(page, candidates[0])
                continue

            fallback_label = self._nearest_patient_window_label(labels_by_page, page.page_number, patient_types)
            if fallback_label and fallback_label in candidate_by_type:
                self._apply_candidate_to_page(page, candidate_by_type[fallback_label])

        return pages

    def _nearest_patient_window_label(
        self,
        labels_by_page: dict[int, str],
        page_number: int,
        patient_types: set[str],
    ) -> str | None:
        previous_label = None
        next_label = None

        previous_page = page_number - 1
        while previous_page > 0:
            label = labels_by_page.get(previous_page)
            if label in patient_types:
                previous_label = label
                break
            previous_page -= 1

        next_page = page_number + 1
        max_page = max(labels_by_page, default=0)
        while next_page <= max_page:
            label = labels_by_page.get(next_page)
            if label in patient_types:
                next_label = label
                break
            next_page += 1

        if previous_label and next_label and previous_label == next_label:
            return previous_label
        return previous_label or next_label

    def _apply_candidate_to_page(self, page: PageExtraction, candidate: dict[str, Any]) -> None:
        page.patient_name = candidate["name"]
        if candidate.get("age_dob") and not self._clean_text(page.patient_dob):
            page.patient_dob = candidate["age_dob"]
        if candidate.get("claim_number") and not self._clean_text(page.patient_identifier):
            page.patient_identifier = candidate["claim_number"]
        page.patient_key = (
            self._build_patient_key(page.patient_name, page.patient_dob, page.patient_identifier)
            or self._normalize_key(candidate.get("claim_number") or candidate["name"])
            or page.patient_key
        )
        if candidate["name"] not in page.mentioned_patient_names:
            page.mentioned_patient_names.append(candidate["name"])

    def _attach_records_to_patients(self, patients: list[PatientSummary], documents: list[DocumentSummary]) -> None:
        for patient in patients:
            patient_docs = [
                document
                for document in documents
                if document.page_numbers
                and self._document_overlaps_patient(document, patient)
                and document.include_in_output
            ]
            patient.office_visits = self._documents_to_office_visits(patient_docs)

            if (patient.page_start <= 0 or patient.page_end <= 0) and patient.office_visits:
                patient.page_start = min(visit.page_start for visit in patient.office_visits)
                patient.page_end = max(visit.page_end for visit in patient.office_visits)

            patient_name = self._best_patient_name_from_documents(patient_docs)
            if patient_name and not self._clean_patient_label(patient.name):
                patient.name = patient_name
            if patient_name and not self._clean_patient_label(patient.header.claimant):
                patient.header.claimant = patient_name

    def _document_overlaps_patient(self, document: DocumentSummary, patient: PatientSummary) -> bool:
        if not document.page_numbers:
            return False
        if patient.page_start <= 0 or patient.page_end <= 0:
            return True

        doc_start = document.page_numbers[0]
        doc_end = document.page_numbers[-1]
        return not (doc_end < patient.page_start or doc_start > patient.page_end)

    def _documents_to_office_visits(self, documents: list[DocumentSummary]) -> list[OfficeVisitItem]:
        visits: list[OfficeVisitItem] = []
        seen: set[tuple[str, str, str, int, int]] = set()

        for document in sorted(
            documents,
            key=lambda item: (
                item.page_numbers[0] if item.page_numbers else 10**9,
                item.page_numbers[-1] if item.page_numbers else 10**9,
                item.id,
            ),
        ):
            if not document.page_numbers:
                continue
            # UI "Office Visits" list should reflect the indexed record order.
            # Keep clinically/functionally relevant referrals, but drop admin/noise/signature-only pages.
            if not self._document_should_appear_in_office_visits(document):
                continue

            visit = OfficeVisitItem(
                title=document.title or document.document_type or "Record",
                date=self._normalize_extracted_date(document.document_date),
                author=self._doctor_last_name_only(self._display_document_author(document)),
                page_start=document.page_numbers[0],
                page_end=document.page_numbers[-1],
            )
            visit_key = (
                self._normalize_key(visit.title),
                self._normalize_key(visit.date),
                self._normalize_key(visit.author),
                visit.page_start,
                visit.page_end,
            )
            if visit_key in seen:
                continue
            seen.add(visit_key)
            visits.append(visit)

        return visits

    def _doctor_last_name_only(self, value: str | None) -> str | None:
        text = self._clean_text(value)
        if not text:
            return None

        def repl(match: re.Match[str]) -> str:
            prefix = match.group(1)
            full_name = match.group(2)
            parts = [part for part in full_name.strip().split() if part]
            last_name = parts[-1] if parts else full_name
            return f"{prefix}{last_name}"

        return DOCTOR_NAME_PATTERN.sub(repl, text)

    def _display_document_author(self, document: DocumentSummary) -> str | None:
        author = self._format_author(document.author, document.author_role)
        if document.summary_kind == SummaryKind.IMAGING and self._looks_like_ordering_provider(author):
            return None
        return author

    def _looks_like_ordering_provider(self, value: str | None) -> bool:
        text = self._clean_text(value)
        if not text:
            return False
        letters = re.sub(r"[^A-Za-z]", "", text)
        return bool(letters) and letters.isupper() and "," in text

    def _best_patient_name_from_documents(self, documents: list[DocumentSummary]) -> str | None:
        candidates: list[str] = []
        for document in documents:
            if cleaned := self._clean_patient_label(document.patient_name):
                candidates.append(cleaned)
            for value in document.mentioned_patient_names:
                if cleaned := self._clean_patient_label(value):
                    candidates.append(cleaned)

        if not candidates:
            return None
        return Counter(candidates).most_common(1)[0][0]

    def _resolve_patient_header(
        self,
        extracted_header: PatientHeader,
        documents: list[DocumentSummary],
        pages: list[PageExtraction],
        fallback_name: str | None,
    ) -> PatientHeader:
        anchor = self._select_header_anchor_document(documents)
        anchored_header = self._extract_header_from_document(anchor, pages) if anchor else PatientHeader()
        best_name = self._clean_patient_label(anchored_header.claimant) or self._clean_patient_label(extracted_header.claimant)
        if not best_name:
            best_name = self._clean_patient_label(fallback_name) or self._best_patient_name_from_documents(documents)

        return PatientHeader(
            to_name=anchored_header.to_name or extracted_header.to_name,
            claim_number=anchored_header.claim_number or extracted_header.claim_number,
            from_name=anchored_header.from_name or extracted_header.from_name,
            age_dob=anchored_header.age_dob or extracted_header.age_dob,
            review_date=anchored_header.review_date or extracted_header.review_date,
            occupation=anchored_header.occupation or extracted_header.occupation,
            claimant=best_name,
            diagnosis_dod=anchored_header.diagnosis_dod or extracted_header.diagnosis_dod,
        )

    def _select_header_anchor_document(self, documents: list[DocumentSummary]) -> DocumentSummary | None:
        ranked = sorted(
            documents,
            key=lambda document: (
                -self._header_anchor_score(document),
                document.page_numbers[0] if document.page_numbers else 10**9,
                document.id,
            ),
        )
        return ranked[0] if ranked and self._header_anchor_score(ranked[0]) > 0 else None

    def _header_anchor_score(self, document: DocumentSummary) -> int:
        title = self._normalize_key(document.title or document.document_type)
        if "medical file review referral form" in title:
            return 100
        if "medical consultant referral form" in title:
            return 95
        if "file review referral" in title:
            return 90
        if "referral form" in title:
            return 70
        if "ltd report" in title:
            return 60
        if self._document_is_noise(document):
            return -10
        return 0

    def _extract_header_from_document(
        self,
        document: DocumentSummary | None,
        pages: list[PageExtraction],
    ) -> PatientHeader:
        if not document or not document.page_numbers:
            return PatientHeader()

        page_map = {page.page_number: page for page in pages}
        anchor_page_number = document.page_numbers[0]
        anchor_text = page_map.get(anchor_page_number).visible_text if anchor_page_number in page_map else ""
        text = anchor_text or "\n".join(
            page_map[page_number].visible_text
            for page_number in document.page_numbers
            if page_number in page_map and page_map[page_number].visible_text.strip()
        )
        if not text:
            return PatientHeader()

        return PatientHeader(
            to_name=self._search_header_value(text, [r"(?im)^TO:\s*(.+)$"]),
            claim_number=self._search_header_value(
                text,
                [
                    r"(?im)^Claim Reference #:\s*(.+)$",
                    r"(?im)^Claim(?: Number| #)?:\s*(.+)$",
                ],
            ),
            from_name=self._search_header_block(
                text,
                r"(?is)FROM:\s*(.*?)\n(?:Referred By:|Claim Type|LDW:|Date:|Claim Status)",
            ),
            age_dob=self._search_header_value(
                text,
                [
                    r"(?im)^DOB:\s*(.+?Age:\s*.+)$",
                    r"(?im)^Age:\s*(.+)$",
                ],
            ),
            review_date=self._normalize_extracted_date(
                self._search_header_value(
                    text,
                    [
                        r"(?im)^LDW:\s*(.+)$",
                        r"(?im)^Date:\s*(.+)$",
                    ],
                )
            ),
            occupation=self._search_header_value(text, [r"(?im)^Occupation:\s*(.+)$"]),
            claimant=self._clean_patient_label(
                self._search_header_value(
                    text,
                    [
                        r"(?im)^Claimant.?s Name:\s*(.+)$",
                        r"(?im)^Claimant:\s*(.+)$",
                    ],
                )
            ),
            diagnosis_dod=self._search_header_block(
                text,
                r"(?is)Previous Medical opinion\s*(.*?)(?:\n\s*\n|Page \d+|$)",
            ),
        )

    def _search_header_value(self, text: str, patterns: list[str]) -> str | None:
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                value = self._clean_text(match.group(1))
                if value:
                    return value
        return None

    def _search_header_block(self, text: str, pattern: str) -> str | None:
        match = re.search(pattern, text)
        if not match:
            return None
        value = "\n".join(part.strip() for part in match.group(1).splitlines() if part.strip())
        return self._clean_text(value)

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

        self._backfill_document_patient_context(documents)
        patient_group_ids: dict[str, str] = {}
        next_group = 1
        for document in documents:
            patient_key = self._normalize_key(document.patient_key or document.patient_name)
            if not patient_key:
                continue
            if patient_key not in patient_group_ids:
                patient_group_ids[patient_key] = f"patient_{next_group:03d}"
                next_group += 1
            document.patient_group_id = patient_group_ids[patient_key]

        return documents

    async def _summarize_documents(
        self,
        documents: list[DocumentSummary],
        pages: list[PageExtraction],
    ) -> list[DocumentSummary]:
        page_map = {page.page_number: page for page in pages}
        summarized: list[DocumentSummary] = []

        for document in documents:
            text = "\n\n".join(
                page_map[page_number].visible_text.strip()
                for page_number in document.page_numbers
                if page_number in page_map and page_map[page_number].visible_text.strip()
            ).strip()

            if not document.include_in_output:
                document.summary = "Excluded from medico-legal output because the indexed document appears administrative."
                summarized.append(document)
                continue

            if not text:
                document.summary = "No extractable text was captured for this document."
                summarized.append(document)
                continue

            document.summary = self._build_extractive_summary(document, text)
            summarized.append(document)

        return summarized

    async def _build_patient_groups(
        self,
        documents: list[DocumentSummary],
        pages: list[PageExtraction],
        job: ExtractionJobDetail | None = None,
    ) -> list[PatientSummary]:
        buckets: dict[str, list[DocumentSummary]] = {}
        order: list[str] = []

        for index, document in enumerate(documents, start=1):
            bucket_key = self._patient_bucket_key(document, index)
            if bucket_key not in buckets:
                buckets[bucket_key] = []
                order.append(bucket_key)
            buckets[bucket_key].append(document)

        patients: list[PatientSummary] = []
        total_buckets = len(order)
        concurrency = max(1, settings.AI_PAGE_CONCURRENCY)
        semaphore = asyncio.Semaphore(concurrency)
        summary_status: dict[int, str] = {
            index: f"Bundle {index}: queued"
            for index in range(1, total_buckets + 1)
        }
        completed_bundles: set[int] = set()
        cached_bundles: set[int] = set()
        failed_bundles: set[int] = set()
        status_lock = asyncio.Lock()

        async def set_bundle_status(index: int, status: str) -> None:
            async with status_lock:
                summary_status[index] = status
                self._update_job_progress(
                    job,
                    "summary",
                    self._format_summary_progress(
                        summary_status,
                        completed_count=len(completed_bundles),
                        cached_count=len(cached_bundles),
                        failed_count=len(failed_bundles),
                        total_bundles=total_buckets,
                    ),
                )

        async def process_bucket(index: int, bucket_key: str) -> PatientSummary:
            bucket_documents = buckets[bucket_key]
            patient_documents = self._select_patient_documents(bucket_documents)
            summary_documents = patient_documents or bucket_documents
            all_page_numbers = sorted(
                {
                    page_number
                    for document in bucket_documents
                    for page_number in document.page_numbers
                }
            )
            async with semaphore:
                page_label = self._page_range(all_page_numbers)
                cached_payload = self._load_patient_bundle_checkpoint(job, index, summary_documents)
                if cached_payload is not None:
                    self._raise_if_cancelled(job)
                    cached_bundles.add(index)
                    completed_bundles.add(index)
                    cached_cost = float(cached_payload.pop("__checkpoint_cost_usd", 0) or 0)
                    await set_bundle_status(index, f"Bundle {index}: cached pages {page_label} | saved cost {self._format_cost(cached_cost)}")
                    patient_payload = cached_payload
                else:
                    self._raise_if_cancelled(job)
                    await set_bundle_status(index, f"Bundle {index}: extracting pages {page_label}")
                    bundle_cost_before = self._pipeline_step_cost(job, "summary")
                    try:
                        patient_payload = await self._extract_patient_payload(
                            summary_documents,
                            pages,
                            index,
                            job,
                            progress_update=lambda status: set_bundle_status(index, f"Bundle {index}: {status} pages {page_label}"),
                        )
                    except JobCancelled:
                        raise
                    except Exception:
                        failed_bundles.add(index)
                        await set_bundle_status(index, f"Bundle {index}: failed pages {page_label}")
                        raise
                    bundle_cost = max(0.0, self._pipeline_step_cost(job, "summary") - bundle_cost_before)
                    self._save_patient_bundle_checkpoint(job, index, summary_documents, patient_payload, bundle_cost)
                    completed_bundles.add(index)
                    await set_bundle_status(index, f"Bundle {index}: done pages {page_label} | cost {self._format_cost(bundle_cost)}")

            patient_header = self._resolve_patient_header(
                patient_payload.get("header", PatientHeader()),
                bucket_documents,
                pages,
                patient_payload.get("name"),
            )
            patient_name = (
                self._clean_patient_label(patient_payload["name"])
                or self._clean_patient_label(patient_header.claimant)
                or self._best_patient_name_from_documents(summary_documents)
                or self._best_patient_name_from_documents(bucket_documents)
            )
            return PatientSummary(
                id=f"patient_{index:03d}",
                name=patient_name,
                header=patient_header,
                summary=patient_payload["summary"],
                page_start=all_page_numbers[0] if all_page_numbers else 0,
                page_end=all_page_numbers[-1] if all_page_numbers else 0,
                opinion=patient_payload["opinion"],
                office_visits=self._documents_to_office_visits(bucket_documents),
            )

        patients = await asyncio.gather(*(process_bucket(index, bucket_key) for index, bucket_key in enumerate(order, start=1)))
        return patients

    def _format_summary_progress(
        self,
        summary_status: dict[int, str],
        *,
        completed_count: int,
        cached_count: int,
        failed_count: int,
        total_bundles: int,
    ) -> str:
        validating_count = sum(1 for status in summary_status.values() if "validating" in status)
        processing_count = sum(1 for status in summary_status.values() if "extracting" in status or "validating" in status)
        active_lines = [
            summary_status[index]
            for index in sorted(summary_status)
            if any(token in summary_status[index] for token in ("extracting", "validating", "failed"))
        ]
        recent_done = [
            summary_status[index]
            for index in sorted(summary_status, reverse=True)
            if "done" in summary_status[index] or "cached" in summary_status[index]
        ][:6]
        return "\n".join(
            [
                (
                    f"Summarized {completed_count}/{total_bundles} bundles. "
                    f"Processing {processing_count}. Validating {validating_count}. Cached {cached_count}. Failed {failed_count}."
                ),
                *active_lines,
                *reversed(recent_done),
            ]
        ).strip()

    def _patient_bundle_artifact_name(self, patient_index: int) -> str:
        return f"patient_bundle_{patient_index:05d}"

    def _patient_bundle_signature(self, documents: list[DocumentSummary]) -> list[dict[str, Any]]:
        return [
            {
                "id": document.id,
                "page_numbers": document.page_numbers,
                "title": document.title,
                "date": document.document_date,
            }
            for document in documents
        ]

    def _load_patient_bundle_checkpoint(
        self,
        job: ExtractionJobDetail | None,
        patient_index: int,
        documents: list[DocumentSummary],
    ) -> dict[str, Any] | None:
        if not job:
            return None
        artifact_name = self._patient_bundle_artifact_name(patient_index)
        if not self.store.artifact_exists(job.id, artifact_name):
            return None
        try:
            payload = json.loads(self.store.read_artifact(job.id, artifact_name).decode())
            if payload.get("cache_version") != PATIENT_BUNDLE_CACHE_VERSION:
                return None
            if payload.get("signature") != self._patient_bundle_signature(documents):
                return None
            row = payload.get("payload")
            if not isinstance(row, dict):
                return None
            header = row.get("header")
            row["header"] = PatientHeader.model_validate(header) if isinstance(header, dict) else PatientHeader()
            row["office_visits"] = self._parse_office_visits(row.get("office_visits"))
            row["__checkpoint_cost_usd"] = payload.get("cost_usd") or 0
            return row
        except Exception:
            logger.warning("Ignoring corrupt patient bundle checkpoint %s for job %s", artifact_name, job.id, exc_info=True)
            return None

    def _save_patient_bundle_checkpoint(
        self,
        job: ExtractionJobDetail | None,
        patient_index: int,
        documents: list[DocumentSummary],
        payload: dict[str, Any],
        cost_usd: float = 0,
    ) -> None:
        if not job:
            return
        self.store.save_artifact(
            job.id,
            self._patient_bundle_artifact_name(patient_index),
            json.dumps(
                {
                    "cache_version": PATIENT_BUNDLE_CACHE_VERSION,
                    "provider": self._ai_provider_label(),
                    "model": self._bundle_model(),
                    "cost_usd": cost_usd,
                    "signature": self._patient_bundle_signature(documents),
                    "payload": self._jsonable_patient_payload(payload),
                },
                indent=2,
            ).encode(),
        )

    def _jsonable_patient_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            **payload,
            "header": payload["header"].model_dump(mode="json") if isinstance(payload.get("header"), PatientHeader) else payload.get("header"),
            "office_visits": [
                visit.model_dump(mode="json") if isinstance(visit, OfficeVisitItem) else visit
                for visit in (payload.get("office_visits") or [])
            ],
        }

    def _pop_ai_usage(self, payload: dict[str, Any]) -> dict[str, int]:
        usage = payload.pop("__usage_metadata", None)
        return usage if isinstance(usage, dict) else {}

    def _estimate_ai_cost(self, model: str, usage: dict[str, int]) -> float:
        input_tokens = max(1, int(usage.get("input_tokens") or 0))
        output_tokens = max(1, int(usage.get("output_tokens") or 0))
        model_key = model.lower()
        if "flash-lite" in model_key:
            input_rate, output_rate = 0.10, 0.40
        elif "flash" in model_key:
            input_rate, output_rate = 0.30, 2.50
        elif "gpt-4.1-mini" in model_key:
            input_rate, output_rate = 0.40, 1.60
        elif "gpt-5.2" in model_key:
            input_rate, output_rate = 1.75, 14.00
        elif "gpt-5" in model_key:
            input_rate, output_rate = 1.25, 10.00
        else:
            input_rate, output_rate = 0.30, 2.50
        return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000

    def _fallback_usage_from_request(self, model: str, user_prompt: str | list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, int]:
        output_text = json.dumps({key: value for key, value in payload.items() if key != "__usage_metadata"}, ensure_ascii=False)
        output_tokens = max(1, len(output_text) // 4)

        if isinstance(user_prompt, str):
            input_tokens = max(1, len(user_prompt) // 4)
        else:
            input_tokens = 0
            for item in user_prompt:
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    input_tokens += max(1, len(item["text"]) // 4)
                elif item.get("type") == "image_url":
                    input_tokens += self._fallback_image_tokens(model)

        return {
            "input_tokens": max(1, input_tokens),
            "output_tokens": output_tokens,
            "total_tokens": max(1, max(1, input_tokens) + output_tokens),
        }

    def _fallback_image_tokens(self, model: str) -> int:
        model_key = model.lower()
        if "gemini" in model_key:
            return 1290
        return 1105

    def _record_ai_usage(
        self,
        job: ExtractionJobDetail | None,
        step_key: str,
        usage: dict[str, int],
        cost_usd: float,
    ) -> None:
        if not job:
            return
        for step in job.pipeline:
            if step.key == step_key:
                step.cost_usd = round(float(step.cost_usd or 0) + cost_usd, 8)
                step.input_tokens = int(step.input_tokens or 0) + int(usage.get("input_tokens") or 0)
                step.output_tokens = int(step.output_tokens or 0) + int(usage.get("output_tokens") or 0)
                break
        self.store.save_job(job)

    def _pipeline_step_cost(self, job: ExtractionJobDetail | None, step_key: str) -> float:
        if not job:
            return 0
        step = next((item for item in job.pipeline if item.key == step_key), None)
        return float(step.cost_usd or 0) if step else 0

    def _format_cost(self, value: float) -> str:
        if value < 0.0001:
            return f"${value:.6f}"
        return f"${value:.4f}"

    def _starts_new_group(self, current_pages: list[PageExtraction], page: PageExtraction) -> bool:
        if not current_pages:
            return True

        previous = current_pages[-1]
        current_patient_key = self._normalize_key(page.patient_key or page.patient_name)
        previous_patient_key = self._normalize_key(previous.patient_key or previous.patient_name)

        if page.starts_new_patient or page.starts_new_document:
            return True

        if page.page_role in {PageRole.DOCUMENT_START, PageRole.COVER} and current_pages:
            return True

        if current_patient_key and previous_patient_key and current_patient_key != previous_patient_key:
            return True

        same_title = self._normalize_key(page.document_title) == self._normalize_key(previous.document_title)
        same_type = self._normalize_key(page.document_type) == self._normalize_key(previous.document_type)
        same_date = self._normalize_key(page.document_date) == self._normalize_key(previous.document_date)

        if page.document_boundary_reason:
            return True
        if page.patient_boundary_reason:
            return True
        if not same_title and not same_type and self._normalize_key(page.document_title):
            return True
        if not same_type and not same_date and self._normalize_key(page.document_type):
            return True
        if previous.page_role == PageRole.SEPARATOR and page.page_role != PageRole.SEPARATOR:
            return True

        return False

    def _finalize_document(self, existing_documents: list[DocumentSummary], pages: list[PageExtraction]) -> DocumentSummary:
        document_index = len(existing_documents) + 1
        patient_name = self._first_value(page.patient_name for page in pages)
        patient_dob = self._first_value(page.patient_dob for page in pages)
        patient_identifier = self._first_value(page.patient_identifier for page in pages)
        patient_key = self._build_patient_key(patient_name, patient_dob, patient_identifier)
        mentioned_patient_names = self._merge_name_lists(page.mentioned_patient_names for page in pages)
        document_title = self._first_value(page.document_title for page in pages)
        document_type = self._most_common(page.document_type for page in pages)
        document_date = self._first_value(page.document_date for page in pages)
        author = self._first_value(page.author for page in pages)
        author_role = self._first_value(page.author_role for page in pages)
        page_numbers = [page.page_number for page in pages]
        classification = self._most_common_relevance(page.clinical_relevance for page in pages)
        summary_kind = self._most_common_summary_kind(page.document_bucket for page in pages)
        if summary_kind == SummaryKind.UNKNOWN:
            summary_kind = self._infer_summary_kind(document_title or document_type, classification)
        include_in_output = self._document_is_in_scope(classification, summary_kind)
        title = document_title or document_type or f"Document {document_index}"
        accession_number = self._first_value(page.accession_number for page in pages)
        exam_title = self._first_value(page.exam_title for page in pages)
        radiologist_name = self._first_value(page.radiologist_name for page in pages)
        narrative_report_available = self._merge_optional_bools(page.narrative_report_available for page in pages)

        return DocumentSummary(
            id=f"doc_{document_index:03d}",
            title=title,
            patient_name=patient_name,
            patient_dob=patient_dob,
            patient_identifier=patient_identifier,
            patient_key=patient_key,
            mentioned_patient_names=mentioned_patient_names,
            document_type=document_type,
            document_date=document_date,
            author=author,
            author_role=author_role,
            page_numbers=page_numbers,
            page_range=self._page_range(page_numbers),
            classification=classification,
            summary_kind=summary_kind,
            include_in_output=include_in_output,
            capture_status="captured" if include_in_output else "excluded",
            accession_number=accession_number,
            exam_title=exam_title,
            radiologist_name=radiologist_name,
            narrative_report_available=narrative_report_available,
        )

    def _refresh_export_filename(self, job: ExtractionJobDetail) -> None:
        preferred_patient = next((patient.name for patient in job.patients if patient.name), None)
        if not preferred_patient:
            preferred_patient = next((document.patient_name for document in job.documents if document.patient_name), None)
        job.export_artifact.filename = self.store.build_export_filename(job.filename, preferred_patient)

    def _mark_job_failed(self, job: ExtractionJobDetail, exc: Exception) -> None:
        detail = exc.detail if isinstance(exc, ProcessingError) else str(exc)
        logger.exception("Extraction job %s failed: %s", job.id, detail)
        for step in job.pipeline:
            if step.status == PipelineStepStatus.RUNNING:
                step.status = PipelineStepStatus.FAILED
                step.detail = detail
                break

        job.status = JobStatus.FAILED
        job.error = detail
        self.store.save_job(job)

    def _mark_job_cancelled(self, job: ExtractionJobDetail) -> None:
        logger.info("Extraction job %s cancelled", job.id)
        for step in job.pipeline:
            if step.status == PipelineStepStatus.RUNNING:
                step.status = PipelineStepStatus.FAILED
                step.detail = "Cancelled by user."
                break
        job.status = JobStatus.CANCELLED
        job.error = "Job cancelled by user."
        self.store.save_job(job)

    def _raise_if_cancelled(self, job: ExtractionJobDetail | None) -> None:
        if not job:
            return
        try:
            latest = self.store.get_job(job.id)
        except Exception:
            latest = job
        if latest.status == JobStatus.CANCELLED:
            job.status = JobStatus.CANCELLED
            raise JobCancelled()

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

    def _build_extractive_summary(self, document: DocumentSummary, text: str) -> str:
        prefix = " ".join(
            piece
            for piece in (
                document.document_date,
                document.title or document.document_type,
                self._format_author(document.author, document.author_role),
            )
            if piece
        ).strip()
        normalized_text = self._normalize_visible_text(text)
        body = self._strip_leading_metadata(normalized_text, document)
        word_limit = self._word_limit_for_document(document.summary_kind)
        excerpt = self._trim_to_word_limit(body, word_limit)
        summary = " ".join(piece for piece in (prefix, excerpt) if piece).strip()

        if document.summary_kind == SummaryKind.IMAGING and document.narrative_report_available is False:
            summary = " ".join(piece for piece in (summary, "No narrative report located.") if piece).strip()

        return summary or prefix or "No extractable text was captured for this document."

    async def _extract_patient_payload(
        self,
        documents: list[DocumentSummary],
        pages: list[PageExtraction],
        patient_index: int,
        job: ExtractionJobDetail | None = None,
        progress_update: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        bundle_text = self._build_patient_bundle_text(documents, pages)
        rule_based_summary = self._build_rule_based_patient_summary(documents, pages)
        if not bundle_text.strip():
            return {
                "name": self._display_patient_name(documents),
                "header": PatientHeader(claimant=self._display_patient_name(documents)),
                "summary": "No extractable patient text was captured.",
                "opinion": "No patient opinion could be generated from the captured material.",
                "office_visits": [],
            }

        if progress_update:
            await progress_update("extracting")
        else:
            self._update_job_progress(
                job,
                "summary",
                f"Running {self._ai_provider_label()} patient bundle extraction for bundle {patient_index}.",
            )
        row = await self._request_ai_json(
            model=self._bundle_model(),
            system_prompt=OPENAI_PATIENT_BUNDLE_PROMPT,
            user_prompt="\n\n".join(
                [
                    f"Patient bundle index: {patient_index}",
                    "Patient bundle text:",
                    bundle_text,
                ]
            ),
            schema=OPENAI_PATIENT_BUNDLE_SCHEMA,
            task_label=f"{self._ai_provider_label()} patient bundle extraction {patient_index}",
        )
        usage = self._pop_ai_usage(row)
        cost = self._estimate_ai_cost(self._bundle_model(), usage)
        self._record_ai_usage(job, "summary", usage, cost)

        header_payload = row.get("header") or {}
        office_visits = self._parse_office_visits(row.get("office_visits"))
        summary_text = (
            self._doctor_last_name_only(self._clean_generated_section_text(row.get("summary")))
            or rule_based_summary
            or "No patient summary generated."
        )
        opinion_text = (
            self._doctor_last_name_only(self._clean_generated_section_text(row.get("opinion")))
            or "No patient opinion generated."
        )

        draft_payload = {
            "name": self._clean_text(row.get("name")) or self._display_patient_name(documents),
            "header": PatientHeader(
                to_name=self._clean_text(header_payload.get("to_name")),
                claim_number=self._clean_text(header_payload.get("claim_number")),
                from_name=self._clean_text(header_payload.get("from_name")),
                age_dob=self._clean_text(header_payload.get("age_dob")),
                review_date=self._normalize_extracted_date(self._clean_text(header_payload.get("review_date"))),
                occupation=self._clean_text(header_payload.get("occupation")),
                claimant=self._clean_patient_label(header_payload.get("claimant"))
                or self._display_patient_name(documents),
                diagnosis_dod=self._clean_text(header_payload.get("diagnosis_dod")),
            ),
            "summary": summary_text,
            "opinion": opinion_text,
            "office_visits": office_visits,
        }
        return await self._validate_patient_payload(
            documents,
            pages,
            patient_index,
            draft_payload,
            rule_based_summary=rule_based_summary,
            job=job,
            progress_update=progress_update,
        )

    async def _validate_patient_payload(
        self,
        documents: list[DocumentSummary],
        pages: list[PageExtraction],
        patient_index: int,
        draft_payload: dict[str, Any],
        *,
        rule_based_summary: str,
        job: ExtractionJobDetail | None = None,
        progress_update: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        if progress_update:
            await progress_update("validating")
        else:
            self._update_job_progress(
                job,
                "summary",
                f"Validating patient bundle {patient_index} against golden rules.",
            )
        draft_visits = [
            visit.model_dump(mode="json") if isinstance(visit, OfficeVisitItem) else visit
            for visit in (draft_payload.get("office_visits") or [])
        ]
        review_prompt = "\n\n".join(
            [
                f"Patient bundle index: {patient_index}",
                "Reference output style:",
                REFERENCE_OUTPUT_STYLE,
                "Document manifest:",
                self._build_patient_document_manifest(documents),
                "Rule-based summary skeleton:",
                rule_based_summary or "[No rule-based summary skeleton available.]",
                "Draft output:",
                json.dumps(
                    {
                        "summary": draft_payload.get("summary") or "",
                        "opinion": draft_payload.get("opinion") or "",
                        "office_visits": draft_visits,
                    },
                    indent=2,
                ),
                "Patient bundle text:",
                self._build_patient_bundle_text(documents, pages),
            ]
        )
        try:
            review_row = await self._request_ai_json(
                model=self._bundle_model(),
                system_prompt=OPENAI_PATIENT_REVIEW_PROMPT,
                user_prompt=review_prompt,
                schema=OPENAI_PATIENT_REVIEW_SCHEMA,
                task_label=f"{self._ai_provider_label()} patient bundle validation {patient_index}",
            )
            usage = self._pop_ai_usage(review_row)
            cost = self._estimate_ai_cost(self._bundle_model(), usage)
            self._record_ai_usage(job, "summary", usage, cost)
        except (OpenAIExtractionError, GeminiExtractionError):
            logger.warning(
                "Golden-rules validation failed for patient bundle %s; keeping draft output.",
                patient_index,
                exc_info=True,
            )
            return draft_payload

        corrected_summary = (
            self._doctor_last_name_only(self._clean_generated_section_text(review_row.get("summary")))
            or self._clean_text(draft_payload.get("summary"))
            or rule_based_summary
            or "No patient summary generated."
        )
        corrected_opinion = (
            self._doctor_last_name_only(self._clean_generated_section_text(review_row.get("opinion")))
            or self._clean_text(draft_payload.get("opinion"))
            or "No patient opinion generated."
        )
        corrected_office_visits = self._parse_office_visits(review_row.get("office_visits"))
        if not corrected_office_visits:
            corrected_office_visits = [
                visit
                for visit in (draft_payload.get("office_visits") or [])
                if isinstance(visit, OfficeVisitItem)
            ]

        return {
            **draft_payload,
            "summary": corrected_summary,
            "opinion": corrected_opinion,
            "office_visits": corrected_office_visits,
        }

    def _build_patient_bundle_text(self, documents: list[DocumentSummary], pages: list[PageExtraction]) -> str:
        page_map = {page.page_number: page for page in pages}
        sections: list[str] = []
        patient_name = self._display_patient_name(documents)
        if patient_name:
            sections.append(f"Patient Name: {patient_name}")

        for index, document in enumerate(documents, start=1):
            meta_bits = [
                f"Document {index}",
                f"Title: {document.title}",
                f"Date: {document.document_date}" if document.document_date else None,
                f"Author: {document.author}" if document.author else None,
                f"Page Start: {document.page_numbers[0]}" if document.page_numbers else None,
                f"Page End: {document.page_numbers[-1]}" if document.page_numbers else None,
                f"Type: {document.document_type}" if document.document_type else None,
                f"Classification: {document.classification.value}",
            ]
            section = "\n".join(bit for bit in meta_bits if bit)
            visible_text = "\n\n".join(
                page_map[page_number].visible_text.strip()
                for page_number in document.page_numbers
                if page_number in page_map and page_map[page_number].visible_text.strip()
            ).strip()
            if visible_text:
                section += f"\nVisible Text:\n{visible_text}"
            sections.append(section)

        return "\n\n".join(section for section in sections if section.strip())

    def _build_patient_document_manifest(self, documents: list[DocumentSummary]) -> str:
        sections: list[str] = []
        for index, document in enumerate(documents, start=1):
            sections.append(
                "\n".join(
                    bit
                    for bit in (
                        f"Document {index}",
                        f"Title: {document.title}",
                        f"Type: {document.document_type}" if document.document_type else None,
                        f"Date: {document.document_date}" if document.document_date else None,
                        f"Author: {document.author}" if document.author else None,
                        f"Pages: {document.page_range}",
                        f"Classification: {document.classification.value}",
                        f"Summary Eligible: {'yes' if self._document_should_feed_patient_output(document) else 'no'}",
                        f"Office Visit Eligible: {'yes' if self._document_should_appear_in_office_visits(document) else 'no'}",
                    )
                    if bit
                )
            )
        return "\n\n".join(sections)

    def _build_rule_based_patient_summary(self, documents: list[DocumentSummary], pages: list[PageExtraction]) -> str:
        page_map = {page.page_number: page for page in pages}
        paragraphs: list[str] = []
        for document in documents:
            if not self._document_should_feed_patient_output(document):
                continue
            visible_text = self._build_document_visible_text(document, page_map)
            paragraph = self._build_extractive_summary(document, visible_text) if visible_text else ""
            cleaned_paragraph = self._clean_generated_section_text(paragraph)
            if cleaned_paragraph:
                paragraphs.append(cleaned_paragraph)
        return "\n\n".join(paragraphs)

    def _build_document_visible_text(self, document: DocumentSummary, page_map: dict[int, PageExtraction]) -> str:
        return "\n\n".join(
            page_map[page_number].visible_text.strip()
            for page_number in document.page_numbers
            if page_number in page_map and page_map[page_number].visible_text.strip()
        ).strip()

    def _parse_office_visits(self, value: Any) -> list[OfficeVisitItem]:
        office_visits: list[OfficeVisitItem] = []
        for item in value or []:
            if not isinstance(item, dict):
                continue
            page_start = int(item.get("page_start") or 0)
            page_end = int(item.get("page_end") or page_start or 0)
            if not item.get("title") or page_start <= 0 or page_end <= 0:
                continue
            office_visits.append(
                OfficeVisitItem(
                    title=self._clean_text(item.get("title")) or "Office visit",
                    date=self._normalize_extracted_date(self._clean_text(item.get("date"))),
                    author=self._doctor_last_name_only(self._clean_text(item.get("author"))),
                    page_start=page_start,
                    page_end=page_end,
                )
            )
        return office_visits

    def _clean_generated_section_text(self, value: Any) -> str | None:
        text = self._clean_text(value)
        if not text:
            return None
        cleaned = re.sub(r"(?im)^\*\*(summary|opinion)\*\*\s*$", "", text)
        cleaned = re.sub(r"(?im)^(summary|opinion)\s*$", "", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return self._clean_text(cleaned)

    def _select_patient_documents(self, documents: list[DocumentSummary]) -> list[DocumentSummary]:
        return [document for document in documents if self._document_should_feed_patient_output(document)]

    def _document_should_feed_patient_output(self, document: DocumentSummary) -> bool:
        if not document.include_in_output:
            return False
        if self._document_is_noise(document):
            return False
        if self._document_is_technical_plan(document):
            return False
        if self._document_is_signature_only(document):
            return False
        return True

    def _document_should_appear_in_office_visits(self, document: DocumentSummary) -> bool:
        if not document.include_in_output:
            return False
        if self._document_is_noise(document):
            return False
        if self._document_is_technical_plan(document):
            return False
        if self._document_is_signature_only(document):
            return False
        return True

    def _document_is_header_only(self, document: DocumentSummary) -> bool:
        title = self._normalize_key(document.title or document.document_type)
        return any(
            phrase in title
            for phrase in (
                "medical file review referral form",
                "medical consultant referral form",
                "file review referral",
                "request for patient records",
            )
        )

    def _document_is_noise(self, document: DocumentSummary) -> bool:
        title = self._normalize_key(document.title or document.document_type)
        return any(
            phrase in title
            for phrase in (
                "acknowledgement and consent",
                "fax transmission",
                "information for new clients",
                "privacy policy",
                "consent to use electronic communications",
                "screening questions table",
                "breathing exercises",
                "how to use pacing",
                "activity diary tracking your activity",
                "types of activities undertaken",
                "mistatim health center",
            )
        )

    def _document_is_signature_only(self, document: DocumentSummary) -> bool:
        title = self._normalize_key(document.title or document.document_type)
        return any(
            phrase in title
            for phrase in (
                "part 8 attending physician",
                "physician stamp must be affixed below",
                "attending physician please ensure practitioner number is entered",
            )
        )

    def _document_is_technical_plan(self, document: DocumentSummary) -> bool:
        title = self._normalize_key(document.title or document.document_type)
        return any(
            phrase in title
            for phrase in (
                "beam set report",
                "beam data",
                "treatment plan report",
                "treatment plan approval",
                "radiotherapy treatment plan approval",
                "plan report",
                "energy layer",
            )
        )

    def _display_patient_name(self, documents: list[DocumentSummary]) -> str | None:
        for document in documents:
            name = self._clean_patient_label(document.patient_name)
            if name:
                return name
            for candidate in document.mentioned_patient_names:
                cleaned = self._clean_patient_label(candidate)
                if cleaned:
                    return cleaned
        return None

    def _patient_bucket_key(self, document: DocumentSummary, index: int) -> str:
        name = self._normalize_key(self._clean_patient_label(document.patient_name))
        mentioned = [
            self._normalize_key(self._clean_patient_label(candidate))
            for candidate in document.mentioned_patient_names
        ]
        mentioned = [candidate for candidate in mentioned if candidate]
        dob = self._normalize_patient_dob(document.patient_dob)

        if name and dob:
            return f"name-dob|{name}|{dob}"
        if name:
            return f"name|{name}"
        if dob:
            return f"dob|{dob}"
        if mentioned:
            return f"mentioned|{mentioned[0]}"
        return f"unassigned|{index:03d}"

    def _backfill_document_patient_context(self, documents: list[DocumentSummary]) -> None:
        last_seen: tuple[str | None, str | None, str | None, str | None] | None = None
        for document in documents:
            if document.patient_key or document.patient_name:
                last_seen = (
                    document.patient_name,
                    document.patient_dob,
                    document.patient_identifier,
                    document.patient_key,
                )
                continue
            if last_seen:
                (
                    document.patient_name,
                    document.patient_dob,
                    document.patient_identifier,
                    document.patient_key,
                ) = last_seen

        next_seen: tuple[str | None, str | None, str | None, str | None] | None = None
        for document in reversed(documents):
            if document.patient_key or document.patient_name:
                next_seen = (
                    document.patient_name,
                    document.patient_dob,
                    document.patient_identifier,
                    document.patient_key,
                )
                continue
            if next_seen:
                (
                    document.patient_name,
                    document.patient_dob,
                    document.patient_identifier,
                    document.patient_key,
                ) = next_seen

    def _parse_relevance(self, value: Any) -> ClinicalRelevance:
        normalized = self._normalize_key(value)
        if normalized == ClinicalRelevance.CLINICAL.value:
            return ClinicalRelevance.CLINICAL
        if normalized == ClinicalRelevance.FUNCTIONAL.value:
            return ClinicalRelevance.FUNCTIONAL
        if normalized == ClinicalRelevance.ADMINISTRATIVE.value:
            return ClinicalRelevance.ADMINISTRATIVE
        return ClinicalRelevance.UNKNOWN

    def _parse_page_role(self, value: Any) -> PageRole:
        normalized = self._normalize_key(value)
        mapping = {
            PageRole.DOCUMENT_START.value: PageRole.DOCUMENT_START,
            PageRole.DOCUMENT_BODY.value: PageRole.DOCUMENT_BODY,
            PageRole.SEPARATOR.value: PageRole.SEPARATOR,
            PageRole.INDEX_ONLY.value: PageRole.INDEX_ONLY,
            PageRole.COVER.value: PageRole.COVER,
            PageRole.OTHER.value: PageRole.OTHER,
        }
        return mapping.get(normalized, PageRole.OTHER)

    def _parse_optional_bool(self, value: Any) -> bool | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return value
        normalized = self._normalize_key(value)
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
        return None

    def _parse_summary_kind(self, value: Any) -> SummaryKind:
        normalized = self._normalize_key(value)
        mapping = {
            SummaryKind.CLINICAL.value: SummaryKind.CLINICAL,
            SummaryKind.IMAGING.value: SummaryKind.IMAGING,
            SummaryKind.PATHOLOGY.value: SummaryKind.PATHOLOGY,
            SummaryKind.FUNCTIONAL.value: SummaryKind.FUNCTIONAL,
            SummaryKind.ADMINISTRATIVE.value: SummaryKind.ADMINISTRATIVE,
            SummaryKind.UNKNOWN.value: SummaryKind.UNKNOWN,
        }
        return mapping.get(normalized, SummaryKind.UNKNOWN)

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

    def _most_common_summary_kind(self, values: Any) -> SummaryKind:
        normalized_values = [value for value in values if value and value != SummaryKind.UNKNOWN]
        if normalized_values:
            return Counter(normalized_values).most_common(1)[0][0]
        return SummaryKind.UNKNOWN

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

    def _merge_optional_bools(self, values: Any) -> bool | None:
        seen_true = False
        seen_false = False
        for value in values:
            if value is True:
                seen_true = True
            elif value is False:
                seen_false = True
        if seen_true:
            return True
        if seen_false:
            return False
        return None

    async def _run_extract(
        self,
        client: AsyncLlamaCloud,
        file_id: str,
        config: dict[str, Any],
        data_schema: dict[str, Any],
        *,
        progress_label: str | None = None,
    ) -> Any:
        if progress_label:
            logger.info("Starting %s", progress_label)
        if hasattr(client, "extract"):
            result = await client.extract.run(
                file_input=file_id,
                configuration=config,
            )
            if progress_label:
                logger.info("Completed %s", progress_label)
            return result

        if hasattr(client, "extraction"):
            result = await client.extraction.extract(
                file_id=file_id,
                config=config,
                data_schema=data_schema,
            )
            if progress_label:
                logger.info("Completed %s", progress_label)
            return result

        raise ExtractionError("Unsupported llama_cloud client: neither extract nor extraction API is available.")

    def _clean_text(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).replace("\u00a0", " ").strip()
        return text or None

    def _normalize_extracted_date(self, value: str | None) -> str | None:
        text = self._clean_text(value)
        if not text:
            return None

        parsed = self._try_parse_date(text)
        if not parsed:
            return text

        return parsed.strftime("%B %d, %Y")

    def _normalize_document_date(self, value: Any, patient_dob: str | None = None) -> str | None:
        document_date = self._normalize_extracted_date(self._clean_text(value))
        dob = self._normalize_extracted_date(patient_dob)
        if document_date and dob and self._normalize_key(document_date) == self._normalize_key(dob):
            return None
        return document_date

    def _try_parse_date(self, value: str) -> datetime | None:
        text = self._clean_text(value)
        if not text:
            return None

        normalized = re.sub(r"\s+", " ", text).strip()
        normalized = normalized.replace("/", "-")
        normalized = re.sub(r"(\d)([A-Za-z])", r"\1-\2", normalized)
        normalized = re.sub(r"([A-Za-z])(\d)", r"\1-\2", normalized)

        formats = [
            "%Y-%m-%d",
            "%d-%m-%Y",
            "%d-%m-%y",
            "%d-%b-%Y",
            "%d-%b-%y",
            "%d-%B-%Y",
            "%d-%B-%y",
            "%b-%d-%Y",
            "%b-%d-%y",
            "%B-%d-%Y",
            "%B-%d-%y",
            "%B %d, %Y",
            "%b %d, %Y",
            "%d %B %Y",
            "%d %b %Y",
        ]

        for date_format in formats:
            try:
                return datetime.strptime(normalized, date_format)
            except ValueError:
                continue

        return None

    def _collapse_patient_sections(self, patients: list[PatientSummary]) -> list[PatientSummary]:
        if len(patients) <= 1:
            return patients

        grouped: dict[str, list[PatientSummary]] = {}
        unnamed: list[PatientSummary] = []
        ordered_keys: list[str] = []

        for patient in patients:
            canonical_name = self._canonical_patient_name(patient.name)
            if canonical_name:
                matched_key = next(
                    (
                        existing_key
                        for existing_key in ordered_keys
                        if self._canonical_names_match(existing_key, canonical_name)
                    ),
                    None,
                )
                group_key = matched_key or canonical_name
                if group_key not in grouped:
                    grouped[group_key] = []
                    ordered_keys.append(group_key)
                grouped[group_key].append(patient)
            else:
                unnamed.append(patient)

        if not grouped:
            return patients

        if len(grouped) == 1 and unnamed:
            grouped[ordered_keys[0]].extend(unnamed)
            unnamed = []

        collapsed: list[PatientSummary] = []
        for canonical_name in ordered_keys:
            merged = self._merge_patient_group(grouped[canonical_name])
            collapsed.append(merged)

        collapsed.extend(unnamed)

        for index, patient in enumerate(collapsed, start=1):
            patient.id = f"patient_{index:03d}"

        if len(collapsed) != len(patients):
            logger.info(
                "Collapsed patient sections from %s to %s after duplicate-name consolidation",
                len(patients),
                len(collapsed),
            )

        collapsed = self._absorb_contained_patient_sections(collapsed)

        for index, patient in enumerate(collapsed, start=1):
            patient.id = f"patient_{index:03d}"

        return collapsed

    def _absorb_contained_patient_sections(self, patients: list[PatientSummary]) -> list[PatientSummary]:
        if len(patients) <= 1:
            return patients

        ordered = sorted(
            patients,
            key=lambda patient: (
                patient.page_start if patient.page_start > 0 else 10**9,
                -(patient.page_end if patient.page_end > 0 else 0),
                patient.id,
            ),
        )

        absorbed_ids: set[str] = set()
        merged: list[PatientSummary] = []
        primary_patient = self._select_primary_patient(ordered)

        for patient in ordered:
            if patient.id in absorbed_ids:
                continue

            container = patient
            container_key = self._canonical_patient_name(container.name)
            for candidate in ordered:
                if candidate.id == container.id or candidate.id in absorbed_ids:
                    continue

                if not self._patient_range_contains(container, candidate):
                    continue

                candidate_key = self._canonical_patient_name(candidate.name)
                same_named_patient = bool(
                    container_key and candidate_key and self._canonical_names_match(container_key, candidate_key)
                )
                same_claimant = self._patient_identity_matches(container, candidate)
                unnamed_subset = bool(container_key and not candidate_key)
                contained_within_primary = bool(
                    primary_patient
                    and primary_patient.id == container.id
                    and self._is_meaningful_patient_name(container.name)
                    and self._is_fragmentary_patient_section(candidate)
                )

                if not same_named_patient and not same_claimant and not unnamed_subset and not contained_within_primary:
                    continue

                container = self._merge_patient_group([container, candidate])
                absorbed_ids.add(candidate.id)
                logger.info(
                    "Absorbed contained patient section %s (%s-%s) into %s (%s-%s)",
                    candidate.id,
                    candidate.page_start,
                    candidate.page_end,
                    patient.id,
                    patient.page_start,
                    patient.page_end,
                )

            merged.append(container)

        return merged

    def _select_primary_patient(self, patients: list[PatientSummary]) -> PatientSummary | None:
        if not patients:
            return None

        return max(
            patients,
            key=lambda patient: (
                patient.page_end - patient.page_start if patient.page_start > 0 and patient.page_end > 0 else -1,
                len(patient.office_visits),
                len(self._clean_text(patient.summary) or ""),
            ),
        )

    def _patient_range_contains(self, container: PatientSummary, candidate: PatientSummary) -> bool:
        if container.page_start <= 0 or container.page_end <= 0:
            return False
        if candidate.page_start <= 0 or candidate.page_end <= 0:
            return False
        if container.page_start == candidate.page_start and container.page_end == candidate.page_end:
            return False
        return container.page_start <= candidate.page_start and container.page_end >= candidate.page_end

    def _patient_identity_matches(self, left: PatientSummary, right: PatientSummary) -> bool:
        left_claim = self._normalize_key(left.header.claim_number)
        right_claim = self._normalize_key(right.header.claim_number)
        if left_claim and right_claim:
            return left_claim == right_claim

        left_claimant = self._canonical_patient_name(left.header.claimant or left.name)
        right_claimant = self._canonical_patient_name(right.header.claimant or right.name)
        if left_claimant and right_claimant:
            return self._canonical_names_match(left_claimant, right_claimant)

        return False

    def _is_meaningful_patient_name(self, value: Any) -> bool:
        return bool(self._canonical_patient_name(value))

    def _is_fragmentary_patient_section(self, patient: PatientSummary) -> bool:
        page_span = 0
        if patient.page_start > 0 and patient.page_end > 0:
            page_span = patient.page_end - patient.page_start + 1

        if not self._is_meaningful_patient_name(patient.name):
            return True

        if page_span <= 12:
            return True

        return len(patient.office_visits) <= 2

    def _merge_patient_group(self, patients: list[PatientSummary]) -> PatientSummary:
        ordered = sorted(
            patients,
            key=lambda patient: (
                patient.page_start if patient.page_start > 0 else 10**9,
                patient.page_end if patient.page_end > 0 else 10**9,
                patient.id,
            ),
        )

        names = [self._clean_patient_label(patient.name) for patient in ordered]
        names = [name for name in names if name]
        name = Counter(names).most_common(1)[0][0] if names else (
            self._clean_patient_label(ordered[0].name) or self._clean_patient_label(ordered[0].header.claimant)
        )

        office_visits: list[OfficeVisitItem] = []
        seen_visits: set[tuple[str, str, str, int, int]] = set()
        for patient in ordered:
            for visit in patient.office_visits:
                visit_key = (
                    self._normalize_key(visit.title),
                    self._normalize_key(visit.date),
                    self._normalize_key(visit.author),
                    visit.page_start,
                    visit.page_end,
                )
                if visit_key in seen_visits:
                    continue
                seen_visits.add(visit_key)
                office_visits.append(visit)

        page_starts = [patient.page_start for patient in ordered if patient.page_start > 0]
        page_ends = [patient.page_end for patient in ordered if patient.page_end > 0]
        if office_visits:
            page_starts.extend(visit.page_start for visit in office_visits if visit.page_start > 0)
            page_ends.extend(visit.page_end for visit in office_visits if visit.page_end > 0)

        return PatientSummary(
            id=ordered[0].id,
            name=name,
            header=self._merge_patient_headers([patient.header for patient in ordered], fallback_name=name),
            summary=self._merge_patient_text([patient.summary for patient in ordered], fallback="No patient summary generated."),
            page_start=min(page_starts) if page_starts else 0,
            page_end=max(page_ends) if page_ends else 0,
            opinion=self._merge_patient_opinion([patient.opinion for patient in ordered]),
            office_visits=office_visits,
        )

    def _merge_patient_text(self, values: list[str], fallback: str) -> str:
        paragraphs: list[str] = []
        seen: set[str] = set()

        for value in values:
            text = self._clean_text(value)
            if not text:
                continue
            for paragraph in [piece.strip() for piece in re.split(r"\n\s*\n", text) if piece.strip()]:
                normalized = self._normalize_key(paragraph)
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    paragraphs.append(paragraph)

        if not paragraphs:
            return fallback
        return "\n\n".join(paragraphs)

    def _merge_patient_opinion(self, values: list[str]) -> str:
        opinions = [self._clean_text(value) for value in values]
        opinions = [value for value in opinions if value]
        if not opinions:
            return "No patient opinion generated."

        def opinion_score(value: str) -> tuple[int, int]:
            normalized = self._normalize_key(value)
            penalty = 0
            if "no evidence-based opinion" in normalized or "no opinion can be formulated" in normalized:
                penalty -= 1000
            if "laboratory evidence alone" in normalized:
                penalty -= 400
            if "from this laboratory evidence alone" in normalized:
                penalty -= 400
            return (penalty + len(value), len(value))

        return max(opinions, key=opinion_score)

    def _merge_patient_headers(self, headers: list[PatientHeader], fallback_name: str | None) -> PatientHeader:
        return PatientHeader(
            to_name=self._most_common(header.to_name for header in headers),
            claim_number=self._most_common(header.claim_number for header in headers),
            from_name=self._most_common(header.from_name for header in headers),
            age_dob=self._most_common(header.age_dob for header in headers),
            review_date=self._most_common(header.review_date for header in headers),
            occupation=self._most_common(header.occupation for header in headers),
            claimant=self._most_common(header.claimant for header in headers) or fallback_name,
            diagnosis_dod=self._most_common(header.diagnosis_dod for header in headers),
        )

    def _clean_patient_label(self, value: Any) -> str | None:
        text = self._clean_text(value)
        if not text:
            return None
        normalized = self._normalize_key(text)
        if normalized in {"patient", "unassigned patient", "unknown", "not visible"}:
            return None
        if re.fullmatch(r"patient\s*\d*", normalized):
            return None
        if "[redacted]" in normalized or "████" in text:
            return None
        return text

    def _normalize_patient_dob(self, value: Any) -> str:
        text = self._clean_text(value)
        if not text:
            return ""
        digits = "".join(char for char in text if char.isdigit())
        return digits or self._normalize_key(text)

    def _normalize_patient_identifier(self, value: Any) -> str:
        text = self._clean_text(value)
        if not text:
            return ""
        alnum = "".join(char.lower() for char in text if char.isalnum())
        return alnum or self._normalize_key(text)

    def _normalize_key(self, value: Any) -> str:
        cleaned = self._clean_text(value)
        return " ".join(cleaned.lower().split()) if cleaned else ""

    def _canonical_patient_name(self, value: Any) -> str:
        text = self._clean_patient_label(value)
        if not text:
            return ""

        normalized = self._normalize_key(text)
        if re.fullmatch(r"patient\s*\d*", normalized):
            return ""

        ignored_tokens = {
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
            "jan",
            "feb",
            "mar",
            "apr",
            "jun",
            "jul",
            "aug",
            "sep",
            "sept",
            "oct",
            "nov",
            "dec",
        }

        tokens = re.findall(r"[a-z0-9]+", normalized)
        tokens = [
            token
            for token in tokens
            if len(token) > 1 and not token.isdigit() and token not in ignored_tokens
        ]
        if not tokens:
            return ""

        return " ".join(sorted(tokens))

    def _canonical_names_match(self, left: str, right: str) -> bool:
        if not left or not right:
            return False
        if left == right:
            return True

        left_tokens = left.split()
        right_tokens = right.split()
        shorter_tokens, longer_tokens = (
            (left_tokens, right_tokens) if len(left_tokens) <= len(right_tokens) else (right_tokens, left_tokens)
        )

        unmatched_longer = longer_tokens.copy()
        matched_tokens = 0

        for token in shorter_tokens:
            if token in unmatched_longer:
                unmatched_longer.remove(token)
                matched_tokens += 1
                continue

            best_index = -1
            best_ratio = 0.0
            for index, candidate in enumerate(unmatched_longer):
                ratio = SequenceMatcher(None, token, candidate).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_index = index

            if best_index < 0 or best_ratio < 0.74:
                return False

            unmatched_longer.pop(best_index)
            matched_tokens += 1

        return matched_tokens == len(shorter_tokens) and matched_tokens >= min(2, len(shorter_tokens))

    def _get_openai_client(self) -> AsyncOpenAI:
        if not settings.OPENAI_API_KEY:
            raise OpenAIExtractionError("OPENAI_API_KEY is not configured.")
        if self._openai_client is None:
            self._openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=300.0)
        return self._openai_client

    def _ai_provider_label(self) -> str:
        return "Gemini" if settings.AI_PROVIDER == "gemini" else "OpenAI"

    def _page_model(self) -> str:
        return settings.GEMINI_PAGE_MODEL if settings.AI_PROVIDER == "gemini" else settings.OPENAI_PAGE_MODEL

    def _bundle_model(self) -> str:
        return settings.GEMINI_BUNDLE_MODEL if settings.AI_PROVIDER == "gemini" else settings.OPENAI_BUNDLE_MODEL

    async def _request_ai_json(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str | list[dict[str, Any]],
        schema: dict[str, Any],
        task_label: str,
    ) -> dict[str, Any]:
        if settings.AI_PROVIDER == "gemini":
            return await self._request_gemini_json(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                task_label=task_label,
            )
        return await self._request_openai_json(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            task_label=task_label,
        )

    async def _request_gemini_json(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str | list[dict[str, Any]],
        schema: dict[str, Any],
        task_label: str,
    ) -> dict[str, Any]:
        if not settings.GEMINI_API_KEY:
            raise GeminiExtractionError("GEMINI_API_KEY is not configured.")

        logger.info("Starting %s with model %s", task_label, model)
        schema_prompt = "\n\n".join(
            [
                "Return only valid JSON. Do not wrap it in markdown fences or add commentary.",
                "Return a JSON object matching this schema exactly.",
                json.dumps(schema, indent=2),
            ]
        )
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [
                {
                    "role": "user",
                    "parts": self._build_gemini_parts(schema_prompt, user_prompt),
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "response_mime_type": "application/json",
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        url = f"{GEMINI_API_BASE_URL}/models/{model}:generateContent"
        params = {"key": settings.GEMINI_API_KEY}

        for attempt in range(1, OPENAI_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=300.0) as client:
                    response = await client.post(url, params=params, json=payload)
                if response.status_code == 429 and attempt < OPENAI_MAX_RETRIES:
                    delay = self._gemini_retry_delay(response, attempt)
                    logger.warning(
                        "%s rate limited on attempt %s/%s. Retrying in %.1f seconds.",
                        task_label,
                        attempt,
                        OPENAI_MAX_RETRIES,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                if response.status_code >= 500 and attempt < OPENAI_MAX_RETRIES:
                    delay = self._gemini_retry_delay(response, attempt)
                    logger.warning(
                        "%s Gemini status %s on attempt %s/%s. Retrying in %.1f seconds.",
                        task_label,
                        response.status_code,
                        attempt,
                        OPENAI_MAX_RETRIES,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
                try:
                    parsed_payload = self._parse_gemini_json_response(response.json(), task_label)
                    usage = parsed_payload.get("__usage_metadata")
                    if (
                        not isinstance(usage, dict)
                        or (int(usage.get("input_tokens") or 0) <= 0 and int(usage.get("output_tokens") or 0) <= 0)
                    ):
                        parsed_payload["__usage_metadata"] = self._fallback_usage_from_request(model, user_prompt, parsed_payload)
                    return parsed_payload
                except GeminiExtractionError:
                    if attempt >= OPENAI_MAX_RETRIES:
                        raise
                    delay = self._gemini_retry_delay(None, attempt)
                    logger.warning(
                        "%s returned invalid JSON on attempt %s/%s. Retrying in %.1f seconds.",
                        task_label,
                        attempt,
                        OPENAI_MAX_RETRIES,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
            except httpx.HTTPStatusError as exc:
                raise GeminiExtractionError(self._describe_gemini_error(exc.response, f"{task_label} failed.")) from exc
            except httpx.HTTPError as exc:
                if attempt >= OPENAI_MAX_RETRIES:
                    raise GeminiExtractionError(f"{task_label} failed after retries: {exc}") from exc
                delay = self._gemini_retry_delay(None, attempt)
                await asyncio.sleep(delay)
            except json.JSONDecodeError as exc:
                raise GeminiExtractionError(f"{task_label} returned invalid JSON.") from exc
            except JobCancelled:
                raise

        raise GeminiExtractionError(f"{task_label} failed without a response.")

    def _build_gemini_parts(self, schema_prompt: str, user_prompt: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
        if isinstance(user_prompt, str):
            return [{"text": "\n\n".join([schema_prompt, user_prompt])}]

        parts: list[dict[str, Any]] = [{"text": schema_prompt}]
        for item in user_prompt:
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append({"text": item["text"]})
                continue
            if item.get("type") == "image_url":
                image_url = item.get("image_url")
                data_url = image_url.get("url") if isinstance(image_url, dict) else None
                if isinstance(data_url, str):
                    mime_type, data = self._parse_data_url(data_url)
                    parts.append({"inline_data": {"mime_type": mime_type, "data": data}})
        return parts

    def _parse_data_url(self, data_url: str) -> tuple[str, str]:
        prefix, _, data = data_url.partition(",")
        mime_type = prefix.removeprefix("data:").removesuffix(";base64") or OPENAI_PAGE_IMAGE_MEDIA_TYPE
        return mime_type, data

    def _parse_gemini_json_response(self, payload: dict[str, Any], task_label: str) -> dict[str, Any]:
        parts = (
            payload.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
        )
        content = "\n".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()
        if not content:
            raise GeminiExtractionError(f"{task_label} returned no content.")
        parsed = self._parse_jsonish_content(content, task_label)
        usage = payload.get("usageMetadata") or {}
        if isinstance(parsed, dict) and isinstance(usage, dict):
            parsed["__usage_metadata"] = {
                "input_tokens": int(usage.get("promptTokenCount") or 0),
                "output_tokens": int(usage.get("candidatesTokenCount") or 0),
                "total_tokens": int(usage.get("totalTokenCount") or 0),
            }
        logger.info("Completed %s", task_label)
        return parsed if isinstance(parsed, dict) else {}

    def _parse_jsonish_content(self, content: str, task_label: str) -> Any:
        candidates = [content.strip()]
        fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", content, flags=re.IGNORECASE | re.DOTALL)
        if fenced:
            candidates.append(fenced.group(1).strip())
        object_start = content.find("{")
        object_end = content.rfind("}")
        if object_start >= 0 and object_end > object_start:
            candidates.append(content[object_start : object_end + 1])

        for candidate in candidates:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        preview = self._clean_text(content[:220]) or "empty response"
        logger.warning("%s raw Gemini response preview: %s", task_label, preview)
        raise GeminiExtractionError(f"{task_label} returned invalid JSON: {preview}")

    def _describe_gemini_error(self, response: httpx.Response, fallback: str) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text or fallback
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        return fallback

    def _gemini_retry_delay(self, response: httpx.Response | None, attempt: int) -> float:
        if response is not None:
            retry_after = response.headers.get("retry-after") or response.headers.get("Retry-After")
            if retry_after:
                try:
                    return min(OPENAI_RETRY_MAX_DELAY_SECONDS, max(1.0, float(retry_after)))
                except ValueError:
                    pass
        return min(OPENAI_RETRY_MAX_DELAY_SECONDS, float(2**attempt))

    async def _request_openai_json(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str | list[dict[str, Any]],
        schema: dict[str, Any],
        task_label: str,
    ) -> dict[str, Any]:
        client = self._get_openai_client()
        logger.info("Starting %s with model %s", task_label, model)
        schema_prompt = "\n\n".join(
            [
                "Return a JSON object matching this schema exactly.",
                json.dumps(schema, indent=2),
            ]
        )
        user_content: str | list[dict[str, Any]]
        if isinstance(user_prompt, list):
            user_content = [{"type": "text", "text": schema_prompt}, *user_prompt]
        else:
            user_content = "\n\n".join([schema_prompt, user_prompt])

        response = None
        for attempt in range(1, OPENAI_MAX_RETRIES + 1):
            try:
                response = await client.chat.completions.create(
                    model=model,
                    temperature=0,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": user_content,
                        },
                    ],
                )
                break
            except AuthenticationError as exc:
                raise OpenAIExtractionError("Authentication failed. Check OPENAI_API_KEY.") from exc
            except RateLimitError as exc:
                if attempt >= OPENAI_MAX_RETRIES:
                    raise OpenAIExtractionError(self._describe_openai_error(exc, "Rate limit or quota exceeded.")) from exc
                delay = self._openai_retry_delay(exc, attempt)
                logger.warning(
                    "%s rate limited on attempt %s/%s. Retrying in %.1f seconds.",
                    task_label,
                    attempt,
                    OPENAI_MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
            except (APIConnectionError, APITimeoutError) as exc:
                if attempt >= OPENAI_MAX_RETRIES:
                    raise OpenAIExtractionError(f"{task_label} failed after retries: {exc}") from exc
                delay = self._openai_retry_delay(exc, attempt)
                logger.warning(
                    "%s transient OpenAI error on attempt %s/%s. Retrying in %.1f seconds: %s",
                    task_label,
                    attempt,
                    OPENAI_MAX_RETRIES,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
            except APIStatusError as exc:
                if attempt >= OPENAI_MAX_RETRIES or not self._is_retryable_openai_status(exc):
                    raise OpenAIExtractionError(self._describe_openai_error(exc, f"{task_label} failed.")) from exc
                delay = self._openai_retry_delay(exc, attempt)
                logger.warning(
                    "%s OpenAI status %s on attempt %s/%s. Retrying in %.1f seconds.",
                    task_label,
                    getattr(exc, "status_code", "unknown"),
                    attempt,
                    OPENAI_MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                raise OpenAIExtractionError(f"{task_label} failed: {exc}") from exc

        if response is None:
            raise OpenAIExtractionError(f"{task_label} failed without a response.")

        content = response.choices[0].message.content if response.choices else None
        if not content:
            raise OpenAIExtractionError(f"{task_label} returned no content.")

        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise OpenAIExtractionError(f"{task_label} returned invalid JSON.") from exc

        usage = getattr(response, "usage", None)
        if isinstance(payload, dict) and usage is not None:
            payload["__usage_metadata"] = {
                "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            }
        if isinstance(payload, dict):
            usage_payload = payload.get("__usage_metadata")
            if (
                not isinstance(usage_payload, dict)
                or (int(usage_payload.get("input_tokens") or 0) <= 0 and int(usage_payload.get("output_tokens") or 0) <= 0)
            ):
                payload["__usage_metadata"] = self._fallback_usage_from_request(model, user_prompt, payload)
        logger.info("Completed %s", task_label)
        return payload if isinstance(payload, dict) else {}

    def _describe_openai_error(self, error: Exception, fallback: str) -> str:
        body = getattr(error, "body", None)
        if isinstance(body, dict):
            if isinstance(body.get("error"), dict) and isinstance(body["error"].get("message"), str):
                return body["error"]["message"]
            if isinstance(body.get("message"), str):
                return body["message"]
        return str(error) or fallback

    def _is_retryable_openai_status(self, error: APIStatusError) -> bool:
        status_code = getattr(error, "status_code", None)
        return isinstance(status_code, int) and status_code in OPENAI_RETRYABLE_STATUS_CODES

    def _openai_retry_delay(self, error: Exception, attempt: int) -> float:
        retry_after = self._extract_openai_retry_after(error)
        if retry_after is not None:
            return min(OPENAI_RETRY_MAX_DELAY_SECONDS, max(1.0, retry_after))
        return min(OPENAI_RETRY_MAX_DELAY_SECONDS, float(2**attempt))

    def _extract_openai_retry_after(self, error: Exception) -> float | None:
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None)
        if headers is None:
            return None

        retry_after = headers.get("retry-after") or headers.get("Retry-After")
        if retry_after is None:
            return None

        try:
            return float(retry_after)
        except (TypeError, ValueError):
            return None

    def _open_pdf_document(self, file_content: bytes, *, task_label: str) -> Any:
        try:
            return pdfium.PdfDocument(file_content)
        except Exception as exc:
            raise OpenAIExtractionError(f"Unable to open PDF locally for {task_label}: {exc}") from exc

    def _render_pdf_page_batch(self, pdf_document: Any, page_numbers: list[int]) -> list[tuple[int, bytes]]:
        rendered: list[tuple[int, bytes]] = []
        for page_number in page_numbers:
            rendered.append((page_number, self._render_pdf_page_image(pdf_document, page_number)))
        return rendered

    def _render_pdf_page_image(self, pdf_document: Any, page_number: int) -> bytes:
        page: Any | None = None
        bitmap: Any | None = None
        pil_image: Any | None = None
        image_buffer = io.BytesIO()
        try:
            page = pdf_document.get_page(page_number - 1)
            bitmap = page.render(scale=self._page_render_scale())
            pil_image = bitmap.to_pil()
            pil_image.save(image_buffer, format="PNG")
            return image_buffer.getvalue()
        except Exception as exc:
            raise self._provider_extraction_error(f"Unable to render PDF page {page_number} for {self._ai_provider_label()} vision parsing: {exc}") from exc
        finally:
            image_buffer.close()
            if pil_image is not None:
                pil_image.close()
            if bitmap is not None and hasattr(bitmap, "close"):
                bitmap.close()
            if page is not None and hasattr(page, "close"):
                page.close()

    def _page_render_scale(self) -> float:
        dpi = max(72, int(settings.OPENAI_PAGE_RENDER_DPI))
        return dpi / 72.0

    def _count_pdf_pages(self, file_content: bytes) -> int:
        pdf_document = self._open_pdf_document(file_content, task_label="image-based page parsing")
        try:
            return len(pdf_document)
        except Exception as exc:
            raise self._provider_extraction_error(f"Unable to determine PDF page count for image-based page parsing: {exc}") from exc
        finally:
            pdf_document.close()

    def _build_patient_key(self, patient_name: str | None, patient_dob: str | None, patient_identifier: str | None) -> str | None:
        parts = [
            self._normalize_key(patient_name),
            self._normalize_key(patient_dob),
            self._normalize_key(patient_identifier),
        ]
        parts = [part for part in parts if part]
        if not parts:
            return None
        return "|".join(parts)

    def _infer_summary_kind(self, document_type: str | None, classification: ClinicalRelevance) -> SummaryKind:
        if classification == ClinicalRelevance.ADMINISTRATIVE:
            return SummaryKind.ADMINISTRATIVE
        if classification == ClinicalRelevance.FUNCTIONAL:
            return SummaryKind.FUNCTIONAL
        if classification == ClinicalRelevance.CLINICAL:
            return SummaryKind.CLINICAL
        return SummaryKind.UNKNOWN

    def _document_is_in_scope(self, classification: ClinicalRelevance, summary_kind: SummaryKind) -> bool:
        if classification == ClinicalRelevance.ADMINISTRATIVE:
            return False
        if summary_kind in {SummaryKind.CLINICAL, SummaryKind.IMAGING, SummaryKind.PATHOLOGY, SummaryKind.FUNCTIONAL}:
            return True
        return classification != ClinicalRelevance.ADMINISTRATIVE

    def _word_limit_for_document(self, summary_kind: SummaryKind) -> int:
        if summary_kind in {SummaryKind.IMAGING, SummaryKind.PATHOLOGY}:
            return 50
        return 200

    def _format_author(self, author: str | None, author_role: str | None) -> str | None:
        if author:
            return author
        return author_role

    def _normalize_visible_text(self, text: str) -> str:
        cleaned_lines = [segment.strip() for segment in text.splitlines() if segment.strip()]
        return " ".join(cleaned_lines)

    def _strip_leading_metadata(self, text: str, document: DocumentSummary) -> str:
        cleaned = text
        candidates = [
            document.title,
            document.document_type,
            f"Date: {document.document_date}" if document.document_date else None,
            document.document_date,
            f"From: {document.author}" if document.author else None,
            document.author,
        ]
        for candidate in candidates:
            if not candidate:
                continue
            normalized_candidate = self._normalize_key(candidate)
            normalized_cleaned = self._normalize_key(cleaned)
            if normalized_cleaned.startswith(normalized_candidate):
                cleaned = " ".join(cleaned.split()[len(candidate.split()):]).strip()
        return cleaned

    def _trim_to_word_limit(self, text: str, word_limit: int) -> str:
        words = text.split()
        if len(words) <= word_limit:
            return " ".join(words)
        return " ".join(words[:word_limit])

    def _clean_name_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        items = value if isinstance(value, list) else [value]
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in items:
            text = self._clean_text(item)
            if not text:
                continue
            for part in re.split(r"[;\n]+", text):
                candidate = self._clean_text(part)
                normalized = self._normalize_key(candidate)
                if candidate and normalized and normalized not in seen:
                    seen.add(normalized)
                    cleaned.append(candidate)
        return cleaned

    def _merge_name_lists(self, value_lists: Any) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for value_list in value_lists:
            for value in value_list or []:
                candidate = self._clean_text(value)
                normalized = self._normalize_key(candidate)
                if candidate and normalized and normalized not in seen:
                    seen.add(normalized)
                    merged.append(candidate)
        return merged

