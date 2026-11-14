from __future__ import annotations

import os
import random

import pytest

from backend import ai_extractor, document_parser, layout_parser, ocr_engine
from backend.pipeline import run_pipeline
from database.models import DocumentFile
from test_data.seed import seed_vendors
from test_data.synthetic import render_pdf
from tests import factories

REAL_OCR = os.getenv("VENDORFLOW_RUN_OCR_TESTS", "").strip().lower() in {"1", "true", "on"}


def row(y: float, cells, confidence: float = 0.95, page: int = 0) -> list[layout_parser.TextWord]:
    """Lay words out left to right on one visual line."""
    words = []
    for x, text in cells:
        words.append(
            layout_parser.TextWord(
                text=text,
                x0=float(x),
                y0=float(y),
                x1=float(x) + 5.5 * len(text),
                y1=float(y) + 11.0,
                confidence=confidence,
                page=page,
            )
        )
    return words


def bank_scan_words(submission, confidence: float = 0.95):
    """A scanned bank letter: no colons, values in cells, one stacked label."""
    words: list[layout_parser.TextWord] = []
    words += row(20, [(30, "Cancelled Cheque - Bank account proof")], confidence)
    words += row(40, [(30, "Account"), (78, "number:"), (300, submission.account_number)], confidence)
    words += row(58, [(30, "Account"), (78, "holder"), (300, submission.bank_holder_name)], confidence)
    words += row(76, [(30, "Bank"), (68, "name"), (300, submission.bank_name)], confidence)
    words += row(94, [(30, "IFSC"), (300, submission.bank_code)], confidence)
    words += row(112, [(30, "Country"), (300, "India")], confidence)
    return words


def image_only_pdf(text_pdf: bytes) -> bytes:
    """Rasterise a text PDF into a PDF that has no text layer at all."""
    import pymupdf

    with pymupdf.open(stream=text_pdf, filetype="pdf") as source:
        png = source[0].get_pixmap(dpi=150).tobytes("png")
    target = pymupdf.open()
    page = target.new_page()
    page.insert_image(page.rect, stream=png)
    data = target.tobytes()
    target.close()
    return data


# ----------------------------------------------------------------- layout


def test_layout_parser_recovers_cells_stacked_labels_and_inline_values():
    words = [
        *row(10, [(30, "Northwind Supply LLC")]),
        *row(30, [(30, "GSTIN:"), (120, "07AABCA1234C1Z3")]),
        *row(50, [(30, "Account"), (78, "holder"), (300, "Northwind"), (390, "Supply"), (470, "LLC")]),
        *row(70, [(30, "Account"), (78, "number:"), (300, "1234", ), (350, "5678")]),
        *row(90, [(30, "Bank name")]),
        *row(104, [(30, "Bank of America")]),
        *row(126, [(30, "IFSC"), (300, "HDFC0001234")]),
    ]
    result = layout_parser.parse(words)
    fields = result.fields()

    assert fields["tax_id"] == "07AABCA1234C1Z3"
    assert fields["account_holder"] == "Northwind Supply LLC"
    assert fields["account_number"] == "12345678"
    assert fields["bank_name"] == "Bank of America"
    assert fields["bank_code"] == "HDFC0001234"
    layouts = {kv.field: kv.layout for kv in result.key_values}
    assert layouts["tax_id"] == "inline"
    assert layouts["account_holder"] == "row"
    assert layouts["bank_name"] == "stacked"


def test_layout_parser_does_not_invent_pairs_from_headings():
    words = [
        *row(10, [(30, "Certificate of Incorporation")]),
        *row(30, [(30, "Registrar of Companies, India")]),
        *row(50, [(30, "This certificate is issued under the Companies Act")]),
    ]
    result = layout_parser.parse(words)
    assert result.key_values == []


def test_layout_parser_prefers_a_printed_label_over_a_misread_cell():
    words = [
        *row(10, [(30, "Legal name: Northwind Supply LLC")]),
        *row(30, [(30, "Legal"), (95, "name"), (300, "Northwind Supply LLD")]),
    ]
    result = layout_parser.parse(words)
    values = [kv.value for kv in result.key_values if kv.field == "legal_name"]
    assert values == ["Northwind Supply LLC"]


