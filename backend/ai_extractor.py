from __future__ import annotations

import json
import os
import re
import urllib.request

from backend.field_validator import compact_tax

_KEY_MAP = {
    "legal name": "legal_name",
    "tax id": "tax_id",
    "gstin": "tax_id",
    "ein": "tax_id",
    "registration number": "registration_number",
    "cin": "registration_number",
    "country": "country",
    "registered address": "address",
    "address": "address",
    "account holder": "account_holder",
    "bank name": "bank_name",
    "account number": "account_number",
    "account no": "account_number",
    "a/c number": "account_number",
    "a/c no": "account_number",
    "account": "account_number",
    "bank code": "bank_code",
    "ifsc": "bank_code",
    "ifsc code": "bank_code",
    "routing number": "bank_code",
    "document type": "document_type",
}

_FIELD_NAMES = (
    "legal_name",
    "tax_id",
    "registration_number",
    "country",
    "address",
    "account_holder",
    "bank_name",
    "account_number",
    "bank_code",
    "document_type",
)


def classify_document(document_type: str, text: str = "") -> str:
    blob = f"{document_type} {text[:400]}".upper()
    if "BANK ACCOUNT" in blob or "ACCOUNT HOLDER" in blob:
        return "bank"
    if "TAX REGISTRATION" in blob or "GSTIN" in blob:
        return "tax"
    if "INCORPOR" in blob:
        return "incorporation"
    return "unknown"


def extract_fields(
    text: str,
    layout_fields: dict[str, str] | None = None,
) -> tuple[dict[str, str], str, list[str], float]:
    """Read document fields. ``layout_fields`` are candidates from OCR geometry.

    Precedence is labelled text, then the layout parser, then the model. Every
    source may only fill a blank, so an OCR misread can never overwrite a value
    the document actually printed as a label.
    """
    rules = _rules_extract(text)
    layout = {key: (layout_fields or {}).get(key, "") for key in _FIELD_NAMES}
    warnings: list[str] = []
    llm, llm_error = _maybe_llm(text)
    if llm_error:
        warnings.append(llm_error)
    merged, method, merge_warnings = merge_sources(rules, layout, llm)
    warnings.extend(merge_warnings)
    filled = sum(1 for value in merged.values() if value)
    confidence = 0.0
    if filled:
        if method == "rules":
            confidence = 0.95 if filled >= 3 else 0.7
        elif "layout" in method:
            confidence = 0.8
        else:
            confidence = 0.6
    return merged, method, warnings, confidence


def merge_extractions(rules: dict[str, str], llm: dict[str, str] | None) -> tuple[dict[str, str], str, list[str]]:
    """Labeled fields win. A model may only fill blanks, never overturn a label."""
    return merge_sources(rules, None, llm)


def merge_sources(
    rules: dict[str, str],
    layout: dict[str, str] | None = None,
    llm: dict[str, str] | None = None,
) -> tuple[dict[str, str], str, list[str]]:
    """Merge readers by trust: labels, then layout geometry, then the model."""
    merged = {key: "" for key in _FIELD_NAMES}
    contributors: set[str] = set()
    warnings: list[str] = []
    for key in _FIELD_NAMES:
        value = (rules.get(key) or "").strip()
        if value:
            merged[key] = value
            contributors.add("rules")
    for name, source, label in (
        ("layout", layout, "Layout parser"),
        ("model", llm, "Model"),
    ):
        if not source:
            continue
        for key in _FIELD_NAMES:
            value = (source.get(key) or "").strip()
            if not value:
                continue
            current = (merged.get(key) or "").strip()
            if not current:
                merged[key] = value
                contributors.add(name)
            elif _same(key, current, value):
                continue
            else:
                warnings.append(
                    f"{label} read {key} as '{value}'; kept the labeled value '{current}'."
                )
    if not contributors:
        return merged, "rules", warnings
    method = "+".join(sorted(contributors))
    return merged, method, warnings


def _same(key: str, left: str, right: str) -> bool:
    if key in {"tax_id", "bank_code", "account_number", "registration_number"}:
        return re.sub(r"[\s-]", "", left).upper() == re.sub(r"[\s-]", "", right).upper()
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


_INLINE_PATTERN = re.compile(r"(?:^|[\s,;|])([A-Za-z0-9/&. ]{2,30}?)\s*[:：=]\s*")


