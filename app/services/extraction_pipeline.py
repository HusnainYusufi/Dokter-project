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

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, AuthenticationError, RateLimitError
import pypdfium2 as pdfium

try:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:  # pragma: no cover - dependency is installed from requirements in runtime images.
    HumanMessage = None
    SystemMessage = None
    ChatGoogleGenerativeAI = None

from app.core.config import settings
from app.core.exceptions import ExportError, GeminiExtractionError, OpenAIExtractionError, ProcessingError
from app.schemas.extraction import (
    ClinicalRelevance,
    DocumentSummary,
    ExtractionJobDetail,
    ExtractionJobSummary,
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
from app.services.job_store import EncryptedJobStore, job_to_summary, utc_now_iso

logger = logging.getLogger(__name__)

CLASSIFY_MAX_CANDIDATES = 6
OPENAI_PAGE_IMAGE_MEDIA_TYPE = "image/png"
OPENAI_MAX_RETRIES = 5
OPENAI_RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
OPENAI_RETRY_MAX_DELAY_SECONDS = 60.0
PAGE_PARSE_CACHE_VERSION = 2
PATIENT_BUNDLE_CACHE_VERSION = 1
DOCTOR_NAME_PATTERN = re.compile(r"\b(Dr\.?\s+)([A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*)+)")

# Hard validators constants

# Generic administrative noise signals
_ADMIN_NOISE_SIGNALS = frozenset({
    "fax transmission",
    "fax cover",
    "acknowledgement and consent",
    "information for new clients",
    "privacy policy",
    "consent to use electronic communications",
    "screening questions table",
    "breathing exercises",
    "how to use pacing",
    "activity diary tracking",
    "physician stamp must be affixed",
    "beam set report",
    "beam data",
    "treatment plan approval",
    "energy layer",
    "part 8 attending physician",
    "radiotherapy treatment plan",
})


class JobCancelled(Exception):
    pass


@dataclass
class PageParseBatchResult:
    batch_index: int
    page_numbers: list[int]
    pages: list[PageExtraction]
    error: str | None = None


_PATIENT_HEADER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "to_name": {"type": "string"},
        "claim_number": {"type": "string"},
        "from_name": {"type": "string"},
        "age_dob": {
            "type": "string",
            "description": "Age or DOB field as shown in the main claimant header.",
        },
        "review_date": {
            "type": "string",
            "description": "Primary report/review date from the top document header. Prefer the date next to a visible `Date:` label. Do not use footer timestamps or fax times.",
        },
        "occupation": {"type": "string"},
        "claimant": {"type": "string"},
        "diagnosis_dod": {"type": "string"},
    },
    "description": "Claimant-style review header copied from the supplied file where visible.",
}


OPENAI_PAGE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "page_number": {"type": "integer"},
        "starts_new_document": {
            "type": "boolean",
            "description": "True if this page begins a new distinct document (new letterhead, new title, new date+author header). False if it continues the previous page's document.",
        },
        "include_in_output": {
            "type": "boolean",
            "description": "False only for pure administrative boilerplate: fax cover sheets, blank routing pages, consent-only forms, signature-only pages, billing pages.",
        },
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
        "page_extract": {
            "type": "string",
            "description": "Clinically and functionally relevant content extracted from this page. 200-600 words. Omit administrative noise (fax headers, addresses, consent boilerplate, signatures, patient ID blocks). Focus on: clinical findings, diagnoses, test results, work-capacity assessments, treatment plans, physician observations, key dates, and relevant functional limitations. For excluded pages (include_in_output=false) leave this empty.",
        },
    },
    "required": ["page_number", "page_extract"],
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

Document boundary (`starts_new_document`):
- Set `true` only when this page clearly begins a NEW distinct document: new letterhead/source, new standalone report/form, new patient, or a new report date+author header that is not continuing the prior page.
- Set `false` when this page continues the same form/report/letter as the previous page, even if it has a new section heading such as Functional Assessment, Return to Work, Physical Restrictions, Screening Questions, Survey Results, or page-part headings.
- A subsection heading inside the same form is NOT a new document.
- Multi-page forms must stay together unless a clearly different standalone document begins.
- Page 1 is always `true`.

Include/exclude (`include_in_output`):
- Set `false` only for pure administrative boilerplate: fax cover sheets, blank routing pages, consent-only forms, signature-only pages, billing/invoice pages.
- Set `true` for everything else including clinical notes, imaging reports, functional assessments, referrals, case-management letters, and telephone interview records.

Classification (`document_bucket`):
- clinical: notes, consultations, referrals, prescriptions, case-management notes, telephone interviews.
- imaging: radiology, X-ray, MRI, CT, ultrasound, ECG, pulmonary function reports.
- pathology: lab, biopsy, histology, specimen reports.
- functional: work-capacity, disability, insurer review, return-to-work, rehabilitation planning.
- administrative: fax covers, consent forms, billing, routing pages, privacy notices.
- unknown: cannot be determined.

Author/title extraction:
- `document_title`: copy the visible heading or report title exactly as it appears. If no clear heading exists, leave empty — do NOT use a fax line, address, phone number, or patient name as the title.
- `author`: copy only a clearly visible signing/authoring physician or provider name from this page (e.g. "Dr. Waslen", "Dr. Flegg", "Lillian MacDonald"). Do not guess from nearby report words. If the page does not clearly show the writer/signer name, leave this field empty; later bundle context may carry author/date from another page in the same document.
- `document_date`: copy the primary date of the document — the report date, visit date, or letter date. Ignore fax transmission timestamps, print timestamps, and page-generation dates.

Content extraction (`page_extract`):
- Capture ALL clinically and functionally relevant content visible on the page. Target 300–700 words per included page. Do not summarise or paraphrase — transcribe the information faithfully.
- ALWAYS INCLUDE (copy exactly as written):
    • Physician/author name, credentials, clinic/hospital name, and date of the report or visit.
    • Every diagnosis, condition, symptom, complaint, and clinical finding stated on the page.
    • All test results, scores, measurements, and values with their units (e.g., MoCA 25/30, DLCO 59%, BP 130/85 mmHg, pulse 92 bpm).
    • Imaging interpretations: organ-by-organ findings, impression, and radiologist name.
    • ECG, PFT, spirometry, and other investigation results in full.
    • Work-capacity, functional, and disability assessments: restrictions, limitations, capacity levels.
    • Medications: name, dose, frequency, and indication.
    • Treatment plans, recommendations, referrals, and follow-up instructions.
    • Relevant history, onset dates, mechanism of injury/illness, and prior visit dates.
    • Any quoted language, checklist responses, or scores the document contains.