def test_plain_text_layout_pass_still_reads_labelled_lines():
    result = layout_parser.parse_text("Legal name: Acme Tools Pvt Ltd\nCountry: India")
    assert result.fields() == {"legal_name": "Acme Tools Pvt Ltd", "country": "India"}


# ------------------------------------------------------------- OCR contract


def test_normalizes_gemini_transcription_and_fields():
    payload = {
        "candidates": [{"content": {"parts": [{"text": '{"text":"Registration certificate","fields":{"tax_id":"07AABCA1234C1Z3"}}'}]}}]
    }

    words = ocr_engine._normalize_gemini(payload, page=2)

    assert [word.text for word in words] == ["Registration certificate", "GSTIN: 07AABCA1234C1Z3"]
    assert words[0].page == 2
    assert words[1].y0 == 20


def test_detects_gemini_image_mime_types():
    assert ocr_engine._image_mime(b"\xff\xd8\xffdata") == "image/jpeg"
    assert ocr_engine._image_mime(b"RIFF0000WEBPdata") == "image/webp"
    assert ocr_engine._image_mime(b"GIF89adata") == "image/gif"
    assert ocr_engine._image_mime(b"\x89PNG\r\n\x1a\n") == "image/png"


def test_extracts_a_gemini_http_error_message():
    class Error:
        def read(self):
            return b'{"error":{"message":"model is unavailable"}}'

    assert ocr_engine._http_error_message(Error()) == "model is unavailable"


def test_uses_gemini_transcription_when_fields_are_empty():
    payload = {"candidates": [{"content": {"parts": [{"text": '{"text":"GSTIN: 07AABCA1234C1Z3\\nLegal name: Acme Tools Pvt Ltd","fields":{}}'}]}}]}

    words = ocr_engine._normalize_gemini(payload, page=0)
    fields = layout_parser.parse(words).fields()

    assert [word.text for word in words] == [
        "GSTIN: 07AABCA1234C1Z3",
        "Legal name: Acme Tools Pvt Ltd",
    ]
    assert fields["tax_id"] == "07AABCA1234C1Z3"
    assert fields["legal_name"] == "Acme Tools Pvt Ltd"


def test_reuses_ocr_result_for_the_same_raster(monkeypatch):
    ocr_engine._ocr_cache.clear()
    calls = []

    def run_once(_image, page):
        calls.append(1)
        return [layout_parser.TextWord("GSTIN: 07AABCA1234C1Z3", 10, 20, 120, 34, 0.85, page)]

    monkeypatch.setattr(ocr_engine, "_recognize_gemini", run_once)

    first = ocr_engine.recognize(b"same image", page=0, engine=object())
    second = ocr_engine.recognize(b"same image", page=3, engine=object())

    assert len(calls) == 1
    assert first[0].page == 0
    assert second[0].page == 3


# --------------------------------------------------------- document parser


def test_text_layer_pdf_is_parsed_without_touching_ocr(monkeypatch):
    calls: list[int] = []

    def never(*_args, **_kwargs):
        calls.append(1)
        raise AssertionError("a text-layer PDF must not be sent to OCR")

    monkeypatch.setattr(ocr_engine, "recognize", never)
    pdf = render_pdf(
        [
            "Cancelled Cheque - Bank account proof",
            "Account number: 50100987654321",
            "Account holder: ABC Technologies Pvt Ltd",
            "Bank name: HDFC Bank",
            "IFSC: HDFC0001234",
            "Country: India",
        ]
    )

    parsed = document_parser.parse_document("bank.pdf", pdf)

    assert parsed.status == "parsed"
    assert parsed.source == "text_layer"
    assert parsed.pages == 1
    assert parsed.ocr_pages == 0
    assert parsed.fields()["account_holder"] == "ABC Technologies Pvt Ltd"
    assert calls == []


