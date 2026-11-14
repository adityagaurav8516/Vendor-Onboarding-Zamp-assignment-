"""Geometry-aware layout parser.

Turns positioned text — from a PDF text layer or from Gemini Flash — into key/value
pairs. It does not need a single ``Label: value`` line, which is exactly what a
scanned, unformatted document does not provide. Three layouts are recognised:

* ``inline``  — ``GSTIN: 07AABCA1234C1ZH``
* ``row``     — label and value are separate cells on one row (``GSTIN    07...``)
* ``stacked`` — the label sits above its value, left-aligned in the same column

The parser only proposes values. Precedence and every decision stay in
``ai_extractor`` and the deterministic rule engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

LABEL_MATCH = 88
ROW_GAP_FACTOR = 1.15
MIN_ROW_GAP = 9.0
STACK_GAP_FACTOR = 2.4
STACK_X_TOLERANCE = 2.0

# Canonical field -> words a vendor document might print instead.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "legal_name": (
        "legal name",
        "legal entity name",
        "name of company",
        "company name",
        "entity name",
        "registered name",
        "name of entity",
        "name of the company",
        "vendor name",
        "applicant name",
    ),
    "tax_id": (
        "tax id",
        "tax identification number",
        "tax registration number",
        "gstin",
        "gstin no",
        "gst number",
        "gst identification number",
        "gst registration number",
        "ein",
        "employer identification number",
        "tin",
        "vat number",
        "pan",
    ),
    "registration_number": (
        "registration number",
        "registration no",
        "company registration number",
        "cin",
        "cin no",
        "company identification number",
        "business registration number",
        "crn",
    ),
    "country": (
        "country",
        "country of incorporation",
        "country of registration",
        "jurisdiction",
    ),
    "address": (
        "registered address",
        "registered office",
        "address",
        "office address",
        "business address",
        "address line",
    ),
    "account_holder": (
        "account holder",
        "account holder name",
        "beneficiary",
        "beneficiary name",
        "name on account",
        "account name",
    ),
    "bank_name": (
        "bank name",
        "bank",
        "financial institution",
        "branch name",
    ),
    "account_number": (
        "account number",
        "account no",
        "account number",
        "a/c number",
        "a/c no",
        "account",
    ),
    "bank_code": (
        "ifsc",
        "ifsc code",
        "routing number",
        "aba number",
        "bank code",
        "sort code",
        "branch code",
        "swift",
        "bic",
    ),
    "document_type": (
        "document type",
        "document",
    ),
}

_ID_FIELDS = {"tax_id", "account_number", "bank_code", "registration_number"}
_TRAILING_FILLER = re.compile(r"\b(no|num|number|code|id)$")
_COLON = re.compile(r"\s*[:：]\s*")
_INLINE_PATTERN = re.compile(r"(?:^|[\s,;|])([A-Za-z0-9/&. ]{2,30}?)\s*[:：=]\s*")


@dataclass(frozen=True)
class TextWord:
    """One positioned word. Coordinates are in the same unit for a whole page."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float = 1.0
    page: int = 0


@dataclass(frozen=True)
class LayoutLine:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float
    page: int = 0
    words: tuple[TextWord, ...] = ()

    @property
    def height(self) -> float:
        return max(1.0, self.y1 - self.y0)


@dataclass(frozen=True)
class KeyValue:
    label: str
    value: str
    field: str
    layout: str
    confidence: float
    page: int = 0


@dataclass
class LayoutResult:
    lines: list[LayoutLine] = field(default_factory=list)
    key_values: list[KeyValue] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines if line.text)

    def fields(self) -> dict[str, str]:
        """Canonical field -> value, one value per field."""
        return {kv.field: kv.value for kv in self.key_values if kv.value}


# --------------------------------------------------------------------- public


def parse(words) -> LayoutResult:
    """Structure positioned words into lines and key/value pairs."""
    lines = group_lines(words)
    return LayoutResult(lines=lines, key_values=extract_key_values(lines))