def _clean_rule_value(value: str, key: str) -> str:
    cleaned = " ".join((value or "").split()).strip(" .,;:-—=·/|")
    if not cleaned:
        return ""
    if key == "account_number":
        split_match = re.search(
            r"\b(?:bank\s+name|bank|ifsc|branch|account\s+holder|a/c)\b",
            cleaned,
            re.IGNORECASE,
        )
        if split_match and split_match.start() > 0:
            cleaned = cleaned[: split_match.start()].strip(" .,;:-—=·/|")
        num_match = re.match(
            r"^([A-Z]{2}\d{2}[A-Za-z0-9]{10,30}|\d[\d\s\-]{4,24}\d|\d{5,24})\b",
            cleaned,
        )
        if num_match:
            candidate = num_match.group(1).strip()
            if len(candidate) < len(cleaned):
                cleaned = candidate
        compact = re.sub(r"\s+", "", cleaned)
        if re.fullmatch(r"[A-Za-z0-9/\-]{5,}", compact):
            cleaned = compact
    if key == "tax_id":
        gstin_m = re.search(r"(\d{2}[A-Za-z]{5}\d{4}[A-Za-z][A-Za-z0-9]Z[A-Za-z0-9])", cleaned)
        if gstin_m:
            return gstin_m.group(1).upper()
        ein_m = re.search(r"(\d{2}-\d{7})", cleaned)
        if ein_m:
            return ein_m.group(1)
        pan_m = re.search(r"([A-Za-z]{5}\d{4}[A-Za-z])", cleaned)
        if pan_m:
            return pan_m.group(1).upper()
    if key in ("legal_name", "account_holder"):
        norm = cleaned.replace("\\n", "\n")
        first_line = [l.strip() for l in norm.splitlines() if l.strip()]
        if first_line:
            cleaned = first_line[0]
        split_match = re.search(
            r"\b(?:\d+[\.\)]\s*)?(?:trade\s+name|constitution|address|date\s+of\s+issue|jurisdiction)\b",
            cleaned,
            re.IGNORECASE,
        )
        if split_match and split_match.start() > 0:
            cleaned = cleaned[: split_match.start()].strip(" .,;:-—=·/|")
    return cleaned


def _rules_extract(text: str) -> dict[str, str]:
    fields = {key: "" for key in _FIELD_NAMES}
    normalized_text = (text or "").replace("\\n", "\n")
    for raw_line in normalized_text.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            continue
        matches = []
        for m in _INLINE_PATTERN.finditer(line):
            lbl = m.group(1).strip().lower()
            lbl_clean = re.sub(r"^\d+[\.\)]\s*", "", lbl)
            key = _KEY_MAP.get(lbl_clean) or _KEY_MAP.get(lbl)
            if key:
                matches.append((m.start(1), m.end(), key))
        if matches:
            for i, (l_start, v_start, key) in enumerate(matches):
                if i + 1 < len(matches):
                    val = line[v_start : matches[i + 1][0]]
                else:
                    val = line[v_start:]
                val = _clean_rule_value(val, key)
                if val and not fields[key]:
                    fields[key] = val
        elif ":" in line:
            label, value = line.split(":", 1)
            lbl = label.strip().lower()
            lbl_clean = re.sub(r"^\d+[\.\)]\s*", "", lbl)
            key = _KEY_MAP.get(lbl_clean) or _KEY_MAP.get(lbl)
            if key and value.strip() and not fields[key]:
                fields[key] = _clean_rule_value(value.strip(), key)
    upper = normalized_text.upper()
    if not fields["tax_id"]:
        match = re.search(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b", upper)
        if match:
            fields["tax_id"] = match.group(0)
        else:
            match = re.search(r"\b\d{2}-\d{7}\b", normalized_text)
            if match:
                fields["tax_id"] = match.group(0)
    if fields["tax_id"]:
        fields["tax_id"] = fields["tax_id"].upper()
        if tax_is_compact_gstin(fields["tax_id"]):
            fields["tax_id"] = compact_tax(fields["tax_id"])
    return fields


def tax_is_compact_gstin(value: str) -> bool:
    return bool(re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]", compact_tax(value)))


def _maybe_llm(text: str) -> tuple[dict[str, str] | None, str | None]:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or not (text or "").strip():
        return None, None
    base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    prompt = (
        "Extract vendor document fields as JSON with keys "
        "legal_name, tax_id, registration_number, country, address, account_holder, "
        "bank_name, account_number, bank_code, document_type. "
        "Use an empty string when a field is not present. Return JSON only.\n\n"
        + text[:6000]
    )
    body = json.dumps(
        {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": "You extract fields from vendor documents. Reply with JSON only."},
                {"role": "user", "content": prompt},
            ],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        return _parse_model_json(content), None
    except Exception as exc:
        return None, f"Model call failed ({exc.__class__.__name__}); used the labeled parser."


def _parse_model_json(content: str) -> dict[str, str] | None:
    if not content:
        return None
    raw = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    target = match.group(0) if match else cleaned
    try:
        raw_dict = json.loads(target)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw_dict, dict):
        return None
    return {key: str(raw_dict.get(key) or "").strip() for key in _FIELD_NAMES}
