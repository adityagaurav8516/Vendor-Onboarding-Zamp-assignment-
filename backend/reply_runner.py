"""Vendor reply loop — selective re-evaluation and before/after diff engine."""

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
    rule_version,
)
from backend.pipeline import (
    KIND_LABEL,
    LOW_OCR_CONFIDENCE,
    REQUIRED_DOCUMENTS,
    SLOT_KIND,
    SOURCES,
)
from database.models import (
    DocumentDiff,
    DocumentFile,
    ExistingVendor,
    FieldCheck,
    FieldDiff,
    Finding,
    FindingDiff,
    ParsedDocument,
    ReplyDiff,
    RunResult,
    StageLog,
    Submission,
)
from frontend.ui import FIELD_LABEL, SLOT_LABEL

SUBMISSION_FIELDS = (
    "legal_name",
    "trade_name",
    "country",
    "tax_id",
    "registration_number",
    "address",
    "contact_name",
    "contact_email",
    "contact_phone",
    "bank_holder_name",
    "bank_name",
    "account_number",
    "bank_code",
    "bank_country",
)

DUPLICATE_KEYS = {"tax_id", "registration_number", "account_number", "bank_code"}

ProgressCallback = Callable[[StageLog, int, int], None]


def bundle_to_submission(bundle: dict) -> Submission:
    """Reconstruct a Submission dataclass from a database bundle dict."""
    vendor = bundle.get("vendor", {})
    return Submission(
        legal_name=str(vendor.get("legal_name", "") or ""),
        trade_name=str(vendor.get("trade_name", "") or ""),
        country=str(vendor.get("country", "") or ""),
        tax_id=str(vendor.get("tax_id", "") or ""),
        registration_number=str(vendor.get("registration_number", "") or ""),
        address=str(vendor.get("address", "") or ""),
        contact_name=str(vendor.get("contact_name", "") or ""),
        contact_email=str(vendor.get("contact_email", "") or ""),
        contact_phone=str(vendor.get("contact_phone", "") or ""),
        bank_holder_name=str(vendor.get("bank_holder_name", "") or ""),
        bank_name=str(vendor.get("bank_name", "") or ""),
        account_number=str(vendor.get("account_number", "") or ""),
        bank_code=str(vendor.get("bank_code", "") or ""),
        bank_country=str(vendor.get("bank_country", "") or ""),
    )


def _carried_document_findings(parent_bundle: dict, slot: str, p_doc: dict) -> list[Finding]:
    """Document findings from the parent run that still hold for an unchanged upload.

    Findings are attached to a slot by the state of the stored document and the
    file name in the message, not by the slot name appearing in the text: a
    flagged upload (unreadable, wrong slot, nothing read) must stay flagged until
    the vendor replaces it.
    """
    filename = p_doc.get("filename") or ""
    parse_status = p_doc.get("parse_status")
    document_type = p_doc.get("document_type")
    has_fields = any(str(value or "").strip() for value in (p_doc.get("fields") or {}).values())

    still_applies = {
        "unreadable_document": parse_status != "parsed",
        "wrong_document": parse_status == "parsed" and document_type == "wrong_slot",
        "extraction_unclear": parse_status == "parsed" and document_type != "wrong_slot" and not has_fields,
        "ocr_low_confidence": True,
    }
    carried: list[Finding] = []
    seen: set[str] = set()
    for item in parent_bundle.get("findings", []):
        code = item.get("code")
        if item.get("stage") != "documents" or not still_applies.get(code, False):
            continue
        if filename and filename not in item.get("message", ""):
            continue
        if code in seen:
            continue
        seen.add(code)
        carried.append(
            Finding(
                code=code,
                severity=item["severity"],
                stage=item["stage"],
                message=item["message"],
                vendor_message=item.get("vendor_message"),
            )
        )

    # Older runs, or findings with unexpected wording: rebuild what the stored state implies.
    if parse_status != "parsed" and "unreadable_document" not in seen:
        carried.append(
            Finding(
                code="unreadable_document",
                severity="pending",
                stage="documents",
                message=f"{filename or slot} could not be parsed as text.",
                vendor_message=(
                    f"The file {filename or slot} could not be read. "
                    "Please upload a readable PDF or a plain text copy."
                ),
            )
        )
    elif parse_status == "parsed" and document_type == "wrong_slot" and "wrong_document" not in seen:
        expected = KIND_LABEL.get(SLOT_KIND.get(slot, ""), "document")
        carried.append(
            Finding(
                code="wrong_document",
                severity="pending",
                stage="documents",
                message=f"{filename or slot} was uploaded as a {expected}, but it reads like a different document.",
                vendor_message=f"The file {filename or slot} does not look like a {expected}. Please upload the {expected}.",
            )
        )
    return carried


