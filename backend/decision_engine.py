from __future__ import annotations

from database.models import Finding


def decide(findings: list[Finding]) -> tuple[str, str, str]:
    """Reject beats a request for more documents. Warnings never block an approval."""
    rejects = [item for item in findings if item.severity == "reject"]
    pendings = [item for item in findings if item.severity == "pending"]
    if rejects:
        parts = [item.message for item in rejects]
        if pendings:
            parts.append(pendings[0].message)
        return "rejected", "Not approved", " ".join(parts[:3])
    if pendings:
        return "pending", "Needs a reply before approval", " ".join(item.message for item in pendings[:3])
    return (
        "approved",
        "Approved for payment setup",
        "The legal name, tax registration, and bank account describe the same entity, and this tax ID is not already approved with a different payout account.",
    )


def stage_status(findings: list[Finding]) -> str:
    if any(item.severity == "reject" for item in findings):
        return "stop"
    if any(item.severity == "pending" for item in findings):
        return "attention"
    return "pass"
