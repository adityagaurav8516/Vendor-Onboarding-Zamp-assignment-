from __future__ import annotations

import re

from backend.field_validator import (
    GST_STATES,
    address_mentions_state,
    compact_tax,
    is_gstin_shape,
    tax_home_country,
)
from backend.names import looks_like_company, looks_like_person, name_score
from database.models import FieldCheck, Finding, ParsedDocument, Submission

MATCH = 92
PARTIAL = 78

_SLOT_KIND = {
    "incorporation": "incorporation",
    "tax_certificate": "tax",
    "bank_proof": "bank",
}
_SLOT_LABEL = {
    "incorporation": "incorporation certificate",
    "tax_certificate": "tax certificate",
    "bank_proof": "bank letter",
}
_INDIA_HINTS = (
    "india", "mumbai", "maharashtra", "delhi", "karnataka", "gujarat", "bengaluru",
    "bangalore", "pune", "hyderabad", "chennai", "kolkata", "ahmedabad", "noida",
)
_US_HINTS = (
    "united states", "usa", "california", "delaware", "new york", "texas",
    "new jersey", "illinois", "washington",
)


def cross_check(submission: Submission, documents: list[ParsedDocument]) -> tuple[list[Finding], list[FieldCheck]]:
    findings: list[Finding] = []
    checks: list[FieldCheck] = []
    foreign: list[str] = []
    by_slot = {doc.slot: doc for doc in documents}

    for slot, id_key, id_label in (
        ("incorporation", "registration_number", "Registration number"),
        ("tax_certificate", "tax_id", "Tax ID"),
    ):
        doc = by_slot.get(slot)
        if doc is None or not _usable(doc):
            continue
        doc_name = doc.fields.get("legal_name", "")
        doc_id = doc.fields.get(id_key, "")
        sub_id = getattr(submission, id_key)
        name_result, name_points = _compare_name(submission.legal_name, doc_name)
        id_result = _compare_token(sub_id, doc_id)
        source = _SLOT_LABEL[slot].capitalize()
        checks.append(_check("Legal name", submission.legal_name, doc_name, source, name_result, name_points))
        checks.append(_check(id_label, sub_id, doc_id, source, id_result, ""))
        foreign.append(slot) if name_result == "mismatch" and id_result == "mismatch" else None
        findings.extend(
            _identity_findings(slot, submission.legal_name, sub_id, doc_name, doc_id, name_result, id_result, id_label)
        )

    bank = by_slot.get("bank_proof")
    bank_fields = bank.fields if bank and _usable(bank) else {}
    findings.extend(_judge_holder(submission, bank_fields, checks))
    if bank and _usable(bank):
        findings.extend(_compare_bank_tokens(submission, bank_fields, checks))

    findings.extend(_state_vs_address(submission))
    jurisdiction = _jurisdiction(submission, documents)
    if jurisdiction:
        findings.append(jurisdiction)

    both_foreign = {"incorporation", "tax_certificate"}.issubset(foreign)
    if both_foreign:
        findings = [item for item in findings if item.code != "identity_document_mismatch"]
        findings.append(
            Finding(
                code="identity_pack_mismatch",
                severity="reject",
                stage="cross",
                message="Both the incorporation certificate and the tax certificate belong to a different entity than the form.",
                vendor_message="The incorporation certificate and the tax certificate are for a different legal entity than the one on the form. Please send both documents for the company you want onboarded.",
            )
        )
    return findings, checks


def _usable(doc: ParsedDocument) -> bool:
    return doc.parse_status == "parsed" and doc.document_type != "wrong_slot"


