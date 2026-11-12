from __future__ import annotations

import random

import pytest

from backend.pipeline import run_pipeline
from database import db
from database.models import DocumentFile, Submission
from test_data.scenarios import SCENARIOS, SCENARIOS_BY_ID, build_documents, to_submission
from test_data.seed import seed_vendors
from tests import factories

DECISIONS = {"approved", "pending", "rejected"}
REQUIRED_SLOTS = {"incorporation", "tax_certificate", "bank_proof"}


def run(submission, documents, registry=None):
    return run_pipeline(submission, documents, registry if registry is not None else seed_vendors())


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.id)
def test_demo_scenario_contract(scenario):
    result = run(to_submission(scenario), build_documents(scenario))
    codes = {finding.code for finding in result.findings}
    assert result.status == scenario.expected_status
    assert set(scenario.expected_codes) <= codes
    assert len(result.stages) == 7
    assert result.notification_subject
    assert result.notification_body


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.id)
def test_demo_scenarios_never_claim_wrong_document_for_demo_files(scenario):
    result = run(to_submission(scenario), build_documents(scenario))
    assert "wrong_document" not in {finding.code for finding in result.findings}


def test_generated_happy_paths_approve_with_consistent_explanations():
    rng = random.Random(20261112)
    for _ in range(25):
        submission = factories.make_vendor(rng)
        result = run(submission, factories.make_documents(submission))
        assert result.status == "approved", [finding.code for finding in result.findings]
        assert not [f for f in result.findings if f.severity in {"pending", "reject"}]
        assert result.notification_subject.startswith("Approved")
        assert {doc.slot for doc in result.documents} == REQUIRED_SLOTS


@pytest.mark.parametrize("drop_slot", sorted(REQUIRED_SLOTS))
def test_missing_document_is_pending(drop_slot):
    rng = random.Random(40)
    submission = factories.make_vendor(rng)
    result = run(
        submission,
        factories.make_documents(submission, drop=(drop_slot,)),
    )
    assert result.status == "pending"
    assert "missing_document" in {finding.code for finding in result.findings}


def test_generated_holder_mismatch_is_pending():
    rng = random.Random(41)
    submission = factories.make_vendor(rng)
    result = run(
        submission,
        factories.make_documents(submission, holder=factories.person_name(rng)),
    )
    assert result.status == "pending"
    assert "bank_holder_mismatch" in {finding.code for finding in result.findings}


def test_generated_gstin_typo_is_pending_with_checksum_reason():
    rng = random.Random(43)
    submission = factories.make_vendor(rng)
    if submission.tax_id[-1] == "A":
        submission.tax_id = submission.tax_id[:-1] + "B"
    else:
        submission.tax_id = submission.tax_id[:-1] + "A"
    result = run(submission, factories.make_documents(submission))
    codes = {finding.code for finding in result.findings}
    assert result.status == "pending"
    assert "tax_bad_checksum" in codes
    assert "wrong_document" not in codes


def test_generated_name_mismatch_on_one_document_is_pending():
    rng = random.Random(47)
    submission = factories.make_vendor(rng)
    result = run(
        submission,
        factories.make_documents(submission, legal_name="Different Holdings Pvt Ltd"),
    )
    codes = {finding.code for finding in result.findings}
    assert result.status == "pending"
    assert codes & {"identity_name_gap", "identity_document_mismatch"}


def test_generated_shared_bank_account_is_pending():
    rng = random.Random(53)
    submission = factories.make_vendor(rng)
    zenith = seed_vendors()[0]
    submission.account_number = zenith.account_number
    submission.bank_code = zenith.bank_code
    submission.bank_name = "HDFC Bank"
    result = run(submission, factories.make_documents(submission))
    assert result.status == "pending"
    assert "shared_bank_account" in {finding.code for finding in result.findings}


def test_generated_blocked_vendor_is_rejected():
    rng = random.Random(59)
    submission = factories.make_vendor(rng)
    blocked = seed_vendors()[2]
    submission.tax_id = blocked.tax_id
    submission.registration_number = blocked.registration_number
    result = run(submission, factories.make_documents(submission))
    codes = {finding.code for finding in result.findings}
    assert result.status == "rejected"
    assert "blocked_vendor" in codes
    assert result.notification_subject.startswith("Not approved")