def test_scanned_pdf_is_rasterised_and_read_by_ocr(monkeypatch):
    words = [
        *row(20, [(30, "Cancelled Cheque - Bank account proof")]),
        *row(40, [(30, "Account"), (78, "number:"), (300, "50100987654321")]),
        *row(58, [(30, "Account"), (78, "holder"), (300, "ABC Technologies Pvt Ltd")]),
    ]
    monkeypatch.setattr(ocr_engine, "available", lambda: (True, "fake engine"))
    monkeypatch.setattr(ocr_engine, "recognize", lambda image, page=0, engine=None: words)
    pdf = image_only_pdf(render_pdf(["Cancelled Cheque", "Account number: 50100987654321"]))

    parsed = document_parser.parse_document("scan.pdf", pdf)

    assert parsed.status == "parsed"
    assert parsed.source == "ocr"
    assert parsed.ocr_pages == 1
    assert parsed.ocr_confidence == pytest.approx(0.95)
    assert parsed.fields()["account_number"] == "50100987654321"
    assert parsed.fields()["account_holder"] == "ABC Technologies Pvt Ltd"


def test_low_confidence_pairs_are_dropped_and_reported(monkeypatch):
    words = [
        *row(20, [(30, "Cancelled Cheque - Bank account proof")], confidence=0.3),
        *row(40, [(30, "Account"), (78, "number:"), (300, "50100987654321")], confidence=0.3),
    ]
    monkeypatch.setattr(ocr_engine, "available", lambda: (True, "fake engine"))
    monkeypatch.setattr(ocr_engine, "recognize", lambda image, page=0, engine=None: words)

    parsed = document_parser.parse_document("scan.png", b"\x89PNG\r\n\x1a\nplaceholder")

    assert parsed.key_values == []
    assert parsed.ocr_pages == 1
    assert parsed.ocr_confidence == pytest.approx(0.3)
    assert any("Gemini Flash" in warning for warning in parsed.warnings)
    assert any("low-confidence" in warning for warning in parsed.warnings)


def test_image_without_ocr_degradates_to_unreadable(monkeypatch):
    monkeypatch.setattr(ocr_engine, "available", lambda: (False, "GEMINI_API_KEY is not set"))

    parsed = document_parser.parse_document("scan.png", b"\x89PNG\r\n\x1a\nplaceholder")

    assert parsed.status == "unreadable"
    assert any("OCR" in warning for warning in parsed.warnings)


