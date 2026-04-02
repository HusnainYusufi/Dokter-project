from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

from llama_cloud import AsyncLlamaCloud
from pypdf import PdfReader

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
            logger.info("Starting extraction job %s for %s", job.id, job.filename)
            job.status = JobStatus.PROCESSING
            self._set_step(
                job,
                "extract",
                PipelineStepStatus.RUNNING,
                "Uploading document and starting extraction",
            )
            self._set_step(job, "boundary", PipelineStepStatus.PENDING, "Waiting for extraction output.")
            self._set_step(job, "summary", PipelineStepStatus.PENDING, "Waiting for patient bundles.")
            self.store.save_job(job)

            payload = await self._extract_job_payload(source_bytes, job.filename, job)
            job.pages = []
            job.documents = []
            job.patients = payload["patients"]
            job.page_count = payload["page_count"]
            job.patient_count = len(job.patients)
            job.document_count = sum(len(patient.office_visits) for patient in job.patients)
            job.capture_certification = (
                f"Llama assigned {job.patient_count} patient section(s) across {job.page_count} pages in one pass."
            )
            self._set_step(job, "extract", PipelineStepStatus.COMPLETED, f"Extracted {job.patient_count} patient section(s).")
            self._set_step(job, "boundary", PipelineStepStatus.COMPLETED, "Patient page ranges assigned by Llama.")
            self._set_step(job, "summary", PipelineStepStatus.COMPLETED, "Prepared patient summaries and opinions.")
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
        except Exception as exc:
            self._mark_job_failed(job, exc)

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

        page_config: dict[str, Any] = {
            "data_schema": PAGE_SCHEMA,
            "extraction_target": "per_page",
            "tier": "agentic",
            "parse_tier": "agentic",
            "system_prompt": PAGE_PROMPT,
        }
        if settings.ENABLE_CONFIDENCE_SCORES:
            page_config["confidence_scores"] = True

        try:
            async with AsyncLlamaCloud(
                api_key=settings.LLAMA_CLOUD_API_KEY,
                timeout=900.0,
            ) as client:
                upload_stream = io.BytesIO(file_content)
                upload_stream.name = filename
                uploaded = await client.files.create(file=upload_stream, purpose="extract")
                uploaded_id = uploaded.id
                self._update_job_progress(job, "extract", "Running whole-file patient extraction.")
                patient_result = await self._run_extract(client, uploaded_id, patient_config, JOB_SCHEMA, progress_label="whole-file patient extraction")
                self._update_job_progress(job, "extract", "Running page-local extraction for record indexing.")
                page_result = await self._run_extract(client, uploaded_id, page_config, PAGE_SCHEMA, progress_label="page-local extraction")
        except Exception as exc:
            raise ExtractionError(str(exc)) from exc

        payload = patient_result.model_dump(mode="json")
        row = payload.get("data") or payload.get("extract_result") or {}
        if isinstance(row, list):
            row = row[0] if row else {}

        initial_patients: list[PatientSummary] = []
        for index, item in enumerate(row.get("patients") or [], start=1):
            page_start = int(item.get("page_start") or 0)
            page_end = int(item.get("page_end") or page_start or 0)
            header_payload = item.get("header") or {}

            initial_patients.append(
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

        pages = self._parse_page_extractions(page_result)
        if pages:
            try:
                self._update_job_progress(job, "boundary", "Discovering patient candidates.", status=PipelineStepStatus.RUNNING)
                pages = await self._assign_patient_coverage(uploaded_id, pages, initial_patients, job)
            except Exception:
                logger.warning("Patient page classification failed; falling back to extracted page ownership", exc_info=True)

        documents = self._build_document_groups(pages) if pages else []
        if documents:
            self._update_job_progress(job, "summary", "Preparing patient bundles.", status=PipelineStepStatus.RUNNING)
            patients = await self._build_patient_groups(documents, pages, job)
        else:
            patients = initial_patients

        inferred_page_count = max(
            [patient.page_end for patient in patients if patient.page_end > 0] + [0]
        )
        patients = self._collapse_patient_sections(patients)
        if documents:
            self._attach_records_to_patients(patients, documents)

        return {
            "page_count": int(actual_page_count or row.get("page_count") or inferred_page_count or 0),
            "patients": patients,
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
                    document_date=self._normalize_extracted_date(self._clean_text(item.get("document_date"))),
                    author=self._clean_text(item.get("author")),
                    visible_text=self._clean_text(item.get("visible_text")) or "",
                )
            )

        logger.info("Built %s page-local extraction rows for record indexing", len(pages))
        return pages

    async def _assign_patient_coverage(
        self,
        file_id: str,
        pages: list[PageExtraction],
        initial_patients: list[PatientSummary],
        job: ExtractionJobDetail | None = None,
    ) -> list[PageExtraction]:
        candidates = self._discover_patient_candidates(initial_patients, pages)
        if not candidates:
            return pages

        if len(candidates) == 1:
            logger.info(
                "Detected single-patient coverage candidate %s; assigning all %s pages to that patient",
                candidates[0]["name"],
                len(pages),
            )
            return self._apply_uniform_patient_coverage(pages, candidates[0])

        labels_by_page = await self._classify_patient_windows(file_id, len(pages), candidates, job)
        return self._apply_window_labels_to_pages(pages, labels_by_page, candidates)

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

    def _apply_uniform_patient_coverage(
        self,
        pages: list[PageExtraction],
        candidate: dict[str, Any],
    ) -> list[PageExtraction]:
        for page in pages:
            self._apply_candidate_to_page(page, candidate)
        return pages

    async def _classify_patient_windows(
        self,
        file_id: str,
        page_count: int,
        candidates: list[dict[str, Any]],
        job: ExtractionJobDetail | None = None,
    ) -> dict[int, str]:
        labels_by_page: dict[int, str] = {}
        rules = self._build_patient_classify_rules(candidates)
        total_windows = max(1, (page_count + CLASSIFY_WINDOW_SIZE - 1) // CLASSIFY_WINDOW_SIZE)

        async with AsyncLlamaCloud(
            api_key=settings.LLAMA_CLOUD_API_KEY,
            timeout=900.0,
        ) as client:
            for window_index, start_page in enumerate(range(1, page_count + 1, CLASSIFY_WINDOW_SIZE), start=1):
                end_page = min(page_count, start_page + CLASSIFY_WINDOW_SIZE - 1)
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

        logger.info("Classified %s pages into patient coverage windows", page_count)
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
        if candidate.get("claim_number") and not self._clean_text(page.patient_identifier):
            page.patient_identifier = candidate["claim_number"]
        page.patient_key = self._normalize_key(candidate.get("claim_number") or candidate["name"]) or page.patient_key
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
            if not self._document_should_feed_patient_output(document):
                continue

            visit = OfficeVisitItem(
                title=document.title or document.document_type or "Record",
                date=self._normalize_extracted_date(document.document_date),
                author=self._display_document_author(document),
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
        for index, bucket_key in enumerate(order, start=1):
            bucket_documents = buckets[bucket_key]
            patient_documents = self._select_patient_documents(bucket_documents)
            summary_documents = patient_documents or bucket_documents
            self._update_job_progress(
                job,
                "summary",
                f"Summarizing patient bundle {index}/{total_buckets} across {len(summary_documents)} record(s).",
            )
            page_numbers = sorted(
                {
                    page_number
                    for document in summary_documents
                    for page_number in document.page_numbers
                }
            )
            patient_payload = await self._extract_patient_payload(summary_documents, pages, index, job)
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
            patients.append(
                PatientSummary(
                    id=f"patient_{index:03d}",
                    name=patient_name,
                    header=patient_header,
                    summary=patient_payload["summary"],
                    page_start=page_numbers[0] if page_numbers else 0,
                    page_end=page_numbers[-1] if page_numbers else 0,
                    opinion=patient_payload["opinion"],
                    office_visits=self._documents_to_office_visits(summary_documents),
                )
            )

        return patients

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
    ) -> dict[str, Any]:
        bundle_text = self._build_patient_bundle_text(documents, pages)
        if not bundle_text.strip():
            return {
                "name": self._display_patient_name(documents),
                "header": PatientHeader(claimant=self._display_patient_name(documents)),
                "summary": "No extractable patient text was captured.",
                "opinion": "No patient opinion could be generated from the captured material.",
                "office_visits": [],
            }

        config: dict[str, Any] = {
            "data_schema": PATIENT_SCHEMA,
            "extraction_target": "per_doc",
            "tier": "agentic",
            "parse_tier": "agentic",
            "system_prompt": PATIENT_PROMPT,
        }

        try:
            async with AsyncLlamaCloud(
                api_key=settings.LLAMA_CLOUD_API_KEY,
                timeout=900.0,
            ) as client:
                upload_stream = io.BytesIO(bundle_text.encode("utf-8"))
                upload_stream.name = f"patient_{patient_index:03d}.txt"
                uploaded = await client.files.create(file=upload_stream, purpose="extract")
                self._update_job_progress(
                    job,
                    "summary",
                    f"Running patient-level extraction for bundle {patient_index}.",
                )
                result = await self._run_extract(
                    client,
                    uploaded.id,
                    config,
                    PATIENT_SCHEMA,
                    progress_label=f"patient bundle {patient_index} extraction",
                )
        except Exception as exc:
            raise ExtractionError(f"Patient summary extraction failed: {exc}") from exc

        payload = result.model_dump(mode="json")
        row = payload.get("data") or payload.get("extract_result") or {}
        if isinstance(row, list):
            row = row[0] if row else {}

        header_payload = row.get("header") or {}
        office_visits: list[OfficeVisitItem] = []
        for item in row.get("office_visits") or []:
            page_start = int(item.get("page_start") or 0)
            page_end = int(item.get("page_end") or page_start or 0)
            if not item.get("title") or page_start <= 0 or page_end <= 0:
                continue
            office_visits.append(
                OfficeVisitItem(
                    title=self._clean_text(item.get("title")) or "Office visit",
                    date=self._clean_text(item.get("date")),
                    author=self._clean_text(item.get("author")),
                    page_start=page_start,
                    page_end=page_end,
                )
            )

        return {
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
            "summary": self._clean_text(row.get("summary")) or "No patient summary generated.",
            "opinion": self._clean_text(row.get("opinion")) or "No patient opinion generated.",
            "office_visits": office_visits,
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

    def _select_patient_documents(self, documents: list[DocumentSummary]) -> list[DocumentSummary]:
        return [document for document in documents if self._document_should_feed_patient_output(document)]

    def _document_should_feed_patient_output(self, document: DocumentSummary) -> bool:
        if not document.include_in_output:
            return False
        if self._document_is_header_only(document):
            return False
        if self._document_is_noise(document):
            return False
        if self._document_is_technical_plan(document):
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

    def _count_pdf_pages(self, file_content: bytes) -> int:
        try:
            reader = PdfReader(io.BytesIO(file_content))
            return len(reader.pages)
        except Exception:
            logger.warning("Unable to determine PDF page count locally before extraction", exc_info=True)
            return 0

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

