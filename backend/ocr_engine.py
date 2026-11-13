"""PaddleOCR adapter.

PaddleOCR is an optional dependency: it is imported lazily, the first scan pays
for model loading, and every failure is reported as a warning instead of an
exception. Nothing in this module decides anything — it only turns pixels into
positioned words, which ``layout_parser`` then structures.

Set ``VENDORFLOW_OCR=off`` to skip OCR entirely, or ``VENDORFLOW_OCR_LANG`` to
use another recognition model.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterable

from backend.layout_parser import TextWord

_DISABLED_VALUES = {"off", "0", "false", "no"}


class OcrUnavailable(RuntimeError):
    """Raised when OCR is disabled or its dependencies are missing."""


_engine = None
_engine_error: str | None = None
_lock = threading.Lock()


def enabled() -> bool:
    return os.getenv("VENDORFLOW_OCR", "on").strip().lower() not in _DISABLED_VALUES


def available() -> tuple[bool, str]:
    """Cheap readiness probe. Does not load a model."""
    if not enabled():
        return False, "OCR is switched off with VENDORFLOW_OCR"
    try:
        import paddleocr  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on the environment
        return False, f"paddleocr is not installed ({exc.__class__.__name__})"
    try:
        import paddle  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on the environment
        return False, f"paddlepaddle is not installed ({exc.__class__.__name__})"
    return True, "PaddleOCR ready"


def _get_engine():
    global _engine, _engine_error
    with _lock:
        if _engine is not None:
            return _engine
        if _engine_error:
            raise OcrUnavailable(_engine_error)
        ready, message = available()
        if not ready:
            _engine_error = message
            raise OcrUnavailable(message)
        try:
            from paddleocr import PaddleOCR

            lang = os.getenv("VENDORFLOW_OCR_LANG", "en")
            # PaddleOCR 3.x pipeline flags. oneDNN is off by default: paddlepaddle
            # 3.3 on Windows CPU fails its PIR lowering in the detection model.
            # Set VENDORFLOW_OCR_MKLDNN=1 to use oneDNN anyway.
            try:
                _engine = PaddleOCR(
                    lang=lang,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=_flag("VENDORFLOW_OCR_ORIENTATION", False),
                    enable_mkldnn=_flag("VENDORFLOW_OCR_MKLDNN", False),
                )
            except TypeError:
                # PaddleOCR 2.x signature.
                _engine = PaddleOCR(lang=lang, use_angle_cls=True, show_log=False)
        except Exception as exc:  # pragma: no cover - depends on the environment
            _engine_error = f"PaddleOCR could not start ({exc.__class__.__name__}: {exc})"
            raise OcrUnavailable(_engine_error) from exc
        return _engine


def recognize(image: bytes, page: int = 0, engine=None) -> list[TextWord]:
    """OCR one image (PNG/JPEG bytes) into positioned words.

    Coordinates are returned in image pixels; the caller rescales them to PDF
    points when the source was rendered from a page.
    """
    array = _to_array(image)
    active = engine if engine is not None else _get_engine()
    raw = _run(active, array)
    return _normalize(raw, page)


# ------------------------------------------------------------------ internals


def _to_array(image: bytes):
    import numpy as np
    from PIL import Image

    with Image.open(_bytes_io(image)) as handle:
        frame = handle.convert("RGB")
        pixels = np.asarray(frame)
    # PaddleOCR follows OpenCV's BGR convention for array input.
    return np.ascontiguousarray(pixels[:, :, ::-1])


def _bytes_io(image: bytes):
    from io import BytesIO

    return BytesIO(image)


def _run(engine, array):
    predict = getattr(engine, "predict", None)
    if callable(predict):
        try:
            return predict(array)
        except (TypeError, ValueError):
            pass
    return engine.ocr(array, cls=True)


def _normalize(raw, page: int) -> list[TextWord]:
    words: list[TextWord] = []
    # PaddleOCR 3.x: one result object per page, carrying arrays of texts and boxes.
    for item in _pages(raw):
        payload = _as_mapping(item)
        if payload is None or "rec_texts" not in payload:
            continue
        texts = payload.get("rec_texts") or []
        scores = payload.get("rec_scores") or []
        boxes = payload.get("rec_polys") or payload.get("rec_boxes") or payload.get("dt_polys") or []
        for text, score, box in zip(texts, scores or [0.0] * len(texts), boxes):
            word = _word(text, score, box, page)
            if word:
                words.append(word)
    if words:
        return words
    # PaddleOCR 2.x: [[box, (text, score)], ...] per page.
    for box, body in _lines(raw):
        if _is_sequence(body) and body:
            text, score = body[0], (body[1] if len(body) > 1 else 0.0)
        else:
            text, score = body, 1.0
        word = _word(text, score, box, page)
        if word:
            words.append(word)
    return words


def _pages(raw) -> Iterable:
    if raw is None:
        return []
    return raw if _is_sequence(raw) else [raw]


def _lines(raw) -> Iterable[tuple]:
    """Walk the 2.x nesting: pages -> [box, (text, score)] -> ..."""
    if not _is_sequence(raw):
        return
    if len(raw) == 2 and _is_box(raw[0]) and not _is_box(raw[1]):
        yield raw[0], raw[1]
        return
    for item in raw:
        yield from _lines(item)


def _is_box(value) -> bool:
    return (
        _is_sequence(value)
        and len(value) == 4
        and _is_sequence(value[0])
        and all(isinstance(number, (int, float)) for number in value[0][:2])
    )


def _as_mapping(item):
    payload = None
    if isinstance(item, dict):
        payload = dict(item)
    else:
        result = getattr(item, "json", None)
        if callable(result):
            result = result()
        if isinstance(result, dict):
            payload = result
        else:
            as_dict = getattr(item, "to_dict", None)
            if callable(as_dict):
                try:
                    value = as_dict()
                except Exception:
                    value = None
                payload = value if isinstance(value, dict) else None
    if payload is None:
        return None
    # PaddleOCR 3.x wraps the page arrays in {"res": {...}}.
    inner = payload.get("res")
    if isinstance(inner, dict):
        return inner
    return payload


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "on", "true", "yes"}


def _is_sequence(value) -> bool:
    return isinstance(value, (list, tuple))


def _word(text, score, box, page: int) -> TextWord | None:
    label = " ".join(str(text or "").split())
    if not label:
        return None
    bounds = _bounds(box)
    if bounds is None:
        return None
    x0, y0, x1, y1 = bounds
    try:
        confidence = float(score)
    except (TypeError, ValueError):
        confidence = 0.0
    return TextWord(
        text=label,
        x0=x0,
        y0=y0,
        x1=x1,
        y1=y1,
        confidence=max(0.0, min(1.0, confidence)),
        page=page,
    )


def _bounds(box) -> tuple[float, float, float, float] | None:
    if box is None:
        return None
    if hasattr(box, "tolist"):
        box = box.tolist()
    if not _is_sequence(box) or not box:
        return None
    points = box
    if len(box) == 4 and all(isinstance(value, (int, float)) for value in box):
        # [x0, y0, x1, y1]
        return float(box[0]), float(box[1]), float(box[2]), float(box[3])
    xs, ys = [], []
    for point in points:
        if _is_sequence(point) and len(point) >= 2:
            xs.append(float(point[0]))
            ys.append(float(point[1]))
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)
