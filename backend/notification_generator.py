from __future__ import annotations

from database.models import Finding, Submission


def draft_notification(submission: Submission, status: str, findings: list[Finding]) -> tuple[str, str]:
    name = submission.legal_name.strip() or "this vendor"
    codes = {item.code for item in findings}
    if status == "approved":
        subject = f"Approved: {name} can be set up for payment"
        body = (
            f"Hello,\n\n"
            f"{name} is approved for vendor setup.\n\n"
            "We checked three things:\n"
            "- The legal name matches the incorporation and tax documents.\n"
            "- The tax ID is valid for the country on the submission.\n"
            "- The bank account holder is that same legal entity, and we do not already have this tax ID on a different account.\n\n"
            "Procurement can add this vendor to the payment system.\n"
        )
        return subject, body

    bullets = []
    for item in findings:
        if item.severity not in {"pending", "reject"} or not item.vendor_message:
            continue
        if item.vendor_message not in bullets:
            bullets.append(item.vendor_message)
    if not bullets:
        bullets.append("A reviewer needs to look at this submission before anyone is paid.")

    if status == "pending":
        subject = f"Action needed: {name}"
        intro = f"We cannot approve {name} yet. Please send the following:"
        closing = "Reply with the items above and the check can be run again."
    else:
        subject = f"Not approved: {name}"
        intro = f"We cannot onboard {name} from this submission."
        if "duplicate_bank_change" in codes:
            closing = (
                "Our team will use the contact details already on file. "
                "Do not send replacement bank details in reply to this note."
            )
        else:
            closing = (
                "This submission will not be used for payment. "
                "A new submission is required if the legal entity should still be onboarded."
            )

    lines = "\n".join(f"- {bullet}" for bullet in bullets)
    body = f"Hello,\n\n{intro}\n\n{lines}\n\n{closing}\n"
    return subject, body
