"""Orchestrator for the new extraction pipeline.

Stages (LlamaIndex-style):
    1. PDF.render_batches -> images per page
    2. Parser.parse_pages -> ParsedPage with evidence array (Gemini)
    3. Filters.scrub  -> drop admin/empty + strip PII from raw text
    4. Grouping.group_documents -> DocumentSegment[]
    5. Grouping.group_patients -> PatientBundle[]
    6. Rendering: header + summary (template-fill) + visits
    7. Opinion: OpenAI call per patient
    8. Persist: encrypted artifacts + .doc export
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from app.core.config import settings
from app.core.exceptions import ProcessingError
from app.schemas.extraction import (
    CostModelBreakdown,
    CostStageBreakdown,
    CostSummary,
    ExtractionJobDetail,
    JobStatus,
    PatientSummary,
    PipelineStepStatus,
)
from app.services.extraction.cost import CostTracker
from app.services.extraction.filters import scrub_pages
from app.services.extraction.grouping import (
    attach_placeholders,
    build_coverage_placeholders,
    group_dated_entries,
    group_patients,
)
from app.services.extraction.identity import resolve_author_identities
from app.services.extraction.header import build_header
from app.services.extraction.llm import RunLogger
from app.services.extraction.opinion import build_opinion
from app.services.extraction.parser import parse_pdf
from app.services.extraction.pdf import count_pages
from app.services.extraction.summary import build_summary
from app.services.job_store import utc_now_iso
from app.services.rules import RuleConfigStore

logger = logging.getLogger(__name__)


class JobCancelled(Exception):
    pass


_running_jobs: set[str] = set()
_cancel_events: dict[str, asyncio.Event] = {}


async def process_job(service, job_id: str) -> None:  # noqa: ANN001 - circular type
    if job_id in _running_jobs:
        logger.info("Skipping duplicate process request for %s", job_id)
        return

    _running_jobs.add(job_id)
    _cancel_events[job_id] = asyncio.Event()
    persistence = service.persistence
    job = persistence.get_job(job_id)
    source_bytes = persistence.read_source_pdf(job_id)
    cancel_event = _cancel_events[job_id]

    def _check_cancel() -> None:
        if cancel_event.is_set():
            raise JobCancelled()

    def _set_step(key: str, status: PipelineStepStatus, detail: str | None = None) -> None:
        for step in job.pipeline:
            if step.key == key:
                step.status = status
                if detail is not None:
                    step.detail = detail
                break
        job.updated_at = utc_now_iso()
        persistence.save_job(job)

    async def _progress(detail: str) -> None:
        try:
            for step in job.pipeline:
                if step.status == PipelineStepStatus.RUNNING:
                    step.detail = detail
                    break
            job.updated_at = utc_now_iso()
            persistence.save_job(job)
        except Exception:
            logger.debug("Progress update failed", exc_info=True)

    cost_tracker = CostTracker()
    _STAGE_TO_STEP = {"parse": "extract", "summarize": "summary"}

    def _apply_cost(event: dict) -> None:
        try:
            totals = event.get("totals") or {}
            job.cost_summary = _build_cost_summary(totals)
            stage = event.get("stage") or ""
            step_key = _STAGE_TO_STEP.get(stage)
            if step_key:
                stage_totals = totals.get("by_stage", {}).get(stage) or {}
                for step in job.pipeline:
                    if step.key == step_key:
                        step.input_tokens = int(stage_totals.get("input_tokens", 0) or 0)
                        step.output_tokens = int(stage_totals.get("output_tokens", 0) or 0)
                        step.cost_usd = float(stage_totals.get("cost_usd", 0.0) or 0.0)
                        break
            job.updated_at = utc_now_iso()
            persistence.save_job(job)
        except Exception:
            logger.debug("Cost update failed", exc_info=True)

    cost_tracker.on_update = _apply_cost

    # Resolve the rule configuration for this run. Jobs created since the rule
    # engine carry an immutable snapshot; older jobs (or retries from before a
    # configuration existed) fall back to the current default so they still
    # benefit from Rule Studio.
    rule_config = job.rule_config
    if rule_config is None:
        try:
            rule_config = RuleConfigStore().resolve_snapshot(None)
        except Exception:  # noqa: BLE001
            logger.warning("Could not resolve a default rule configuration.", exc_info=True)
            rule_config = None
        if rule_config is not None:
            job.rule_config = rule_config
            job.rule_config_id = rule_config.id
            job.rule_config_name = rule_config.name
            job.rule_config_version = rule_config.version

    try:
        logger.info(
            "Starting extraction job %s for %s (rule configuration: %s)",
            job.id,
            job.filename,
            f"{rule_config.name} v{rule_config.version}" if rule_config else "built-in",
        )
        _check_cancel()
        job.status = JobStatus.PROCESSING
        job.processing_started_at = utc_now_iso()
        _set_step("extract", PipelineStepStatus.RUNNING, "Rendering pages and parsing evidence with Gemini.")
        _set_step("boundary", PipelineStepStatus.PENDING, "Waiting for parser.")
        _set_step("summary", PipelineStepStatus.PENDING, "Waiting for entry organization.")

        async def _cancel_watcher() -> None:
            while not cancel_event.is_set():
                await asyncio.sleep(2)
                try:
                    latest = persistence.get_job(job_id)
                    if latest.status == JobStatus.CANCELLED:
                        cancel_event.set()
                        return
                except Exception:
                    pass

        watcher = asyncio.create_task(_cancel_watcher())
        try:
            run_logger = RunLogger(job_id=job.id)
            page_count = count_pages(source_bytes)
            job.page_count = page_count
            persistence.save_job(job)

            provider_label = "OpenAI" if settings.AI_PROVIDER == "openai" else "Gemini"
            await _progress(f"Parsing {page_count} page(s) with {provider_label}.")
            parsed_pages = await parse_pdf(
                source_bytes,
                page_count=page_count,
                check_cancel=_check_cancel,
                progress=_progress,
                run_logger=run_logger,
                cost_tracker=cost_tracker,
                rule_config=rule_config,
            )
            _check_cancel()

            scrubbed_pages = scrub_pages(parsed_pages)
            _set_step(
                "extract",
                PipelineStepStatus.COMPLETED,
                f"Parsed {page_count} page(s) with evidence schema.",
            )
            _set_step("boundary", PipelineStepStatus.RUNNING, "Organizing dated and authored entries.")
            persistence.save_job(job)
            _check_cancel()

            await _progress("Resolving provider name spellings across the file.")
            scrubbed_pages = await resolve_author_identities(
                scrubbed_pages, run_logger=run_logger, cost_tracker=cost_tracker
            )
            _check_cancel()

            await _progress("Organizing dated and authored entries into document cards.")
            documents = group_dated_entries(scrubbed_pages)
            patients = group_patients(documents)
            # Every physical page must be visibly accounted for: pages no
            # included document claimed (admin/blank/unparseable) surface as
            # deterministic placeholder cards in their chronological position.
            attach_placeholders(
                patients, build_coverage_placeholders(scrubbed_pages, patients)
            )
            _set_step(
                "boundary",
                PipelineStepStatus.COMPLETED,
                f"Resolved {len(documents)} document(s) across {len(patients)} patient(s).",
            )
            _set_step("summary", PipelineStepStatus.RUNNING, "Generating headers, summaries, and opinions.")
            persistence.save_job(job)
            _check_cancel()

            patient_summaries: list[PatientSummary] = []
            for index, bundle in enumerate(patients, start=1):
                _check_cancel()
                await _progress(
                    f"Building patient {index}/{len(patients)} ({bundle.name or 'patient'})."
                )
                header = build_header(bundle)
                paragraphs, summary_text = await build_summary(
                    bundle,
                    run_logger=run_logger,
                    cost_tracker=cost_tracker,
                    progress=_progress,
                    bundle_index=index,
                    rule_config=rule_config,
                )
                opinion_text = await build_opinion(
                    bundle,
                    header,
                    run_logger=run_logger,
                    cost_tracker=cost_tracker,
                    rule_config=rule_config,
                )
                patient_id = bundle.id
                patient_summaries.append(
                    PatientSummary(
                        id=patient_id,
                        name=bundle.name or header.claimant or "Patient",
                        header=header,
                        summary=summary_text,
                        summary_paragraphs=paragraphs,
                        page_start=bundle.page_start,
                        page_end=bundle.page_end,
                        opinion=opinion_text,
                    )
                )

            job.patients = patient_summaries
            job.patient_count = len(patient_summaries)
            job.document_count = sum(
                sum(1 for paragraph in p.summary_paragraphs if not paragraph.is_placeholder)
                for p in patient_summaries
            )
            job.capture_certification = (
                f"Parsed {job.page_count} page(s) and prepared {job.patient_count} patient section(s)."
            )
            _set_step(
                "summary",
                PipelineStepStatus.COMPLETED,
                f"Prepared {job.patient_count} patient section(s).",
            )
            _set_step("export", PipelineStepStatus.RUNNING, "Generating Word-compatible .doc export.")
            persistence.save_job(job)
            _check_cancel()

            _refresh_export_filename(job)
            export_bytes = service.exporter.render(job)
            persistence.save_artifact(job.id, "summary_doc", export_bytes)
            persistence.save_artifact(
                job.id,
                "extraction_result",
                json.dumps(
                    {
                        "patients": [p.model_dump(mode="json") for p in job.patients],
                        "page_count": job.page_count,
                    },
                    indent=2,
                ).encode(),
            )
            job.export_artifact.ready = True
            job.export_artifact.size_bytes = len(export_bytes)
            _set_step("export", PipelineStepStatus.COMPLETED, f"Prepared {job.export_artifact.filename}.")
            job.status = JobStatus.COMPLETED
            job.error = None
            persistence.save_job(job)
            logger.info("Completed extraction job %s", job.id)
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
    except JobCancelled:
        _mark_cancelled(persistence, job)
    except ProcessingError as exc:
        _mark_failed(persistence, job, exc, str(exc.detail))
    except Exception as exc:  # noqa: BLE001
        _mark_failed(persistence, job, exc, str(exc) or exc.__class__.__name__)
    finally:
        _running_jobs.discard(job_id)
        _cancel_events.pop(job_id, None)


def _mark_cancelled(persistence, job: ExtractionJobDetail) -> None:  # noqa: ANN001
    job.status = JobStatus.CANCELLED
    job.error = "Job cancelled by user."
    for step in job.pipeline:
        if step.status == PipelineStepStatus.RUNNING:
            step.status = PipelineStepStatus.FAILED
            step.detail = "Cancelled by user."
    persistence.save_job(job)


def _mark_failed(persistence, job: ExtractionJobDetail, exc: Exception, message: str) -> None:  # noqa: ANN001
    logger.exception("Extraction job %s failed: %s", job.id, exc)
    job.status = JobStatus.FAILED
    job.error = message or exc.__class__.__name__
    for step in job.pipeline:
        if step.status == PipelineStepStatus.RUNNING:
            step.status = PipelineStepStatus.FAILED
            step.detail = message[:200] or "Failed."
    persistence.save_job(job)


def _refresh_export_filename(job: ExtractionJobDetail) -> None:
    base = re.sub(r"[^a-zA-Z0-9._-]+", "-", (job.filename or "patient").rsplit(".", 1)[0]).strip("-").lower() or "patient"
    job.export_artifact.filename = f"{base}-summary.doc"


def _build_cost_summary(totals: dict) -> CostSummary:
    by_stage = [
        CostStageBreakdown(
            stage=stage,
            calls=int(values.get("calls", 0) or 0),
            input_tokens=int(values.get("input_tokens", 0) or 0),
            output_tokens=int(values.get("output_tokens", 0) or 0),
            cached_input_tokens=int(values.get("cached_input_tokens", 0) or 0),
            cost_usd=float(values.get("cost_usd", 0.0) or 0.0),
        )
        for stage, values in (totals.get("by_stage") or {}).items()
    ]
    by_model = [
        CostModelBreakdown(
            provider=str(item.get("provider") or ""),
            model=str(item.get("model") or ""),
            calls=int(item.get("calls", 0) or 0),
            input_tokens=int(item.get("input_tokens", 0) or 0),
            output_tokens=int(item.get("output_tokens", 0) or 0),
            cached_input_tokens=int(item.get("cached_input_tokens", 0) or 0),
            cost_usd=float(item.get("cost_usd", 0.0) or 0.0),
        )
        for item in (totals.get("by_model") or [])
    ]
    return CostSummary(
        calls=int(totals.get("calls", 0) or 0),
        input_tokens=int(totals.get("input_tokens", 0) or 0),
        output_tokens=int(totals.get("output_tokens", 0) or 0),
        cached_input_tokens=int(totals.get("cached_input_tokens", 0) or 0),
        cost_usd=float(totals.get("cost_usd", 0.0) or 0.0),
        by_stage=by_stage,
        by_model=by_model,
    )
