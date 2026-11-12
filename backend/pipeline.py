from __future__ import annotations

import time
from collections.abc import Callable

from backend import (
    ai_extractor,
    cross_verification,
    decision_engine,
    document_parser,
    duplicate_detector,
    field_validator,
    notification_generator,
)
from database.models import (
    DocumentFile,
    ExistingVendor,
    Finding,
    ParsedDocument,
    RunResult,
    StageLog,
    Submission,
)

REQUIRED_DOCUMENTS = (
    ("incorporation", "Certificate of incorporation"),
    ("tax_certificate", "GST certificate"),
    ("bank_proof", "Cancelled cheque or bank letter"),
)

SLOT_KIND = {
    "incorporation": "incorporation",
    "tax_certificate": "tax",
    "bank_proof": "bank",
}

KIND_LABEL = {
    "incorporation": "incorporation certificate",
    "tax": "tax certificate",
    "bank": "bank letter or cheque",
    "unknown": "unrecognised document",
}

TOTAL_STAGES = 7

ProgressCallback = Callable[[StageLog, int, int], None]


def run_pipeline(
    submission: Submission,
    documents: list[DocumentFile],
    existing: list[ExistingVendor] | None = None,
    scenario_id: str | None = None,
    on_stage: ProgressCallback | None = None,
) -> RunResult:
    """Deterministic, ordered pipeline. The model reads documents; Python decides."""
    existing = existing or []
    findings: list[Finding] = []
    checks = []
    stages: list[StageLog] = []
    started = time.perf_counter()
    last = started

    def record(key: str, title: str, stage_findings: list[Finding], summary: str, details: list[str]) -> None:
        nonlocal last
        now = time.perf_counter()
        stage = StageLog(
            key=key,
            title=title,
            status=decision_engine.stage_status(stage_findings),
            summary=summary,
            details=details,
            elapsed_ms=int((now - last) * 1000),
        )
        last = now
        stages.append(stage)
        if on_stage is not None:
            on_stage(stage, len(stages), TOTAL_STAGES)

    # 1. Intake ------------------------------------------------------------
    record(
        "intake",
        "Submission received",
        [],
        f"Intake complete for {submission.legal_name.strip() or 'an unnamed vendor'}.",
        [
            f"Country: {submission.country or 'not given'}",
            f"Documents attached: {len(documents)}",
            f"Contact: {submission.contact_email.strip() or 'not given'}",
        ],
    )

    # 2. Documents ---------------------------------------------------------
    parsed_docs: list[ParsedDocument] = []
    document_findings: list[Finding] = []
    provided = {doc.slot for doc in documents}
    missing = [(slot, label) for slot, label in REQUIRED_DOCUMENTS if slot not in provided]
    for slot, label in missing:
        document_findings.append(
            Finding(
                code="missing_document",
                severity="pending",
                stage="documents",
                message=f"{label} was not uploaded for slot '{slot}'.",
                vendor_message=f"Please upload the {label.lower()}.",
            )
        )

    for doc in documents:
        status, text = document_parser.parse_bytes(doc.filename, doc.content)
        document_type = "unreadable"
        fields: dict[str, str] = {}
        method = "none"
        confidence = 0.0
        warnings: list[str] = []

        if status == "parsed":
            document_type = ai_extractor.classify_document("", text)
            expected = SLOT_KIND.get(doc.slot, "")
            if document_type not in {expected, "unknown"}:
                document_findings.append(
                    Finding(
                        code="wrong_document",
                        severity="pending",
                        stage="documents",
                        message=(
                            f"{doc.filename} was uploaded as a {KIND_LABEL.get(expected, 'document')}, "
                            f"but it reads like a {KIND_LABEL.get(document_type, 'different document')}."
                        ),
                        vendor_message=(
                            f"The file {doc.filename} does not look like a "
                            f"{KIND_LABEL.get(expected, 'the requested document')}. "
                            f"Please upload the {KIND_LABEL.get(expected, 'correct document')}."
                        ),
                    )
                )
                document_type = "wrong_slot"
            else:
                fields, method, warnings, confidence = ai_extractor.extract_fields(text)
                if not any((value or "").strip() for value in fields.values()):
                    document_findings.append(
                        Finding(
                            code="extraction_unclear",
                            severity="pending",
                            stage="documents",
                            message=f"No labelled fields could be read from {doc.filename}.",
                            vendor_message=(
                                f"We could not read the details in {doc.filename}. "
                                "Please upload a text-based PDF or a clear scan."
                            ),
                        )
                    )
        else:
            document_findings.append(
                Finding(
                    code="unreadable_document",
                    severity="pending",
                    stage="documents",
                    message=f"{doc.filename} could not be parsed as text.",
                    vendor_message=(
                        f"The file {doc.filename} could not be read. "
                        "Please upload a readable PDF or a plain text copy."
                    ),
                )
            )

        parsed_docs.append(
            ParsedDocument(
                slot=doc.slot,
                filename=doc.filename,
                parse_status=status,
                text_excerpt=text[:5000],
                document_type=document_type,
                fields=fields,
                method=method,
                confidence=confidence,
                warnings=warnings,
                content=doc.content,
            )
        )

    findings.extend(document_findings)
    parsed_count = sum(1 for doc in parsed_docs if doc.parse_status == "parsed")
    unreadable_count = len(parsed_docs) - parsed_count
    field_count = sum(
        1
        for doc in parsed_docs
        for value in doc.fields.values()
        if (value or "").strip()
    )
    doc_details = [
        f"{doc.filename}: {doc.parse_status}, {doc.document_type}, fields={sum(1 for v in doc.fields.values() if v)}, "
        f"confidence={doc.confidence:.2f}"
        for doc in parsed_docs
    ]
    doc_details.extend(f"Missing: {label}" for _slot, label in missing)
    record(
        "documents",
        "Documents processed",
        document_findings,
        (
            f"{parsed_count} of {len(documents)} uploaded document(s) parsed, "
            f"{field_count} labelled field(s) read, {len(missing)} required document(s) missing."
        ),
        doc_details or ["No documents were attached."],
    )

    # 3. Field validation --------------------------------------------------
    validation_findings = field_validator.validate_submission(submission)
    findings.extend(validation_findings)
    failed_checks = [f for f in validation_findings if f.severity in {"pending", "reject"}]
    notes = [f for f in validation_findings if f.severity == "warn"]
    record(
        "validate",
        "Field validation",
        validation_findings,
        (
            "All format and completeness checks passed."
            if not failed_checks
            else f"{len(failed_checks)} field check(s) need attention."
        ),
        [f.message for f in failed_checks] + [f.message for f in notes],
    )

    # 4. Cross-document verification --------------------------------------
    cross_findings, cross_checks = cross_verification.cross_check(submission, parsed_docs)
    checks.extend(cross_checks)
    findings.extend(cross_findings)
    matched = sum(1 for check in cross_checks if check.result == "match")
    compared = sum(1 for check in cross_checks if check.result != "missing")
    record(
        "cross",
        "Cross-document verification",
        cross_findings,
        (
            f"{matched} of {compared} comparable value(s) matched across documents."
            if compared
            else "There were no readable document values to compare against the form."
        ),
        [
            f"{check.field}: {check.result or 'not compared'} "
            f"(form '{check.submitted}' vs {check.source} '{check.extracted}')"
            for check in cross_checks
        ],
    )

    # 5. Duplicate and blocklist detection --------------------------------
    duplicate_findings, duplicate_of = duplicate_detector.find_duplicates(submission, existing)
    blocked = duplicate_detector.blocked_vendor(submission, existing)
    if blocked is not None:
        duplicate_findings.append(blocked)
    findings.extend(duplicate_findings)
    record(
        "duplicate",
        "Duplicate detection",
        duplicate_findings,
        (
            "No duplicate or blocked records found."
            if not duplicate_findings
            else f"{len(duplicate_findings)} registry note(s) raised."
        ),
        [
            f"{item.code}: {item.message}"
            for item in duplicate_findings
        ]
        or ["Checked tax ID, registration number and payout account against the vendor registry."],
    )

    # 6. Decision ----------------------------------------------------------
    status, headline, summary = decision_engine.decide(findings)
    pending_actions = [f.vendor_message for f in findings if f.severity in {"pending", "reject"} and f.vendor_message]
    record(
        "decision",
        "Decision generated",
        [f for f in findings if f.severity in {"pending", "reject"}],
        f"{status.upper()}: {headline}",
        [
            f"Rejecting rule: {f.code}" if f.severity == "reject" else f"Review rule: {f.code}"
            for f in findings
            if f.severity in {"pending", "reject"}
        ]
        or ["All deterministic rules passed."],
    )

    # 7. Explanation and audit --------------------------------------------
    subject, body = notification_generator.draft_notification(submission, status, findings)
    record(
        "audit",
        "Notification and audit log",
        [],
        f"Vendor message drafted with {len(pending_actions)} requested action(s); decision, findings and reasons packaged for the audit log.",
        [subject],
    )

    methods = {doc.method for doc in parsed_docs if doc.method in {"rules", "model"}}
    if methods == {"rules", "model"}:
        extraction_method = "mixed (labels + model)"
    elif methods == {"model"}:
        extraction_method = "model"
    elif methods:
        extraction_method = "labels"
    else:
        extraction_method = "none"

    return RunResult(
        status=status,
        headline=headline,
        summary=summary,
        findings=findings,
        stages=stages,
        checks=checks,
        notification_subject=subject,
        notification_body=body,
        extraction_method=extraction_method,
        duplicate_of=duplicate_of,
        documents=parsed_docs,
        submission=submission,
        scenario_id=scenario_id,
    )
