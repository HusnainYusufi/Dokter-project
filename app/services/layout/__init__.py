"""Document-structure analysis, off unless a provider is configured."""
from __future__ import annotations

import logging
from functools import lru_cache

from app.core.config import settings
from app.services.layout.base import (
    LayoutField,
    LayoutProvider,
    LayoutTable,
    NoLayoutProvider,
    PageLayout,
)
from app.services.layout.render import index_evidence_rows, render_layout_block

logger = logging.getLogger(__name__)

__all__ = [
    "LayoutField",
    "LayoutProvider",
    "LayoutTable",
    "PageLayout",
    "get_layout_provider",
    "index_evidence_rows",
    "layout_enabled",
    "render_layout_block",
]


@lru_cache(maxsize=1)
def get_layout_provider() -> LayoutProvider:
    """The configured provider, or a no-op.

    Misconfiguration degrades to no-op rather than failing startup: layout is an
    enrichment, and a missing endpoint should not stop the service parsing
    documents the way it always has.
    """
    choice = (settings.LAYOUT_PROVIDER or "none").strip().lower()
    if choice in {"", "none", "off", "disabled"}:
        return NoLayoutProvider()

    if choice == "azure":
        endpoint = (settings.AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT or "").strip()
        key = (settings.AZURE_DOCUMENT_INTELLIGENCE_KEY or "").strip()
        if not endpoint or not key:
            logger.error(
                "LAYOUT_PROVIDER=azure but AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT or "
                "AZURE_DOCUMENT_INTELLIGENCE_KEY is unset. Layout analysis is DISABLED."
            )
            return NoLayoutProvider()
        # Checked here rather than on the first page, so turning the provider on
        # without its SDK says so at startup instead of quietly doing nothing
        # for a whole job.
        try:
            import azure.ai.documentintelligence  # noqa: F401
        except ImportError:
            logger.error(
                "LAYOUT_PROVIDER=azure but the SDK is not installed. Layout analysis is "
                "DISABLED. Install it with: pip install azure-ai-documentintelligence"
            )
            return NoLayoutProvider()
        from app.services.layout.providers import AzureDocumentIntelligenceProvider

        logger.info("Layout analysis enabled: Azure Document Intelligence (prebuilt-layout).")
        return AzureDocumentIntelligenceProvider(endpoint, key)

    if choice == "textract":
        try:
            import boto3  # noqa: F401
        except ImportError:
            logger.error(
                "LAYOUT_PROVIDER=textract but boto3 is not installed. Layout analysis is "
                "DISABLED. Install it with: pip install boto3"
            )
            return NoLayoutProvider()
        from app.services.layout.providers import TextractProvider

        logger.info("Layout analysis enabled: AWS Textract (TABLES, FORMS) in %s.", settings.S3_REGION)
        return TextractProvider(settings.S3_REGION)

    logger.warning("Unknown LAYOUT_PROVIDER %r; layout disabled.", choice)
    return NoLayoutProvider()


def layout_enabled() -> bool:
    return not isinstance(get_layout_provider(), NoLayoutProvider)