def _identity_findings(slot, legal, sub_id, doc_name, doc_id, name_result, id_result, id_label) -> list[Finding]:
    label = _SLOT_LABEL[slot]
    findings: list[Finding] = []
    if not doc_name and not doc_id:
        findings.append(
            Finding(
                code="doc_field_missing",
                severity="pending",
                stage="cross",
                message=f"The {label} does not show a legal name or {id_label.lower()}.",
                vendor_message=f"Please send a {label} that shows the legal name and the {id_label.lower()}.",
            )
        )
        return findings
    if name_result == "mismatch" and id_result == "mismatch":
        findings.append(
            Finding(
                code="identity_document_mismatch",
                severity="pending",
                stage="cross",
                message=f"The {label} belongs to {doc_name} ({doc_id}), not {legal} ({sub_id}).",
                vendor_message=f"The {label} is issued to {doc_name} ({doc_id}), not to {legal}. Please send the {label} for {legal}.",
            )
        )
        return findings
    if not doc_name or name_result == "missing":
        findings.append(
            Finding(
                code="doc_field_missing",
                severity="pending",
                stage="cross",
                message=f"The {label} does not show a legal name.",
                vendor_message=f"Please send a {label} that shows the legal name.",
            )
        )
    elif name_result in {"mismatch", "partial"}:
        findings.append(
            Finding(
                code="identity_name_gap",
                severity="pending",
                stage="cross",
                message=f"The name on the {label} is '{doc_name}', which does not match {legal}.",
                vendor_message=f"The {label} names '{doc_name}'. Please send one in the legal name {legal}.",
            )
        )
    if not doc_id or id_result == "missing":
        findings.append(
            Finding(
                code="doc_field_missing",
                severity="pending",
                stage="cross",
                message=f"The {label} does not show a {id_label.lower()}.",
                vendor_message=f"Please send a {label} that shows the {id_label.lower()}.",
            )
        )
    elif id_result == "mismatch":
        findings.append(
            Finding(
                code="identity_id_mismatch",
                severity="pending",
                stage="cross",
                message=f"{id_label} on the form is {sub_id}, but the {label} shows {doc_id}.",
                vendor_message=f"The {id_label.lower()} on the form does not match the {label}. Please confirm which one is current.",
            )
        )
    return findings


def _judge_holder(submission: Submission, bank_fields: dict, checks: list[FieldCheck]) -> list[Finding]:
    submitted = submission.bank_holder_name.strip()
    extracted = (bank_fields.get("account_holder") or "").strip()
    findings: list[Finding] = []
    if extracted:
        form_result, form_score = _compare_name(submitted, extracted)
        checks.append(_check("Account holder", submitted, extracted, "Bank letter", form_result, form_score))
        if form_result == "mismatch":
            findings.append(
                Finding(
                    code="bank_letter_disagrees",
                    severity="pending",
                    stage="cross",
                    message=f"The form says the account is held by {submitted}, but the bank letter says {extracted}.",
                    vendor_message="The account holder on the form does not match the bank letter. Please send a corrected form or a letter for the account you want paid.",
                )
            )
    evidence = extracted or submitted
    source = "Bank letter" if extracted else "Form"
    result, score = _compare_name(submission.legal_name, evidence)
    checks.append(_check("Account holder vs legal name", submission.legal_name, evidence, source, result, score))
    if result in {"match", ""}:
        return findings
    if result == "missing":
        findings.append(
            Finding(
                code="bank_holder_missing",
                severity="pending",
                stage="cross",
                message="There is no account holder name to compare with the legal entity.",
                vendor_message="Please name the bank account holder. It has to be the legal entity.",
            )
        )
        return findings
    if result == "partial":
        findings.append(
            Finding(
                code="bank_holder_partial",
                severity="pending",
                stage="cross",
                message=f"Account holder '{evidence}' is close to {submission.legal_name} but not the same after company suffixes are ignored.",
                vendor_message=f"The bank account name is close to {submission.legal_name} but not the same. Please send a letter that shows the full legal name.",
            )
        )
        return findings
    if looks_like_person(evidence) and looks_like_company(submission.legal_name):
        message = (
            f"The bank account is held by {evidence}, a person, not by {submission.legal_name}. "
            "Payment would not reach the company."
        )
        vendor = (
            f"The bank account is held by {evidence}. Please provide an account in the legal name "
            f"{submission.legal_name}."
        )
    else:
        message = f"The bank account is held by {evidence}, which is not {submission.legal_name}."
        vendor = f"The bank account must be in the legal name {submission.legal_name}. The account we received is held by {evidence}."
    findings.append(
        Finding(
                            code="bank_holder_mismatch",
                severity="pending",
            stage="cross",
            message=message,
            vendor_message=vendor,
        )
    )
    return findings


def _compare_bank_tokens(submission: Submission, fields: dict, checks: list[FieldCheck]) -> list[Finding]:
    findings: list[Finding] = []
    pairs = (
        ("account_number", "Account number", "account number"),
        ("bank_code", "Bank code", "bank code"),
    )
    for key, label, phrase in pairs:
        extracted = fields.get(key, "")
        submitted = getattr(submission, key)
        if not extracted:
            continue
        result = _compare_token(submitted, extracted)
        checks.append(_check(label, submitted, extracted, "Bank letter", result, ""))
        if result == "mismatch":
            findings.append(
                Finding(
                    code=f"{key}_mismatch",
                    severity="pending",
                    stage="cross",
                    message=f"{label} on the bank letter does not match the form.",
                    vendor_message=f"The {phrase} on the bank letter does not match the form. Please confirm which account should be paid.",
                )
            )
    return findings


