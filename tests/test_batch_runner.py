"""Tests for backend/batch_runner.py."""

from __future__ import annotations

import csv
import io
import json
import zipfile

import pytest

from backend.batch_runner import (
    SUBMISSION_FIELDS,
    BatchParseError,
    BatchResult,
    generate_synthetic_docs,
    parse_csv,
    parse_zip,
    results_csv_bytes,
    row_to_submission,
    run_batch,
    sample_csv_bytes,
)
from backend.document_parser import parse_document
from backend.field_validator import gstin_checksum
from database import db
from database.models import DocumentFile, Submission


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    test_db = tmp_path / "batch_test.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    monkeypatch.setattr(db, "_READY", False)
    db.init_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gstin(state: str, pan: str) -> str:
    base = f"{state}{pan}1Z"
    return base + gstin_checksum(base)


def _make_csv_row(**overrides) -> dict:
    name = overrides.get("legal_name", "Test Vendor Pvt Ltd")
    tax_id = overrides.get("tax_id", _gstin("07", "AABCT1234T"))
    state_code = tax_id[:2] if len(tax_id) >= 2 else "07"
    state_addresses = {
        "07": "1 Test Street, New Delhi, Delhi 110001",
        "27": "7 BKC, Bandra, Mumbai, Maharashtra 400051",
        "29": "42 MG Road, Bengaluru, Karnataka 560001",
    }
    address = overrides.get("address", state_addresses.get(state_code, "1 Test Street, New Delhi, Delhi 110001"))
    base = {
        "legal_name": name,
        "trade_name": name.replace(" Pvt Ltd", ""),
        "country": "India",
        "tax_id": tax_id,
        "registration_number": overrides.get("registration_number", "U72900DL2015PTC999001"),
        "address": address,
        "contact_name": "Test Contact",
        "contact_email": "test@testvendor.in",
        "contact_phone": "9876543299",
        "bank_holder_name": name,
        "bank_name": "HDFC Bank",
        "account_number": "50100999999901",
        "bank_code": "HDFC0001234",
        "bank_country": "India",
    }
    base.update(overrides)
    return base


def _make_csv_bytes(*rows: dict) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=SUBMISSION_FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in SUBMISSION_FIELDS})
    return buf.getvalue().encode("utf-8")


def _make_zip_bytes(*submissions: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i, sub in enumerate(submissions, start=1):
            payload = {"submission": sub}
            zf.writestr(f"vendor_{i:02d}.json", json.dumps(payload))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# parse_csv tests
# ---------------------------------------------------------------------------


def test_parse_csv_valid():
    row1 = _make_csv_row(legal_name="Alpha Corp Pvt Ltd")
    row2 = _make_csv_row(
        legal_name="Beta Corp Pvt Ltd",
        tax_id=_gstin("27", "AABCB9999Z"),
        registration_number="U72900MH2016PTC999002",
        account_number="50100999999902",
    )
    data = _make_csv_bytes(row1, row2)
    items = parse_csv(data)
    assert len(items) == 2
    assert items[0]["legal_name"] == "Alpha Corp Pvt Ltd"
    assert items[1]["legal_name"] == "Beta Corp Pvt Ltd"
    # Row numbers are injected
    assert items[0]["_row"] == 2
    assert items[1]["_row"] == 3


def test_parse_csv_missing_columns():
    # CSV that only has two columns
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["legal_name", "country"])
    writer.writeheader()
    writer.writerow({"legal_name": "Oops", "country": "India"})
    data = buf.getvalue().encode("utf-8")
    with pytest.raises(BatchParseError, match="missing required columns"):
        parse_csv(data)


def test_parse_csv_empty_data_rows():
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=SUBMISSION_FIELDS)
    writer.writeheader()
    data = buf.getvalue().encode("utf-8")
    with pytest.raises(BatchParseError, match="no data rows"):
        parse_csv(data)


def test_parse_csv_strips_whitespace():
    row = _make_csv_row(legal_name="  Whitespace Corp  ", country=" India ")
    data = _make_csv_bytes(row)
    items = parse_csv(data)
    assert items[0]["legal_name"] == "Whitespace Corp"
    assert items[0]["country"] == "India"


