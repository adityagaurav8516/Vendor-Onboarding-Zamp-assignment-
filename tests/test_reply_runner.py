from __future__ import annotations

from backend.pipeline import run_pipeline
from backend.reply_runner import (
    bundle_to_submission,
    identify_changes,
    run_vendor_reply,
)
from database import db
from database.models import DocumentFile, Submission
from test_data import synthetic
from test_data.scenarios import (
    SCENARIOS_BY_ID,
    _BAD_GSTIN,
    _MISSING_GST,
    _pack,
    build_documents,
    to_submission,
)


def test_identify_changes():
    original = Submission(legal_name="Alpha Ltd", tax_id="07AAAAA0000A1Z5")
    updated = Submission(legal_name="Alpha Ltd", tax_id="07AAAAA0000A1Z8")
    new_docs = {"tax_certificate": DocumentFile("tax_certificate", "tax.pdf", b"pdf content")}

    changed_fields, changed_slots = identify_changes(original, updated, new_docs)
    assert changed_fields == {"tax_id"}
    assert changed_slots == {"tax_certificate"}


def test_reply_missing_document_to_approved(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reply_missing_doc.db")
    monkeypatch.setattr(db, "_READY", False)
    db.init_db()

    # Step 1: Run scenario missing_gst (fails because tax_certificate is missing)
    scenario = SCENARIOS_BY_ID["missing_gst"]
    original_result = run_pipeline(
        to_submission(scenario), build_documents(scenario), db.load_registry(), scenario_id=scenario.id
    )
    assert original_result.status == "pending"
    assert any(f.code == "missing_document" for f in original_result.findings)

    parent_id, parent_ref = db.save_run(original_result)
    parent_bundle = db.get_submission_bundle(parent_id)

    # Step 2: Vendor replies by uploading the missing GST certificate
    pack = _pack(_MISSING_GST)
    gst_bytes = synthetic.render_pdf(pack["tax_certificate"])
    new_docs = {
        "tax_certificate": DocumentFile("tax_certificate", "sundara-gst-cert.pdf", gst_bytes)
    }

    updated_sub = bundle_to_submission(parent_bundle)
    reply_result, diff = run_vendor_reply(
        parent_bundle=parent_bundle,
        updated_submission=updated_sub,
        new_documents=new_docs,
        existing=db.load_registry(),
    )

    # Step 3: Assert workflow transition and selective rule re-evaluation
    assert reply_result.status == "approved"
    assert diff.before_status == "pending"
    assert diff.after_status == "approved"
    assert any(f.code == "missing_document" for f in diff.resolved_findings)
    assert not diff.remaining_findings

    # Check document diff
    tax_diff = next(d for d in diff.document_diffs if d.slot == "tax_certificate")
    assert tax_diff.action == "added"
    assert tax_diff.new_filename == "sundara-gst-cert.pdf"

    # Selective execution check: incorporation and bank proof are preserved
    assert any("Document parsing & classification" in r and "Tax" in r for r in diff.affected_rules)
    assert any("preserved" in r for r in diff.skipped_rules)


def test_reply_corrected_gstin_checksum_to_approved(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reply_gstin_fix.db")
    monkeypatch.setattr(db, "_READY", False)
    db.init_db()

    # Step 1: Run invalid_gstin scenario
    scenario = SCENARIOS_BY_ID["invalid_gstin"]
    original_result = run_pipeline(
        to_submission(scenario), build_documents(scenario), db.load_registry(), scenario_id=scenario.id
    )
    assert original_result.status == "pending"
    assert any(f.code == "tax_bad_checksum" for f in original_result.findings)

    parent_id, _ = db.save_run(original_result)
    parent_bundle = db.get_submission_bundle(parent_id)

    # Step 2: Correct GSTIN typo (replace check digit 'A' with valid '5')
    updated_sub = bundle_to_submission(parent_bundle)
    correct_tax = updated_sub.tax_id[:-1] + "5"
    updated_sub.tax_id = correct_tax

    # Also update the document synthetic content for tax_certificate
    pack = _pack(_BAD_GSTIN, tax_id=correct_tax)
    tax_bytes = synthetic.render_pdf(pack["tax_certificate"])
    new_docs = {"tax_certificate": DocumentFile("tax_certificate", "helios-tax-fixed.pdf", tax_bytes)}

    reply_result, diff = run_vendor_reply(
        parent_bundle=parent_bundle,
        updated_submission=updated_sub,
        new_documents=new_docs,
        existing=db.load_registry(),
    )

    assert reply_result.status == "approved"
    assert diff.before_status == "pending"
    assert diff.after_status == "approved"
    assert any(f.code == "tax_bad_checksum" for f in diff.resolved_findings)
    assert any(d.field == "tax_id" for d in diff.field_diffs)


def test_reply_corrected_bank_holder_name_to_approved(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reply_holder_fix.db")
    monkeypatch.setattr(db, "_READY", False)
    db.init_db()

    # Step 1: Run identity_mismatch (holder mismatch)
    scenario = SCENARIOS_BY_ID["identity_mismatch"]
    original_result = run_pipeline(
        to_submission(scenario), build_documents(scenario), db.load_registry(), scenario_id=scenario.id
    )
    assert original_result.status == "pending"
    assert any(f.code == "bank_holder_mismatch" for f in original_result.findings)

    parent_id, _ = db.save_run(original_result)
    parent_bundle = db.get_submission_bundle(parent_id)

    # Step 2: Vendor corrects bank holder name to company legal name and uploads matching bank proof
    from test_data.scenarios import _HOLDER_MISMATCH
    updated_sub = bundle_to_submission(parent_bundle)
    updated_sub.bank_holder_name = updated_sub.legal_name

    pack = _pack(_HOLDER_MISMATCH, holder=updated_sub.legal_name)
    bank_bytes = synthetic.render_pdf(pack["bank_proof"])
    new_docs = {"bank_proof": DocumentFile("bank_proof", "pqr-bank-proof-fixed.pdf", bank_bytes)}

    reply_result, diff = run_vendor_reply(
        parent_bundle=parent_bundle,
        updated_submission=updated_sub,
        new_documents=new_docs,
        existing=db.load_registry(),
    )

    assert reply_result.status == "approved"
    assert diff.before_status == "pending"
    assert diff.after_status == "approved"
    assert any(f.code == "bank_holder_mismatch" for f in diff.resolved_findings)
    assert any(d.field == "bank_holder_name" for d in diff.field_diffs)


def test_reply_persisted_in_database_with_parent_id_and_events(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reply_db_link.db")
    monkeypatch.setattr(db, "_READY", False)
    db.init_db()

    scenario = SCENARIOS_BY_ID["missing_gst"]
    original_result = run_pipeline(
        to_submission(scenario), build_documents(scenario), db.load_registry(), scenario_id=scenario.id
    )
    parent_id, parent_ref = db.save_run(original_result)
    parent_bundle = db.get_submission_bundle(parent_id)

    pack = _pack(_MISSING_GST)
    gst_bytes = synthetic.render_pdf(pack["tax_certificate"])
    new_docs = {"tax_certificate": DocumentFile("tax_certificate", "sundara-tax.pdf", gst_bytes)}
    updated_sub = bundle_to_submission(parent_bundle)

    reply_result, diff = run_vendor_reply(
        parent_bundle=parent_bundle,
        updated_submission=updated_sub,
        new_documents=new_docs,
        existing=db.load_registry(),
    )

    reply_id, reply_ref = db.save_run(reply_result)

    # Verify link on child
    reply_bundle = db.get_submission_bundle(reply_id)
    assert reply_bundle["submission"]["parent_id"] == parent_id
    assert reply_bundle["parent"]["id"] == parent_id
    assert reply_bundle["parent"]["reference"] == parent_ref

    # Verify link on parent
    updated_parent_bundle = db.get_submission_bundle(parent_id)
    assert len(updated_parent_bundle["replies"]) == 1
    assert updated_parent_bundle["replies"][0]["id"] == reply_id
    assert updated_parent_bundle["replies"][0]["reference"] == reply_ref

    # Verify audit events
    reply_events = [e["event"] for e in reply_bundle["events"]]
    parent_events = [e["event"] for e in updated_parent_bundle["events"]]
    assert "vendor_reply" in reply_events
    assert "reply_received" in parent_events


def test_reply_partial_fix_remains_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reply_partial.db")
    monkeypatch.setattr(db, "_READY", False)
    db.init_db()

    # Step 1: Create a submission with 2 issues: missing tax cert AND invalid phone
    scenario = SCENARIOS_BY_ID["missing_gst"]
    sub = to_submission(scenario)
    sub.contact_phone = "123"  # bad phone format
    original_result = run_pipeline(sub, build_documents(scenario), db.load_registry())
    assert original_result.status == "pending"
    codes = {f.code for f in original_result.findings}
    assert "missing_document" in codes
    assert "bad_phone" in codes

    parent_id, _ = db.save_run(original_result)
    parent_bundle = db.get_submission_bundle(parent_id)

    # Step 2: Vendor uploads the missing tax cert, but leaves bad_phone untouched
    pack = _pack(_MISSING_GST)
    gst_bytes = synthetic.render_pdf(pack["tax_certificate"])
    new_docs = {"tax_certificate": DocumentFile("tax_certificate", "tax.pdf", gst_bytes)}
    updated_sub = bundle_to_submission(parent_bundle)

    reply_result, diff = run_vendor_reply(
        parent_bundle=parent_bundle,
        updated_submission=updated_sub,
        new_documents=new_docs,
        existing=db.load_registry(),
    )

    # Missing document is resolved, but bad_phone remains
    assert reply_result.status == "pending"
    assert diff.before_status == "pending"
    assert diff.after_status == "pending"
    resolved_codes = {f.code for f in diff.resolved_findings}
    remaining_codes = {f.code for f in diff.remaining_findings}
    assert "missing_document" in resolved_codes
    assert "bad_phone" in remaining_codes

