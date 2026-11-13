from __future__ import annotations

import pytest

from backend.submission_loader import SubmissionLoadError, load_payload


def payload(documents=None, **submission):
    body = {"legal_name": "Acme Tools Pvt Ltd", "country": "India"}
    body.update(submission)
    return {"submission": body, "documents": documents or {}}


def test_loads_document_paths_relative_to_a_base_directory(tmp_path):
    (tmp_path / "incorporation.txt").write_text(
        "Certificate of Incorporation\nLegal name: Acme Tools Pvt Ltd", encoding="utf-8"
    )

    loaded = load_payload(
        payload({"incorporation": "incorporation.txt"}),
        base_dirs=[tmp_path],
        strict_paths=True,
    )

    assert loaded.submission.legal_name == "Acme Tools Pvt Ltd"
    assert [doc.filename for doc in loaded.documents] == ["incorporation.txt"]
    assert loaded.documents[0].content.startswith(b"Certificate")
    assert loaded.missing_paths == []
    assert loaded.slots == {"incorporation"}


def test_absolute_paths_are_accepted(tmp_path):
    document = tmp_path / "bank.txt"
    document.write_text("Cancelled Cheque - Bank account proof", encoding="utf-8")

    loaded = load_payload(
        payload({"bank_proof": str(document)}), base_dirs=[tmp_path / "elsewhere"], strict_paths=True
    )

    assert loaded.documents[0].slot == "bank_proof"


def test_missing_path_is_a_warning_when_not_strict(tmp_path):
    loaded = load_payload(payload({"bank_proof": "nope.pdf"}), base_dirs=[tmp_path])

    assert loaded.documents == []
    assert loaded.missing_paths == ["nope.pdf"]
    assert "bank_proof" in loaded.warnings[0]


def test_missing_path_is_an_error_when_strict(tmp_path):
    with pytest.raises(SubmissionLoadError, match="Document not found: nope.pdf"):
        load_payload(payload({"bank_proof": "nope.pdf"}), base_dirs=[tmp_path], strict_paths=True)


def test_unknown_submission_field_is_rejected():
    with pytest.raises(SubmissionLoadError, match="Unknown submission field"):
        load_payload({"submission": {"legl_name": "typo"}})


def test_unknown_document_slot_is_rejected():
    with pytest.raises(SubmissionLoadError, match="Unknown document slot"):
        load_payload({"submission": {"legal_name": "Acme"}, "documents": {"gst": "a.pdf"}})


def test_bare_payload_without_the_wrapper_is_accepted(tmp_path):
    document = tmp_path / "bank.txt"
    document.write_text("Cancelled Cheque", encoding="utf-8")

    loaded = load_payload(
        {"legal_name": "Acme Tools", "documents": {"bank_proof": "bank.txt"}},
        base_dirs=[tmp_path],
    )

    assert loaded.submission.legal_name == "Acme Tools"
    assert loaded.documents[0].slot == "bank_proof"


def test_documents_must_be_an_object():
    with pytest.raises(SubmissionLoadError, match="'documents' must be a JSON object"):
        load_payload({"submission": {"legal_name": "Acme"}, "documents": ["a.pdf"]})


def test_null_documents_are_rejected_instead_of_being_silently_dropped():
    with pytest.raises(SubmissionLoadError, match="'documents' must be a JSON object"):
        load_payload({"submission": {"legal_name": "Acme"}, "documents": None})


def test_wrapper_typo_is_rejected_instead_of_losing_documents():
    with pytest.raises(SubmissionLoadError, match="Unknown top-level field.*documemts"):
        load_payload(
            {
                "submission": {"legal_name": "Acme"},
                "documemts": {"bank_proof": "bank.pdf"},
            }
        )
