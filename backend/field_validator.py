from __future__ import annotations

import re

from database.models import Finding, Submission

GST_STATES = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
    "97": "Other Territory",
}

STATE_ALIASES = {
    "Odisha": ("odisha", "orissa"),
    "Uttarakhand": ("uttarakhand", "uttaranchal"),
    "Dadra and Nagar Haveli and Daman and Diu": ("dadra", "daman", "diu"),
    "Andaman and Nicobar Islands": ("andaman", "nicobar"),
    "Jammu and Kashmir": ("jammu", "kashmir"),
}

# IRS prefixes that are not assigned to an EIN.
_INVALID_EIN_PREFIXES = {
    "00", "07", "08", "09", "17", "18", "19", "28", "29",
    "49", "69", "70", "78", "79", "89", "96", "97",
}

_GSTIN_SHAPE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]$")
_CIN_SHAPE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
_IFSC_SHAPE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

IFSC_BANKS = {
    "HDFC": ("hdfc",),
    "ICIC": ("icici",),
    "SBIN": ("state bank", "sbi"),
    "UTIB": ("axis",),
    "KKBK": ("kotak",),
    "BARB": ("baroda",),
    "PUNB": ("punjab national", "pnb"),
    "CNRB": ("canara",),
    "UBIN": ("union bank",),
    "YESB": ("yes bank",),
    "INDB": ("indusind",),
    "IDIB": ("indian bank",),
    "IOBA": ("indian overseas",),
    "CBIN": ("central bank",),
    "BKID": ("bank of india",),
    "MAHB": ("bank of maharashtra",),
    "FDRL": ("federal",),
    "HSBC": ("hsbc",),
    "CITI": ("citibank", "citi"),
    "SCBL": ("standard chartered",),
}

FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "proton.me", "icloud.com",
}

REQUIRED_FIELDS = (
    ("legal_name", "legal name"),
    ("country", "country"),
    ("tax_id", "tax ID"),
    ("registration_number", "registration number"),
    ("address", "address"),
    ("contact_email", "contact email"),
    ("contact_phone", "contact phone"),
    ("bank_holder_name", "bank account holder"),
    ("bank_name", "bank name"),
    ("account_number", "account number"),
    ("bank_code", "bank code"),
    ("bank_country", "bank country"),
)

SUPPORTED_COUNTRIES = ("India", "United States")


def gstin_checksum(first14: str) -> str:
    total = 0
    for index, char in enumerate(first14):
        code = _CHARSET.index(char)
        factor = 1 if index % 2 == 0 else 2
        product = code * factor
        total += product // 36 + product % 36
    return _CHARSET[(36 - (total % 36)) % 36]


def compact_tax(value: str) -> str:
    return re.sub(r"[\s-]", "", (value or "").upper())


def is_gstin_shape(value: str) -> bool:
    return bool(_GSTIN_SHAPE.fullmatch(compact_tax(value)))


def is_valid_gstin(value: str) -> bool:
    compact = compact_tax(value)
    if not _GSTIN_SHAPE.fullmatch(compact):
        return False
    if compact[:2] not in GST_STATES:
        return False
    return gstin_checksum(compact[:14]) == compact[14]


def ein_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def is_ein_shape(value: str) -> bool:
    return len(ein_digits(value)) == 9 and bool(re.fullmatch(r"[\d\s-]+", (value or "").strip()))


def is_valid_ein(value: str) -> bool:
    digits = ein_digits(value)
    if len(digits) != 9 or not is_ein_shape(value):
        return False
    return digits[:2] not in _INVALID_EIN_PREFIXES


def canonical_ein(value: str) -> str:
    digits = ein_digits(value)
    if len(digits) != 9:
        return (value or "").strip()
    return f"{digits[:2]}-{digits[2:]}"


def aba_checksum_ok(value: str) -> bool:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) != 9:
        return False
    nums = [int(ch) for ch in digits]
    total = (
        3 * (nums[0] + nums[3] + nums[6])
        + 7 * (nums[1] + nums[4] + nums[7])
        + (nums[2] + nums[5] + nums[8])
    )
    return total % 10 == 0


def tax_regime(value: str) -> str:
    if is_gstin_shape(value):
        return "gstin"
    if is_ein_shape(value):
        return "ein"
    return "unknown"


def tax_home_country(value: str) -> str | None:
    regime = tax_regime(value)
    if regime == "gstin":
        return "India"
    if regime == "ein":
        return "United States"
    return None


def address_mentions_state(address: str, state: str) -> bool:
    text = (address or "").lower()
    aliases = STATE_ALIASES.get(state, (state.lower(),))
    return any(re.search(rf"\b{re.escape(alias)}\b", text) for alias in aliases)