def test_png_and_jpeg_magic_are_treated_as_scans(monkeypatch):
    monkeypatch.setattr(ocr_engine, "available", lambda: (False, "off"))
    for magic in (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff\xe0"):
        parsed = document_parser.parse_document("upload.bin", magic + b"0" * 32)
        assert parsed.source in {"none", "ocr"}
        assert any("OCR" in warning for warning in parsed.warnings)


# ----------------------------------------------------------- rule engine


def _scanned_run(monkeypatch, submission, confidence: float = 0.95):
    monkeypatch.setattr(ocr_engine, "available", lambda: (True, "fake engine"))
    monkeypatch.setattr(
        ocr_engine,
        "recognize",
        lambda image, page=0, engine=None: bank_scan_words(submission, confidence),
    )
    documents = [doc for doc in factories.make_documents(submission) if doc.slot != "bank_proof"]
    documents.append(
        DocumentFile(
            slot="bank_proof",
            filename="bank-scan.pdf",
            content=image_only_pdf(render_pdf(["Cancelled Cheque - Bank account proof"])),
        )
    )
    return run_pipeline(submission, documents, seed_vendors())


def test_scanned_document_feeds_the_same_deterministic_rules(monkeypatch):
    rng = random.Random(97)
    submission = factories.make_vendor(rng)
    result = _scanned_run(monkeypatch, submission)

    codes = {finding.code for finding in result.findings}
    assert result.status == "approved", codes
    assert codes == set()
    assert result.extraction_method == "labels + OCR layout"
    scan = next(doc for doc in result.documents if doc.slot == "bank_proof")
    assert "layout" in scan.method
    assert scan.fields["account_holder"] == submission.legal_name
    assert any("Gemini Flash" in warning for warning in scan.warnings)


def test_ocr_holder_mismatch_is_still_decided_by_the_rule_engine(monkeypatch):
    rng = random.Random(98)
    submission = factories.make_vendor(rng)
    submission.bank_holder_name = factories.person_name(rng)
    result = _scanned_run(monkeypatch, submission)

    codes = {finding.code for finding in result.findings}
    assert "bank_holder_mismatch" in codes
    assert result.status == "pending"


def test_noisy_scan_adds_a_warning_but_never_blocks_an_approval(monkeypatch):
    rng = random.Random(99)
    submission = factories.make_vendor(rng)
    result = _scanned_run(monkeypatch, submission, confidence=0.6)

    assert result.status == "approved"
    low = [finding for finding in result.findings if finding.code == "ocr_low_confidence"]
    assert low and low[0].severity == "warn"
    assert all(finding.severity == "warn" for finding in result.findings)


@pytest.mark.skipif(
    not REAL_OCR,
    reason="set VENDORFLOW_RUN_OCR_TESTS=1 and GEMINI_API_KEY to run live Gemini Flash",
)
def test_real_gemini_flash_reads_a_rendered_scan():
    pdf = image_only_pdf(
        render_pdf(
            [
                "Cancelled Cheque - Bank account proof",
                "Account number: 50100987654321",
                "Account holder: ABC Technologies Pvt Ltd",
                "Bank name: HDFC Bank",
            ]
        )
    )

    parsed = document_parser.parse_document("scan.pdf", pdf)

    assert parsed.status == "parsed", parsed.warnings
    assert parsed.ocr_pages == 1
    assert parsed.fields().get("account_number") == "50100987654321"


def test_account_number_with_trailing_bank_name_parsed_cleanly():
    text = "Account Number: 004705001234 Bank name: ICICI Bank"
    res = layout_parser.parse_text(text)
    fields = res.fields()
    assert fields.get("account_number") == "004705001234"
    assert fields.get("bank_name") == "ICICI Bank"

    extracted, _, _, _ = ai_extractor.extract_fields(text)
    assert extracted.get("account_number") == "004705001234"
    assert extracted.get("bank_name") == "ICICI Bank"


def test_account_number_with_equals_sign_and_bank_name_parsed_cleanly():
    text = "account number: 275429653327 bank name = icici bank"
    res = layout_parser.parse_text(text)
    fields = res.fields()
    assert fields.get("account_number") == "275429653327"
    assert fields.get("bank_name") == "icici bank"

    extracted, _, _, _ = ai_extractor.extract_fields(text)
    assert extracted.get("account_number") == "275429653327"
    assert extracted.get("bank_name") == "icici bank"


def test_compare_bank_tokens_handles_trailing_label_without_mismatch():
    from backend.cross_verification import _compare_bank_tokens

    rng = random.Random(42)
    sub = factories.make_vendor(rng)
    sub.account_number = "004705001234"

    # Even if extracted token contains trailing bank name
    checks = []
    findings = _compare_bank_tokens(
        sub,
        {"account_number": "004705001234 Bank name: ICICI Bank", "bank_code": sub.bank_code},
        checks,
    )
    assert not any(f.code == "account_number_mismatch" for f in findings)
    assert checks[0].result == "match"


def test_normalizes_gemini_markdown_code_fences():
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": (
                                "```json\n"
                                "{\n"
                                '  "text": "Registration Number: 29AABCZ9876K1Z9\\n\\n1. Legal Name: Zenith Cloud Solutions Private Limited",\n'
                                '  "fields": {\n'
                                '    "legal_name": "Zenith Cloud Solutions Private Limited",\n'
                                '    "tax_id": "29AABCZ9876K1Z9",\n'
                                '    "registration_number": "29AABCZ9876K1Z9"\n'
                                "  }\n"
                                "}\n"
                                "```"
                            )
                        }
                    ]
                }
            }
        ]
    }
    words = ocr_engine._normalize_gemini(payload, page=0)
    layout = layout_parser.parse(words)
    fields = layout.fields()
    assert fields["legal_name"] == "Zenith Cloud Solutions Private Limited"
    assert fields["tax_id"] == "29AABCZ9876K1Z9"


def test_clean_ocr_field_and_layout_parser_clean_gstin_and_legal_name():
    cleaned_tax = ocr_engine._clean_ocr_field("tax_id", "29AABCZ9876K1Z9\\n\\n2")
    assert cleaned_tax == "29AABCZ9876K1Z9"

    raw_name = (
        'Zenith Cloud Solutions Private Limited\\n\\n3. Trade Name: Zenith Cloud\\n\\n'
        '","fields":{"legal_name":"Zenith Cloud Solutions Private Limited"}'
    )
    cleaned_name = ocr_engine._clean_ocr_field("legal_name", raw_name)
    assert cleaned_name == "Zenith Cloud Solutions Private Limited"

