from __future__ import annotations

import random
import string

from backend.field_validator import gstin_checksum
from database.models import DocumentFile, Submission

STATES = (
    ("07", "DL", "Delhi", "New Delhi, Delhi 110019"),
    ("27", "MH", "Maharashtra", "Andheri East, Mumbai, Maharashtra 400069"),
    ("29", "KA", "Karnataka", "Whitefield, Bengaluru, Karnataka 560066"),
    ("06", "HR", "Haryana", "Udyog Vihar, Gurugram, Haryana 122015"),
)

BANKS = (
    ("HDFC Bank", "HDFC0001234"),
    ("ICICI Bank", "ICIC0001234"),
    ("Axis Bank", "UTIB0000456"),
    ("Kotak Mahindra Bank", "KKBK0000123"),
)

COMPANY_WORDS = (
    "Nimbus",
    "Verdant",
    "Solaris",
    "Kestrel",
    "Aurelia",
    "Basalt",
    "Cobalt",
    "Dunlin",
    "Everest",
    "Falcon",
    "Granite",
    "Harbor",
)

FIRST_NAMES = ("Rahul", "Priya", "Vikram", "Anita", "Neha", "Sanjay", "Meera", "Arjun")
LAST_NAMES = ("Sharma", "Menon", "Sethi", "Rao", "Gupta", "Bhatia", "Kulkarni", "Nair")


def letters(rng: random.Random, count: int) -> str:
    return "".join(rng.choice(string.ascii_uppercase) for _ in range(count))


def digits(rng: random.Random, count: int) -> str:
    return "".join(rng.choice(string.digits) for _ in range(count))


def gstin_for(state_code: str, pan: str) -> str:
    base = f"{state_code}{pan}1Z"
    return base + gstin_checksum(base)


def make_pan(rng: random.Random) -> str:
    return f"{letters(rng, 5)}{digits(rng, 4)}{letters(rng, 1)}"


def make_cin(rng: random.Random, state_code: str) -> str:
    return f"U{digits(rng, 5)}{state_code}{digits(rng, 4)}PTC{digits(rng, 6)}"


def person_name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def company_name(rng: random.Random) -> str:
    return f"{rng.choice(COMPANY_WORDS)} {rng.choice(COMPANY_WORDS)} Pvt Ltd"


def make_vendor(rng: random.Random) -> Submission:
    state_code, state_abbr, _state, address = rng.choice(STATES)
    bank_name, ifsc = rng.choice(BANKS)
    pan = make_pan(rng)
    name = company_name(rng)
    slug = name.split()[0].lower()
    return Submission(
        legal_name=name,
        trade_name=f"{name.split()[0]} {name.split()[1]}",
        country="India",
        tax_id=gstin_for(state_code, pan),
        registration_number=make_cin(rng, state_abbr),
        address=address,
        contact_name=person_name(rng),
        contact_email=f"accounts@{slug}-{digits(rng, 3)}.in",
        contact_phone=f"9{digits(rng, 9)}",
        bank_holder_name=name,
        bank_name=bank_name,
        account_number=f"77{digits(rng, 11)}",
        bank_code=ifsc,
        bank_country="India",
    )


def make_documents(
    submission: Submission,
    *,
    holder: str | None = None,
    legal_name: str | None = None,
    tax_id: str | None = None,
    account_number: str | None = None,
    bank_code: str | None = None,
    bank_name: str | None = None,
    drop: tuple[str, ...] = (),
) -> list[DocumentFile]:
    """Fast text documents. PDF parsing is covered by the demo pack tests."""
    name = legal_name or submission.legal_name
    tax = tax_id or submission.tax_id
    account = account_number or submission.account_number
    holder = holder or submission.bank_holder_name
    code = bank_code or submission.bank_code
    bank = bank_name or submission.bank_name
    packs = {
        "incorporation": [
            "Certificate of Incorporation",
            "Registrar of Companies, India",
            f"CIN: {submission.registration_number}",
            f"Legal name: {name}",
            f"Registered address: {submission.address}",
            "Country: India",
        ],
        "tax_certificate": [
            "GST Registration Certificate",
            f"GSTIN: {tax}",
            f"Legal name: {name}",
            f"Registered address: {submission.address}",
            "Country: India",
        ],
        "bank_proof": [
            "Cancelled Cheque - Bank account proof",
            f"Account number: {account}",
            f"Account holder: {holder}",
            f"Bank name: {bank}",
            f"IFSC: {code}",
            "Country: India",
        ],
    }
    documents = []
    for slot, lines in packs.items():
        if slot in drop:
            continue
        documents.append(
            DocumentFile(
                slot=slot,
                filename=f"generated-{slot}.txt",
                content="\n".join(lines).encode("utf-8"),
            )
        )
    return documents