def _finding(code: str, severity: str, message: str, vendor_message: str | None = None) -> Finding:
    return Finding(
        code=code,
        severity=severity,
        stage="validate",
        message=message,
        vendor_message=vendor_message,
    )


def validate_submission(submission: Submission) -> list[Finding]:
    findings: list[Finding] = []
    missing = [
        label for key, label in REQUIRED_FIELDS if not str(getattr(submission, key) or "").strip()
    ]
    if missing:
        joined = ", ".join(missing)
        findings.append(
            _finding(
                "missing_fields",
                "pending",
                f"The submission is missing {joined}.",
                f"Please complete these fields and resubmit: {joined}.",
            )
        )

    email = (submission.contact_email or "").strip()
    if email and not _EMAIL_SHAPE.fullmatch(email):
        findings.append(
            _finding(
                "bad_email",
                "pending",
                f"Contact email {email} is not a usable address.",
                "Please send a contact email we can reply to.",
            )
        )
    elif email and email.split("@")[-1].lower() in FREE_EMAIL_DOMAINS:
        findings.append(
            _finding(
                "free_email",
                "warn",
                "Contact email is a free inbox. Noted, not blocked: small vendors often use one.",
            )
        )

    phone = re.sub(r"[\s\-()]", "", submission.contact_phone or "")
    if phone:
        digits = phone[3:] if phone.startswith("+91") else phone
        if submission.country == "India" and not re.fullmatch(r"[6-9]\d{9}", digits):
            findings.append(
                _finding(
                    "bad_phone",
                    "pending",
                    "Contact phone is not a 10-digit Indian mobile number.",
                    "Please send a 10-digit Indian mobile number, for example 9876543210.",
                )
            )
        elif submission.country == "United States" and not re.fullmatch(r"(?:\+?1)?\d{10}", phone):
            findings.append(
                _finding(
                    "bad_phone",
                    "pending",
                    "Contact phone is not a 10-digit US number.",
                    "Please send a 10-digit US phone number.",
                )
            )

    if submission.country and submission.country not in SUPPORTED_COUNTRIES:
        findings.append(
            _finding(
                "country_unsupported",
                "pending",
                f"{submission.country} is outside the rules in this version, so a person has to review it.",
                f"We cannot auto-check a {submission.country} vendor yet. A reviewer will follow up.",
            )
        )

    if submission.tax_id.strip() and submission.country in SUPPORTED_COUNTRIES:
        findings.extend(_validate_tax(submission.country, submission.tax_id))

    if submission.country == "India" and submission.registration_number.strip():
        cin = re.sub(r"\s", "", submission.registration_number.upper())
        if not _CIN_SHAPE.fullmatch(cin):
            findings.append(
                _finding(
                    "bad_registration",
                    "pending",
                    "Registration number is not a 21-character CIN.",
                    "Please send the 21-character CIN from the incorporation certificate.",
                )
            )
    elif (
        submission.country == "United States"
        and _CIN_SHAPE.fullmatch(re.sub(r"\s", "", submission.registration_number.upper()))
    ):
        findings.append(
            _finding(
                "cin_on_us_vendor",
                "warn",
                "Registration number looks like an Indian CIN on a United States vendor.",
            )
        )

    if submission.bank_code.strip() and submission.country in SUPPORTED_COUNTRIES:
        findings.extend(
            _validate_bank_code(submission.country, submission.bank_code, submission.bank_name)
        )

    if submission.account_number.strip():
        digits = re.sub(r"\s", "", submission.account_number)
        if not digits.isdigit():
            findings.append(
                _finding(
                    "bad_account_number",
                    "pending",
                    "Account number contains characters other than digits.",
                    "Please send the account number as digits only.",
                )
            )
        elif submission.country == "India" and not 9 <= len(digits) <= 18:
            findings.append(
                _finding(
                    "bad_account_number",
                    "pending",
                    "Indian account numbers are usually 9 to 18 digits.",
                    "Please confirm the account number. It should be 9 to 18 digits.",
                )
            )
        elif submission.country == "United States" and not 4 <= len(digits) <= 17:
            findings.append(
                _finding(
                    "bad_account_number",
                    "pending",
                    "US account number length looks wrong.",
                    "Please confirm the account number.",
                )
            )

    return findings


