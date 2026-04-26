from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from app.services.extraction import ExtractionService

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="extraction-worker")
_queued_job_ids: set[str] = set()


def enqueue_extraction_job(job_id: str) -> None:
    if job_id in _queued_job_ids:
        logger.info("Extraction job %s is already queued", job_id)
        return

    _queued_job_ids.add(job_id)
    _executor.submit(_run_extraction_job, job_id)


def _run_extraction_job(job_id: str) -> None:
    try:
        asyncio.run(ExtractionService().process_job(job_id))
    finally:
        _queued_job_ids.discard(job_id)