- EXCLUDE only: fax transmission headers, mailing addresses, phone/fax numbers, patient ID/SIN/health card numbers, blank signature boxes, generic consent boilerplate unrelated to clinical care, watermarks, and background template text.
- For pages where `include_in_output` is false, leave `page_extract` empty.
- Write in continuous, dense prose — do not use bullet lists in the extract. Preserve exact clinical terminology, numbers, and physician names.
- If the page is a very brief one-line result or a blank continuation, a shorter extract is acceptable; otherwise aim for completeness over brevity.
"""

OPENAI_PATIENT_BUNDLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "header": _PATIENT_HEADER_SCHEMA,
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
- Use only the supplied patient bundle text. Do not invent or infer missing facts.
- Return one JSON object matching the requested schema.
- `header`: copy claimant-style fields exactly where visible (`to_name`, `claim_number`, `from_name`, `age_dob`, `review_date`, `occupation`, `claimant`, `diagnosis_dod`). `from_name` is the person or entity who wrote or sent the document, NOT the recipient.
- Prefer the primary report/visit/letter date. Ignore fax timestamps, print times, page generation times, and scanner metadata.
- Format all dates in full written form, e.g. `January 11, 2026`. No abbreviations.
- Plain text only inside `summary` and `opinion`: no bullets, no numbered lists, no headings, no bold, no tables, no markdown, hard paragraph returns only, no en-dash decorators.
- No unnecessary blank lines. Each paragraph must represent one complete document-level thought.

Locked extractive mode for `summary`:
- Copy phrases and sentences DIRECTLY from the supplied bundle text. Compression by deletion only — no substitution, no rewording of any kind.
- Every retained phrase must be traceable word-for-word to the source extract.
- Preserve original file order and the source's internal content sequence. Do not reorganize, synthesize, or group findings across documents.
- Preserve exact clinical terms and values: "hyperinflated", "DLCO 59%", "MoCA 25/30", "PESE positive", "FEV1/FVC 85%", "MRC score 2/5".
- Forbidden: paraphrase, causation, interpretation, clinical significance, invented connectives.
- Forbidden phrases: "this suggests", "consistent with", "supports", "necessitate", "underscores", "contraindicates", "further underscore", "indicative of".
- If a sentence requires invented wording, omit it entirely.
- Convert source tables, checkbox rows, labels, and OCR markdown into concise plain prose while preserving exact words, values, checked answers, and clinical terms. Do not copy table layout, checkbox symbols, bullet markers, headings, or form labels unless the label is necessary to understand the value.
- Omit administrative/header label dumps from summaries: `Date:`, `To:`, `From:`, `Claimant Name`, `Date of Birth`, `Claim Type`, addresses, IDs, phone/fax, page headers, signature metadata, and scan artifacts.
- Never output malformed OCR strings such as `To: Dr. From:` or duplicated prefixes inside a paragraph. If a phrase is malformed or uncertain, omit it.

`summary` structure:
- One paragraph per document section in original file order.
- Skip ONLY pure administrative boilerplate (consent forms, blank fax covers, billing pages, routing-only pages with no clinical content).
- Each paragraph MUST begin on its first line with the prefix: FullDate DocumentType Author
  - FullDate = the full written date visible anywhere in the same document section, including a previous page of that same form/report (e.g. `November 7, 2025`).
  - DocumentType = the document's heading or report type exactly as written (e.g. `CT Brain w/o Contrast`, `Pulmonary Function Lab`).
  - Author = `Dr. Lastname` for signing physicians; full name for other authors (e.g. `Lillian MacDonald`). Author is the person who WROTE or SIGNED the document — never the recipient. If the author appears on a previous page of the same document section, carry that author forward.
  - If a physician/clinician name is present in the same document section, include it in the summary prefix. Do not drop visible authors from summary prefixes.
  - Omit only elements that are genuinely not visible in that document — never invent.
  - Never output placeholder labels such as `Document 17`, `Document 3`, or `Unknown Document`. If the supplied metadata contains a placeholder, derive the document type from the same document text; if no type is clear, omit DocumentType.
- Metadata may be derived only from the same document section and only for the prefix/office visit fields; never turn derived metadata into clinical content.
- Length targets: clinical, referral, functional, work-capacity, case-management, and telephone records approximately 200 words; imaging and pathology approximately 50 words. Include all material clinical findings, scores, measurements, diagnoses, medications, and functional limitations stated in the source.
- Omit repeated identifiers, mailing addresses, patient ID blocks, and phone/fax numbers.
- Each document section must be ONE continuous paragraph after the prefix. Do not insert internal line breaks after labels, headings, checkbox questions, or table rows.
- Do not repeat the document prefix/date/title/author inside the paragraph body. If OCR contains a second embedded date/title/author line for the same document, remove the duplicate line and keep only the required prefix.
- For ED/consultation notes, use the actual visit/report date as the prefix date and do not create a second artificial paragraph from signature/history metadata.

`opinion`:
- Analytical only — cite objective findings by value and author where available (e.g. "MoCA 25/30 (Dr. Zaluski)", "DLCO 59% (Dr. Joanis)", "PESE positive").
- Do not repeat the summary chronology line by line.
- Do not restate raw form fields.
- No invented interpretations beyond what the source documents explicitly state.
- 3–5 short paragraphs maximum.

`office_visits`:
- File order only. Keep office_visits in the same order as the original PDF pages, not chronological date order.
- `author` = the person who WROTE or SIGNED the document (not the recipient/addressee). Use same-document context, including previous pages, to fill date/author when a continuation page omits them.
- `title` must be the real document heading/type from source text. Never use placeholder titles like `Document 17`; omit uncertain words instead of guessing.
- Include all clinically or functionally relevant documents. Exclude only consent, billing, fax cover, routing, and signature-only pages.
"""

