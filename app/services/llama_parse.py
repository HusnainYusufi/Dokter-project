from __future__ import annotations

from app.core.config import settings
from app.core.exceptions import LlamaParseError


class LlamaParseService:
    """Service to handle PDF parsing using LlamaCloud Parse (Agentic Plus)."""

    def __init__(self) -> None:
        """Initialize service.

        We create the API client inside `parse_pdf()` so HTTP resources are closed
        cleanly per-request.
        """
        if not settings.LLAMA_CLOUD_API_KEY:
            raise LlamaParseError("Missing LLAMA_CLOUD_API_KEY")

    async def parse_pdf(self, file_content: bytes, filename: str) -> list[str]:
        """Parse a PDF file content to markdown.

        Args:
            file_content: The binary content of the PDF file.
            filename: The name of the file.

        Returns:
            The parsed markdown string.

        Raises:
            LlamaParseError: If parsing fails.
        """
        try:
            import llama_cloud
            from llama_cloud import AsyncLlamaCloud

            async with AsyncLlamaCloud(
                api_key=settings.LLAMA_CLOUD_API_KEY,
                timeout=600.0,
            ) as client:
                # Convenience method: upload + parse + wait + fetch result.
                result = await client.parsing.parse(
                    tier="agentic_plus",
                    version="latest",
                    upload_file=(filename, file_content, "application/pdf"),
                    expand=["markdown"],
                    # Keep these lightweight; tune later if needed.
                    output_options={
                        "markdown": {
                            "tables": {
                                # Often better for downstream summarization than giant MD tables.
                                "output_tables_as_markdown": False,
                            },
                        },
                    },
                )

            if result.markdown is None or not result.markdown.pages:
                raise LlamaParseError("No markdown pages returned from parsing")

            pages: list[str] = []
            for page in result.markdown.pages:
                # Discriminated union: failed pages have `success=False` and `error`.
                if getattr(page, "success", True) is False:
                    page_num = getattr(page, "page_number", "?")
                    err = getattr(page, "error", "Unknown error")
                    pages.append(f"[Failed to parse page {page_num}] {err}")
                else:
                    pages.append(getattr(page, "markdown", "") or "")

            return pages

        except Exception as e:
            if isinstance(e, LlamaParseError):
                raise
            # Wrap SDK/API errors into your app's error type.
            if e.__class__.__module__.startswith("llama_cloud"):
                raise LlamaParseError(str(e)) from e
            raise LlamaParseError(str(e)) from e