# ---------------------------------------------------------------------------
# parse_zip tests
# ---------------------------------------------------------------------------


def test_parse_zip_valid():
    sub1 = _make_csv_row(legal_name="Zip Vendor One")
    sub2 = _make_csv_row(
        legal_name="Zip Vendor Two",
        tax_id=_gstin("29", "AABCZ8888Y"),
        registration_number="U72900KA2017PTC888001",
        account_number="50100888888801",
    )
    data = _make_zip_bytes(sub1, sub2)
    items = parse_zip(data)
    assert len(items) == 2
    names = {item["_name"] for item in items}
    assert "vendor_01.json" in names
    assert "vendor_02.json" in names
    for item in items:
        assert "submission" in item
        assert "legal_name" in item["submission"]


def test_parse_zip_no_json_files():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "no vendors here")
    with pytest.raises(BatchParseError, match="no .json files"):
        parse_zip(buf.getvalue())


def test_parse_zip_invalid_json():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("vendor_01.json", "{ not valid json }")
    with pytest.raises(BatchParseError, match="Invalid JSON"):
        parse_zip(buf.getvalue())


def test_parse_zip_missing_submission_key():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("vendor_01.json", json.dumps({"data": {}}))
    with pytest.raises(BatchParseError, match="missing top-level 'submission'"):
        parse_zip(buf.getvalue())


def test_parse_zip_missing_fields():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("vendor_01.json", json.dumps({"submission": {"legal_name": "Incomplete"}}))
    with pytest.raises(BatchParseError, match="missing submission fields"):
        parse_zip(buf.getvalue())


def test_parse_zip_bad_zip():
    with pytest.raises(BatchParseError, match="Not a valid ZIP"):
        parse_zip(b"this is not a zip file")


# ---------------------------------------------------------------------------
# generate_synthetic_docs tests
# ---------------------------------------------------------------------------


def test_generate_synthetic_docs_returns_three_slots():
    sub = Submission(**_make_csv_row())
    docs = generate_synthetic_docs(sub)
    assert len(docs) == 3
    slots = {d.slot for d in docs}
    assert slots == {"incorporation", "tax_certificate", "bank_proof"}


def test_generate_synthetic_docs_all_have_content():
    sub = Submission(**_make_csv_row())
    docs = generate_synthetic_docs(sub)
    for doc in docs:
        assert isinstance(doc.content, bytes)
        assert len(doc.content) > 100  # real PDF is never tiny
        assert doc.filename.endswith(".pdf")


def test_generate_synthetic_docs_contain_vendor_name():
    sub = Submission(**_make_csv_row(legal_name="Unique Vendor Name"))
    docs = generate_synthetic_docs(sub)
    for doc in docs:
        if doc.slot in ("incorporation", "tax_certificate"):
            parsed = parse_document(doc.filename, doc.content)
            assert "Unique Vendor Name" in parsed.text


# ---------------------------------------------------------------------------
# run_batch tests
# ---------------------------------------------------------------------------


def test_run_batch_csv_approved_vendor(tmp_path):
    """A well-formed India vendor with valid GSTIN should be approved."""
    row = _make_csv_row(
        legal_name="BatchApproved Pvt Ltd",
        tax_id=_gstin("07", "AABCB1111A"),
        registration_number="U72900DL2015PTC777001",
        account_number="50100777777001",
        bank_code="HDFC0007771",
    )
    results = run_batch([row], source="csv")
    assert len(results) == 1
    r = results[0]
    assert r.status == "approved"
    assert r.submission_id is not None
    assert r.reference is not None
    assert r.elapsed_ms >= 0


def test_run_batch_csv_pending_bad_gstin():
    """A vendor with a bad GSTIN checksum should be pending."""
    row = _make_csv_row(
        legal_name="BadGSTIN Corp Pvt Ltd",
        tax_id=_gstin("07", "AABCQ2222B")[:-1] + "X",  # corrupt last char
        registration_number="U72900DL2015PTC777002",
        account_number="50100777777002",
        bank_code="HDFC0007772",
    )
    results = run_batch([row], source="csv")
    assert len(results) == 1
    assert results[0].status == "pending"
    assert results[0].top_code != "—"


