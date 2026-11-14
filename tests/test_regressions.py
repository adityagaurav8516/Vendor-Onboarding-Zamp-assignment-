"""Regression tests for the reply-loop, registry, parser and batch bugs."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import replace

import pytest

from backend import batch_runner, layout_parser, ocr_engine, submission_loader
from backend.batch_runner import parse_csv, row_to_submission, run_batch
from backend.duplicate_detector import find_duplicates
from backend.field_validator import validate_submission
from backend.pipeline import run_pipeline
from backend.reply_runner import bundle_to_submission, run_vendor_reply
from database import db
from database.models import DocumentFile, ExistingVendor, Submission
from tests.factories import make_documents, make_vendor
import random


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "regress.db")
    monkeypatch.setattr(db, "_READY", False)
    db.init_db()


def _save(result):
    submission_id, _ = db.save_run(result)
    return db.get_submission_bundle(submission_id)


def _vendor(seed=3) -> Submission:
    return make_vendor(random.Random(seed))


def _codes(result):
    return {f.code for f in result.findings}


# ------------------------------------------------------------ reply loop


def test_reply_keeps_unreadable_document_finding_on_unrelated_change():
    sub = _vendor()
    docs = [d for d in make_documents(sub) if d.slot != "bank_proof"]
    docs.append(DocumentFile("bank_proof", "cheque.pdf", b"%PDF-1.4 not really a pdf"))
    first = run_pipeline(sub, docs, db.load_registry())
    assert "unreadable_document" in _codes(first)
    bundle = _save(first)

    updated = bundle_to_submission(bundle)
    updated.contact_name = "Someone Else"
    reply, _ = run_vendor_reply(bundle, updated, {}, db.load_registry())
    assert "unreadable_document" in _codes(reply)
    assert reply.status == "pending"


def test_reply_keeps_wrong_slot_finding_on_unrelated_change():
    sub = _vendor()
    docs = {d.slot: d for d in make_documents(sub)}
    wrong = DocumentFile("bank_proof", "oops.txt", docs["tax_certificate"].content)
    first = run_pipeline(sub, [docs["incorporation"], docs["tax_certificate"], wrong], db.load_registry())
    assert "wrong_document" in _codes(first)
    bundle = _save(first)

    updated = bundle_to_submission(bundle)
    updated.trade_name = "Renamed Trading"
    reply, _ = run_vendor_reply(bundle, updated, {}, db.load_registry())
    assert "wrong_document" in _codes(reply)
    assert reply.status == "pending"


def test_reply_correcting_bank_country_clears_cross_border_bank():
    sub = replace(_vendor(), bank_country="United States")
    first = run_pipeline(sub, make_documents(sub), db.load_registry())
    assert "cross_border_bank" in _codes(first)
    bundle = _save(first)

    updated = bundle_to_submission(bundle)
    updated.bank_country = "India"
    reply, diff = run_vendor_reply(bundle, updated, {}, db.load_registry())
    assert "cross_border_bank" not in _codes(reply)
    assert any(f.code == "cross_border_bank" for f in diff.resolved_findings)
    assert reply.status == "approved"


def test_reply_is_not_matched_against_its_own_record():
    sub = _vendor()
    docs = [d for d in make_documents(sub) if d.slot != "tax_certificate"]
    first = run_pipeline(sub, docs, db.load_registry())
    assert first.status == "pending"
    bundle = _save(first)

    updated = bundle_to_submission(bundle)
    updated.account_number = updated.account_number[:-1] + "0"
    reply, _ = run_vendor_reply(bundle, updated, {}, db.load_registry())
    assert "prior_submission" not in _codes(reply)
    assert reply.duplicate_of is None


def test_reply_on_approved_record_does_not_trigger_payout_change_reject():
    sub = _vendor()
    first = run_pipeline(sub, make_documents(sub), db.load_registry())
    assert first.status == "approved"
    bundle = _save(first)

    updated = bundle_to_submission(bundle)
    updated.account_number = updated.account_number[:-1] + "0"
    reply, _ = run_vendor_reply(bundle, updated, {}, db.load_registry())
    assert "duplicate_bank_change" not in _codes(reply)
    assert reply.status != "rejected"


# ------------------------------------------------------------ registry


def _registry_vendor(status: str) -> ExistingVendor:
    return ExistingVendor(
        id=7, legal_name="Acme Pvt Ltd", country="India", tax_id="27AABCA1234C1Z5",
        registration_number="U11111MH2010PTC111111", account_number="1234567890",
        bank_code="HDFC0001234", contact_email="a@acme.in", status=status,
    )


@pytest.mark.parametrize("status", ["approved", "Approved", "APPROVED"])
def test_registry_status_is_case_insensitive(status):
    sub = Submission(
        legal_name="Acme Pvt Ltd", country="India", tax_id="27AABCA1234C1Z5",
        account_number="9999999999", bank_code="HDFC0001234",
    )
    findings, duplicate_of = find_duplicates(sub, [_registry_vendor(status)])
    assert [f.code for f in findings] == ["duplicate_bank_change"]
    assert duplicate_of == 7


def test_find_duplicates_can_skip_a_vendor_record():
    sub = Submission(legal_name="Acme Pvt Ltd", country="India", tax_id="27AABCA1234C1Z5")
    findings, duplicate_of = find_duplicates(sub, [_registry_vendor("pending")], exclude_vendor_id=7)
    assert findings == [] and duplicate_of is None


# ------------------------------------------------------------ batch


def _sample_row(index=0) -> dict:
    row = dict(batch_runner._SAMPLE_ROWS[index])
    row["_row"] = index + 2
    return row


def test_same_company_twice_in_one_batch_is_not_approved_twice():
    results = run_batch([_sample_row(0), _sample_row(0)], "csv", existing=db.load_registry())
    assert results[0].status == "approved"
    assert results[1].status != "approved"


def test_same_company_twice_with_registry_loaded_by_runner():
    results = run_batch([_sample_row(1), _sample_row(1)], "csv")
    assert [r.status for r in results][0] == "approved"
    assert results[1].status != "approved"


def test_csv_rows_with_extra_columns_do_not_crash():
    header = ",".join(batch_runner.SUBMISSION_FIELDS)
    row = ",".join(str(_sample_row(0)[f]).replace(",", " ") for f in batch_runner.SUBMISSION_FIELDS)
    rows = parse_csv(f"{header}\n{row},unexpected,another\n".encode())
    assert len(rows) == 1
    assert None not in rows[0]
    results = run_batch(rows, "csv", existing=db.load_registry())
    assert results[0].status != "error"


def test_row_to_submission_handles_null_and_numbers():
    sub = row_to_submission({"legal_name": "X Ltd", "address": None, "contact_phone": 9876543210, "bank_name": 110019.0})
    assert sub.address == ""
    assert sub.contact_phone == "9876543210"
    assert sub.bank_name == "110019"


def test_zip_with_null_and_numeric_values_does_not_crash():
    base = {k: batch_runner._SAMPLE_ROWS[0][k] for k in batch_runner.SUBMISSION_FIELDS}
    base["trade_name"] = None
    base["address"] = "11 Nehru Place, New Delhi 110019"
    base["contact_phone"] = 9876500001
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("v.json", json.dumps({"submission": base}))
    items = batch_runner.parse_zip(buf.getvalue())
    results = run_batch(items, "zip", existing=db.load_registry())
    assert results[0].status != "error", results[0].error


def test_sample_csv_labels_match_outcomes():
    rows = parse_csv(batch_runner.sample_csv_bytes())
    results = run_batch(rows, "csv", existing=db.load_registry())
    by_name = {r.vendor_name: r for r in results}
    assert all(r.status != "error" for r in results), [r.error for r in results if r.error]
    for name in ("GlobalMart Retail Pvt Ltd", "Orion Infra Pvt Ltd"):
        assert by_name[name].status == "approved", (name, by_name[name].top_code)
    assert sum(1 for r in results[:15] if r.status == "approved") == 15
    assert by_name["Bluepeak Traders Pvt Ltd"].status == "rejected"


def test_primary_rule_is_the_decisive_rule():
    rows = parse_csv(batch_runner.sample_csv_bytes())
    bluepeak = next(r for r in rows if r["legal_name"].startswith("Bluepeak"))
    from test_data.seed import ensure_seed
    ensure_seed()
    results = run_batch([bluepeak], "csv", existing=db.load_registry())
    assert results[0].status == "rejected"
    assert results[0].top_code == "blocked_vendor"


# ------------------------------------------------------------ parsers


@pytest.mark.parametrize(
    "label,expected",
    [("Bank Code", "bank_code"), ("Bank Name", "bank_name"), ("Bank", "bank_name"), ("IFSC", "bank_code")],
)
def test_bank_code_label_is_not_read_as_bank_name(label, expected):
    assert layout_parser.match_label(label) == expected


def test_gemini_rows_do_not_overlap_when_transcript_has_blank_lines():
    payload = {"candidates": [{"content": {"parts": [{"text": json.dumps({
        "text": "Cancelled Cheque\n\nAccount number: 123456789\n\n\nIFSC: HDFC0001234",
        "fields": {"account_number": "123456789", "bank_code": "HDFC0001234"},
    })}]}}]}
    words = ocr_engine._normalize_gemini(payload, 0)
    ys = [w.y0 for w in words]
    assert len(ys) == len(set(ys)), ys
    assert len(words) == 5


def test_us_phone_with_plus_one_is_valid():
    sub = replace(_vendor(), country="United States", contact_phone="+1 (415) 555-0001")
    assert "bad_phone" not in {f.code for f in validate_submission(sub)}
    bad = replace(sub, contact_phone="+1 415 555")
    assert "bad_phone" in {f.code for f in validate_submission(bad)}


def test_json_null_becomes_empty_text():
    sub = submission_loader.submission_from_payload({"legal_name": "A Ltd", "trade_name": None, "address": 110019})
    assert sub.trade_name == ""
    assert sub.address == "110019"
