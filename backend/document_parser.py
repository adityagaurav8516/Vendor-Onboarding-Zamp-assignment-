"""Document intake: text layer first, OCR only where it is needed.

PyMuPDF opens the PDF, returns the embedded text layer and its word geometry, and
rasterises the pages that have no usable text layer. Gemini Flash reads those
pixels, ``layout_parser`` rebuilds labels and values from the boxes, and the
pipeline still receives plain text plus a few candidate fields. It never
receives a verdict.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from io import BytesIO

from backend import layout_parser, ocr_engine

MIN_TEXT_CHARS = 20
SCANNED_PAGE_CHARS = int(os.getenv("VENDORFLOW_OCR_PAGE_CHARS", "40"))
# 200 DPI preserves legible business-document text while rendering less than half
# as many pixels as 300 DPI. Override for unusually small or degraded scans.
OCR_DPI = int(os.getenv("VENDORFLOW_OCR_DPI", "200"))
MIN_KV_CONFIDENCE = float(os.getenv("VENDORFLOW_OCR_MIN_KV_CONFIDENCE", "0.6"))

PLAIN_SUFFIXES = (".txt", ".text", ".md", ".csv")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif")
_IMAGE_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*", b"GIF8", b"BM")


@dataclass
class ParsedContent:
    """Everything the pipeline needs about one uploaded file."""

    status: str
    text: str
    source: str = "none"
    pages: int = 0
    key_values: list[layout_parser.KeyValue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ocr_confidence: float = 0.0
    ocr_pages: int = 0

    def fields(self) -> dict[str, str]:
        return {kv.field: kv.value for kv in self.key_values if kv.value}


def parse_document(filename: str, content: bytes) -> ParsedContent:
    name = (filename or "").lower()
    if name.endswith(PLAIN_SUFFIXES):
        return _parse_plain(content)
    if _is_pdf(name, content):
        return _parse_pdf(content)
    if name.endswith(IMAGE_SUFFIXES) or content.startswith(_IMAGE_MAGIC):
        return _parse_image(content)
    return _parse_plain(content, minimum=MIN_TEXT_CHARS)


def parse_bytes(filename: str, content: bytes) -> tuple[str, str]:
    """Backwards-compatible view: (status, text)."""
    parsed = parse_document(filename, content)
    return parsed.status, parsed.text


# --------------------------------------------------------------- entry points


def _parse_plain(content: bytes, minimum: int = 1) -> ParsedContent:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return ParsedContent("unreadable", "", "none")
    if len(text.strip()) < minimum:
        return ParsedContent("unreadable", "", "none")
    return _finish(text, [], source="plain_text", pages=1)


def _parse_image(content: bytes) -> ParsedContent:
    ready, message = ocr_engine.available()
    if not ready:
        return ParsedContent(
            "unreadable",
            "",
            "none",
            pages=1,
            warnings=[f"The image needs OCR, but it is not available: {message}."],
        )
    try:
        words = ocr_engine.recognize(content, page=0)
    except ocr_engine.OcrUnavailable as exc:
        return ParsedContent(
            "unreadable", "", "none", pages=1, warnings=[f"OCR could not run: {exc}."]
        )
    except Exception as exc:
        return ParsedContent(
            "unreadable",
            "",
            "none",
            pages=1,
            warnings=[f"OCR failed on the image ({exc.__class__.__name__})."],
        )
    if not words:
        return ParsedContent(
            "unreadable",
            "",
            "ocr",
            pages=1,
            warnings=["OCR found no readable text in the image."],
        )
    return _finish(
        "",
        words,
        source="ocr",
        pages=1,
        ocr_pages=1,
        ocr_confidence=_mean([word.confidence for word in words]),
    )


def _parse_pdf(content: bytes) -> ParsedContent:
    fitz = _load_pymupdf()
    if fitz is None:
        return _parse_pdf_pypdf(content)
    try:
        document = fitz.open(stream=content, filetype="pdf")
    except Exception:
        return _parse_pdf_pypdf(content)
    with document:
        if getattr(document, "needs_pass", False) or getattr(document, "is_encrypted", False):
            return ParsedContent("unreadable", "", "none")
        page_count = document.page_count
        if not page_count:
            return ParsedContent("unreadable", "", "none")

        page_texts: list[str] = []
        page_ocr_text: dict[int, str] = {}
        words: list[layout_parser.TextWord] = []
        warnings: list[str] = []
        ocr_confidences: list[float] = []
        ocr_pages = 0

        for index, page in enumerate(document):
            page_text = page.get_text("text") or ""
            page_words = [_text_word(item, index) for item in page.get_text("words") or []]
            page_words = [word for word in page_words if word is not None]
            words.extend(page_words)
            page_texts.append(page_text.strip())

            if not _needs_ocr(page_text, page_words, page):
                continue
            ocr_words, confidence, error = _ocr_page(page, index)
            if error:
                warnings.append(error)
            if not ocr_words:
                continue
            ocr_pages += 1
            ocr_confidences.append(confidence)
            words.extend(ocr_words)
            page_ocr_text[index] = layout_parser.parse(ocr_words).text

        if not any(page_texts) and not ocr_pages:
            return ParsedContent(
                "unreadable", "", "none", pages=page_count, warnings=warnings
            )

        text = "\n".join(
            page_ocr_text.get(index, page_texts[index]) for index in range(page_count)
        )
        result = _finish(
            text,
            words,
            source="text_layer+ocr" if ocr_pages and any(page_texts) else ("ocr" if ocr_pages else "text_layer"),
            pages=page_count,
            ocr_pages=ocr_pages,
            ocr_confidence=_mean(ocr_confidences),
        )
        result.warnings = warnings + result.warnings
        return result


def _parse_pdf_pypdf(content: bytes) -> ParsedContent:
    """Fallback when PyMuPDF is missing: text only, no geometry, no OCR."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted:
            return ParsedContent("unreadable", "", "none")
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception:
        return ParsedContent("unreadable", "", "none")
    text = "\n".join(pages).strip()
    if len(text) < MIN_TEXT_CHARS:
        return ParsedContent(
            "unreadable",
            "",
            "none",
            pages=len(pages),
            warnings=["This PDF has no text layer and PyMuPDF is not installed, so it could not be scanned."],
        )
    return _finish(text, [], source="text_layer", pages=len(pages))


