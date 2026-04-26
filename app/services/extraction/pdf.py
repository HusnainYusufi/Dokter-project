"""PDF rendering helpers backed by pypdfium2."""
from __future__ import annotations

import io
from typing import Iterator

import pypdfium2 as pdfium

from app.core.config import settings
from app.core.exceptions import ProcessingError


def open_pdf(file_content: bytes):  # noqa: ANN201
    try:
        return pdfium.PdfDocument(file_content)
    except Exception as exc:  # noqa: BLE001
        raise ProcessingError(f"Unable to open PDF: {exc}") from exc


def count_pages(file_content: bytes) -> int:
    pdf = open_pdf(file_content)
    try:
        return len(pdf)
    except Exception as exc:  # noqa: BLE001
        raise ProcessingError(f"Unable to count PDF pages: {exc}") from exc
    finally:
        try:
            pdf.close()
        except Exception:
            pass


def _render_scale() -> float:
    dpi = max(72, int(settings.OPENAI_PAGE_RENDER_DPI))
    return dpi / 72.0


def render_page_image(pdf, page_number: int) -> bytes:  # noqa: ANN001
    page = None
    bitmap = None
    pil_image = None
    buf = io.BytesIO()
    try:
        page = pdf.get_page(page_number - 1)
        bitmap = page.render(scale=_render_scale())
        pil_image = bitmap.to_pil()
        pil_image.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        raise ProcessingError(f"Unable to render PDF page {page_number}: {exc}") from exc
    finally:
        buf.close()
        if pil_image is not None:
            try:
                pil_image.close()
            except Exception:
                pass
        if bitmap is not None and hasattr(bitmap, "close"):
            try:
                bitmap.close()
            except Exception:
                pass
        if page is not None and hasattr(page, "close"):
            try:
                page.close()
            except Exception:
                pass


def render_page_batches(
    file_content: bytes,
    *,
    batch_size: int | None = None,
) -> Iterator[list[tuple[int, bytes]]]:
    """Yield batches of (page_number, image_bytes) tuples."""
    size = max(1, int(batch_size or settings.OPENAI_PAGE_BATCH_SIZE))
    pdf = open_pdf(file_content)
    try:
        page_count = len(pdf)
        for start in range(1, page_count + 1, size):
            end = min(page_count, start + size - 1)
            batch: list[tuple[int, bytes]] = []
            for page_no in range(start, end + 1):
                batch.append((page_no, render_page_image(pdf, page_no)))
            yield batch
    finally:
        try:
            pdf.close()
        except Exception:
            pass
