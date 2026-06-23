"""Optional LlamaParse enrichment.

LlamaParse (LlamaCloud) renders each PDF page to clean **markdown** - preserving
tables, headings, form layout, and image/figure descriptions - far better than
raw OCR. We use it as an *enrichment* layer: the per-page markdown is handed to
the existing evidence extractor alongside the page image, so the model reads
faint scans, tables, and image pages more reliably.

This module is deliberately defensive: it is gated behind ``USE_LLAMAPARSE`` and
every failure path returns an empty map, so the extraction pipeline always falls
back to image-only parsing and can never be broken by LlamaParse being
unavailable, unconfigured, or returning an unexpected shape.
"""
from __future__ import annotations

import logging
import os
import tempfile

from app.core.config import settings

logger = logging.getLogger(__name__)


def _extract_page_markdown(documents: list, fallback_text: str = "") -> dict[int, str]:  # noqa: ANN001
    """Best-effort pull of {page_number: markdown} from LlamaParse documents.

    LlamaParse (split_by_page=True) returns one document per page; the page
    number lives in metadata under one of a few keys depending on version. We
    fall back to the document order when no explicit page number is present.
    """
    out: dict[int, str] = {}
    for index, doc in enumerate(documents, start=1):
        text = getattr(doc, "text", None) or getattr(doc, "get_content", lambda: "")()
        text = (text or "").strip()
        if not text:
            continue
        meta = getattr(doc, "metadata", None) or {}
        page_no = (
            meta.get("page_number")
            or meta.get("page")
            or meta.get("page_index")
            or index
        )
        try:
            page_no = int(page_no)
        except (TypeError, ValueError):
            page_no = index
        # If two documents map to the same page, keep the richer one.
        if len(text) > len(out.get(page_no, "")):
            out[page_no] = text
    return out


async def page_markdown_map(file_content: bytes) -> dict[int, str]:
    """Return {page_number: markdown} for the PDF, or {} when unavailable.

    Never raises: any failure (disabled, missing key, package not installed,
    API error, unexpected shape) degrades to an empty map and image-only parsing.
    """
    if not settings.USE_LLAMAPARSE:
        return {}
    if not settings.LLAMA_CLOUD_API_KEY:
        logger.info("USE_LLAMAPARSE is set but LLAMA_CLOUD_API_KEY is missing; skipping.")
        return {}

    try:
        try:
            from llama_cloud_services import LlamaParse  # type: ignore
        except Exception:  # noqa: BLE001
            from llama_parse import LlamaParse  # type: ignore
    except Exception:  # noqa: BLE001
        logger.warning("llama-parse / llama-cloud-services not installed; skipping enrichment.")
        return {}

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            handle.write(file_content)
            tmp_path = handle.name

        parser = LlamaParse(
            api_key=settings.LLAMA_CLOUD_API_KEY,
            result_type="markdown",
            parse_mode=settings.LLAMAPARSE_MODE,
            split_by_page=True,
            language=settings.LLAMAPARSE_LANGUAGE,
            verbose=False,
        )
        documents = await parser.aload_data(tmp_path)
        markdown = _extract_page_markdown(documents)
        if markdown:
            logger.info("LlamaParse enriched %d page(s) with markdown.", len(markdown))
        return markdown
    except Exception:  # noqa: BLE001
        logger.warning("LlamaParse enrichment failed; continuing image-only.", exc_info=True)
        return {}
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