def identify_changes(
    original: Submission,
    updated: Submission,
    new_documents: dict[str, DocumentFile] | None = None,
) -> tuple[set[str], set[str]]:
    """Determine which fields and document slots changed between original and updated."""
    changed_fields = {
        name
        for name in SUBMISSION_FIELDS
        if getattr(updated, name, "").strip() != getattr(original, name, "").strip()
    }
    changed_slots = set()
    if new_documents:
        for slot, doc in new_documents.items():
            if doc is not None and (doc.content or doc.filename):
                changed_slots.add(slot)
    return changed_fields, changed_slots


def compute_diff(
    parent_bundle: dict,
    new_result: RunResult,
    changed_fields: set[str],
    new_documents: dict[str, DocumentFile] | None = None,
    affected_rules: list[str] | None = None,
    skipped_rules: list[str] | None = None,
) -> ReplyDiff:
    """Build a structured before/after diff comparing the original run with the reply run."""
    new_documents = new_documents or {}
    affected_rules = affected_rules or []
    skipped_rules = skipped_rules or []

    parent_sub = parent_bundle["submission"]
    parent_vendor = parent_bundle["vendor"]
    before_status = parent_sub["status"]
    after_status = new_result.status

    # 1. Field diffs
    field_diffs: list[FieldDiff] = []
    for field_name in sorted(changed_fields):
        old_val = str(parent_vendor.get(field_name, "") or "")
        new_val = str(getattr(new_result.submission, field_name, "") or "")
        field_diffs.append(
            FieldDiff(
                field=field_name,
                label=FIELD_LABEL.get(field_name, field_name.replace("_", " ").title()),
                old_value=old_val,
                new_value=new_val,
            )
        )

    # 2. Document diffs
    parent_docs_by_slot = {doc["slot"]: doc for doc in parent_bundle.get("documents", [])}
    new_docs_by_slot = {doc.slot: doc for doc in new_result.documents}
    document_diffs: list[DocumentDiff] = []

    all_slots = [slot for slot, _ in REQUIRED_DOCUMENTS]
    for slot in set(all_slots) | set(parent_docs_by_slot) | set(new_docs_by_slot):
        label = SLOT_LABEL.get(slot, slot.replace("_", " ").title())
        old_doc = parent_docs_by_slot.get(slot)
        new_doc = new_docs_by_slot.get(slot)
        old_fn = old_doc["filename"] if old_doc else None
        new_fn = new_doc.filename if new_doc else None

        if slot in new_documents and new_documents[slot] is not None and new_documents[slot].content:
            action = "added" if old_doc is None else "replaced"
        elif new_doc is not None and old_doc is not None:
            action = "retained"
        elif new_doc is None and old_doc is None:
            action = "missing"
        else:
            action = "retained"

        document_diffs.append(
            DocumentDiff(
                slot=slot,
                label=label,
                action=action,
                old_filename=old_fn,
                new_filename=new_fn,
            )
        )

    # 3. Findings diff (resolved, remaining, new)
    parent_findings = parent_bundle.get("findings", [])
    parent_signatures = {
        (f["code"], f["message"]): f for f in parent_findings if f.get("severity") in {"pending", "reject", "warn"}
    }
    new_signatures = {
        (f.code, f.message): f for f in new_result.findings if f.severity in {"pending", "reject", "warn"}
    }

    resolved_findings: list[FindingDiff] = []
    for sig, f in parent_signatures.items():
        if sig not in new_signatures:
            resolved_findings.append(
                FindingDiff(
                    code=f["code"],
                    severity=f["severity"],
                    stage=f["stage"],
                    message=f["message"],
                    status="resolved",
                )
            )

    remaining_findings: list[FindingDiff] = []
    for sig, f in parent_signatures.items():
        if sig in new_signatures:
            remaining_findings.append(
                FindingDiff(
                    code=f["code"],
                    severity=f["severity"],
                    stage=f["stage"],
                    message=f["message"],
                    status="remaining",
                )
            )

    new_findings: list[FindingDiff] = []
    for sig, f in new_signatures.items():
        if sig not in parent_signatures:
            new_findings.append(
                FindingDiff(
                    code=f.code,
                    severity=f.severity,
                    stage=f.stage,
                    message=f.message,
                    status="new",
                )
            )

    return ReplyDiff(
        parent_id=parent_sub["id"],
        parent_reference=parent_sub["reference"],
        reply_id=None,
        reply_reference=None,
        before_status=before_status,
        after_status=after_status,
        field_diffs=field_diffs,
        document_diffs=document_diffs,
        resolved_findings=resolved_findings,
        remaining_findings=remaining_findings,
        new_findings=new_findings,
        affected_rules=affected_rules,
        skipped_rules=skipped_rules,
    )


