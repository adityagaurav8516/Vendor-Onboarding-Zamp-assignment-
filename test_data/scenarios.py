from __future__ import annotations

from dataclasses import dataclass

from backend.field_validator import gstin_checksum
from database.models import DocumentFile, Submission
from test_data import synthetic

REQUIRED_SLOTS = ("incorporation", "tax_certificate", "bank_proof")


def _gstin(state: str, pan: str) -> str:
    base = f"{state}{pan}1Z"
    return base + gstin_checksum(base)


def _pack(
    submission: dict,
    *,
    holder: str | None = None,
    account: str | None = None,
    ifsc: str | None = None,
    bank_name: str | None = None,
    legal_name: str | None = None,
    tax_id: str | None = None,
    skip: tuple[str, ...] = (),
) -> dict[str, list[str]]:
    name = legal_name or submission["legal_name"]
    tax = tax_id or submission["tax_id"]
    account = account or submission["account_number"]
    holder = holder or submission["bank_holder_name"]
    ifsc = ifsc or submission["bank_code"]
    bank_name = bank_name or submission["bank_name"]
    documents = {
        "incorporation": [
            "Certificate of Incorporation",
            "Registrar of Companies, India",
            f"CIN: {submission['registration_number']}",
            f"Legal name: {name}",
            f"Registered address: {submission['address']}",
            "Country: India",
        ],
        "tax_certificate": [
            "GST Registration Certificate",
            f"GSTIN: {tax}",
            f"Legal name: {name}",
            f"Registered address: {submission['address']}",
            "Country: India",
        ],
        "bank_proof": [
            "Cancelled Cheque - Bank account proof",
            f"Account number: {account}",
            f"Account holder: {holder}",
            f"Bank name: {bank_name}",
            f"IFSC: {ifsc}",
            "Country: India",
        ],
    }
    for slot in skip:
        documents.pop(slot, None)
    return documents


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    description: str
    expected_status: str
    expected_codes: tuple[str, ...]
    submission: dict
    documents: dict[str, list[str]]


_HAPPY = {
    "legal_name": "ABC Technologies Pvt Ltd",
    "trade_name": "ABC Tech",
    "country": "India",
    "tax_id": _gstin("07", "AABCA1234C"),
    "registration_number": "U72900DL2015PTC123456",
    "address": "22 Nehru Place, New Delhi, Delhi 110019",
    "contact_name": "Rahul Sharma",
    "contact_email": "rahul@abctech.in",
    "contact_phone": "9876543210",
    "bank_holder_name": "ABC Technologies Pvt Ltd",
    "bank_name": "HDFC Bank",
    "account_number": "50100987654321",
    "bank_code": "HDFC0001234",
    "bank_country": "India",
}

_MISSING_GST = {
    "legal_name": "Sundara Textiles Pvt Ltd",
    "trade_name": "Sundara Textiles",
    "country": "India",
    "tax_id": _gstin("07", "AABCS2345L"),
    "registration_number": "U17111DL2012PTC222333",
    "address": "B-14 Okhla Industrial Area, New Delhi, Delhi 110020",
    "contact_name": "Priya Menon",
    "contact_email": "priya@sundaratextiles.in",
    "contact_phone": "9811122233",
    "bank_holder_name": "Sundara Textiles Pvt Ltd",
    "bank_name": "ICICI Bank",
    "account_number": "001105009876",
    "bank_code": "ICIC0001234",
    "bank_country": "India",
}

_HOLDER_MISMATCH = {
    "legal_name": "PQR Enterprises Pvt Ltd",
    "trade_name": "PQR Enterprises",
    "country": "India",
    "tax_id": _gstin("07", "AABCP3456F"),
    "registration_number": "U74999DL2018PTC345678",
    "address": "88 Connaught Place, New Delhi, Delhi 110001",
    "contact_name": "Vikram Sethi",
    "contact_email": "vikram@pqrenterprises.in",
    "contact_phone": "9911223344",
    "bank_holder_name": "Rahul Sharma",
    "bank_name": "Kotak Mahindra Bank",
    "account_number": "918273645012",
    "bank_code": "KKBK0000123",
    "bank_country": "India",
}

_BAD_GSTIN = {
    "legal_name": "Helios Devices Pvt Ltd",
    "trade_name": "Helios Devices",
    "country": "India",
    "tax_id": _gstin("07", "AABCH5678K")[:-1] + "A",
    "registration_number": "U26100DL2017PTC333444",
    "address": "5 Rajouri Garden, New Delhi, Delhi 110027",
    "contact_name": "Anita Rao",
    "contact_email": "anita@heliosdevices.in",
    "contact_phone": "9822334455",
    "bank_holder_name": "Helios Devices Pvt Ltd",
    "bank_name": "Axis Bank",
    "account_number": "50200112233445",
    "bank_code": "UTIB0000456",
    "bank_country": "India",
}