def parse_text(text: str) -> LayoutResult:
    """Layout pass for plain text: line order stands in for geometry."""
    lines: list[LayoutLine] = []
    for index, raw in enumerate((text or "").splitlines()):
        clean = " ".join(raw.split())
        if not clean:
            continue
        top = index * 12.0
        lines.append(
            LayoutLine(
                text=clean,
                x0=0.0,
                y0=top,
                x1=float(len(clean)) * 6.0,
                y1=top + 10.0,
                confidence=1.0,
                page=0,
            )
        )
    return LayoutResult(lines=lines, key_values=extract_key_values(lines))


def match_label(text: str) -> str:
    """Return the canonical field a piece of text names, or ``''``."""
    cleaned = _normalise_label(text)
    if not cleaned:
        return ""
    candidates = [cleaned]
    stripped = _TRAILING_FILLER.sub("", cleaned).strip()
    if stripped and stripped != cleaned:
        candidates.append(stripped)
    best_field, best_score = "", 0.0
    for field_name, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            key = _normalise_label(alias)
            for candidate in candidates:
                if candidate == key:
                    return field_name
                if len(candidate) < 4:
                    continue
                score = fuzz.token_sort_ratio(candidate, key)
                if score > best_score:
                    best_field, best_score = field_name, score
    return best_field if best_score >= LABEL_MATCH else ""


def group_lines(words) -> list[LayoutLine]:
    """Cluster words into visual lines, page by page, then order them."""
    pages: dict[int, list[TextWord]] = {}
    for word in words or []:
        if not str(word.text or "").strip():
            continue
        pages.setdefault(word.page, []).append(word)

    lines: list[LayoutLine] = []
    for page, items in sorted(pages.items()):
        buckets: list[list[TextWord]] = []
        for word in sorted(items, key=lambda item: ((item.y0 + item.y1) / 2.0, item.x0)):
            bucket = _bucket_for(buckets, word)
            if bucket is None:
                buckets.append([word])
            else:
                bucket.append(word)
        lines.extend(_line_from(bucket, page) for bucket in buckets)
    lines.sort(key=lambda line: (line.page, line.y0, line.x0))
    return lines


def extract_key_values(lines: list[LayoutLine]) -> list[KeyValue]:
    """Find label/value pairs by geometry, keeping the best read per field."""
    found: dict[str, KeyValue] = {}
    for index, line in enumerate(lines):
        text = " ".join((line.text or "").split())
        if not text:
            continue
        candidates = _candidates(lines, index, line, text)
        for kv in candidates:
            current = found.get(kv.field)
            if current is None or kv.confidence > current.confidence + 1e-9:
                found[kv.field] = kv
    return sorted(found.values(), key=lambda kv: (kv.page, kv.field))


# ------------------------------------------------------------------ internals


def _candidates(lines: list[LayoutLine], index: int, line: LayoutLine, text: str) -> list[KeyValue]:
    inline = _inline_split(line, text)
    if inline:
        return inline
    row = _row_split(line, text)
    if row:
        return [row]
    stacked = _stacked_split(lines, index, line, text)
    return [stacked] if stacked else []


def _inline_split(line: LayoutLine, text: str) -> list[KeyValue]:
    matches = []
    for m in _INLINE_PATTERN.finditer(text):
        lbl = m.group(1).strip()
        fld = match_label(lbl)
        if fld:
            matches.append((m.start(1), m.end(), lbl, fld))
    if not matches:
        parts = _COLON.split(text, maxsplit=1)
        if len(parts) == 2:
            label, value = parts
            field_name = match_label(label)
            if field_name:
                value = _clean_value(value, field_name)
                if _value_ok(value, field_name):
                    return [
                        KeyValue(
                            label=label.strip(),
                            value=value,
                            field=field_name,
                            layout="inline",
                            confidence=line.confidence,
                            page=line.page,
                        )
                    ]
        return []

    results = []
    for i, (l_start, v_start, lbl, fld) in enumerate(matches):
        if i + 1 < len(matches):
            val = text[v_start : matches[i + 1][0]]
        else:
            val = text[v_start:]
        val = _clean_value(val, fld)
        if _value_ok(val, fld):
            results.append(
                KeyValue(
                    label=lbl.strip(),
                    value=val,
                    field=fld,
                    layout="inline",
                    confidence=line.confidence,
                    page=line.page,
                )
            )
    return results


