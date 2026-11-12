from __future__ import annotations

import re

from backend.names import name_score
from database.models import ExistingVendor, Finding, Submission


def find_duplicates(
    submission: Submission, existing: list[ExistingVendor]
) -> tuple[list[Finding], int | None]:
    tax = _tax(submission.tax_id)
    registration = _token(submission.registration_number)
    account = _digits(submission.account_number)
    code = _token(submission.bank_code)
    hits = []
    for vendor in existing:
        reasons = []
        if tax and tax == _tax(vendor.tax_id):
            reasons.append("tax ID")
        if registration and registration == _token(vendor.registration_number):
            reasons.append("registration number")
        same_country = vendor.country.strip().casefold() == submission.country.strip().casefold()
        if same_country and name_score(submission.legal_name, vendor.legal_name) >= 92:
            if "tax ID" not in reasons and "registration number" not in reasons:
                reasons.append("legal name")
        if reasons:
            hits.append((vendor, reasons))

    identity = [hit for hit in hits if "tax ID" in hit[1] or "registration number" in hit[1]]
    if not identity:
        findings = []
        owner = _shared_account(submission, existing)
        if owner is not None:
            findings.append(
                Finding(
                    code="shared_bank_account",
                    severity="pending",
                    stage="duplicate",
                    message=(
                        f"The payout account ending {_digits(submission.account_number)[-4:] or 'unknown'} is "
                        f"already on record for {owner.legal_name} (record {owner.id}), a different legal entity."
                    ),
                    vendor_message=(
                        "The bank account on this submission is already used by another vendor record. "
                        "A reviewer will confirm the account is genuinely yours before any payment is set up."
                    ),
                )
            )
        for vendor, reasons in hits:
            if reasons == ["legal name"]:
                findings.append(
                    Finding(
                        code="similar_name",
                        severity="warn",
                        stage="duplicate",
                        message=(
                            f"Legal name is similar to record {vendor.id} ({vendor.legal_name}), "
                            "but the tax ID is different, so this is not treated as a duplicate."
                        ),
                    )
                )
        return findings, None

    approved = [hit for hit in identity if vendor_status(hit[0]) == "approved"]
    pool = approved or identity
    vendor, reasons = max(pool, key=lambda hit: _rank(hit[0], hit[1]))
    reason_text = _join(reasons)
    if vendor.status != "approved":
        return [
            Finding(
                code="prior_submission",
                severity="warn",
                stage="duplicate",
                message=(
                    f"A previous submission for this {reason_text} is already {vendor.status} "
                    f"(record {vendor.id}, {vendor.legal_name}). This pack is still judged on its own."
                ),
            )
        ], vendor.id

    same_bank = account and account == _digits(vendor.account_number) and code == _token(vendor.bank_code)
    if same_bank:
        return [
            Finding(
                code="duplicate_same_account",
                severity="pending",
                stage="duplicate",
                message=(
                    f"This {reason_text} is already approved as {vendor.legal_name} (record {vendor.id}) "
                    "with the same payout account. A second record is not needed."
                ),
                vendor_message=(
                    f"We already have an approved vendor for this {reason_text}. "
                    "No further documents are needed unless your details have changed through your usual procurement contact."
                ),
            )
        ], vendor.id

    old_last4 = _digits(vendor.account_number)[-4:] or "unknown"
    new_last4 = account[-4:] or "unknown"
    return [
        Finding(
            code="duplicate_bank_change",
            severity="reject",
            stage="duplicate",
            message=(
                f"Matches approved vendor {vendor.legal_name} (record {vendor.id}) on {reason_text}, "
                f"but the bank account ends {new_last4} instead of {old_last4}. "
                "Payout details on an approved vendor are not changed from a new submission."
            ),
            vendor_message=(
                "A vendor with this tax registration is already approved. "
                "We will not change payout details from this submission. "
                "Procurement will contact the email already on file."
            ),
        )
    ], vendor.id


def _shared_account(submission: Submission, existing: list[ExistingVendor]) -> ExistingVendor | None:
    """Same payout account under a different legal entity is a review item, never a verdict."""
    account = _digits(submission.account_number)
    code = _token(submission.bank_code)
    if not account or not code:
        return None
    for vendor in existing:
        if account and account == _digits(vendor.account_number) and code == _token(vendor.bank_code):
            return vendor
    return None


def blocked_vendor(submission: Submission, existing: list[ExistingVendor]) -> Finding | None:
    """A vendor the company has already blocked on this tax ID or registration cannot be onboarded."""
    tax = _tax(submission.tax_id)
    registration = _token(submission.registration_number)
    for vendor in existing:
        if vendor_status(vendor) != "blocked":
            continue
        if (tax and tax == _tax(vendor.tax_id)) or (
            registration and registration == _token(vendor.registration_number)
        ):
            return Finding(
                code="blocked_vendor",
                severity="reject",
                stage="duplicate",
                message=(
                    f"This tax registration belongs to {vendor.legal_name} (record {vendor.id}), "
                    "which is blocked from onboarding."
                ),
                vendor_message=(
                    "This tax registration is on our blocked list. "
                    "Contact the procurement team if you believe this is an error."
                ),
            )
    return None


def vendor_status(vendor: ExistingVendor) -> str:
    return (vendor.status or "").lower()


def _rank(vendor: ExistingVendor, reasons: list[str]) -> int:
    score = 0
    if "tax ID" in reasons:
        score += 4
    if "registration number" in reasons:
        score += 2
    if vendor.status == "approved":
        score += 1
    return score


def _join(reasons: list[str]) -> str:
    if len(reasons) == 1:
        return reasons[0]
    return " and ".join(reasons)


def _tax(value: str) -> str:
    return re.sub(r"[\s-]", "", (value or "").upper())


def _token(value: str) -> str:
    return re.sub(r"[\s-]", "", (value or "").upper())


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")