def _state_vs_address(submission: Submission) -> list[Finding]:
    if submission.country != "India" or not is_gstin_shape(submission.tax_id):
        return []
    compact = compact_tax(submission.tax_id)
    state = GST_STATES.get(compact[:2])
    if not state or not submission.address.strip():
        return []
    if address_mentions_state(submission.address, state):
        return []
    return [
        Finding(
            code="state_address_mismatch",
            severity="pending",
            stage="cross",
            message=f"GSTIN is registered in {state}, but the address does not mention that state.",
            vendor_message=f"The GSTIN is registered in {state}, but the address does not show that state. Please confirm both.",
        )
    ]


def _jurisdiction(submission: Submission, documents: list[ParsedDocument]) -> Finding | None:
    claimed = submission.country
    if claimed not in {"India", "United States"}:
        return None
    address_regions = _regions(submission.address)
    doc_countries = set()
    for doc in documents:
        if not _usable(doc):
            continue
        country = (doc.fields.get("country") or "").strip()
        if country in {"India", "United States"} and doc.slot != "bank_proof":
            doc_countries.add(country)
    notes: list[str] = []
    if address_regions and claimed not in address_regions:
        notes.append(f"the address reads as {', '.join(sorted(address_regions))}")
    other_docs = sorted(country for country in doc_countries if country != claimed)
    if other_docs:
        notes.append(f"the identity documents name {other_docs[0]}")
    tax_country = tax_home_country(submission.tax_id)
    bank_country = submission.bank_country.strip()
    if tax_country and bank_country and tax_country != bank_country:
        notes.append(f"the bank account is in {bank_country} while the tax ID belongs to {tax_country}")
    elif bank_country and claimed and bank_country != claimed and "cross" not in notes:
        notes.append(f"the bank account is in {bank_country}")
    if not notes:
        return None
    # A bank in another country, with everything else aligned, is a question, not a broken identity.
    only_bank = len(notes) == 1 and notes[0].startswith("the bank account is in") and not tax_country
    detail = "; ".join(notes)
    if only_bank or (len(notes) == 1 and notes[0].startswith("the bank account is in") and tax_country == claimed):
        return Finding(
            code="cross_border_bank",
            severity="pending",
            stage="cross",
            message=f"Payout country does not match the vendor country: {detail}.",
            vendor_message=f"The bank account country does not match the company country ({detail}). Please send a short explanation and the bank letter.",
        )
    if not any(note.startswith("the address") or note.startswith("the identity") or "tax ID belongs" in note for note in notes):
        return None
    return Finding(
        code="jurisdiction_conflict",
        severity="pending",
        stage="cross",
        message=f"The submission does not sit in one country: {detail}.",
        vendor_message=(
            "Please resubmit a single legal entity, with the tax ID, the address, and the bank account all in that same country. "
            f"Right now, {detail}."
        ),
    )


def _regions(text: str) -> set[str]:
    found = set()
    if _has_hint(text, _INDIA_HINTS):
        found.add("India")
    if _has_hint(text, _US_HINTS):
        found.add("United States")
    return found


def _has_hint(text: str, hints: tuple[str, ...]) -> bool:
    blob = (text or "").lower()
    return any(re.search(rf"\b{re.escape(hint)}\b", blob) for hint in hints)


def _compare_name(left: str, right: str) -> tuple[str, str]:
    if not (left or "").strip() or not (right or "").strip():
        return "missing", ""
    score = name_score(left, right)
    if score >= MATCH:
        return "match", str(score)
    if score >= PARTIAL:
        return "partial", str(score)
    return "mismatch", str(score)


def _compare_token(left: str, right: str) -> str:
    if not (left or "").strip() or not (right or "").strip():
        return "missing"
    a = re.sub(r"[\s-]", "", left).upper()
    b = re.sub(r"[\s-]", "", right).upper()
    return "match" if a == b else "mismatch"


def _check(field: str, submitted: str, extracted: str, source: str, result: str, score: str) -> FieldCheck:
    return FieldCheck(
        field=field,
        submitted=submitted or "",
        extracted=extracted or "",
        source=source,
        result=result,
        score=score,
    )