def _validate_tax(country: str, tax_id: str) -> list[Finding]:
    regime = tax_regime(tax_id)
    shown = tax_id.strip().upper()
    if country == "India":
        if regime == "ein":
            return [
                _finding(
                    "tax_wrong_regime",
                    "reject",
                    f"{shown} is a US EIN. An Indian vendor needs a 15-character GSTIN.",
                    "The tax ID is a US EIN. Please send the company GSTIN.",
                )
            ]
        if regime != "gstin":
            return [
                _finding(
                    "tax_bad_format",
                    "pending",
                    "Tax ID is not a GSTIN. Expected 15 characters, for example 27AABCN1234C1Z2.",
                    "Please send the 15-character GSTIN exactly as it appears on the tax certificate.",
                )
            ]
        compact = compact_tax(tax_id)
        if compact[:2] not in GST_STATES:
            return [
                _finding(
                    "tax_bad_state",
                    "pending",
                    f"GSTIN state code {compact[:2]} is not a recognized GST state.",
                    "The GSTIN state code is not one we can place. Please confirm the number.",
                )
            ]
        if gstin_checksum(compact[:14]) != compact[14]:
            return [
                _finding(
                    "tax_bad_checksum",
                    "pending",
                    f"GSTIN {compact} has the right shape but fails the checksum, which usually means a typo.",
                    f"GSTIN {compact} failed the checksum. Please confirm all 15 characters and resend.",
                )
            ]
        return []

    if regime == "gstin":
        return [
            _finding(
                "tax_wrong_regime",
                "reject",
                f"{compact_tax(tax_id)} is an Indian GSTIN, but the vendor is submitted as a United States company.",
                f"The tax ID {compact_tax(tax_id)} is an Indian GSTIN. A United States vendor needs an EIN such as 12-3456789.",
            )
        ]
    if regime != "ein":
        return [
            _finding(
                "tax_bad_format",
                "pending",
                "Tax ID is not a US EIN. Expected 9 digits, usually written XX-XXXXXXX.",
                "Please send the EIN as 9 digits, written like 12-3456789.",
            )
        ]
    digits = ein_digits(tax_id)
    if digits[:2] in _INVALID_EIN_PREFIXES:
        return [
            _finding(
                "tax_bad_format",
                "reject",
                f"EIN prefix {digits[:2]} is not in the IRS assigned ranges.",
                f"EIN prefix {digits[:2]} is not a valid IRS prefix. Please send the EIN from the CP 575 letter.",
            )
        ]
    return []


def _validate_bank_code(country: str, bank_code: str, bank_name: str) -> list[Finding]:
    code = re.sub(r"[\s-]", "", bank_code.upper())
    if country == "India":
        if aba_checksum_ok(code) and code.isdigit():
            return [
                _finding(
                    "bad_bank_code",
                    "pending",
                    "Bank code is a US routing number. An Indian account needs an 11-character IFSC.",
                    "Please send the IFSC from the bank letter. A routing number cannot be used for an Indian account.",
                )
            ]
        if not _IFSC_SHAPE.fullmatch(code):
            return [
                _finding(
                    "bad_bank_code",
                    "pending",
                    "Bank code is not an IFSC. Expected 11 characters, with a 0 in the fifth position.",
                    "Please send the 11-character IFSC from the bank letter.",
                )
            ]
        return _ifsc_bank_findings(code, bank_name)

    if _IFSC_SHAPE.fullmatch(code):
        return [
            _finding(
                "bad_bank_code",
                "pending",
                "Bank code looks like an Indian IFSC, not a US routing number.",
                "Please send the 9-digit ACH routing number for the US account.",
            )
        ]
    if not (code.isdigit() and len(code) == 9 and aba_checksum_ok(code)):
        return [
            _finding(
                "bad_bank_code",
                "pending",
                "Routing number failed the ABA checksum.",
                "Please confirm the 9-digit routing number. The one provided failed the checksum.",
            )
        ]
    return []


def _ifsc_bank_findings(code: str, bank_name: str) -> list[Finding]:
    prefix = code[:4]
    aliases = IFSC_BANKS.get(prefix)
    if not aliases:
        return [
            _finding(
                "ifsc_prefix_unknown",
                "warn",
                f"IFSC prefix {prefix} is not in the local bank list. The format is valid, so this is only a note.",
            )
        ]
    haystack = (bank_name or "").lower()
    if haystack and not any(alias in haystack for alias in aliases):
        expected = aliases[0].upper()
        return [
            _finding(
                "bank_name_mismatch",
                "pending",
                f"IFSC {code} belongs to {expected}, but the bank name is {bank_name.strip() or 'blank'}.",
                f"IFSC {code} belongs to {expected}, which does not match the bank name on the form. Please correct one of them.",
            )
        ]
    return []
