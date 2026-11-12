"""Verify fake vendor inputs against the real pipeline as plain assertions.

Run with `pytest -q` or `python -m pytest -q rhd/fake_vendor_test.py`.
"""
from __future__ import annotations

from backend.field_validator import gstin_checksum
from backend.pipeline import run_pipeline
from database.models import DocumentFile, Submission
from test_data.seed import seed_vendors


def gst(state_code: str, pan: str) -> str:
    base = f"{state_code}{pan}1Z"
    return base + gstin_checksum(base)


_MA_PAN = "AABCA1234C"
_KA_PAN = "AABPA9876K"
_DL_PAN = "AACX5555K"

CASES = [
    {
        "name": "fake 1: clean India vendor",
        "submit": Submission(
            legal_name="Sai Constructors Pvt Ltd",
            country="India",
            tax_id=gst("27", _MA_PAN),
            registration_number="U24290MH2010PTC098765",
            address="Mumbai, Maharashtra 400051",
            contact_name="Meera Nair",
            contact_email="accounts@sai-construct.in",
            contact_phone="9822676543",
            bank_holder_name="Sai Constructors Pvt Ltd",
            bank_name="ICICI Bank",
            account_number="50100987654321",
            bank_code="ICIC0009876",
            bank_country="India",
        ),
        "docs": {
            "incorporation": [
                "Certificate of Incorporation",
                "Registrar of Companies, India",
                "CIN: U24290MH2010PTC098765",
                "Legal name: Sai Constructors Pvt Ltd",
                "Registered address: Mumbai, Maharashtra 400051",
                "Country: India",
            ],
            "tax_certificate": [
                "GST Registration Certificate",
                f"GSTIN: {gst('27', _MA_PAN)}",
                "Legal name: Sai Constructors Pvt Ltd",
                "Registered address: Mumbai, Maharashtra 400051",
                "Country: India",
            ],
            "bank_proof": [
                "Cancelled Cheque - Bank account proof",
                "Account number: 50100987654321",
                "Account holder: Sai Constructors Pvt Ltd",
                "Bank name: ICICI Bank",
                "IFSC: ICIC0009876",
                "Country: India",
            ],
        },
        "expect": "approved",
        "codes": set(),
    },
    {
        "name": "fake 2: missing incorporation document",
        "submit": Submission(
            legal_name="Blue River Logistics",
            country="India",
            tax_id=gst("29", _KA_PAN),
            registration_number="U72900KA2013PTC555555",
            address="Bengaluru, Karnataka 560038",
            contact_name="Aravind Menon",
            contact_email="a.menon@blueriver.in",
            contact_phone="9632587410",
            bank_holder_name="Blue River Logistics",
            bank_name="Kotak Mahindra Bank",
            account_number="50100987654321",
            bank_code="KKBK0009876",
            bank_country="India",
        ),
        "docs": {
            "tax_certificate": [
                "GST Registration Certificate",
                f"GSTIN: {gst('29', _KA_PAN)}",
                "Legal name: Blue River Logistics",
                "Registered address: Bengaluru, Karnataka 560038",
                "Country: India",
            ],
            "bank_proof": [
                "Cancelled Cheque - Bank account proof",
                "Account number: 50100987654321",
                "Account holder: Blue River Logistics",
                "Bank name: Kotak Mahindra Bank",
                "IFSC: KKBK0009876",
                "Country: India",
            ],
        },
        "expect": "pending",
        "codes": {"missing_document"},
    },
    {
        "name": "fake 3: identity mismatch (bank proof names a different bank)",
        "submit": Submission(
            legal_name="Raj Traders",
            country="India",
            tax_id=gst("27", _MA_PAN),
            registration_number="U24290MH2011PTC111111",
            address="Mumbai, Maharashtra 400051",
            contact_name="Ravi Gupta",
            contact_email="ravi@rajtraders.in",
            contact_phone="9888776655",
            bank_holder_name="Raj Traders",
            bank_name="HDFC Bank",
            account_number="50100987654321",
            bank_code="HDFC0009876",
            bank_country="India",
        ),
        "docs": {
            "incorporation": [
                "Certificate of Incorporation",
                "CIN: U24290MH2011PTC111111",
                "Legal name: Raj Traders",
                "Country: India",
            ],
            "tax_certificate": [
                "GST Registration Certificate",
                f"GSTIN: {gst('27', _MA_PAN)}",
                "Legal name: Raj Traders",
                "Country: India",
            ],
            "bank_proof": [
                "Cancelled Cheque - Bank account proof",
                "Account number: 50100987654321",
                "Account holder: Raj Traders",
                "Bank name: Axis Bank",
                "IFSC: UTIB0009876",
                "Country: India",
            ],
        },
        "expect": "pending",
        "codes": {"bank_code_mismatch"},
    },
    {
        "name": "fake 4: invalid GSTIN (checksum typo) + otherwise complete pack",
        "submit": Submission(
            legal_name="Neon Pack Private Limited",
            country="India",
            tax_id=gst("27", _MA_PAN)[:-1] + ("A" if gst("27", _MA_PAN)[-1] != "A" else "B"),
            registration_number="U65900DL2017PTC222222",
            address="Mumbai, Maharashtra 400051",
            contact_name="Shreya Singh",
            contact_email="accounts@neonpack.in",
            contact_phone="9932105678",
            bank_holder_name="Neon Pack Private Limited",
            bank_name="HDFC Bank",
            account_number="50100987654321",
            bank_code="HDFC0009876",
            bank_country="India",
        ),
        "docs": {
            "incorporation": [
                "Certificate of Incorporation",
                "CIN: U65900DL2017PTC222222",
                "Legal name: Neon Pack Private Limited",
                "Country: India",
            ],
            "tax_certificate": [
                "GST Registration Certificate",
                f"GSTIN: {gst('27', _MA_PAN)[:-1] + ('A' if gst('27', _MA_PAN)[-1] != 'A' else 'B')}",
                "Legal name: Neon Pack Private Limited",
                "Country: India",
            ],
            "bank_proof": [
                "Cancelled Cheque - Bank account proof",
                "Account number: 50100987654321",
                "Account holder: Neon Pack Private Limited",
                "Bank name: HDFC Bank",
                "IFSC: HDFC0009876",
                "Country: India",
            ],
        },
        "expect": "pending",
        "codes": {"tax_bad_checksum"},
    },
    {
        "name": "fake 5: duplicate payout account under a different legal name",
        "submit": Submission(
            legal_name="Valley Trading Co",
            country="India",
            tax_id=gst("29", _KA_PAN),
            registration_number="U72900KA2012PTC333333",
            address="Bengaluru, Karnataka 560066",
            contact_name="Divya Rao",
            contact_email="divya@valleytrading.in",
            contact_phone="9876105234",
            bank_holder_name="Valley Trading Co",
            bank_name="HDFC Bank",
            account_number="50100234567890",
            bank_code="HDFC0001234",
            bank_country="India",
        ),
        "docs": {
            "incorporation": [
                "Certificate of Incorporation",
                "CIN: U72900KA2012PTC333333",
                "Legal name: Valley Trading Co",
                "Country: India",
            ],
            "tax_certificate": [
                "GST Registration Certificate",
                f"GSTIN: {gst('29', _KA_PAN)}",
                "Legal name: Valley Trading Co",
                "Country: India",
            ],
            "bank_proof": [
                "Cancelled Cheque - Bank account proof",
                "Account number: 50100234567890",
                "Account holder: Valley Trading Co",
                "Bank name: HDFC Bank",
                "IFSC: HDFC0001234",
                "Country: India",
            ],
        },
        "expect": "pending",
        "codes": {"shared_bank_account"},
    },
    {
        "name": "fake 6: blocked vendor trying again with the blocked registration",
        "submit": Submission(
            legal_name="Bluepeak Logistics",
            country="India",
            tax_id=gst("06", "AABCB7890G"),
            registration_number="U51909DL2016PTC111222",
            address="Plot 14, Udyog Vihar, Gurugram, Haryana 122015",
            contact_name="Nitin Kumar",
            contact_email="nitin@bluepeak.in",
            contact_phone="9878765432",
            bank_holder_name="Bluepeak Logistics",
            bank_name="Axis Bank",
            account_number="50100987654321",
            bank_code="UTIB0009876",
            bank_country="India",
        ),
        "docs": {
            "incorporation": [
                "Certificate of Incorporation",
                "CIN: U51909DL2016PTC111222",
                "Legal name: Bluepeak Logistics",
                "Country: India",
            ],
            "tax_certificate": [
                "GST Registration Certificate",
                f"GSTIN: {gst('06', 'AABCB7890G')}",
                "Legal name: Bluepeak Logistics",
                "Country: India",
            ],
            "bank_proof": [
                "Cancelled Cheque - Bank account proof",
                "Account number: 50100987654321",
                "Account holder: Bluepeak Logistics",
                "Bank name: Axis Bank",
                "IFSC: UTIB0009876",
                "Country: India",
            ],
        },
        "expect": "rejected",
        "codes": {"blocked_vendor"},
    },
]


def _build_docs(slot_lines: dict) -> list[DocumentFile]:
    return [
        DocumentFile(
            slot=slot,
            filename=f"fake-{i}.txt",
            content="\n".join(lines).encode("utf-8"),
        )
        for i, (slot, lines) in enumerate(slot_lines.items())
    ]


def test_fake_vendor_inputs() -> None:
    registry = seed_vendors()
    for case in CASES:
        result = run_pipeline(case["submit"], _build_docs(case["docs"]), registry)
        codes = {finding.code for finding in result.findings}
        assert result.status == case["expect"], (
            f"{case['name']}: expected status {case['expect']}, got {result.status}"
        )
        missing = case["codes"] - codes
        assert not missing, (
            f"{case['name']}: expected codes missing {sorted(missing)}"
        )