def test_united_states_vendor_uses_ein_and_routing_rules():
    submission = Submission(
        legal_name="Northwind Supply LLC",
        country="United States",
        tax_id="12-3456789",
        registration_number="DE-6543210",
        address="1201 Market Street, Wilmington, Delaware 19801",
        contact_name="Dana Reed",
        contact_email="dana@northwindsupply.com",
        contact_phone="3025550117",
        bank_holder_name="Northwind Supply LLC",
        bank_name="Bank of America",
        account_number="123456789012",
        bank_code="021000102",
        bank_country="United States",
    )
    lines = {
        "incorporation": [
            "Certificate of Incorporation",
            "State of Delaware",
            "Registration number: DE-6543210",
            "Legal name: Northwind Supply LLC",
            "Country: United States",
        ],
        "tax_certificate": [
            "IRS Tax Registration Letter",
            "EIN: 12-3456789",
            "Legal name: Northwind Supply LLC",
            "Country: United States",
        ],
        "bank_proof": [
            "Cancelled Cheque - Bank account proof",
            "Account number: 123456789012",
            "Account holder: Northwind Supply LLC",
            "Bank name: Bank of America",
            "Routing number: 021000102",
            "Country: United States",
        ],
    }
    documents = [
        DocumentFile(slot=slot, filename=f"{slot}.txt", content="\n".join(values).encode())
        for slot, values in lines.items()
    ]
    result = run(submission, documents)
    assert result.status == "approved", [finding.code for finding in result.findings]

    submission.bank_code = "021000103"
    bad_routing = [
        DocumentFile(
            slot="bank_proof",
            filename="bank.txt",
            content="\n".join(
                [
                    "Cancelled Cheque - Bank account proof",
                    "Account number: 123456789012",
                    "Account holder: Northwind Supply LLC",
                    "Bank name: Bank of America",
                    "Routing number: 021000103",
                    "Country: United States",
                ]
            ).encode(),
        )
    ]
    result = run(submission, documents[:2] + bad_routing)
    assert result.status == "pending"
    assert "bad_bank_code" in {finding.code for finding in result.findings}


def test_unreadable_document_is_pending():
    rng = random.Random(61)
    submission = factories.make_vendor(rng)
    documents = factories.make_documents(submission)
    documents[-1] = type(documents[-1])(
        slot="bank_proof",
        filename="broken.pdf",
        content=b"not really a pdf",
    )
    result = run(submission, documents)
    assert result.status == "pending"
    assert "unreadable_document" in {finding.code for finding in result.findings}


def test_document_in_the_wrong_slot_is_pending():
    rng = random.Random(67)
    submission = factories.make_vendor(rng)
    documents = factories.make_documents(submission)
    bank = documents[-1]
    documents[1] = type(bank)(slot="tax_certificate", filename="bank-as-tax.txt", content=bank.content)
    documents.pop()
    result = run(submission, documents)
    assert result.status == "pending"
    assert "wrong_document" in {finding.code for finding in result.findings}


def test_generated_runs_follow_decision_invariants():
    rng = random.Random(71)
    registry = seed_vendors()
    for _ in range(30):
        submission = factories.make_vendor(rng)
        if rng.random() < 0.3:
            submission.bank_holder_name = factories.person_name(rng)
        if rng.random() < 0.2:
            submission = submission.__class__(**{**submission.__dict__, "contact_phone": "12345"})
        documents = factories.make_documents(submission)
        if rng.random() < 0.2:
            documents = documents[:-1]
        result = run(submission, documents, registry)
        findings = result.findings
        assert result.status in DECISIONS
        assert result.stages and result.stages[-1].key == "audit"
        if result.status == "approved":
            assert not [f for f in findings if f.severity in {"pending", "reject"}]
            assert {doc.slot for doc in result.documents} == REQUIRED_SLOTS
        if any(f.severity == "reject" for f in findings):
            assert result.status == "rejected"
        elif any(f.severity == "pending" for f in findings):
            assert result.status == "pending"


def test_sqlite_round_trip_and_override(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "vendorflow.db")
    monkeypatch.setattr(db, "_READY", False)
    db.init_db()

    scenario = SCENARIOS_BY_ID["happy_path"]
    result = run_pipeline(
        to_submission(scenario),
        build_documents(scenario),
        db.load_registry(),
        scenario_id=scenario.id,
    )
    submission_id, reference = db.save_run(result)
    assert reference == f"VF-{submission_id:04d}"

    bundle = db.get_submission_bundle(submission_id)
    assert bundle is not None
    assert bundle["submission"]["status"] == "approved"
    assert len(bundle["documents"]) == 3
    assert bundle["documents"][0]["content"].startswith(b"%PDF")
    assert len(bundle["stages"]) == 7
    assert bundle["findings"] == []

    db.apply_admin_decision(submission_id, "rejected", "Reviewer override", "qa", "rejected_override")
    assert db.get_submission(submission_id)["status"] == "rejected"
    assert db.get_submission_bundle(submission_id)["events"][-1]["event"] == "rejected_override"
    assert db.find_by_reference(reference)["status"] == "rejected"
    assert db.dashboard_counts()["rejected"] == 1
