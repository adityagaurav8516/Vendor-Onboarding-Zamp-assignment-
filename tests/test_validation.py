from __future__ import annotations

import random

from backend.decision_engine import decide
from backend.duplicate_detector import blocked_vendor, find_duplicates
from backend.field_validator import (
    gstin_checksum,
    is_ein_shape,
    is_valid_ein,
    is_valid_gstin,
    validate_submission,
)
from backend.names import name_score, normalize_name
from backend.notification_generator import draft_notification
from database.models import Finding, Submission
from tests.factories import gstin_for, make_vendor
from test_data.seed import seed_vendors


def codes(findings: list[Finding]) -> set[str]:
    return {finding.code for finding in findings}


def test_gstin_checksum_accepts_valid_and_rejects_typo():
    valid = gstin_for("07", "AABCA1234C")
    assert is_valid_gstin(valid)
    typo = valid[:-1] + ("A" if valid[-1] != "A" else "B")
    assert not is_valid_gstin(typo)
    assert gstin_checksum(valid[:14]) == valid[14]


def test_gstin_state_prefix_must_exist():
    assert is_valid_gstin(gstin_for("27", "AABCX5678D"))
    unknown_state = "99" + gstin_for("07", "AABCA1234C")[2:]
    assert not is_valid_gstin(unknown_state)


def test_ein_shape_and_unassigned_prefix():
    assert is_ein_shape("12-3456789")
    assert is_valid_ein("12-3456789")
    assert not is_valid_ein("00-1234567")
    assert not is_ein_shape("12345")


def test_ifsc_bank_prefix_must_match_bank_name():
    submission = make_vendor(random.Random(7))
    submission.bank_code = "HDFC0001234"
    submission.bank_name = "ICICI Bank"
    found = codes(validate_submission(submission))
    assert "bank_name_mismatch" in found


def test_phone_formats():
    base = make_vendor(random.Random(11))
    base.contact_phone = "+91 98765 43210"
    assert "bad_phone" not in codes(validate_submission(base))
    base.contact_phone = "12345"
    assert "bad_phone" in codes(validate_submission(base))
    base.contact_phone = "5876543210"
    assert "bad_phone" in codes(validate_submission(base))


def test_bad_cin_is_pending():
    submission = make_vendor(random.Random(13))
    submission.registration_number = "NOT-A-CIN"
    assert "bad_registration" in codes(validate_submission(submission))


def test_missing_required_field_is_pending():
    submission = make_vendor(random.Random(17))
    submission.contact_email = ""
    assert "missing_fields" in codes(validate_submission(submission))


def test_name_normalization_handles_company_suffixes():
    assert name_score("ABC Technologies Private Limited", "ABC Technologies Pvt. Ltd.") >= 92
    assert normalize_name("ABC Technologies Pvt. Ltd.") == "abc technologies"
    assert name_score("Zenith Components", "Orbit Logistics") < 60


def test_decision_order_reject_beats_pending():
    findings = [
        Finding("a", "pending", "validate", "minor"),
        Finding("b", "reject", "cross", "identity"),
    ]
    status, headline, summary = decide(findings)
    assert status == "rejected"
    assert headline
    assert "identity" in summary


def test_decision_warn_does_not_block_approval():
    assert decide([Finding("note", "warn", "validate", "free email")])[0] == "approved"


def test_pending_notification_lists_actionable_items():
    submission = Submission(legal_name="ABC Technologies Pvt Ltd")
    finding = Finding(
        "missing_document",
        "pending",
        "documents",
        "GST certificate missing.",
        "Please upload the GST certificate.",
    )
    subject, body = draft_notification(submission, "pending", [finding])
    assert "Action needed" in subject
    assert "Please upload the GST certificate." in body


def test_duplicate_bank_change_is_rejected():
    submission = make_vendor(random.Random(19))
    zenith = seed_vendors()[0]
    submission.tax_id = zenith.tax_id
    submission.account_number = "779999999999"
    findings, duplicate_of = find_duplicates(submission, seed_vendors())
    assert "duplicate_bank_change" in codes(findings)
    assert duplicate_of == zenith.id


def test_same_tax_id_and_same_account_is_a_duplicate_request():
    submission = make_vendor(random.Random(23))
    zenith = seed_vendors()[0]
    submission.tax_id = zenith.tax_id
    submission.account_number = zenith.account_number
    submission.bank_code = zenith.bank_code
    findings, _ = find_duplicates(submission, seed_vendors())
    assert "duplicate_same_account" in codes(findings)


def test_shared_account_under_new_entity_needs_review():
    submission = make_vendor(random.Random(29))
    zenith = seed_vendors()[0]
    submission.account_number = zenith.account_number
    submission.bank_code = zenith.bank_code
    findings, _ = find_duplicates(submission, seed_vendors())
    shared = [finding for finding in findings if finding.code == "shared_bank_account"]
    assert shared
    assert shared[0].severity == "pending"


def test_blocked_vendor_is_rejected():
    submission = make_vendor(random.Random(31))
    blocked = seed_vendors()[2]
    submission.tax_id = blocked.tax_id
    finding = blocked_vendor(submission, seed_vendors())
    assert finding is not None
    assert finding.severity == "reject"
