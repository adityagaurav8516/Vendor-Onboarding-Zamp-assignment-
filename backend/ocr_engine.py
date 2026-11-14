"""Gemini Flash extraction adapter for scanned documents and images."""

from __future__ import annotations

import base64
import json
import os
import threading
from collections import OrderedDict
from dataclasses import replace
from hashlib import sha256
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.layout_parser import TextWord

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is a declared dependency
    load_dotenv = None
else:
    load_dotenv()

_DISABLED_VALUES = {"off", "0", "false", "no"}
_cache_lock = threading.Lock()
_ocr_cache: OrderedDict[str, tuple[TextWord, ...]] = OrderedDict()
OCR_CACHE_SIZE = 64
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
FIELDS = (
    "legal_name", "tax_id", "registration_number", "country", "address",
    "account_holder", "bank_name", "account_number", "bank_code",
)
FIELD_LABELS = {
    "legal_name": "Legal name", "tax_id": "GSTIN", "registration_number": "Registration number",
    "country": "Country", "address": "Address", "account_holder": "Account holder",
    "bank_name": "Bank name", "account_number": "Account number", "bank_code": "IFSC",
}


class OcrUnavailable(RuntimeError):
    """Raised when Gemini extraction is disabled, unconfigured, or unavailable."""


def enabled() -> bool:
    return os.getenv("VENDORFLOW_OCR", "on").strip().lower() not in _DISABLED_VALUES


def provider() -> str:
    return "gemini_flash"


def available() -> tuple[bool, str]:
    """Cheap readiness probe. Does not make a network request."""
    if not enabled():
        return False, "OCR is switched off with VENDORFLOW_OCR"
    if not os.getenv("GEMINI_API_KEY", "").strip():
        return False, "GEMINI_API_KEY is not set"
    return True, "Gemini Flash ready"


def recognize(image: bytes, page: int = 0, engine=None) -> list[TextWord]:
    """Send one rendered page to Gemini Flash and return parser-compatible lines."""
    del engine
    cache_key = sha256(image).hexdigest()
    with _cache_lock:
        cached = _ocr_cache.get(cache_key)
        if cached is not None:
            _ocr_cache.move_to_end(cache_key)
            return [replace(word, page=page) for word in cached]

    words = _recognize_gemini(image, page)
    template = tuple(replace(word, page=0) for word in words)
    with _cache_lock:
        _ocr_cache[cache_key] = template
        _ocr_cache.move_to_end(cache_key)
        while len(_ocr_cache) > OCR_CACHE_SIZE:
            _ocr_cache.popitem(last=False)
    return words


