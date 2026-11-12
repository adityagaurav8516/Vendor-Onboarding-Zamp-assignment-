from __future__ import annotations

from database.models import ExistingVendor

# Synthetic registry: the vendors the company has already seen.
VENDORS: tuple[dict, ...] = (
    {
        "legal_name": "Zenith Components Private Limited",
        "trade_name": "Zenith Components",
        "country": "India",
        "tax_id": "27AABCX5678D1Z2",
        "registration_number": "U29299MH2012PTC234567",
        "address": "Plot 9, MIDC, Pune, Maharashtra 411057",
        "contact_name": "Meera Kulkarni",
        "contact_email": "meera@zenithcomponents.in",
        "contact_phone": "9820011223",
        "bank_holder_name": "Zenith Components Private Limited",
        "bank_name": "HDFC Bank",
        "account_number": "50100234567890",
        "bank_code": "HDFC0001234",
        "bank_country": "India",
        "status": "approved",
    },
    {
        "legal_name": "Orbit Logistics Pvt Ltd",
        "trade_name": "Orbit Logistics",
        "country": "India",
        "tax_id": "29AABCP3456F1ZE",
        "registration_number": "U63030KA2019PTC456789",
        "address": "14 Whitefield Main Road, Bengaluru, Karnataka 560066",
        "contact_name": "Arjun Nair",
        "contact_email": "arjun@orbitlogistics.in",
        "contact_phone": "9845098450",
        "bank_holder_name": "Orbit Logistics Pvt Ltd",
        "bank_name": "ICICI Bank",
        "account_number": "001105001234",
        "bank_code": "ICIC0001234",
        "bank_country": "India",
        "status": "pending",
    },
    {
        "legal_name": "Bluepeak Traders Pvt Ltd",
        "trade_name": "Bluepeak Traders",
        "country": "India",
        "tax_id": "06AABCB7890G1ZK",
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
        "status": "blocked",
    },
)


def seed_vendors() -> list[ExistingVendor]:
    """Pure view of the seed registry, used by tests and scenario previews."""
    return [
        ExistingVendor(
            id=index,
            legal_name=vendor["legal_name"],
            country=vendor["country"],
            tax_id=vendor["tax_id"],
            registration_number=vendor["registration_number"],
            account_number=vendor["account_number"],
            bank_code=vendor["bank_code"],
            contact_email=vendor["contact_email"],
            status=vendor["status"],
        )
        for index, vendor in enumerate(VENDORS, start=1)
    ]


def ensure_seed() -> None:
    from database import db

    if db.registry_count() == 0:
        for vendor in VENDORS:
            db.insert_registry_vendor(**vendor)