_DUPLICATE_ACCOUNT = {
    "legal_name": "Nova Supplies Pvt Ltd",
    "trade_name": "Nova Supplies",
    "country": "India",
    "tax_id": _gstin("09", "AABCN9012E"),
    "registration_number": "U46900DL2020PTC444555",
    "address": "31 Sector 18, Noida, Uttar Pradesh 201301",
    "contact_name": "Neha Gupta",
    "contact_email": "purchase@novasupplies.in",
    "contact_phone": "9899001122",
    "bank_holder_name": "Nova Supplies Pvt Ltd",
    "bank_name": "HDFC Bank",
    "account_number": "50100234567890",
    "bank_code": "HDFC0001234",
    "bank_country": "India",
}

_BLOCKED = {
    "legal_name": "Bluepeak Traders Pvt Ltd",
    "trade_name": "Bluepeak Traders",
    "country": "India",
    "tax_id": _gstin("06", "AABCB7890G"),
    "registration_number": "U51909DL2016PTC111222",
    "address": "Plot 14, Udyog Vihar, Gurugram, Haryana 122015",
    "contact_name": "Sanjay Bhatia",
    "contact_email": "sanjay@bluepeaktraders.in",
    "contact_phone": "9810011223",
    "bank_holder_name": "Bluepeak Traders Pvt Ltd",
    "bank_name": "Axis Bank",
    "account_number": "912010011223344",
    "bank_code": "UTIB0000456",
    "bank_country": "India",
}

SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id="happy_path",
        name="Happy path",
        description=(
            "All required documents uploaded, every format check passes, names match across "
            "documents, and the registry has no duplicate."
        ),
        expected_status="approved",
        expected_codes=(),
        submission=_HAPPY,
        documents=_pack(_HAPPY),
    ),
    Scenario(
        id="missing_gst",
        name="Missing GST certificate",
        description="Company and bank paper are present, but the GST certificate was not uploaded.",
        expected_status="pending",
        expected_codes=("missing_document",),
        submission=_MISSING_GST,
        documents=_pack(_MISSING_GST, skip=("tax_certificate",)),
    ),
    Scenario(
        id="identity_mismatch",
        name="Bank holder is a person",
        description=(
            "The company is PQR Enterprises Pvt Ltd, but the payout account is held by an "
            "individual. Money would not reach the company."
        ),
        expected_status="pending",
        expected_codes=("bank_holder_mismatch",),
        submission=_HOLDER_MISMATCH,
        documents=_pack(_HOLDER_MISMATCH, holder="Rahul Sharma"),
    ),
    Scenario(
        id="invalid_gstin",
        name="GSTIN fails the checksum",
        description=(
            "The tax ID has the right 15-character shape but the check digit is wrong, the "
            "usual sign of a typo in a real submission."
        ),
        expected_status="pending",
        expected_codes=("tax_bad_checksum",),
        submission=_BAD_GSTIN,
        documents=_pack(_BAD_GSTIN, tax_id=_BAD_GSTIN["tax_id"]),
    ),
    Scenario(
        id="duplicate_bank_account",
        name="Bank account already on file",
        description=(
            "A different legal entity submits the payout account already approved for "
            "Zenith Components. This needs a human, not an accusation."
        ),
        expected_status="pending",
        expected_codes=("shared_bank_account",),
        submission=_DUPLICATE_ACCOUNT,
        documents=_pack(_DUPLICATE_ACCOUNT),
    ),
    Scenario(
        id="blocked_vendor",
        name="Blocked tax registration",
        description="The tax registration and CIN belong to a vendor the company has blocked.",
        expected_status="rejected",
        expected_codes=("blocked_vendor",),
        submission=_BLOCKED,
        documents=_pack(_BLOCKED),
    ),
)

SCENARIOS_BY_ID = {scenario.id: scenario for scenario in SCENARIOS}


def to_submission(scenario: Scenario) -> Submission:
    return Submission(**scenario.submission)


def build_documents(scenario: Scenario) -> list[DocumentFile]:
    documents = []
    for slot in REQUIRED_SLOTS:
        lines = scenario.documents.get(slot)
        if not lines:
            continue
        stem = f"{scenario.id}-{slot}"
        documents.append(
            DocumentFile(
                slot=slot,
                filename=f"{stem}.pdf",
                content=synthetic.document_bytes(stem, lines),
            )
        )
    return documents