REFERENCE_OUTPUT_STYLE = """Reference output style:
- Summary is original PDF file order, one plain-text paragraph per included document.
- Each summary paragraph starts on the same line with: FullDate DocumentType Author.
- Summary paragraphs read like concise medico-legal review paragraphs, not raw form-field dumps.
- Summary paragraphs contain no bullets, headings, markdown, checkbox symbols, tables, or internal line breaks.
- Opinion is a separate set of short analytical paragraphs, not a copied chronology or a field-by-field restatement.
- Office visits are listed in original PDF file order.
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
- Tightly extractive — every retained phrase must trace word-for-word to the source bundle text.
- Compress by deletion only; do NOT reword, paraphrase, or substitute any clinical term, finding, or value.
- Preserve original file order and the source's internal content sequence. Do not reorganize, synthesize, or group findings across documents.
- Preserve exact numbers and terms: "DLCO 59%", "MoCA 25/30", "FEV1/FVC 85%", "PESE positive", "MRC score 2/5".
- No inference, synthesis, causation, or added clinical significance.
- Forbidden phrases: "this suggests", "consistent with", "supports", "necessitate", "underscores", "contraindicates", "highlights", "indicative of".
- Convert source tables, checkbox rows, labels, and OCR markdown into concise plain prose while preserving exact values and checked answers. No bullets, numbered lists, headings, tables, markdown, checkbox symbols, or raw form layouts in `summary`.
- Omit administrative/header label dumps from summaries: `Date:`, `To:`, `From:`, `Claimant Name`, `Date of Birth`, `Claim Type`, addresses, IDs, phone/fax, page headers, signature metadata, and scan artifacts.
- Never output malformed OCR strings such as `To: Dr. From:` or duplicated prefixes inside a paragraph. If a phrase is malformed or uncertain, omit it.
- One paragraph per included document in original file order.
- Include referrals, imaging, pathology, functional/work-capacity, case-management notes. Exclude ONLY admin/billing/consent/fax/routing/signature pages.
- Each paragraph MUST start on the same line with: FullDate DocumentType Author (omitting only elements genuinely not visible).
- Author = the person who WROTE or SIGNED the document — not the recipient/addressee. Carry date/author forward from previous pages when they belong to the same document section.
- If a physician/clinician name is present in the same document section, include it in the summary prefix. Do not drop visible authors from summary prefixes.
- All dates in full written format, e.g. January 27, 2023. Physician names: Dr. Lastname format.
- Never output placeholder labels such as `Document 17`, `Document 3`, or `Unknown Document`. If metadata has a placeholder, derive the document type from the same document text; if unclear, omit DocumentType.
- Metadata may be derived only from the same document section and only for prefix/office visit fields; never turn derived metadata into clinical content.
- Length targets: clinical, referral, functional, work-capacity, case-management, and telephone records approximately 200 words; imaging and pathology approximately 50 words. Preserve material clinical findings, scores, and functional limitations.
- Strip addresses, postal codes, phone/fax numbers, email, SIN, health card numbers, and member-ID boilerplate.
- Each document section must be ONE continuous paragraph after the prefix. Do not insert internal line breaks after labels, headings, checkbox questions, or table rows.
- Do not repeat the document prefix/date/title/author inside the paragraph body. If OCR contains a second embedded date/title/author line for the same document, remove the duplicate line and keep only the required prefix.
- For ED/consultation notes, use the actual visit/report date as the prefix date and do not create a second artificial paragraph from signature/history metadata.

Opinion rules:
- Analytical only, citing objective findings by value and author where available (e.g. "MoCA 25/30 (Dr. Zaluski)", "DLCO 59% (Dr. Joanis)").
- Do not repeat the summary chronology line-by-line.
- Do not add interpretive causation beyond what the source documents explicitly state.
- Concise: 3–5 short paragraphs maximum.

Office visit rules:
- File order only. Keep the same order as original PDF pages, not chronological date order.
- `author` = the person who WROTE or SIGNED the document — never the recipient or addressee. Use same-document context, including previous pages, when continuation pages omit date/author.
- `title` must be the real document heading/type from source text. Never use placeholder titles like `Document 17`; omit uncertain words instead of guessing.
- Include all clinically or functionally relevant documents. Exclude consent, billing, fax, routing, and signature-only pages.

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
        self._cancel_events: dict[str, asyncio.Event] = {}
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
        # Signal any running async tasks to stop immediately
        cancel_event = self._cancel_events.get(job_id)
        if cancel_event:
            cancel_event.set()
        return job_to_summary(job)

    def retry_job(self, job_id: str) -> ExtractionJobSummary:
        job = self.store.get_job(job_id)
        if not self.store.artifact_exists(job.id, "source_pdf"):
            raise ProcessingError("Source PDF is not available for retry.", status_code=400)

        job.status = JobStatus.QUEUED
        job.processing_started_at = None
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
            job.processing_started_at = None
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
        self._cancel_events[job_id] = asyncio.Event()
        job = self.store.get_job(job_id)
        source_bytes = self.store.read_artifact(job_id, "source_pdf")

        try:
            logger.info("Starting extraction job %s for %s", job.id, job.filename)
            self._raise_if_cancelled(job)
            job.status = JobStatus.PROCESSING
            job.processing_started_at = utc_now_iso()
            self._set_step(
                job,
                "extract",
                PipelineStepStatus.RUNNING,
                f"Rendering pages and parsing page-local signals with {self._ai_provider_label()}.",
            )
            self._set_step(job, "boundary", PipelineStepStatus.PENDING, "Waiting for page capture to complete.")
            self._set_step(job, "summary", PipelineStepStatus.PENDING, "Waiting for patient boundary resolution.")
            self.store.save_job(job)

            # Background cancel watcher — polls every 2s so cancel is responsive
            cancel_event = self._cancel_events.get(job_id)

            async def _cancel_watcher() -> None:
                while cancel_event and not cancel_event.is_set():
                    await asyncio.sleep(2)
                    try:
                        latest = self.store.get_job(job_id)
                        if latest.status == JobStatus.CANCELLED:
                            cancel_event.set()
                            return
                    except Exception:
                        pass

            watcher_task = asyncio.create_task(_cancel_watcher())
            try:
                payload = await self._extract_job_payload(source_bytes, job.filename, job)
            finally:
                watcher_task.cancel()
                try:
                    await watcher_task
                except asyncio.CancelledError:
                    pass
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
                self._clean_text(payload.get("boundary_detail")) or "Resolved patient boundaries.",
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
            self._cancel_events.pop(job_id, None)

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

        # --- Patient Boundary Stage ---
        self._update_job_progress(job, "boundary", "Grouping pages into documents and assigning patient ownership.", status=PipelineStepStatus.RUNNING)
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
                f"Parsed {actual_page_count} page(s), grouped {len(documents)} document(s), "
                f"and resolved {len(patients)} patient section(s) using {boundary_strategy}."
            ),
        }

    async def _extract_pages_with_ai(
        self,
        file_content: bytes,
        page_count: int,
        *,
        job: ExtractionJobDetail | None = None,
    ) -> list[PageExtraction]:
        if page_count <= 0:
            return []

        # One page per AI request keeps page extracts complete and prevents
        # cross-page evidence loss when pages belong to different documents.
        batch_size = 1
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

        def build_extractions_from_payload(payload: dict[str, Any], page_numbers: list[int]) -> list[PageExtraction]:
            rows = payload.get("pages") if isinstance(payload, dict) else []
            rows_by_page: dict[int, dict[str, Any]] = {}
            if isinstance(rows, list):
                for item in rows:
                    if not isinstance(item, dict):
                        continue
                    page_number = int(item.get("page_number") or 0)
                    if page_number > 0:
                        rows_by_page[page_number] = item

            batch_extracted: list[PageExtraction] = []
            for page_number in page_numbers:
                item = rows_by_page.get(page_number, {})
                patient_name = self._clean_patient_label(item.get("patient_name"))
                patient_dob = self._clean_text(item.get("patient_dob"))
                patient_identifier = self._clean_text(item.get("patient_identifier"))
                # AI signals: boundary detection is now part of the parse step
                starts_new = bool(item.get("starts_new_document", page_number == page_numbers[0]))
                include_out = item.get("include_in_output")
                include_out = True if include_out is None else bool(include_out)
                page = PageExtraction(
                    page_number=page_number,
                    starts_new_document=starts_new,
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
                    visible_text=self._clean_text(item.get("page_extract")) or "",
                )
                # Store include_in_output hint in boundary_hint for use during grouping
                if not include_out:
                    page.boundary_hint = "exclude"
                batch_extracted.append(self._repair_page_extraction(page))
            return batch_extracted

        async def parse_page_numbers_once(batch_index: int, page_numbers: list[int]) -> tuple[list[PageExtraction], float]:
            batch_pages = self._render_pdf_page_batch(pdf_document, page_numbers)
            payload = await self._request_ai_json(
                model=self._page_model(),
                system_prompt=OPENAI_PAGE_PROMPT,
                user_prompt=self._build_openai_page_batch_content(batch_pages),
                schema=OPENAI_PAGE_BATCH_SCHEMA,
                task_label=f"{self._ai_provider_label()} page parsing batch {batch_index}/{total_batches}",
            )
            self._raise_if_cancelled(job)
            usage = self._pop_ai_usage(payload)
            cost = self._estimate_ai_cost(self._page_model(), usage)
            self._record_ai_usage(job, "extract", usage, cost)
            return build_extractions_from_payload(payload, page_numbers), cost

        async def parse_batch(batch_index: int, batch_page_numbers: list[int]) -> PageParseBatchResult:
            async with semaphore:
                self._raise_if_cancelled(job)
                page_label = (
                    str(batch_page_numbers[0])
                    if len(batch_page_numbers) == 1
                    else f"{batch_page_numbers[0]}-{batch_page_numbers[-1]}"
                )
                checkpoint = self._load_page_parse_batch_checkpoint(job, batch_index, batch_page_numbers)
                if checkpoint is not None:
                    checkpoint_pages, checkpoint_cost = checkpoint
                    async with status_lock:
                        completed_batches.add(batch_index)
                    await set_batch_status(batch_index, f"Page {page_label}: parse cached | saved cost {self._format_cost(checkpoint_cost)}")
                    return PageParseBatchResult(batch_index=batch_index, page_numbers=batch_page_numbers, pages=checkpoint_pages)

                await set_batch_status(batch_index, f"Page {page_label}: parsing")
                try:
                    batch_extracted, cost = await parse_page_numbers_once(batch_index, batch_page_numbers)
                except JobCancelled:
                    raise
                except Exception as exc:
                    if len(batch_page_numbers) > 1:
                        try:
                            split_pages: list[PageExtraction] = []
                            split_cost = 0.0
                            await set_batch_status(batch_index, f"Batch {batch_index}: splitting pages {page_label} into single-page retries")
                            for page_number in batch_page_numbers:
                                self._raise_if_cancelled(job)
                                single_pages, single_cost = await parse_page_numbers_once(batch_index, [page_number])
                                split_pages.extend(single_pages)
                                split_cost += single_cost
                                await set_batch_status(
                                    batch_index,
                                    f"Batch {batch_index}: recovered page {page_number} from split retry | cost {self._format_cost(split_cost)}",
                                )
                            self._save_page_parse_batch_checkpoint(job, batch_index, batch_page_numbers, split_pages, split_cost)
                            async with status_lock:
                                completed_batches.add(batch_index)
                            await set_batch_status(batch_index, f"Batch {batch_index}: parsed pages {page_label} via split retry | cost {self._format_cost(split_cost)}")
                            return PageParseBatchResult(batch_index=batch_index, page_numbers=batch_page_numbers, pages=split_pages)
                        except JobCancelled:
                            raise
                        except Exception as split_exc:
                            exc = split_exc
                    error = exc.detail if isinstance(exc, ProcessingError) else str(exc)
                    async with status_lock:
                        failed_batches[batch_index] = f"pages {page_label}: {error}"
                    await set_batch_status(batch_index, f"Page {page_label}: parse failed - {self._short_error(error)}")
                    return PageParseBatchResult(batch_index=batch_index, page_numbers=batch_page_numbers, pages=[], error=error)

                self._save_page_parse_batch_checkpoint(job, batch_index, batch_page_numbers, batch_extracted, cost)
                async with status_lock:
                    completed_batches.add(batch_index)
                await set_batch_status(batch_index, f"Page {page_label}: parse done | cost {self._format_cost(cost)}")
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
            if "parse done" in batch_status[index] or "cached" in batch_status[index]
        ][:6]
        return "\n".join(
            [
                f"Parsed {completed_count}/{total_batches} pages. Failed {failed_count}.",
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
                    "Return one JSON row per page image. For `page_extract`, write 200-600 words of clinically relevant content only — skip fax headers, addresses, consent text, and admin boilerplate."
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

    def _all_pages_have_patient_assignment(self, pages: list[PageExtraction]) -> bool:
        return all(self._normalize_key(page.patient_key or page.patient_name) for page in pages)

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
            if not self._document_should_appear_in_office_visits(document):
                continue
            page_numbers = sorted(document.page_numbers)

            visit = OfficeVisitItem(
                title=document.title or document.document_type or "Record",
                date=self._normalize_extracted_date(document.document_date),
                author=self._doctor_last_name_only(self._display_document_author(document)),
                page_start=page_numbers[0],
                page_end=page_numbers[-1],
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
            parts = [p for p in full_name.strip().split() if p]
            last_name = parts[-1] if parts else full_name
            return f"{prefix}{last_name}"

        result = DOCTOR_NAME_PATTERN.sub(repl, text)
        # Generic cleanup only; the bundle model decides real author/date from context.
        if re.fullmatch(r"Dr\.?\s+[A-Z]\.?", result.strip()):
            return None
        return result

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
            from_name=self._clean_header_person_name(anchored_header.from_name or extracted_header.from_name),
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

    def _clean_header_person_name(self, value: Any) -> str | None:
        text = self._clean_text(value)
        if not text:
            return None
        for line in text.splitlines():
            line = self._clean_text(line)
            if not line:
                continue
            normalized = self._normalize_key(line)
            if any(skip in normalized for skip in ("claimant information", "claimant name", "date of birth", "contract", "current diagnosis")):
                continue
            return line
        return None

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

    async def _build_patient_groups(
        self,
        documents: list[DocumentSummary],
        pages: list[PageExtraction],
        job: ExtractionJobDetail | None = None,
    ) -> list[PatientSummary]:
        buckets, order = self._build_pre_summary_patient_buckets(documents)
        buckets, order = self._drop_contained_patient_buckets(buckets, order)

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

        tasks = [
            asyncio.create_task(process_bucket(index, bucket_key))
            for index, bucket_key in enumerate(order, start=1)
        ]
        try:
            patients = await asyncio.gather(*tasks)
        except (JobCancelled, Exception):
            for task in tasks:
                if not task.done():
                    task.cancel()
            raise
        return patients

    def _drop_contained_patient_buckets(
        self,
        buckets: dict[str, list[DocumentSummary]],
        order: list[str],
    ) -> tuple[dict[str, list[DocumentSummary]], list[str]]:
        """Drop smaller buckets whose page range falls inside the largest bucket's span."""
        if len(order) <= 1:
            return buckets, order

        # Compute min/max page range per bucket
        ranges: dict[str, tuple[int, int]] = {}
        for key in order:
            all_pages = [
                pn
                for doc in buckets.get(key, [])
                for pn in doc.page_numbers
            ]
            if all_pages:
                ranges[key] = (min(all_pages), max(all_pages))

        if not ranges:
            return buckets, order

        # Largest bucket = widest page span
        largest_key = max(ranges, key=lambda k: ranges[k][1] - ranges[k][0])
        largest_min, largest_max = ranges[largest_key]

        dropped: set[str] = set()
        for key in order:
            if key == largest_key:
                continue
            if key not in ranges:
                continue
            bucket_min, bucket_max = ranges[key]
            # If this bucket's entire range sits inside the largest bucket's span, drop it
            if bucket_min >= largest_min and bucket_max <= largest_max:
                dropped.add(key)
                logger.info(
                    "Dropped contained bucket (pages %d-%d) — inside largest bucket span %d-%d.",
                    bucket_min, bucket_max, largest_min, largest_max,
                )

        if not dropped:
            return buckets, order

        # Merge dropped documents into the largest bucket
        for key in dropped:
            buckets.setdefault(largest_key, []).extend(buckets.pop(key, []))

        remaining = len(order) - len(dropped)
        logger.info("Dropped %d contained buckets; %d bucket(s) remain.", len(dropped), remaining)
        return (
            {key: value for key, value in buckets.items() if key not in dropped},
            [key for key in order if key not in dropped],
        )

    def _format_summary_progress(
        self,
        summary_status: dict[int, str],
        *,
        completed_count: int,
        cached_count: int,
        failed_count: int,
        total_bundles: int,
    ) -> str:
        merging_count = sum(1 for status in summary_status.values() if "merging" in status)
        processing_count = sum(
            1
            for status in summary_status.values()
            if any(token in status for token in ("extracting", "chunk", "merging"))
        )
        active_lines = [
            summary_status[index]
            for index in sorted(summary_status)
            if any(token in summary_status[index] for token in ("extracting", "chunk", "merging", "failed"))
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
                    f"Processing {processing_count}. Merging {merging_count}. Cached {cached_count}. Failed {failed_count}."
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

    # -------------------------------------------------------------------------
    # Golden-rules hard validators
    # -------------------------------------------------------------------------

    def _build_summary_prefix(self, document: DocumentSummary) -> str:
        parts = [
            document.document_date,
            document.title or document.document_type,
            self._doctor_last_name_only(self._format_author(document.author, document.author_role)),
        ]
        return " ".join(p for p in parts if p).strip()

    def _repair_placeholder_document_title(
        self,
        title: str,
        document_type: str | None,
        document_date: str | None,
        author: str | None,
        pages: list[PageExtraction],
    ) -> str:
        if not re.fullmatch(r"(?i)document\s+\d+", self._clean_text(title) or ""):
            return title
        for candidate in (
            document_type,
            self._infer_title_from_page_text(pages),
            self._first_meaningful_page_line(pages),
            author,
            document_date,
        ):
            cleaned = self._clean_text(candidate)
            if cleaned and not re.fullmatch(r"(?i)document\s+\d+", cleaned):
                return cleaned[:120]
        return title

    _TITLE_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
        (re.compile(r"\bmember'?s\s+statement\b", re.IGNORECASE), "Member's Statement"),
        (re.compile(r"\bphysician'?s\s+initial\s+report\s+form\b", re.IGNORECASE), "Physician's Initial Report Form"),
        (re.compile(r"\bfunctional\s+assessment\b", re.IGNORECASE), "Functional Assessment"),
        (re.compile(r"\breturn\s+to\s+work\b", re.IGNORECASE), "Return to Work"),
        (re.compile(r"\bphysical\s+restrictions?\s*/\s*limitations?\b", re.IGNORECASE), "Physical Restrictions / Limitations"),
        (re.compile(r"\bscc\s+ot\s+progress\s+note\b", re.IGNORECASE), "SCC OT Progress Note"),
        (re.compile(r"\bmontreal\s+cognitive\s+assessment\b|\bmoca\b", re.IGNORECASE), "Montreal Cognitive Assessment"),
        (re.compile(r"\bgad-?7\b", re.IGNORECASE), "GAD-7 Questionnaire"),
        (re.compile(r"\bct\s+brain\s+w/?o\s+contrast\b", re.IGNORECASE), "CT Brain w/o Contrast"),
        (re.compile(r"\bdx\s+chest\s+2\s+views\b|\bmedical\s+imaging\b", re.IGNORECASE), "Medical Imaging"),
        (re.compile(r"\bchest\s+and\s+right\s+ribs\b", re.IGNORECASE), "Chest and Right Ribs"),
        (re.compile(r"\bsinuses\b", re.IGNORECASE), "SINUSES"),
        (re.compile(r"\belectrocardiogram\b|\becg\b", re.IGNORECASE), "ECG Report"),
        (re.compile(r"\bpulmonary\s+function\b|\bdlco\b|\bspirometry\b", re.IGNORECASE), "Pulmonary Function Lab"),
        (re.compile(r"\bscreening\s+tool\s+for\s+post-covid\s+physical\s+sequelae\b", re.IGNORECASE), "Screening Tool for Post-COVID Physical Sequelae"),
        (re.compile(r"\bpost\s+covid-19\s+functional\s+status\s+scale\b", re.IGNORECASE), "Post COVID-19 Functional Status Scale"),
        (re.compile(r"\bpost\s+covid-19\s+symptom\s+checklist\b", re.IGNORECASE), "Post COVID-19 Symptom Checklist"),
        (re.compile(r"\bed\s+md\s+assessment\b|\bfinal\s+diagnosis\s+viral\s+bronchitis\b", re.IGNORECASE), "ED MD Assessment"),
    )

    def _infer_title_from_page_text(self, pages: list[PageExtraction]) -> str | None:
        text = "\n".join(page.visible_text or "" for page in pages)
        if not text:
            return None
        for pattern, title in self._TITLE_HINTS:
            if pattern.search(text):
                return title
        return None

    _JUNK_TITLE_PATTERNS = re.compile(
        r"""
        ^\d{4}[/-]\d{2}[/-]\d{2}   # date-only line
        | \bfax\b
        | \bpage[:\s]\d
        | \bfrom:\s
        | \bto:\s
        | \btel\b
        | \bphone\b
        | @                          # email address fragment
        | \d{3}[-.\s]\d{3}[-.\s]\d{4}  # phone number
        | \bsk\b.*\bs\d[a-z]\d       # SK postal code
        | \bwall\s+st\b
        | \bsaskatoon\b.*\bsk\b
        | ^#+\s                      # markdown heading
        | ^the\s+text\s+            # OCR background artefact
        | king\s+midas              # known background watermark
        | ^wanda\s+russell          # patient name alone
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    def _first_meaningful_page_line(self, pages: list[PageExtraction]) -> str | None:
        skip_tokens = (
            "patient name",
            "date of birth",
            "claimant",
            "contract",
            "page ",
            "diagnosis",
            "health card",
            "sin:",
            "postal",
        )
        for page in pages:
            for line in (page.visible_text or "").splitlines():
                cleaned = self._clean_text(line)
                if not cleaned or len(cleaned) < 8:
                    continue
                normalized = self._normalize_key(cleaned)
                if any(token in normalized for token in skip_tokens):
                    continue
                if self._JUNK_TITLE_PATTERNS.search(cleaned):
                    continue
                # Require at least two real words
                words = re.findall(r"[A-Za-z]{2,}", cleaned)
                if len(words) < 2:
                    continue
                return cleaned
        return None


    def _starts_new_group(self, current_pages: list[PageExtraction], page: PageExtraction) -> bool:
        if not current_pages:
            return True

        # Primary signal: AI-detected boundary during parse step
        if page.starts_new_patient:
            return True
        if page.starts_new_document:
            if self._looks_like_same_form_continuation(current_pages[-1], page):
                return False
            return True

        # Fallback: different patient key means a new bundle
        previous = current_pages[-1]
        current_patient_key = self._normalize_key(page.patient_key or page.patient_name)
        previous_patient_key = self._normalize_key(previous.patient_key or previous.patient_name)
        if current_patient_key and previous_patient_key and current_patient_key != previous_patient_key:
            return True

        return False

    _CONTINUATION_SECTION_TITLES: frozenset[str] = frozenset({
        "functional assessment",
        "return to work",
        "physical restrictions limitations",
        "physical restrictions",
        "limitations",
        "survey results",
        "screening questions",
        "screening tool",
        "post covid 19 symptom checklist",
        "post covid 19 functional status scale",
        "part 1",
        "part 2",
        "page 2",
        "page 3",
    })

    def _looks_like_same_form_continuation(self, previous: PageExtraction, page: PageExtraction) -> bool:
        current_patient_key = self._normalize_key(page.patient_key or page.patient_name)
        previous_patient_key = self._normalize_key(previous.patient_key or previous.patient_name)
        if current_patient_key and previous_patient_key and current_patient_key != previous_patient_key:
            return False

        title = self._normalize_key(" ".join(part for part in (page.document_title, page.document_type) if part))
        if not title:
            return False
        return any(section in title for section in self._CONTINUATION_SECTION_TITLES)

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
        author = self._doctor_last_name_only(self._first_value(page.author for page in pages))
        author_role = self._first_value(page.author_role for page in pages)
        page_numbers = [page.page_number for page in pages]
        classification = self._most_common_relevance(page.clinical_relevance for page in pages)
        summary_kind = self._most_common_summary_kind(page.document_bucket for page in pages)
        if summary_kind == SummaryKind.UNKNOWN:
            summary_kind = self._infer_summary_kind(document_title or document_type, classification)
        # If every page in this group was AI-marked as exclude, honour that
        all_excluded = all(page.boundary_hint == "exclude" for page in pages)
        include_in_output = (not all_excluded) and self._document_is_in_scope(classification, summary_kind)
        title = document_title or document_type or f"Document {document_index}"
        title = self._repair_placeholder_document_title(title, document_type, document_date, author, pages)
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
        # Fast path: check in-memory event first (set by cancel_job)
        cancel_event = self._cancel_events.get(job.id)
        if cancel_event and cancel_event.is_set():
            job.status = JobStatus.CANCELLED
            raise JobCancelled()
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

    def _build_extractive_summary(self, document: DocumentSummary, text: str) -> str:
        prefix = " ".join(
            piece
            for piece in (
                document.document_date,
                document.title or document.document_type,
                self._doctor_last_name_only(self._format_author(document.author, document.author_role)),
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
        self._raise_if_cancelled(job)
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

        document_chunks = self._split_patient_documents_for_summary(documents, pages)
        if len(document_chunks) > 1:
            return await self._extract_chunked_patient_payload(
                document_chunks,
                pages,
                patient_index,
                rule_based_summary,
                job=job,
                progress_update=progress_update,
            )

        if progress_update:
            await progress_update("extracting")
        else:
            self._update_job_progress(
                job,
                "summary",
                f"Running {self._ai_provider_label()} patient bundle extraction for bundle {patient_index}.",
            )
        row = await self._request_bundle_ai_json(
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
            task_label=f"OpenAI patient bundle extraction {patient_index}",
        )
        usage = self._pop_ai_usage(row)
        cost = self._estimate_ai_cost(self._bundle_model(), usage)
        self._record_ai_usage(job, "summary", usage, cost)

        header_payload = row.get("header") or {}
        office_visits = self._parse_office_visits(row.get("office_visits"))
        summary_text = (
            self._clean_generated_section_text(row.get("summary"))
            or rule_based_summary
            or "No patient summary generated."
        )
        opinion_text = (
            self._clean_generated_section_text(row.get("opinion"))
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
        return draft_payload

    async def _extract_chunked_patient_payload(
        self,
        document_chunks: list[list[DocumentSummary]],
        pages: list[PageExtraction],
        patient_index: int,
        rule_based_summary: str,
        *,
        job: ExtractionJobDetail | None = None,
        progress_update: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        chunk_payloads: list[dict[str, Any]] = []
        all_documents = [document for chunk in document_chunks for document in chunk]
        total_chunks = len(document_chunks)

        for chunk_index, chunk_documents in enumerate(document_chunks, start=1):
            self._raise_if_cancelled(job)
            if progress_update:
                await progress_update(f"extracting chunk {chunk_index}/{total_chunks}")

            row = await self._request_bundle_ai_json(
                model=self._bundle_model(),
                system_prompt=OPENAI_PATIENT_BUNDLE_PROMPT,
                user_prompt="\n\n".join(
                    [
                        f"Patient bundle index: {patient_index}",
                        f"Chunk: {chunk_index}/{total_chunks}",
                        "Patient bundle text:",
                        self._build_patient_bundle_text(chunk_documents, pages, total_limit=self._BUNDLE_CHUNK_TEXT_LIMIT),
                    ]
                ),
                schema=OPENAI_PATIENT_BUNDLE_SCHEMA,
                task_label=f"{self._ai_provider_label()} patient bundle extraction {patient_index} chunk {chunk_index}/{total_chunks}",
            )
            usage = self._pop_ai_usage(row)
            cost = self._estimate_ai_cost(self._bundle_model(), usage)
            self._record_ai_usage(job, "summary", usage, cost)
            chunk_payloads.append(row)

        self._raise_if_cancelled(job)
        if progress_update:
            await progress_update(f"merging {total_chunks} chunks")

        merged = await self._merge_patient_chunk_payloads(
            chunk_payloads,
            all_documents,
            pages,
            patient_index,
            rule_based_summary,
            job=job,
        )
        self._raise_if_cancelled(job)
        return merged

    async def _merge_patient_chunk_payloads(
        self,
        chunk_payloads: list[dict[str, Any]],
        documents: list[DocumentSummary],
        pages: list[PageExtraction],
        patient_index: int,
        rule_based_summary: str,
        *,
        job: ExtractionJobDetail | None = None,
    ) -> dict[str, Any]:
        self._raise_if_cancelled(job)
        chunk_summaries = []
        chunk_opinions = []
        chunk_visits: list[OfficeVisitItem] = []
        headers: list[PatientHeader] = []

        for index, payload in enumerate(chunk_payloads, start=1):
            header = payload.get("header") or {}
            if isinstance(header, dict):
                headers.append(
                    PatientHeader(
                        to_name=self._clean_text(header.get("to_name")),
                        claim_number=self._clean_text(header.get("claim_number")),
                        from_name=self._clean_header_person_name(header.get("from_name")),
                        age_dob=self._clean_text(header.get("age_dob")),
                        review_date=self._normalize_extracted_date(self._clean_text(header.get("review_date"))),
                        occupation=self._clean_text(header.get("occupation")),
                        claimant=self._clean_patient_label(header.get("claimant")),
                        diagnosis_dod=self._clean_text(header.get("diagnosis_dod")),
                    )
                )
            if summary := self._clean_generated_section_text(payload.get("summary")):
                chunk_summaries.append(
                    f"Chunk {index} summary:\n"
                    f"{self._truncate_for_merge(summary, self._MERGE_CHUNK_SUMMARY_LIMIT)}"
                )
            if opinion := self._clean_generated_section_text(payload.get("opinion")):
                chunk_opinions.append(
                    f"Chunk {index} opinion:\n"
                    f"{self._truncate_for_merge(opinion, self._MERGE_CHUNK_OPINION_LIMIT)}"
                )
            chunk_visits.extend(self._parse_office_visits(payload.get("office_visits")))

        header = self._merge_patient_headers(headers, self._display_patient_name(documents))
        manifest = self._truncate_for_merge(
            self._build_patient_document_manifest(documents),
            self._MERGE_MANIFEST_LIMIT,
        )
        skeleton = self._truncate_for_merge(
            rule_based_summary or "[No rule-based summary skeleton available.]",
            self._MERGE_RULE_SUMMARY_LIMIT,
        )
        summaries_text = self._truncate_for_merge(
            "\n\n".join(chunk_summaries),
            self._MERGE_ALL_SUMMARIES_LIMIT,
        )
        opinions_text = self._truncate_for_merge(
            "\n\n".join(chunk_opinions),
            self._MERGE_ALL_OPINIONS_LIMIT,
        )
        merge_prompt = "\n\n".join(
            [
                f"Patient bundle index: {patient_index}",
                "Merge these chunk-level outputs into one final patient output.",
                "Do not add facts beyond the chunk summaries and document manifest.",
                "Preserve the summary paragraph order exactly as chunk order then document order.",
                "Document manifest:",
                manifest,
                "Rule-based summary skeleton:",
                skeleton,
                "Chunk summaries:",
                summaries_text,
                "Chunk opinions:",
                opinions_text,
            ]
        )
        merge_prompt = self._truncate_for_merge(merge_prompt, self._MERGE_PROMPT_LIMIT)
        try:
            row = await self._request_bundle_ai_json(
                model=self._bundle_model(),
                system_prompt=OPENAI_PATIENT_REVIEW_PROMPT,
                user_prompt=merge_prompt,
                schema=OPENAI_PATIENT_REVIEW_SCHEMA,
                task_label=f"OpenAI patient bundle merge {patient_index}",
            )
            usage = self._pop_ai_usage(row)
            cost = self._estimate_ai_cost(self._bundle_model(), usage)
            self._record_ai_usage(job, "summary", usage, cost)
        except OpenAIExtractionError:
            logger.warning(
                "Patient bundle %s merge failed after chunk extraction; using deterministic chunk merge.",
                patient_index,
                exc_info=True,
            )
            row = {}

        return {
            "name": self._display_patient_name(documents),
            "header": header,
            "summary": self._clean_generated_section_text(row.get("summary"))
            or summaries_text
            or rule_based_summary
            or "No patient summary generated.",
            "opinion": self._clean_generated_section_text(row.get("opinion"))
            or opinions_text
            or "No patient opinion generated.",
            "office_visits": self._parse_office_visits(row.get("office_visits")) or chunk_visits,
        }


    # Max chars of page_extract per document and total bundle, to avoid output truncation
    _BUNDLE_TEXT_PER_DOC_LIMIT = 3_000
    _BUNDLE_TEXT_TOTAL_LIMIT = 55_000
    _BUNDLE_CHUNK_TEXT_LIMIT = 42_000
    _BUNDLE_CHUNK_DOCUMENT_LIMIT = 50
    _MERGE_CHUNK_SUMMARY_LIMIT = 25_000
    _MERGE_CHUNK_OPINION_LIMIT = 4_000
    _MERGE_MANIFEST_LIMIT = 80_000
    _MERGE_RULE_SUMMARY_LIMIT = 80_000
    _MERGE_ALL_SUMMARIES_LIMIT = 260_000
    _MERGE_ALL_OPINIONS_LIMIT = 40_000
    _MERGE_PROMPT_LIMIT = 450_000

    def _truncate_for_merge(self, text: str | None, limit: int) -> str:
        value = self._clean_text(text) or ""
        if len(value) <= limit:
            return value
        return value[:limit].rstrip() + "\n\n[Truncated for merge prompt size limit.]"

    def _split_patient_documents_for_summary(
        self,
        documents: list[DocumentSummary],
        pages: list[PageExtraction],
    ) -> list[list[DocumentSummary]]:
        page_map = {page.page_number: page for page in pages}
        chunks: list[list[DocumentSummary]] = []
        current: list[DocumentSummary] = []
        current_size = 0

        for document in documents:
            doc_size = self._estimate_document_bundle_chars(document, page_map)
            should_split = (
                current
                and (
                    len(current) >= self._BUNDLE_CHUNK_DOCUMENT_LIMIT
                    or current_size + doc_size > self._BUNDLE_CHUNK_TEXT_LIMIT
                )
            )
            if should_split:
                chunks.append(current)
                current = []
                current_size = 0
            current.append(document)
            current_size += doc_size

        if current:
            chunks.append(current)
        return chunks or [documents]

    def _estimate_document_bundle_chars(
        self,
        document: DocumentSummary,
        page_map: dict[int, PageExtraction],
    ) -> int:
        text_size = sum(
            len(page_map[page_number].visible_text.strip())
            for page_number in document.page_numbers
            if page_number in page_map and page_map[page_number].visible_text.strip()
        )
        return min(text_size, self._BUNDLE_TEXT_PER_DOC_LIMIT) + 500

    def _build_patient_bundle_text(
        self,
        documents: list[DocumentSummary],
        pages: list[PageExtraction],
        *,
        total_limit: int | None = None,
    ) -> str:
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
            raw_text = "\n\n".join(
                page_map[page_number].visible_text.strip()
                for page_number in document.page_numbers
                if page_number in page_map and page_map[page_number].visible_text.strip()
            ).strip()
            if raw_text:
                capped = raw_text[: self._BUNDLE_TEXT_PER_DOC_LIMIT]
                if len(raw_text) > self._BUNDLE_TEXT_PER_DOC_LIMIT:
                    capped += " [...]"
                section += f"\nExtract:\n{capped}"
            sections.append(section)

        full_text = "\n\n".join(section for section in sections if section.strip())
        limit = total_limit or self._BUNDLE_TEXT_TOTAL_LIMIT
        if len(full_text) > limit:
            full_text = full_text[:limit] + "\n\n[Bundle text truncated to fit context limit.]"
        return full_text

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
        if self._document_has_placeholder_title(document) and not self._document_has_clinical_extract(document):
            return False
        if self._document_is_admin_only_title(document):
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
        if self._document_has_placeholder_title(document) and not self._document_has_clinical_extract(document):
            return False
        if self._document_is_admin_only_title(document):
            return False
        if self._document_is_noise(document):
            return False
        if self._document_is_technical_plan(document):
            return False
        if self._document_is_signature_only(document):
            return False
        return True

    def _document_has_placeholder_title(self, document: DocumentSummary) -> bool:
        title = self._normalize_key(document.title or "")
        return bool(re.fullmatch(r"document\s+\d+", title))

    def _document_has_clinical_extract(self, document: DocumentSummary) -> bool:
        # Known clinical kind → always include
        if document.summary_kind not in {SummaryKind.ADMINISTRATIVE, SummaryKind.UNKNOWN}:
            return True
        # For UNKNOWN/ADMINISTRATIVE: trust the AI's include_in_output flag as the
        # best available signal (set to False only for real boilerplate by the page parser)
        return document.include_in_output

    def _document_is_admin_only_title(self, document: DocumentSummary) -> bool:
        title = self._normalize_key(" ".join(part for part in (document.title, document.document_type) if part))
        admin_phrases = (
            "consent to use electronic communications",
            "consent for electronic communication",
            "electronic communications health information management",
            "health information management",
            "fax cover",
            "privacy notice",
            "billing",
            "invoice",
        )
        return any(phrase in title for phrase in admin_phrases)

    def _document_is_noise(self, document: DocumentSummary) -> bool:
        """Administrative boilerplate with no clinical/functional content."""
        if document.summary_kind == SummaryKind.ADMINISTRATIVE:
            return self._document_admin_noise_score(document) >= 1
        return self._document_admin_noise_score(document) >= 2

    def _document_is_signature_only(self, document: DocumentSummary) -> bool:
        """Pages that are exclusively physician-stamp or signature fields."""
        title = self._normalize_key(document.title or document.document_type)
        return any(sig in title for sig in ("physician stamp", "attending physician", "part 8 attending"))

    def _document_is_technical_plan(self, document: DocumentSummary) -> bool:
        """Radiotherapy/technical plan data pages that aren't clinical narrative."""
        title = self._normalize_key(document.title or document.document_type)
        return any(kw in title for kw in ("beam set", "beam data", "energy layer", "plan report", "plan approval"))

    def _document_admin_noise_score(self, document: DocumentSummary) -> int:
        """Score how many generic administrative-noise signals match this document (0 = none)."""
        title = self._normalize_key(document.title or document.document_type or "")
        score = 0
        for signal in _ADMIN_NOISE_SIGNALS:
            if signal in title:
                score += 1
        # Additional signals from classification
        if document.summary_kind == SummaryKind.ADMINISTRATIVE and not document.author:
            score += 1
        return score

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
        if document.patient_group_id:
            return f"group|{document.patient_group_id}"
        # Use canonical name (sorted tokens, drops middle initials/noise) for stable
        # bucketing so "Wanda C Russell" and "Wanda Russell" land in the same bucket.
        canonical = self._canonical_patient_name(document.patient_name)
        if canonical:
            return f"canonical|{canonical}"
        dob = self._normalize_patient_dob(document.patient_dob)
        if dob:
            return f"dob|{dob}"
        return f"unassigned|{index:03d}"

    def _build_pre_summary_patient_buckets(
        self,
        documents: list[DocumentSummary],
    ) -> tuple[dict[str, list[DocumentSummary]], list[str]]:
        # Backfill patient context before bucketing so that documents
        # with no per-page patient signals inherit from the nearest known document.
        self._backfill_document_patient_context(documents)

        # Detect dominant patient: if one canonical name owns ≥ 60 % of named
        # documents, assign unassigned/admin documents to that name instead of
        # fragmenting them into their own buckets.
        dominant_key = self._detect_dominant_patient_key(documents)

        buckets: dict[str, list[DocumentSummary]] = {}
        order: list[str] = []

        for index, document in enumerate(documents, start=1):
            bucket_key = self._patient_bucket_key(document, index)
            if bucket_key.startswith("unassigned|"):
                if dominant_key:
                    bucket_key = dominant_key
                else:
                    bucket_key = self._resolve_unassigned_bucket_key(documents, index - 1, bucket_key)
            if bucket_key not in buckets:
                buckets[bucket_key] = []
                order.append(bucket_key)
            buckets[bucket_key].append(document)

        logger.info(
            "Pre-summary patient buckets: %d documents → %d bucket(s): %s",
            len(documents),
            len(order),
            ", ".join(order),
        )
        return buckets, order

    def _detect_dominant_patient_key(self, documents: list[DocumentSummary]) -> str | None:
        """Return the bucket key for the dominant patient if one name accounts for ≥ 60 % of named documents."""
        from collections import Counter
        counts: Counter[str] = Counter()
        for doc in documents:
            key = self._patient_bucket_key(doc, 0)
            if not key.startswith("unassigned|"):
                counts[key] += 1
        if not counts:
            return None
        dominant_key, dominant_count = counts.most_common(1)[0]
        total_named = sum(counts.values())
        if dominant_count / total_named >= 0.60:
            return dominant_key
        return None

    def _resolve_unassigned_bucket_key(
        self,
        documents: list[DocumentSummary],
        document_index: int,
        fallback_key: str,
    ) -> str:
        previous_key = self._nearest_assigned_bucket_key(documents, document_index, step=-1)
        next_key = self._nearest_assigned_bucket_key(documents, document_index, step=1)
        if previous_key and next_key and previous_key == next_key:
            return previous_key
        if previous_key and not next_key:
            return previous_key
        if next_key and not previous_key:
            return next_key
        return fallback_key

    def _nearest_assigned_bucket_key(
        self,
        documents: list[DocumentSummary],
        start_index: int,
        *,
        step: int,
    ) -> str | None:
        index = start_index + step
        while 0 <= index < len(documents):
            candidate = self._patient_bucket_key(documents[index], index + 1)
            if not candidate.startswith("unassigned|"):
                return candidate
            index += step
        return None

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

    def _parse_clinical_relevance(self, value: Any) -> ClinicalRelevance:
        normalized = self._normalize_key(value)
        mapping = {
            ClinicalRelevance.CLINICAL.value: ClinicalRelevance.CLINICAL,
            ClinicalRelevance.FUNCTIONAL.value: ClinicalRelevance.FUNCTIONAL,
            ClinicalRelevance.ADMINISTRATIVE.value: ClinicalRelevance.ADMINISTRATIVE,
            ClinicalRelevance.UNKNOWN.value: ClinicalRelevance.UNKNOWN,
            "imaging": ClinicalRelevance.CLINICAL,
            "pathology": ClinicalRelevance.CLINICAL,
        }
        return mapping.get(normalized, ClinicalRelevance.UNKNOWN)

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
        sorted_pages = sorted(set(page_numbers))
        if len(sorted_pages) == 1:
            return str(sorted_pages[0])
        return f"{sorted_pages[0]}-{sorted_pages[-1]}"

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

    def _repair_page_extraction(self, page: PageExtraction) -> PageExtraction:
        """Light fill-only pass: populates empty identity fields from OCR text.

        Boundary detection (starts_new_document) is set by the AI during the
        parse step. This method only provides fallback values for fields the
        AI left completely empty.
        """
        text = self._clean_text(page.visible_text) or ""
        if not text:
            return page

        top_text = "\n".join(self._top_nonempty_lines(text, limit=12))

        # Fill only completely empty identity fields from page signals
        if not page.patient_name:
            page.patient_name = self._extract_patient_name_from_text(top_text)
        if not page.patient_dob:
            page.patient_dob = self._extract_patient_dob_from_text(top_text)
        if not page.patient_identifier:
            page.patient_identifier = self._extract_patient_identifier_from_text(top_text)
        page.patient_key = self._build_patient_key(page.patient_name, page.patient_dob, page.patient_identifier)

        # Merge any additional patient name candidates into mentioned list
        additional_names = self._extract_patient_name_candidates(text)
        if page.patient_name:
            additional_names.append(page.patient_name)
        page.mentioned_patient_names = self._merge_name_lists([page.mentioned_patient_names, additional_names])

        # Light imaging-specific fields (accession, exam title, radiologist)
        if not page.accession_number:
            page.accession_number = self._search_header_value(
                top_text, [r"(?im)^Accession(?: Number| #)?[:\s]+(.+)$"]
            )
        if not page.exam_title:
            page.exam_title = self._search_header_value(
                top_text, [r"(?im)^Exam(?: Title)?[:\s]+(.+)$", r"(?im)^Procedure[:\s]+(.+)$"]
            )
        if not page.radiologist_name:
            page.radiologist_name = self._search_header_value(
                top_text, [r"(?im)^Radiologist[:\s]+(.+)$", r"(?im)^Interpreting Physician[:\s]+(.+)$"]
            )
        if page.narrative_report_available is None:
            page.narrative_report_available = any(
                marker in self._normalize_key(text)
                for marker in ("clinical history", "technique", "findings", "impression")
            )

        return page

    def _top_nonempty_lines(self, text: str, limit: int = 12) -> list[str]:
        return [line.strip() for line in text.splitlines() if line.strip()][:limit]

    def _extract_patient_name_from_text(self, text: str) -> str | None:
        patterns = [
            r"(?im)^Claimant(?:'s)? Name[:\s]+([^\n]+)$",
            r"(?im)^Claimant[:\s]+([^\n]+)$",
            r"(?im)^Patient(?: Name)?[:\s]+([^\n]+)$",
            r"(?im)^Re[:\s]+([A-Z][A-Za-z' -]+(?:,\s*[A-Z][A-Za-z' -]+)?)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return self._clean_patient_label(match.group(1))
        return None

    def _extract_patient_name_candidates(self, text: str) -> list[str]:
        candidates: list[str] = []
        for pattern in (
            r"(?im)^(?:Claimant(?:'s)? Name|Claimant|Patient(?: Name)?)[:\s]+([^\n]+)$",
            r"(?im)^Re[:\s]+([A-Z][A-Za-z' -]+(?:,\s*[A-Z][A-Za-z' -]+)?)$",
        ):
            for match in re.finditer(pattern, text):
                if cleaned := self._clean_patient_label(match.group(1)):
                    candidates.append(cleaned)
        return self._clean_name_list(candidates)

    def _extract_patient_dob_from_text(self, text: str) -> str | None:
        patterns = [
            r"(?im)^(?:DOB|Date of Birth|Birth Date)[:\s]+([^\n]+)$",
            r"(?im)^Age\s*/\s*DOB[:\s]+([^\n]+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                raw = self._clean_text(match.group(1))
                if not raw:
                    continue
                date_match = re.search(r"([A-Za-z]+\s+\d{1,2},\s+\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})", raw)
                return self._normalize_extracted_date(date_match.group(1) if date_match else raw)
        return None

    def _extract_patient_identifier_from_text(self, text: str) -> str | None:
        return self._search_header_value(
            text,
            [
                r"(?im)^(?:Health Card Number|Health Card|PHN|Patient ID|Member ID)[:\s]+(.+)$",
            ],
        )

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
            from_name=self._clean_header_person_name(self._most_common(header.from_name for header in headers)),
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
        # Bundling always uses OpenAI regardless of AI_PROVIDER (faster, better quality)
        return settings.OPENAI_BUNDLE_MODEL

    def _schema_name(self, task_label: str) -> str:
        name = re.sub(r"[^A-Za-z0-9_]+", "_", task_label).strip("_").lower()
        return name[:64] or "structured_response"

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

    async def _request_bundle_ai_json(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str | list[dict[str, Any]],
        schema: dict[str, Any],
        task_label: str,
    ) -> dict[str, Any]:
        """Always uses OpenAI for bundling, regardless of AI_PROVIDER setting."""
        if not settings.OPENAI_API_KEY:
            raise OpenAIExtractionError("OPENAI_API_KEY is not configured for bundle summarisation.")
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
        if ChatGoogleGenerativeAI is None or HumanMessage is None or SystemMessage is None:
            raise GeminiExtractionError("langchain-google-genai is not installed. Run pip install -r requirements.txt.")

        has_images = isinstance(user_prompt, list) and any(
            item.get("type") == "image_url" for item in user_prompt
        )

        if has_images:
            return await self._request_gemini_json_two_pass(
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                task_label=task_label,
            )

        return await self._request_gemini_json_direct(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            task_label=task_label,
        )

    async def _request_gemini_json_two_pass(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: list[dict[str, Any]],
        schema: dict[str, Any],
        task_label: str,
    ) -> dict[str, Any]:
        logger.info("Starting %s (two-pass: OCR then JSON) with model %s", task_label, model)
        chat_model = ChatGoogleGenerativeAI(
            model=model,
            api_key=settings.GEMINI_API_KEY,
            temperature=0,
            max_tokens=65536,
            timeout=300,
            max_retries=0,
        )

        ocr_prompt = """Transcribe all visible text from the page image(s) into clean markdown.
Preserve structure: headings, tables, lists, checkboxes, form fields.
For checkboxes: use [x] for checked, [ ] for unchecked.
For tables: use markdown table syntax.
For form fields: show "Label: Value" format.
Include ALL text exactly as shown. Do not summarize."""

        ocr_messages = [
            SystemMessage(content="You are an expert document OCR system."),
            HumanMessage(content=self._build_ocr_content(ocr_prompt, user_prompt)),
        ]

        page_labels = [
            item.get("text", "").replace("Page ", "").strip()
            for item in user_prompt
            if item.get("type") == "text" and str(item.get("text", "")).startswith("Page ")
        ]
        page_numbers = []
        for label in page_labels:
            try:
                page_numbers.append(int(label))
            except (ValueError, TypeError):
                pass

        markdown_text = ""
        for attempt in range(1, OPENAI_MAX_RETRIES + 1):
            try:
                response = await chat_model.ainvoke(ocr_messages)
                markdown_text = self._extract_raw_text_from_langchain(response) or ""
                if markdown_text.strip():
                    break
                if attempt >= OPENAI_MAX_RETRIES:
                    logger.warning("%s OCR pass returned empty text; marking page(s) as excluded.", task_label)
                    result = {
                        "pages": [
                            {
                                "page_number": pn,
                                "starts_new_document": False,
                                "include_in_output": False,
                                "document_bucket": "administrative",
                                "boundary_hint": "exclude",
                                "page_extract": "",
                            }
                            for pn in page_numbers
                        ]
                    }
                    result["__usage_metadata"] = self._fallback_usage_from_request(model, "", result)
                    return result
            except JobCancelled:
                raise
            except GeminiExtractionError:
                if attempt >= OPENAI_MAX_RETRIES:
                    raise
            except Exception as exc:
                if attempt >= OPENAI_MAX_RETRIES:
                    raise GeminiExtractionError(f"{task_label} OCR pass failed: {exc}") from exc
            delay = self._gemini_retry_delay(attempt)
            logger.warning("%s OCR pass failed on attempt %s/%s. Retrying.", task_label, attempt, OPENAI_MAX_RETRIES)
            await asyncio.sleep(delay)

        logger.info("%s OCR pass extracted %d chars", task_label, len(markdown_text))

        # Extract per-page text blocks from the OCR markdown
        page_texts = self._split_ocr_markdown_by_page(markdown_text, page_numbers)

        # Metadata-only JSON schema — no visible_text, so output stays small and reliable
        metadata_schema = {
            "type": "object",
            "properties": {
                "pages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "page_number": {"type": "integer"},
                            "patient_name": {"type": "string"},
                            "patient_dob": {"type": "string"},
                            "patient_identifier": {"type": "string"},
                            "mentioned_patient_names": {"type": "array", "items": {"type": "string"}},
                            "document_title": {"type": "string"},
                            "document_type": {"type": "string"},
                            "document_bucket": {
                                "type": "string",
                                "enum": ["clinical", "imaging", "pathology", "functional", "administrative", "unknown"],
                            },
                            "document_date": {"type": "string"},
                            "author": {"type": "string"},
                        },
                        "required": ["page_number"],
                    },
                }
            },
            "required": ["pages"],
        }

        json_model = chat_model.bind(response_mime_type="application/json")
        json_prompt = f"""Below is the transcribed text from each page. Extract ONLY metadata fields — do NOT reproduce the page text.

{markdown_text[:8000]}

Return JSON with a "pages" array. Each item: page_number, patient_name, patient_dob, patient_identifier, mentioned_patient_names, document_title, document_type, document_bucket, document_date, author.
document_bucket must be one of: clinical, imaging, pathology, functional, administrative, unknown.
If a field is not visible on that page, leave it as an empty string."""

        json_messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=json_prompt),
        ]

        meta_payload: dict[str, Any] = {"pages": []}
        for attempt in range(1, OPENAI_MAX_RETRIES + 1):
            try:
                response = await json_model.ainvoke(json_messages)
                raw_text = self._extract_raw_text_from_langchain(response)
                if not raw_text:
                    raise GeminiExtractionError(f"{task_label} metadata pass returned empty response.")
                parsed = self._parse_jsonish_content(raw_text, task_label)
                if isinstance(parsed, dict):
                    meta_payload = parsed
                break
            except JobCancelled:
                raise
            except GeminiExtractionError:
                if attempt >= OPENAI_MAX_RETRIES:
                    logger.warning("%s metadata pass failed after retries; using OCR text only.", task_label)
                    break
                delay = self._gemini_retry_delay(attempt)
                logger.warning("%s metadata pass failed on attempt %s/%s. Retrying.", task_label, attempt, OPENAI_MAX_RETRIES)
                await asyncio.sleep(delay)
            except Exception as exc:
                if attempt >= OPENAI_MAX_RETRIES:
                    logger.warning("%s metadata pass failed: %s. Using OCR text only.", task_label, exc)
                    break
                delay = self._gemini_retry_delay(attempt)
                await asyncio.sleep(delay)

        # Merge: inject full OCR text into each page row
        meta_by_page: dict[int, dict[str, Any]] = {}
        for row in (meta_payload.get("pages") or []):
            if isinstance(row, dict):
                pn = int(row.get("page_number") or 0)
                if pn > 0:
                    meta_by_page[pn] = row

        merged_pages = []
        for pn in page_numbers:
            row = meta_by_page.get(pn, {"page_number": pn})
            row["page_extract"] = page_texts.get(pn, "")
            merged_pages.append(row)

        if not merged_pages and page_numbers:
            merged_pages = [{"page_number": pn, "page_extract": page_texts.get(pn, "")} for pn in page_numbers]

        result = {"pages": merged_pages}
        result["__usage_metadata"] = self._fallback_usage_from_request(model, markdown_text, result)
        logger.info("Completed %s", task_label)
        return result

    def _split_ocr_markdown_by_page(self, markdown_text: str, page_numbers: list[int]) -> dict[int, str]:
        """Split OCR markdown into per-page text blocks."""
        if not page_numbers:
            return {}

        # Try splitting on "Page N" markers the model may have produced
        page_texts: dict[int, str] = {}
        split_pattern = re.compile(r"(?:^|\n)(?:---\s*)?Page\s+(\d+)\s*(?:---)?", re.IGNORECASE)
        parts = split_pattern.split(markdown_text)

        if len(parts) > 1:
            i = 1
            while i < len(parts) - 1:
                try:
                    pn = int(parts[i])
                    text = parts[i + 1].strip()
                    if pn in page_numbers:
                        page_texts[pn] = text
                except (ValueError, IndexError):
                    pass
                i += 2

        # If splitting didn't work (model produced continuous text), divide evenly
        if not page_texts and page_numbers:
            chunk = max(1, len(markdown_text) // len(page_numbers))
            for idx, pn in enumerate(page_numbers):
                start = idx * chunk
                end = start + chunk if idx < len(page_numbers) - 1 else len(markdown_text)
                page_texts[pn] = markdown_text[start:end].strip()

        # Fill any missing pages with full text as fallback
        for pn in page_numbers:
            if pn not in page_texts:
                page_texts[pn] = markdown_text.strip()

        return page_texts

    def _build_ocr_content(self, ocr_prompt: str, user_prompt: list[dict[str, Any]]) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [{"type": "text", "text": ocr_prompt}]
        for item in user_prompt:
            if item.get("type") == "text" and "Page " in str(item.get("text", "")):
                content.append({"type": "text", "text": item["text"]})
            if item.get("type") == "image_url":
                image_url = item.get("image_url")
                data_url = image_url.get("url") if isinstance(image_url, dict) else None
                if isinstance(data_url, str):
                    mime_type, data = self._parse_data_url(data_url)
                    content.append({"type": "image", "base64": data, "mime_type": mime_type})
        return content

    async def _request_gemini_json_direct(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str | list[dict[str, Any]],
        schema: dict[str, Any],
        task_label: str,
    ) -> dict[str, Any]:
        logger.info("Starting %s with model %s", task_label, model)
        chat_model = ChatGoogleGenerativeAI(
            model=model,
            api_key=settings.GEMINI_API_KEY,
            temperature=0,
            max_tokens=65536,
            timeout=300,
            max_retries=0,
        )
        json_model = chat_model.bind(response_mime_type="application/json")
        messages = self._build_langchain_gemini_messages(system_prompt, user_prompt, schema)

        for attempt in range(1, OPENAI_MAX_RETRIES + 1):
            try:
                response = await json_model.ainvoke(messages)
                raw_text = self._extract_raw_text_from_langchain(response)
                if not raw_text:
                    raise GeminiExtractionError(f"{task_label} returned empty response.")
                parsed_payload = self._parse_jsonish_content(raw_text, task_label)
                if not isinstance(parsed_payload, dict):
                    raise GeminiExtractionError(f"{task_label} did not return a JSON object.")
                parsed_payload["__usage_metadata"] = self._fallback_usage_from_request(model, user_prompt, parsed_payload)
                logger.info("Completed %s", task_label)
                return parsed_payload
            except JobCancelled:
                raise
            except GeminiExtractionError:
                if attempt >= OPENAI_MAX_RETRIES:
                    raise
                delay = self._gemini_retry_delay(attempt)
                logger.warning(
                    "%s LangChain Gemini request failed on attempt %s/%s. Retrying in %.1f seconds.",
                    task_label,
                    attempt,
                    OPENAI_MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                if attempt >= OPENAI_MAX_RETRIES:
                    raise GeminiExtractionError(f"{task_label} failed after LangChain Gemini retries: {exc}") from exc
                delay = self._gemini_retry_delay(attempt)
                logger.warning(
                    "%s LangChain Gemini request failed on attempt %s/%s. Retrying in %.1f seconds: %s",
                    task_label,
                    attempt,
                    OPENAI_MAX_RETRIES,
                    delay,
                    self._short_error(str(exc)),
                )
                await asyncio.sleep(delay)

        raise GeminiExtractionError(f"{task_label} failed without a response.")

    def _extract_raw_text_from_langchain(self, raw: Any) -> str | None:
        if hasattr(raw, "text"):
            text = raw.text
            if isinstance(text, str):
                return text
        if hasattr(raw, "content"):
            content = raw.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, str):
                        parts.append(part)
                    elif isinstance(part, dict) and part.get("type") == "text":
                        parts.append(str(part.get("text", "")))
                return "\n".join(parts)
        return None

    def _build_langchain_gemini_messages(
        self,
        system_prompt: str,
        user_prompt: str | list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
    ) -> list[Any]:
        schema_hint = ""
        if schema:
            schema_hint = f"\n\nReturn JSON matching this schema:\n```json\n{json.dumps(schema, indent=2)}\n```"
        schema_prompt = f"Return only valid JSON.{schema_hint}"
        if isinstance(user_prompt, str):
            content: str | list[dict[str, Any]] = "\n\n".join([schema_prompt, user_prompt])
        else:
            content = [{"type": "text", "text": schema_prompt}]
            for item in user_prompt:
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    content.append({"type": "text", "text": item["text"]})
                    continue
                if item.get("type") == "image_url":
                    image_url = item.get("image_url")
                    data_url = image_url.get("url") if isinstance(image_url, dict) else None
                    if isinstance(data_url, str):
                        mime_type, data = self._parse_data_url(data_url)
                        content.append({"type": "image", "base64": data, "mime_type": mime_type})
        return [SystemMessage(content=system_prompt), HumanMessage(content=content)]

    def _parse_data_url(self, data_url: str) -> tuple[str, str]:
        prefix, _, data = data_url.partition(",")
        mime_type = prefix.removeprefix("data:").removesuffix(";base64") or OPENAI_PAGE_IMAGE_MEDIA_TYPE
        return mime_type, data

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
        preview = self._clean_text(content[:500]) or "empty response"
        logger.warning("%s raw Gemini response preview: %s", task_label, preview)
        if content.lstrip().startswith("{") and not content.rstrip().endswith("}"):
            raise GeminiExtractionError(f"{task_label} returned truncated JSON. Response likely hit output limit.")
        raise GeminiExtractionError(f"{task_label} returned invalid JSON: {preview}")

    def _gemini_retry_delay(self, attempt: int) -> float:
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
        schema_prompt = "Return only structured JSON matching the configured response schema."
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
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": self._schema_name(task_label),
                            "schema": schema,
                            "strict": False,
                        },
                    },
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