def test_run_batch_mixed_results():
    """Run 3 vendors (approved / pending / pending) and check counts."""
    rows = [
        _make_csv_row(
            legal_name="Mix Good Pvt Ltd",
            tax_id=_gstin("27", "AABCM1111C"),
            registration_number="U72900MH2015PTC666001",
            account_number="50100666666001",
            bank_code="HDFC0006661",
        ),
        _make_csv_row(
            legal_name="Mix Bad Pvt Ltd",
            tax_id=_gstin("27", "AABCM2222D")[:-1] + "X",
            registration_number="U72900MH2015PTC666002",
            account_number="50100666666002",
            bank_code="HDFC0006662",
        ),
        _make_csv_row(
            legal_name="Mix Unsupported Ltd",
            country="USA",
            tax_id="82-9999999",
            registration_number="EIN-829999999",
            account_number="50100666666003",
            bank_code="CHASUS33",
            bank_country="USA",
        ),
    ]
    results = run_batch(rows, source="csv")
    assert len(results) == 3
    statuses = {r.vendor_name: r.status for r in results}
    assert statuses["Mix Good Pvt Ltd"] == "approved"
    assert statuses["Mix Bad Pvt Ltd"] == "pending"
    assert statuses["Mix Unsupported Ltd"] in ("pending", "rejected")


def test_run_batch_progress_callback():
    """Progress callback is called with correct (done, total, name, status) args."""
    row = _make_csv_row(
        legal_name="Progress Test Pvt Ltd",
        tax_id=_gstin("07", "AABCP3333E"),
        registration_number="U72900DL2015PTC555001",
        account_number="50100555555001",
        bank_code="HDFC0005551",
    )
    calls = []

    def on_p(done, total, name, status):
        calls.append((done, total, name, status))

    run_batch([row], source="csv", on_progress=on_p)
    # First call: status=None (starting), second call: status=<result>
    assert len(calls) == 2
    assert calls[0][3] is None
    assert calls[1][3] in ("approved", "pending", "rejected", "error")
    assert calls[1][1] == 1  # total=1


def test_run_batch_zip_source():
    """ZIP-sourced batch should work the same as CSV."""
    sub = _make_csv_row(
        legal_name="ZipBatch Pvt Ltd",
        tax_id=_gstin("29", "AABCZ4444F"),
        registration_number="U72900KA2015PTC444001",
        account_number="50100444444001",
        bank_code="HDFC0004441",
    )
    zip_bytes = _make_zip_bytes(sub)
    items = parse_zip(zip_bytes)
    results = run_batch(items, source="zip")
    assert len(results) == 1
    assert results[0].status == "approved"


# ---------------------------------------------------------------------------
# sample_csv_bytes / results_csv_bytes tests
# ---------------------------------------------------------------------------


def test_sample_csv_bytes_has_25_rows():
    data = sample_csv_bytes()
    reader = csv.DictReader(io.StringIO(data.decode("utf-8")))
    rows = list(reader)
    assert len(rows) == 25


def test_sample_csv_bytes_has_all_columns():
    data = sample_csv_bytes()
    reader = csv.DictReader(io.StringIO(data.decode("utf-8")))
    for col in SUBMISSION_FIELDS:
        assert col in (reader.fieldnames or [])


def test_results_csv_bytes():
    results = [
        BatchResult(1, 101, "VF-20260001", "Test Vendor", "approved", "—", 320),
        BatchResult(2, None, None, "Bad Vendor", "error", "—", 5, error="boom"),
    ]
    data = results_csv_bytes(results)
    reader = csv.DictReader(io.StringIO(data.decode("utf-8")))
    rows = list(reader)
    assert len(rows) == 2
    assert rows[0]["Decision"] == "approved"
    assert rows[1]["Error"] == "boom"


def test_batch_results_detected_as_stale_after_db_reset():
    items = parse_csv(sample_csv_bytes())[:2]
    results = run_batch(items, source="csv")
    assert len(results) == 2
    valid_refs = {s["reference"] for s in db.list_submissions()}
    assert any(r.reference and r.reference in valid_refs for r in results)

    # After reset
    db.reset_demo_data()
    valid_refs_after = {s["reference"] for s in db.list_submissions()}
    assert not any(r.reference and r.reference in valid_refs_after for r in results)

