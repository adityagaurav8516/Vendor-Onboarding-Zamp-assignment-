from __future__ import annotations

import json

from backend import cli
from database import db


def test_cli_demo_outputs_structured_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cli.db")
    monkeypatch.setattr(db, "_READY", False)
    assert cli.main(["--demo", "missing_gst"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "pending"
    assert payload["reference"] is None
    assert "missing_document" in {finding["code"] for finding in payload["findings"]}
    assert payload["required_actions"]
    assert len(payload["stages"]) == 7


def test_cli_reads_submission_and_document_paths(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cli.db")
    monkeypatch.setattr(db, "_READY", False)
    documents = tmp_path / "documents"
    documents.mkdir()
    incorporation = documents / "incorporation.txt"
    incorporation.write_text(
        "\n".join(
            [
                "Certificate of Incorporation",
                "CIN: U72900DL2015PTC123456",
                "Legal name: Acme Tools Pvt Ltd",
                "Country: India",
            ]
        ),
        encoding="utf-8",
    )
    submission = {
        "legal_name": "Acme Tools Pvt Ltd",
        "country": "India",
        "tax_id": "07AABCA1234C1ZH",
        "registration_number": "U72900DL2015PTC123456",
        "address": "22 Nehru Place, New Delhi, Delhi 110019",
        "contact_name": "Rahul Sharma",
        "contact_email": "rahul@acmetools.in",
        "contact_phone": "9876543210",
        "bank_holder_name": "Acme Tools Pvt Ltd",
        "bank_name": "HDFC Bank",
        "account_number": "50100987654321",
        "bank_code": "HDFC0001234",
        "bank_country": "India",
    }
    input_path = tmp_path / "submission.json"
    input_path.write_text(
        json.dumps({"submission": submission, "documents": {"incorporation": str(incorporation)}}),
        encoding="utf-8",
    )

    assert cli.main([str(input_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    codes = {finding["code"] for finding in payload["findings"]}
    assert payload["status"] == "pending"
    assert {"missing_document"} <= codes
    assert payload["documents"][0]["parse_status"] == "parsed"


def test_cli_save_returns_reference(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cli.db")
    monkeypatch.setattr(db, "_READY", False)
    assert cli.main(["--demo", "happy_path", "--save"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["reference"] == "VF-0001"
    assert db.find_by_reference("VF-0001")["status"] == "approved"