def _recognize_gemini(image: bytes, page: int) -> list[TextWord]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise OcrUnavailable("GEMINI_API_KEY is not set")
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip() or "gemini-3.5-flash"
    endpoint = (os.getenv("GEMINI_ENDPOINT", GEMINI_ENDPOINT).strip() or GEMINI_ENDPOINT).format(model=model)
    payload = {
        "contents": [{"parts": [
            {"text": _prompt()},
            {"inline_data": {"mime_type": _image_mime(image), "data": base64.b64encode(image).decode("ascii")}},
        ]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=float(os.getenv("GEMINI_TIMEOUT", "60"))) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = _http_error_message(exc)
        suffix = f": {detail}" if detail else ""
        raise OcrUnavailable(f"Gemini request failed (HTTP {exc.code}{suffix})") from exc
    except (OSError, URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OcrUnavailable(f"Gemini request failed ({exc.__class__.__name__})") from exc
    words = _normalize_gemini(result, page)
    if words:
        return words
    raise OcrUnavailable(_gemini_error(result) or "Gemini returned no readable text")


def _prompt() -> str:
    return (
        "Read this vendor document. Transcribe all visible text accurately, preserving lines. "
        "Extract only values visibly supported by the image. Return JSON only with this schema: "
        '{"text":"full transcription","fields":{"legal_name":"","tax_id":"",'
        '"registration_number":"","country":"","address":"","account_holder":"",'
        '"bank_name":"","account_number":"","bank_code":""}}. '
        "Extract only the specific value for each field without including adjacent labels or other fields on the same line "
        "(for example, account_number must be digits only, never including trailing labels like 'bank name' or bank names). "
        "Use empty strings for fields not present. Do not infer or invent values."
    )


def _image_mime(image: bytes) -> str:
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        return "image/webp"
    if image.startswith(b"GIF8"):
        return "image/gif"
    return "image/png"


def _clean_ocr_field(field: str, val) -> str:
    import re
    cleaned = str(val or "").strip()
    if not cleaned:
        return ""
    if field == "account_number":
        split_m = re.search(r"\b(?:bank\s+name|bank|ifsc|branch|account\s+holder|a/c)\b", cleaned, re.IGNORECASE)
        if split_m and split_m.start() > 0:
            cleaned = cleaned[: split_m.start()].strip(" .,;:-—=·/|")
        num_m = re.match(r"^([A-Z]{2}\d{2}[A-Za-z0-9]{10,30}|\d[\d\s\-]{4,24}\d|\d{5,24})\b", cleaned)
        if num_m:
            cand = num_m.group(1).strip()
            if len(cand) < len(cleaned):
                cleaned = cand
    elif field == "tax_id":
        gstin_m = re.search(r"(\d{2}[A-Za-z]{5}\d{4}[A-Za-z][A-Za-z0-9]Z[A-Za-z0-9])", cleaned)
        if gstin_m:
            return gstin_m.group(1).upper()
        ein_m = re.search(r"(\d{2}-\d{7})", cleaned)
        if ein_m:
            return ein_m.group(1)
        pan_m = re.search(r"([A-Za-z]{5}\d{4}[A-Za-z])", cleaned)
        if pan_m:
            return pan_m.group(1).upper()
    elif field in ("legal_name", "account_holder"):
        norm = cleaned.replace("\\n", "\n")
        first_line = [l.strip() for l in norm.splitlines() if l.strip()]
        if first_line:
            cleaned = first_line[0]
        split_m = re.search(
            r"\b(?:\d+[\.\)]\s*)?(?:trade\s+name|constitution|address|date\s+of\s+issue|jurisdiction)\b",
            cleaned,
            re.IGNORECASE,
        )
        if split_m and split_m.start() > 0:
            cleaned = cleaned[: split_m.start()].strip(" .,;:-—=·/|")
    elif field == "registration_number":
        cin_m = re.search(r"\b([A-Za-z]\d{5}[A-Za-z]{2}\d{4}[A-Za-z]{3}\d{6})\b", cleaned)
        if cin_m:
            return cin_m.group(1).upper()
    return cleaned


def _parse_gemini_json(text: str) -> dict | None:
    if not text:
        return None
    raw = text.strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass

    import re
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            pass

    return None


def _normalize_gemini(payload, page: int) -> list[TextWord]:
    response_text = _gemini_text(payload)
    if not response_text:
        return []
    extracted = _parse_gemini_json(response_text)
    if extracted is None:
        import re
        plain = re.sub(r"^```(?:json)?\s*", "", response_text.strip(), flags=re.IGNORECASE)
        plain = re.sub(r"\s*```$", "", plain).strip()
        return _lines(plain, page)
    lines = _lines(str(extracted.get("text") or ""), page)
    fields = extracted.get("fields") or {}
    if isinstance(fields, dict):
        semantic_lines = [
            f"{FIELD_LABELS[field]}: {_clean_ocr_field(field, fields.get(field))}"
            for field in FIELDS if _clean_ocr_field(field, fields.get(field))
        ]
        # Start below the last transcript row, not after the *count* of rows: blank
        # transcript lines use up row numbers and would otherwise collide with these.
        next_row = int(max(word.y0 for word in lines) // 20) + 1 if lines else 0
        lines.extend(_lines("\n".join(semantic_lines), page, start=next_row))
    return lines


def _lines(text: str, page: int, start: int = 0) -> list[TextWord]:
    words = []
    for row, line in enumerate(text.splitlines(), start=start):
        cleaned = " ".join(line.split())
        if cleaned:
            words.append(TextWord(cleaned, 0.0, float(row * 20), float(max(len(cleaned), 1) * 8), float(row * 20 + 14), 0.8, page))
    return words


def _gemini_text(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""
    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    return "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()


def _gemini_error(payload) -> str:
    if not isinstance(payload, dict):
        return "Gemini returned an invalid response"
    error = payload.get("error") or {}
    return str(error.get("message") or "").strip() if isinstance(error, dict) else ""


def _http_error_message(error: HTTPError) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    return _gemini_error(payload)