# ------------------------------------------------------------------ internals


def _finish(
    text: str,
    words,
    source: str,
    pages: int = 0,
    ocr_pages: int = 0,
    ocr_confidence: float = 0.0,
) -> ParsedContent:
    layout = layout_parser.parse(words) if words else layout_parser.parse_text(text)
    kept = [kv for kv in layout.key_values if kv.confidence >= MIN_KV_CONFIDENCE]
    dropped = len(layout.key_values) - len(kept)
    body = (text or layout.text).strip()
    warnings: list[str] = []
    if ocr_pages:
        warnings.append(
            f"Read {ocr_pages} page(s) with Gemini Flash (mean confidence {ocr_confidence:.2f}); "
            f"the layout parser recovered {len(kept)} key/value pair(s)."
        )
    if dropped:
        warnings.append(
            f"{dropped} low-confidence key/value pair(s) were ignored so they cannot affect a decision."
        )
    if len(body) < MIN_TEXT_CHARS:
        return ParsedContent(
            "unreadable",
            body,
            source,
            pages=pages,
            key_values=[],
            warnings=warnings,
            ocr_confidence=ocr_confidence,
            ocr_pages=ocr_pages,
        )
    return ParsedContent(
        "parsed",
        body,
        source,
        pages=pages,
        key_values=kept,
        warnings=warnings,
        ocr_confidence=ocr_confidence,
        ocr_pages=ocr_pages,
    )


def _load_pymupdf():
    try:
        import pymupdf

        return pymupdf
    except ImportError:
        try:
            import fitz

            return fitz
        except ImportError:
            return None


def _is_pdf(name: str, content: bytes) -> bool:
    return content.startswith(b"%PDF") or name.endswith(".pdf")


def _text_word(item, page: int) -> layout_parser.TextWord | None:
    # PyMuPDF "words": (x0, y0, x1, y1, text, block_no, line_no, word_no)
    try:
        x0, y0, x1, y1, text = item[0], item[1], item[2], item[3], item[4]
    except (IndexError, TypeError):
        return None
    label = " ".join(str(text or "").split())
    if not label:
        return None
    return layout_parser.TextWord(
        text=label,
        x0=float(x0),
        y0=float(y0),
        x1=float(x1),
        y1=float(y1),
        confidence=1.0,
        page=page,
    )


def _needs_ocr(page_text: str, page_words, page) -> bool:
    if len(page_text.strip()) >= SCANNED_PAGE_CHARS and len(page_words) >= 4:
        return False
    if len(page_text.strip()) >= SCANNED_PAGE_CHARS:
        return False
    try:
        has_images = bool(page.get_images(full=False))
    except Exception:
        has_images = False
    return has_images


def _ocr_page(page, index: int) -> tuple[list[layout_parser.TextWord], float, str | None]:
    ready, message = ocr_engine.available()
    if not ready:
        return [], 0.0, f"Page {index + 1} has no text layer and OCR is unavailable: {message}."
    try:
        pixmap = page.get_pixmap(dpi=OCR_DPI)
        raster = pixmap.tobytes("png")
    except Exception as exc:
        return [], 0.0, f"Page {index + 1} could not be rendered at {OCR_DPI} dpi ({exc.__class__.__name__})."
    try:
        words = ocr_engine.recognize(raster, page=index)
    except ocr_engine.OcrUnavailable as exc:
        return [], 0.0, f"Page {index + 1} could not be scanned: {exc}."
    except Exception as exc:
        return [], 0.0, f"OCR failed on page {index + 1} ({exc.__class__.__name__})."
    if not words:
        return [], 0.0, f"Page {index + 1} has no text layer and OCR found nothing readable."
    # Raster coordinates are pixels; the text layer is in PDF points.
    scale = 72.0 / OCR_DPI
    scaled = [
        layout_parser.TextWord(
            text=word.text,
            x0=word.x0 * scale,
            y0=word.y0 * scale,
            x1=word.x1 * scale,
            y1=word.y1 * scale,
            confidence=word.confidence,
            page=word.page,
        )
        for word in words
    ]
    return scaled, _mean([word.confidence for word in words]), None


def _mean(values) -> float:
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else 0.0
