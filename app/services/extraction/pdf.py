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


def ink_ratio(png_bytes: bytes) -> float:
    """Fraction of dark (non-white) pixels in a rendered page PNG.

    Near zero for a blank or near-blank page; high for a photograph, X-ray, or
    other image-heavy scan. Used to rescue image pages that the vision model
    mislabels as empty. Returns 0.0 on any decode error (safe no-op).
    """
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(png_bytes)) as image:
            histogram = image.convert("L").histogram()
        total = sum(histogram)
        if not total:
            return 0.0
        dark = sum(histogram[:200])  # pixels darker than ~200/255
        return dark / total
    except Exception:  # noqa: BLE001
        return 0.0


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


def render_page_batches(file_content: bytes) -> Iterator[list[tuple[int, bytes]]]:
    """Yield exactly ONE (page_number, image_bytes) tuple per batch - one page
    per AI call, always.

    This is deliberately NOT configurable. When several page images share one
    vision call, the model conflates content BETWEEN the images - a dated entry
    from one page gets written into another page's response entry - and no
    amount of response-side mapping can undo it, because the misattribution
    happens inside the model. This exact failure shipped twice via an
    environment override (OPENAI_PAGE_BATCH_SIZE) that re-enabled batching in
    production while the code default said 1, showing up as summaries citing
    the wrong PDF page (e.g. a June 22 visit on page 568 indexed as page 570).
    With one page per call, content for page N can only ever come from page
    N's own image, making page attribution structurally exact."""
    pdf = open_pdf(file_content)
    try:
        page_count = len(pdf)
        for page_no in range(1, page_count + 1):
            yield [(page_no, render_page_image(pdf, page_no))]
    finally:
        try:
            pdf.close()
        except Exception:
            pass
