from __future__ import annotations

import re

from rapidfuzz import fuzz

_SUFFIXES = (
    "private limited",
    "pvt ltd",
    "pvt limited",
    "limited liability company",
    "corporation",
    "incorporated",
    "limited",
    "gmbh",
    "corp",
    "llc",
    "llp",
    "ltd",
    "plc",
    "pte",
    "inc",
)

_COMPANY_WORD = re.compile(
    r"\b(pvt|private|ltd|limited|llc|inc|corp|corporation|llp|gmbh|plc|incorporated)\b",
    re.IGNORECASE,
)


def normalize_name(value: str) -> str:
    text = (value or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    changed = True
    while changed and text:
        changed = False
        for suffix in _SUFFIXES:
            updated = re.sub(rf"(?:^|\s){re.escape(suffix)}$", "", text).strip()
            if updated and updated != text:
                text = updated
                changed = True
                break
    return text


def name_score(left: str, right: str) -> int:
    a = normalize_name(left)
    b = normalize_name(right)
    if not a or not b:
        return 0
    if a == b:
        return 100
    return int(round(fuzz.token_sort_ratio(a, b)))


def looks_like_company(value: str) -> bool:
    return bool(_COMPANY_WORD.search(value or ""))


def looks_like_person(value: str) -> bool:
    if looks_like_company(value):
        return False
    tokens = re.findall(r"[A-Za-z]+", value or "")
    return 2 <= len(tokens) <= 4