def run_vendor_reply(
    parent_bundle: dict,
    updated_submission: Submission,
    new_documents: dict[str, DocumentFile] | None = None,
    existing: list[ExistingVendor] | None = None,
    on_stage: ProgressCallback | None = None,
) -> tuple[RunResult, ReplyDiff]:
    """Selective re-evaluation pipeline for vendor replies.

    Only affected rules are re-evaluated based on the specific fields
    or documents uploaded; unaffected rule results and parsed documents
    are preserved from the original parent run.
    """
    new_documents = new_documents or {}
    existing = existing or []
    # The vendor row is updated in place by a reply, so it must never be compared with itself.
    own_vendor_id = (parent_bundle.get("vendor") or {}).get("id")
    parent_sub = parent_bundle["submission"]
    parent_id = parent_sub["id"]
    parent_ref = parent_sub["reference"]
    original_submission = bundle_to_submission(parent_bundle)

    changed_fields, changed_slots = identify_changes(
        original_submission, updated_submission, new_documents
    )

    affected_rules: list[str] = []
    skipped_rules: list[str] = []
    findings: list[Finding] = []
    checks: list[FieldCheck] = []
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
            on_stage(stage, len(stages), 7)

    # ---------------------------------------------------------------- Stage 1: Intake
    intake_details = [
        f"Reply to submission {parent_ref} (originally {parent_sub['status']}).",
        f"Modified fields ({len(changed_fields)}): {', '.join(sorted(changed_fields)) or 'none'}.",
        f"Updated documents ({len(changed_slots)}): {', '.join(sorted(changed_slots)) or 'none'}.",
    ]
    record(
        "intake",
        "Vendor reply received",
        [],
        f"Intake complete for reply to {parent_ref} ({updated_submission.legal_name or 'vendor'}).",
        intake_details,
    )

    # ---------------------------------------------------------------- Stage 2: Documents
    parsed_docs: list[ParsedDocument] = []
    doc_findings: list[Finding] = []
    doc_details: list[str] = []

    parent_docs_by_slot = {doc["slot"]: doc for doc in parent_bundle.get("documents", [])}

    # Identify all active slots
    all_known_slots = set(slot for slot, _ in REQUIRED_DOCUMENTS) | set(parent_docs_by_slot) | set(changed_slots)

    # Re-evaluate or preserve parsed documents slot by slot
    for slot, label in REQUIRED_DOCUMENTS:
        if slot in changed_slots and new_documents.get(slot):
            # Newly uploaded or replacement document — RE-RUN PARSING & EXTRACTION
            doc_file = new_documents[slot]
            affected_rules.append(f"Document parsing & classification ({SLOT_LABEL.get(slot, slot)})")
            parsed = document_parser.parse_document(doc_file.filename, doc_file.content)
            status, text = parsed.status, parsed.text
            document_type = "unreadable"
            fields: dict[str, str] = {}
            method = "none"
            confidence = 0.0
            warnings = list(parsed.warnings)

            if status == "parsed":
                document_type = ai_extractor.classify_document("", text)
                expected = SLOT_KIND.get(slot, "")
                if document_type not in {expected, "unknown"}:
                    doc_findings.append(
                        Finding(
                            code="wrong_document",
                            severity="pending",
                            stage="documents",
                            message=(
                                f"{doc_file.filename} was uploaded as a {KIND_LABEL.get(expected, 'document')}, "
                                f"but it reads like a {KIND_LABEL.get(document_type, 'different document')}."
                            ),
                            vendor_message=(
                                f"The file {doc_file.filename} does not look like a "
                                f"{KIND_LABEL.get(expected, 'the requested document')}. "
                                f"Please upload the {KIND_LABEL.get(expected, 'correct document')}."
                            ),
                        )
                    )
                    document_type = "wrong_slot"
                else:
                    fields, method, warnings, confidence = ai_extractor.extract_fields(
                        text, layout_fields=parsed.fields()
                    )
                    warnings = list(parsed.warnings) + warnings
                    if not any((val or "").strip() for val in fields.values()):
                        doc_findings.append(
                            Finding(
                                code="extraction_unclear",
                                severity="pending",
                                stage="documents",
                                message=f"No labelled fields could be read from {doc_file.filename}.",
                                vendor_message=(
                                    f"We could not read the details in {doc_file.filename}. "
                                    "Please upload a text-based PDF or a clear scan."
                                ),
                            )
                        )
            else:
                doc_findings.append(
                    Finding(
                        code="unreadable_document",
                        severity="pending",
                        stage="documents",
                        message=f"{doc_file.filename} could not be parsed as text.",
                        vendor_message=(
                            f"The file {doc_file.filename} could not be read. "
                            "Please upload a readable PDF or a plain text copy."
                        ),
                    )
                )

            if parsed.ocr_pages and 0.0 < parsed.ocr_confidence < LOW_OCR_CONFIDENCE:
                doc_findings.append(
                    Finding(
                        code="ocr_low_confidence",
                        severity="warn",
                        stage="documents",
                        message=(
                            f"{doc_file.filename} was scanned with Gemini Flash at mean confidence "
                            f"{parsed.ocr_confidence:.2f}; the values read from it may need a human eye."
                        ),
                        vendor_message=None,
                    )
                )

            parsed_doc = ParsedDocument(
                slot=slot,
                filename=doc_file.filename,
                parse_status=status,
                text_excerpt=text[:5000],
                document_type=document_type,
                fields=fields,
                method=method,
                confidence=confidence,
                warnings=warnings,
                content=doc_file.content,
            )
            parsed_docs.append(parsed_doc)
            doc_details.append(
                f"Re-evaluated {doc_file.filename} ({slot}): {status}, {document_type}, "
                f"read via {SOURCES.get(parsed.source, parsed.source)}, fields={sum(1 for v in fields.values() if v)}"
            )
        elif slot in parent_docs_by_slot:
            # Unchanged document — PRESERVED from parent run
            p_doc = parent_docs_by_slot[slot]
            skipped_rules.append(f"Document parsing ({SLOT_LABEL.get(slot, slot)}: preserved from {parent_ref})")
            parsed_doc = ParsedDocument(
                slot=slot,
                filename=p_doc["filename"],
                parse_status=p_doc["parse_status"],
                text_excerpt=p_doc.get("text_excerpt", ""),
                document_type=p_doc.get("document_type", ""),
                fields=p_doc.get("fields", {}),
                method=p_doc.get("method", ""),
                confidence=float(p_doc.get("confidence", 0.0)),
                warnings=p_doc.get("warnings", []),
                content=p_doc.get("content") or b"",
            )
            parsed_docs.append(parsed_doc)
            # Carry forward any uncorrected findings for this unchanged slot
            doc_findings.extend(_carried_document_findings(parent_bundle, slot, p_doc))
            doc_details.append(f"Preserved {p_doc['filename']} ({slot}) from original run (0 extraction calls).")
        else:
            # Document missing
            affected_rules.append(f"Required document check ({SLOT_LABEL.get(slot, slot)})")
            doc_findings.append(
                Finding(
                    code="missing_document",
                    severity="pending",
                    stage="documents",
                    message=f"{label} was not uploaded for slot '{slot}'.",
                    vendor_message=f"Please upload the {label.lower()}.",
                )
            )
            doc_details.append(f"Missing: {label} (slot '{slot}')")

    findings.extend(doc_findings)
    if changed_slots:
        doc_summary = (
            f"Selective re-run: {len(changed_slots)} uploaded document(s) parsed, "
            f"{len(parsed_docs) - len(changed_slots)} document(s) preserved from {parent_ref}."
        )
    else:
        doc_summary = f"All {len(parsed_docs)} document(s) preserved from {parent_ref} (0 OCR/extraction calls)."

    record("documents", "Documents processed", doc_findings, doc_summary, doc_details)

    # ---------------------------------------------------------------- Stage 3: Field validation
    val_findings: list[Finding] = []
    if changed_fields:
        affected_rules.append(f"Field validation on modified fields ({', '.join(sorted(changed_fields))})")
        val_findings = field_validator.validate_submission(updated_submission)
        findings.extend(val_findings)
        failed_checks = [f for f in val_findings if f.severity in {"pending", "reject"}]
        notes = [f for f in val_findings if f.severity == "warn"]
        val_summary = (
            f"Selective re-validation of {len(changed_fields)} modified field(s): "
            + ("all passed." if not failed_checks else f"{len(failed_checks)} issue(s) found.")
        )
        val_details = (
            [f.message for f in failed_checks]
            + [f.message for f in notes]
            + [f"Re-validated modified fields: {', '.join(sorted(changed_fields))}."]
        )
    else:
        skipped_rules.append(f"Field validation (preserved from {parent_ref}: no form fields changed)")
        # Reuse original validation findings
        for f in parent_bundle.get("findings", []):
            if f.get("stage") == "validate":
                val_findings.append(
                    Finding(
                        code=f["code"],
                        severity=f["severity"],
                        stage=f["stage"],
                        message=f["message"],
                        vendor_message=f.get("vendor_message"),
                    )
                )
        findings.extend(val_findings)
        failed_checks = [f for f in val_findings if f.severity in {"pending", "reject"}]
        val_summary = "Field validation skipped: form fields unchanged; previous checks preserved."
        val_details = ["Form values unchanged from original run (0 validation calls)."]

    record("validate", "Field validation", val_findings, val_summary, val_details)

    # ---------------------------------------------------------------- Stage 4: Cross-document verification
    # Identify if cross-verification inputs were affected
    cross_incorp_affected = "incorporation" in changed_slots or bool(
        changed_fields.intersection({"legal_name", "registration_number"})
    )
    cross_tax_affected = "tax_certificate" in changed_slots or bool(
        changed_fields.intersection({"legal_name", "tax_id"})
    )
    cross_bank_affected = "bank_proof" in changed_slots or bool(
        changed_fields.intersection({"bank_holder_name", "bank_name", "account_number", "bank_code"})
    )
    # Payout country is compared with the vendor country and the tax ID's home country.
    cross_payout_country_affected = bool(changed_fields.intersection({"bank_country", "country", "tax_id"}))
    cross_state_affected = bool(changed_fields.intersection({"tax_id", "address"}))
    cross_jurisdiction_affected = (
        bool(changed_fields.intersection({"country", "bank_country"})) or bool(changed_slots)
    )

    any_cross_affected = (
        cross_incorp_affected
        or cross_tax_affected
        or cross_bank_affected
        or cross_state_affected
        or cross_payout_country_affected
        or cross_jurisdiction_affected
    )

    if any_cross_affected:
        affected_cross_parts = []
        if cross_incorp_affected:
            affected_cross_parts.append("incorporation certificate")
        if cross_tax_affected:
            affected_cross_parts.append("tax certificate")
        if cross_bank_affected:
            affected_cross_parts.append("bank proof")
        if cross_state_affected:
            affected_cross_parts.append("state vs address")
        if cross_payout_country_affected or cross_jurisdiction_affected:
            affected_cross_parts.append("payout country")
        affected_rules.append(f"Cross-document verification ({', '.join(affected_cross_parts)})")

        cross_findings, cross_checks = cross_verification.cross_check(updated_submission, parsed_docs)
        checks.extend(cross_checks)
        findings.extend(cross_findings)
        matched = sum(1 for check in cross_checks if check.result == "match")
        compared = sum(1 for check in cross_checks if check.result != "missing")
        cross_summary = (
            f"Selective cross-check ({', '.join(affected_cross_parts)}): {matched} of {compared} values matched."
        )
        cross_details = [
            f"{check.field}: {check.result or 'not compared'} "
            f"(form '{check.submitted}' vs {check.source} '{check.extracted}')"
            for check in cross_checks
        ]
    else:
        skipped_rules.append(f"Cross-document verification (preserved from {parent_ref}: documents and checked fields unchanged)")
        # Reuse original checks and cross findings
        for c in parent_bundle.get("checks", []):
            checks.append(
                FieldCheck(
                    field=c["field"],
                    submitted=c.get("submitted", ""),
                    extracted=c.get("extracted", ""),
                    source=c.get("source", ""),
                    result=c.get("result", ""),
                    score=c.get("score", ""),
                )
            )
        cross_findings = []
        for f in parent_bundle.get("findings", []):
            if f.get("stage") == "cross":
                finding = Finding(
                    code=f["code"],
                    severity=f["severity"],
                    stage=f["stage"],
                    message=f["message"],
                    vendor_message=f.get("vendor_message"),
                )
                cross_findings.append(finding)
                findings.append(finding)
        cross_summary = "Cross-document verification skipped: inputs unchanged; previous results preserved."
        cross_details = ["No document replacements or identity fields modified."]

    record("cross", "Cross-document verification", cross_findings, cross_summary, cross_details)

    # ---------------------------------------------------------------- Stage 5: Duplicate detection
    dup_affected = bool(changed_fields.intersection(DUPLICATE_KEYS))
    if dup_affected:
        affected_rules.append(f"Duplicate & blocklist detection (identifiers: {', '.join(sorted(changed_fields.intersection(DUPLICATE_KEYS)))})")
        dup_findings, duplicate_of = duplicate_detector.find_duplicates(
            updated_submission, existing, exclude_vendor_id=own_vendor_id
        )
        blocked = duplicate_detector.blocked_vendor(
            updated_submission, existing, exclude_vendor_id=own_vendor_id
        )
        if blocked is not None:
            dup_findings.append(blocked)
        findings.extend(dup_findings)
        dup_summary = (
            "No duplicate or blocked records found for updated identifiers."
            if not dup_findings
            else f"{len(dup_findings)} registry note(s) raised."
        )
        dup_details = [f"{item.code}: {item.message}" for item in dup_findings] or [
            "Checked updated tax ID, CIN and payout account against registry."
        ]
    else:
        skipped_rules.append(f"Duplicate detection (preserved from {parent_ref}: tax ID, CIN, and bank account unchanged)")
        duplicate_of = parent_sub.get("duplicate_of")
        dup_findings = []
        for f in parent_bundle.get("findings", []):
            if f.get("stage") == "duplicate":
                finding = Finding(
                    code=f["code"],
                    severity=f["severity"],
                    stage=f["stage"],
                    message=f["message"],
                    vendor_message=f.get("vendor_message"),
                )
                dup_findings.append(finding)
                findings.append(finding)
        dup_summary = f"Duplicate detection skipped: registry identifiers unchanged from {parent_ref}."
        dup_details = ["Reused registry verification from original run (0 ms)."]

    record("duplicate", "Duplicate detection", dup_findings, dup_summary, dup_details)

    # ---------------------------------------------------------------- Stage 6: Decision
    affected_rules.append("Decision engine aggregation")
    status, headline, summary = decision_engine.decide(findings)
    pending_actions = [f.vendor_message for f in findings if f.severity in {"pending", "reject"} and f.vendor_message]
    decision_details = [
        f"Rejecting rule: {f.code}" if f.severity == "reject" else f"Review rule: {f.code}"
        for f in findings
        if f.severity in {"pending", "reject"}
    ] or ["All affected and preserved deterministic rules passed."]
    record(
        "decision",
        "Decision generated",
        [f for f in findings if f.severity in {"pending", "reject"}],
        f"{status.upper()}: {headline}",
        decision_details,
    )

    # ---------------------------------------------------------------- Stage 7: Notification & Audit
    affected_rules.append("Notification draft & audit lineage")
    subject, body = notification_generator.draft_notification(updated_submission, status, findings)
    audit_summary = (
        f"Reply run logged. Status: {status.upper()} (transition from {parent_sub['status'].upper()}). "
        f"{len(affected_rules)} rules re-evaluated, {len(skipped_rules)} preserved."
    )
    record(
        "audit",
        "Notification and audit log",
        [],
        audit_summary,
        [subject, f"Linked to parent submission {parent_ref}", f"Rule engine {rule_version.RULE_ENGINE_VERSION}"],
    )

    methods = {doc.method for doc in parsed_docs if doc.method not in {"", "none"}}
    sources: list[str] = []
    if any("rules" in method for method in methods):
        sources.append("labels")
    if any("layout" in method for method in methods):
        sources.append("OCR layout")
    if any("model" in method for method in methods):
        sources.append("model")
    extraction_method = " + ".join(sources) or "none"

    result = RunResult(
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
        submission=updated_submission,
        scenario_id=parent_sub.get("scenario_id"),
        rule_engine_version=rule_version.RULE_ENGINE_VERSION,
        parent_id=parent_id,
    )

    diff = compute_diff(
        parent_bundle=parent_bundle,
        new_result=result,
        changed_fields=changed_fields,
        new_documents=new_documents,
        affected_rules=affected_rules,
        skipped_rules=skipped_rules,
    )

    return result, diff