def _row_split(line: LayoutLine, text: str) -> KeyValue | None:
    words = [word for word in line.words if str(word.text or "").strip()]
    if len(words) < 2:
        return None
    heights = sorted(max(1.0, word.y1 - word.y0) for word in words)
    median_height = heights[len(heights) // 2]
    threshold = max(MIN_ROW_GAP, ROW_GAP_FACTOR * median_height)
    gaps = [
        (words[position + 1].x0 - words[position].x1, position)
        for position in range(len(words) - 1)
    ]
    gap, split_at = max(gaps)
    if gap < threshold:
        return None
    label = " ".join(word.text.strip() for word in words[: split_at + 1])
    field_name = match_label(label)
    if not field_name:
        return None
    value_words = words[split_at + 1 :]
    value = _clean_value(" ".join(word.text.strip() for word in value_words), field_name)
    if not _value_ok(value, field_name):
        return None
    confidence = sum(word.confidence for word in value_words) / len(value_words)
    return KeyValue(
        label=label,
        value=value,
        field=field_name,
        layout="row",
        confidence=min(line.confidence, confidence) if line.confidence else confidence,
        page=line.page,
    )


def _stacked_split(lines: list[LayoutLine], index: int, line: LayoutLine, text: str) -> KeyValue | None:
    field_name = match_label(text.rstrip(":").strip())
    if not field_name:
        return None
    # A label line must not already carry a value after a colon.
    if ":" in text and text.split(":", 1)[1].strip():
        return None
    height = line.height
    for following in lines[index + 1 : index + 3]:
        if following.page != line.page:
            return None
        gap = following.y0 - line.y1
        if gap > STACK_GAP_FACTOR * height or gap < -height:
            return None
        if abs(following.x0 - line.x0) > max(STACK_X_TOLERANCE * height, 28.0):
            continue
        candidate = " ".join((following.text or "").split())
        if not candidate or ":" in candidate or match_label(candidate):
            continue
        value = _clean_value(candidate, field_name)
        if not _value_ok(value, field_name):
            continue
        return KeyValue(
            label=text.strip(),
            value=value,
            field=field_name,
            layout="stacked",
            confidence=min(line.confidence, following.confidence),
            page=line.page,
        )
    return None


def _bucket_for(buckets: list[list[TextWord]], word: TextWord) -> list[TextWord] | None:
    for bucket in buckets:
        top = min(item.y0 for item in bucket)
        bottom = max(item.y1 for item in bucket)
        height = max(1.0, min(word.y1 - word.y0, bottom - top))
        overlap = min(bottom, word.y1) - max(top, word.y0)
        if overlap >= 0.5 * height:
            return bucket
        if abs((word.y0 + word.y1) / 2.0 - (top + bottom) / 2.0) <= 0.35 * height:
            return bucket
    return None


def _line_from(words: list[TextWord], page: int) -> LayoutLine:
    ordered = sorted(words, key=lambda item: item.x0)
    confidence = sum(word.confidence for word in ordered) / len(ordered) if ordered else 1.0
    return LayoutLine(
        text=" ".join(word.text.strip() for word in ordered),
        x0=min(word.x0 for word in ordered),
        y0=min(word.y0 for word in ordered),
        x1=max(word.x1 for word in ordered),
        y1=max(word.y1 for word in ordered),
        confidence=confidence,
        page=page,
        words=tuple(ordered),
    )


def _normalise_label(text: str) -> str:
    value = re.sub(r"[^a-z0-9 &/]+", " ", (text or "").lower())
    value = value.replace("&", " and ")
    return " ".join(value.split())


def _clean_value(value: str, field_name: str) -> str:
    cleaned = " ".join((value or "").split()).strip(" .,;:-—=·/|")
    if not cleaned:
        return ""
    if field_name == "account_number":
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
    if field_name in _ID_FIELDS:
        compact = re.sub(r"\s+", "", cleaned)
        if re.fullmatch(r"[A-Za-z0-9/\-]{5,}", compact):
            cleaned = compact.upper() if field_name != "account_number" else compact
    return cleaned


def _value_ok(value: str, field_name: str) -> bool:
    if len(value) < 2:
        return False
    if value.endswith(":"):
        return False
    other = match_label(value)
    if other and other != field_name and len(value.split()) <= 5:
        return False
    return True
