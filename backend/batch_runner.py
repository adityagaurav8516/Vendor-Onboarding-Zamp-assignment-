"""Batch pipeline runner — processes multiple submissions from CSV or ZIP."""

from __future__ import annotations

import csv
import io
import json
import time
import zipfile
from dataclasses import dataclass, field
from typing import Callable

from backend.field_validator import gstin_checksum
from backend.pipeline import run_pipeline
from backend.submission_loader import coerce_text
from database import db
from database.models import DocumentFile, ExistingVendor, Finding, Submission
from test_data import synthetic

# ---------------------------------------------------------------------------
# CSV / ZIP column names (must match Submission fields exactly)
# ---------------------------------------------------------------------------

SUBMISSION_FIELDS = (
    "legal_name",
    "trade_name",
    "country",
    "tax_id",
    "registration_number",
    "address",
    "contact_name",
    "contact_email",
    "contact_phone",
    "bank_holder_name",
    "bank_name",
    "account_number",
    "bank_code",
    "bank_country",
)


# ---------------------------------------------------------------------------
# BatchResult dataclass
# ---------------------------------------------------------------------------


@dataclass
class BatchResult:
    row_index: int
    submission_id: int | None
    reference: str | None
    vendor_name: str
    status: str
    top_code: str
    elapsed_ms: int
    error: str | None = None


# ---------------------------------------------------------------------------
# Synthetic document generation
# ---------------------------------------------------------------------------


def generate_synthetic_docs(submission: Submission) -> list[DocumentFile]:
    """Auto-generate three synthetic PDF documents from submission fields."""
    s = submission
    inc_lines = [
        "Certificate of Incorporation",
        "Registrar of Companies",
        f"CIN: {s.registration_number}",
        f"Legal name: {s.legal_name}",
        f"Registered address: {s.address}",
        f"Country: {s.country}",
    ]
    tax_lines = [
        "GST Registration Certificate",
        f"GSTIN: {s.tax_id}",
        f"Legal name: {s.legal_name}",
        f"Registered address: {s.address}",
        f"Country: {s.country}",
    ]
    bank_lines = [
        "Cancelled Cheque - Bank account proof",
        f"Account number: {s.account_number}",
        f"Account holder: {s.bank_holder_name}",
        f"Bank name: {s.bank_name}",
        f"IFSC: {s.bank_code}",
        f"Country: {s.bank_country or s.country}",
    ]
    return [
        DocumentFile("incorporation", "incorporation.pdf", synthetic.render_pdf(inc_lines)),
        DocumentFile("tax_certificate", "tax_certificate.pdf", synthetic.render_pdf(tax_lines)),
        DocumentFile("bank_proof", "bank_proof.pdf", synthetic.render_pdf(bank_lines)),
    ]


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------


class BatchParseError(Exception):
    pass


def parse_csv(file_bytes: bytes) -> list[dict]:
    """Parse CSV bytes → list of row dicts.

    Raises BatchParseError if required columns are missing.
    """
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise BatchParseError("CSV file is empty or has no header row.")
    missing = [col for col in SUBMISSION_FIELDS if col not in reader.fieldnames]
    if missing:
        raise BatchParseError(
            f"CSV is missing required columns: {', '.join(missing)}. "
            f"Download the template to see the expected format."
        )
    rows = []
    for i, row in enumerate(reader, start=2):  # row 1 = header
        # Strip whitespace from all values. Cells beyond the header land under the
        # ``None`` key (as a list); they carry no known field, so they are dropped.
        clean = {
            k: (v or "").strip()
            for k, v in row.items()
            if k is not None and not isinstance(v, list)
        }
        clean["_row"] = i
        rows.append(clean)
    if not rows:
        raise BatchParseError("CSV has a header row but no data rows.")
    return rows


def row_to_submission(row: dict) -> Submission:
    return Submission(**{k: coerce_text(row.get(k)) for k in SUBMISSION_FIELDS})


def primary_code(findings: list[Finding]) -> str:
    """The rule that decided the outcome: a reject, else a pending reason, else a warning."""
    for severity in ("reject", "pending", "warn"):
        for item in findings:
            if item.severity == severity:
                return item.code
    return "—"


# ---------------------------------------------------------------------------
# ZIP parsing
# ---------------------------------------------------------------------------


def parse_zip(file_bytes: bytes) -> list[dict]:
    """Parse a ZIP file → list of item dicts.

    Each item dict has:
        submission: dict (required)
        documents: dict[slot → DocumentFile] (may be empty; docs generated synthetically)
        _name: str (source JSON filename)

    Expected ZIP layout:
        vendor_01.json          ← {"submission": {...}, "documents": {...}}
        vendor_02.json
        docs/vendor_01_inc.pdf  ← optional PDFs referenced from JSON
        ...
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
    except zipfile.BadZipFile as exc:
        raise BatchParseError(f"Not a valid ZIP file: {exc}") from exc

    names = zf.namelist()
    json_files = [n for n in names if n.endswith(".json") and not n.startswith("__MACOSX")]
    if not json_files:
        raise BatchParseError(
            "ZIP contains no .json files. Each vendor must be a JSON file; "
            "see the expected structure in the upload page."
        )

    items = []
    for jname in sorted(json_files):
        try:
            payload = json.loads(zf.read(jname))
        except json.JSONDecodeError as exc:
            raise BatchParseError(f"Invalid JSON in {jname}: {exc}") from exc
        if "submission" not in payload:
            raise BatchParseError(
                f"{jname}: missing top-level 'submission' key. "
                "Expected format: {{\"submission\": {{...}}, \"documents\": {{...}}}}"
            )
        sub_dict = payload["submission"]
        missing = [f for f in SUBMISSION_FIELDS if f not in sub_dict]
        if missing:
            raise BatchParseError(
                f"{jname}: missing submission fields: {', '.join(missing)}"
            )

        # Build DocumentFile objects from optional embedded doc refs
        doc_map: dict[str, DocumentFile] = {}
        for slot, path in (payload.get("documents") or {}).items():
            if path in names:
                try:
                    content = zf.read(path)
                    doc_map[slot] = DocumentFile(slot, path.split("/")[-1], content)
                except Exception:
                    pass  # silently skip unreadable docs; synthetics fill the gap

        items.append({"submission": sub_dict, "doc_map": doc_map, "_name": jname})

    return items


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------


def run_batch(
    items: list[dict],
    source: str,  # "csv" or "zip"
    existing: list[ExistingVendor] | None = None,
    on_progress: Callable[[int, int, str, str | None], None] | None = None,
) -> list[BatchResult]:
    """Process a list of parsed items (from parse_csv or parse_zip).

    on_progress(done, total, vendor_name, status_or_None) is called after
    each vendor finishes.  `status` is None while running.
    """
    # Work on a private copy: every vendor saved below joins it, so a company that
    # appears twice in one batch is judged against its own earlier row.
    existing = list(db.load_registry() if existing is None else existing)

    results: list[BatchResult] = []
    total = len(items)

    for i, item in enumerate(items):
        if source == "csv":
            row = item
            vendor_name = row.get("legal_name") or f"Row {row.get('_row', i + 2)}"
        else:
            row = item["submission"]
            vendor_name = row.get("legal_name") or item.get("_name", f"Item {i + 1}")

        if on_progress:
            on_progress(i, total, vendor_name, None)

        t0 = time.perf_counter()
        try:
            submission = row_to_submission(row)

            if source == "csv":
                documents = generate_synthetic_docs(submission)
            else:
                # ZIP: use embedded docs where available, synthesise the rest
                doc_map: dict[str, DocumentFile] = item.get("doc_map", {})
                synth = {d.slot: d for d in generate_synthetic_docs(submission)}
                documents = [doc_map.get(slot, synth[slot]) for slot in ("incorporation", "tax_certificate", "bank_proof")]

            result = run_pipeline(submission, documents, existing, scenario_id=None)
            submission_id, reference = db.save_run(result)
            vendor_id = db.vendor_id_for_submission(submission_id)
            if vendor_id is not None:
                existing.append(
                    ExistingVendor(
                        id=vendor_id,
                        legal_name=submission.legal_name,
                        country=submission.country,
                        tax_id=submission.tax_id,
                        registration_number=submission.registration_number,
                        account_number=submission.account_number,
                        bank_code=submission.bank_code,
                        contact_email=submission.contact_email,
                        status=result.status,
                    )
                )

            top_code = primary_code(result.findings)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)

            br = BatchResult(
                row_index=i + 1,
                submission_id=submission_id,
                reference=reference,
                vendor_name=vendor_name,
                status=result.status,
                top_code=top_code,
                elapsed_ms=elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            br = BatchResult(
                row_index=i + 1,
                submission_id=None,
                reference=None,
                vendor_name=vendor_name,
                status="error",
                top_code="—",
                elapsed_ms=elapsed_ms,
                error=str(exc),
            )

        results.append(br)
        if on_progress:
            on_progress(i + 1, total, vendor_name, br.status)

    return results


# ---------------------------------------------------------------------------
# CSV template
# ---------------------------------------------------------------------------


def _gstin(state: str, pan: str) -> str:
    base = f"{state}{pan}1Z"
    return base + gstin_checksum(base)


# 25-row sample CSV covering approved / pending / rejected outcomes
_SAMPLE_ROWS: list[dict] = [
    # --- 15 approved India vendors ---
    {
        "legal_name": "Aurora Dynamics Pvt Ltd", "trade_name": "Aurora Dynamics",
        "country": "India", "tax_id": _gstin("07", "AABCA1234C"),
        "registration_number": "U72900DL2015PTC111001",
        "address": "11 Nehru Place, New Delhi 110019",
        "contact_name": "Arjun Mehta", "contact_email": "arjun@aurora.in",
        "contact_phone": "9876500001",
        "bank_holder_name": "Aurora Dynamics Pvt Ltd", "bank_name": "HDFC Bank",
        "account_number": "50100100000001", "bank_code": "HDFC0001001", "bank_country": "India",
    },
    {
        "legal_name": "Bright Horizons Solutions Pvt Ltd", "trade_name": "Bright Horizons",
        "country": "India", "tax_id": _gstin("29", "AABCB2345D"),
        "registration_number": "U72200KA2016PTC111002",
        "address": "42 MG Road, Bengaluru, Karnataka 560001",
        "contact_name": "Deepa Rao", "contact_email": "deepa@brighthorizons.in",
        "contact_phone": "9876500002",
        "bank_holder_name": "Bright Horizons Solutions Pvt Ltd", "bank_name": "ICICI Bank",
        "account_number": "001200000002", "bank_code": "ICIC0001002", "bank_country": "India",
    },
    {
        "legal_name": "ClearPath Technologies Pvt Ltd", "trade_name": "ClearPath",
        "country": "India", "tax_id": _gstin("27", "AABCC3456E"),
        "registration_number": "U72900MH2017PTC111003",
        "address": "7 BKC, Bandra, Mumbai, Maharashtra 400051",
        "contact_name": "Suresh Nair", "contact_email": "suresh@clearpath.in",
        "contact_phone": "9876500003",
        "bank_holder_name": "ClearPath Technologies Pvt Ltd", "bank_name": "Axis Bank",
        "account_number": "50200000000003", "bank_code": "UTIB0000003", "bank_country": "India",
    },
    {
        "legal_name": "Delta Forge Industries Pvt Ltd", "trade_name": "Delta Forge",
        "country": "India", "tax_id": _gstin("33", "AABCD4567F"),
        "registration_number": "U28910TN2015PTC111004",
        "address": "8 Anna Salai, Chennai, Tamil Nadu 600002",
        "contact_name": "Priya Krishnamurthy", "contact_email": "priya@deltaforge.in",
        "contact_phone": "9876500004",
        "bank_holder_name": "Delta Forge Industries Pvt Ltd", "bank_name": "Kotak Mahindra Bank",
        "account_number": "1234500000004", "bank_code": "KKBK0000004", "bank_country": "India",
    },
    {
        "legal_name": "EcoPower Ventures Pvt Ltd", "trade_name": "EcoPower",
        "country": "India", "tax_id": _gstin("09", "AABCE5678G"),
        "registration_number": "U40100UP2018PTC111005",
        "address": "21 Sector 62, Noida, Uttar Pradesh 201309",
        "contact_name": "Rohit Gupta", "contact_email": "rohit@ecopower.in",
        "contact_phone": "9876500005",
        "bank_holder_name": "EcoPower Ventures Pvt Ltd", "bank_name": "SBI",
        "account_number": "30000000000005", "bank_code": "SBIN0000005", "bank_country": "India",
    },
    {
        "legal_name": "Frontier Logistics Pvt Ltd", "trade_name": "Frontier Logistics",
        "country": "India", "tax_id": _gstin("06", "AABCF6789H"),
        "registration_number": "U63090HR2016PTC111006",
        "address": "14 Sector 44, Gurugram, Haryana 122003",
        "contact_name": "Kavya Sharma", "contact_email": "kavya@frontierlog.in",
        "contact_phone": "9876500006",
        "bank_holder_name": "Frontier Logistics Pvt Ltd", "bank_name": "Yes Bank",
        "account_number": "60000000000006", "bank_code": "YESB0000006", "bank_country": "India",
    },
    {
        "legal_name": "GlobalMart Retail Pvt Ltd", "trade_name": "GlobalMart",
        "country": "India", "tax_id": _gstin("19", "AABCG7890I"),
        "registration_number": "U52100WB2019PTC111007",
        "address": "3 Park Street, Kolkata, West Bengal 700016",
        "contact_name": "Ananya Das", "contact_email": "ananya@globalmart.in",
        "contact_phone": "9876500007",
        "bank_holder_name": "GlobalMart Retail Pvt Ltd", "bank_name": "PNB",
        "account_number": "50000000000007", "bank_code": "PUNB0000007", "bank_country": "India",
    },
    {
        "legal_name": "HighTech Fabricators Pvt Ltd", "trade_name": "HighTech Fab",
        "country": "India", "tax_id": _gstin("24", "AABCH8901J"),
        "registration_number": "U28910GJ2017PTC111008",
        "address": "56 GIDC, Vadodara, Gujarat 390010",
        "contact_name": "Manish Patel", "contact_email": "manish@hightechfab.in",
        "contact_phone": "9876500008",
        "bank_holder_name": "HighTech Fabricators Pvt Ltd", "bank_name": "Bank of Baroda",
        "account_number": "10000200000008", "bank_code": "BARB0VADBRC", "bank_country": "India",
    },
    {
        "legal_name": "Indus Pharma Pvt Ltd", "trade_name": "Indus Pharma",
        "country": "India", "tax_id": _gstin("36", "AABCI9012K"),
        "registration_number": "U24231TS2016PTC111009",
        "address": "9 Hitech City, Hyderabad, Telangana 500081",
        "contact_name": "Srinivas Reddy", "contact_email": "srinivas@induspharma.in",
        "contact_phone": "9876500009",
        "bank_holder_name": "Indus Pharma Pvt Ltd", "bank_name": "HDFC Bank",
        "account_number": "50100100000009", "bank_code": "HDFC0001009", "bank_country": "India",
    },
    {
        "legal_name": "Jade Textiles Pvt Ltd", "trade_name": "Jade Textiles",
        "country": "India", "tax_id": _gstin("08", "AABCJ0123L"),
        "registration_number": "U17200RJ2015PTC111010",
        "address": "72 MI Road, Jaipur, Rajasthan 302001",
        "contact_name": "Meera Joshi", "contact_email": "meera@jadetextiles.in",
        "contact_phone": "9876500010",
        "bank_holder_name": "Jade Textiles Pvt Ltd", "bank_name": "ICICI Bank",
        "account_number": "001200000010", "bank_code": "ICIC0001010", "bank_country": "India",
    },
    {
        "legal_name": "Kiran Energy Systems Pvt Ltd", "trade_name": "Kiran Energy",
        "country": "India", "tax_id": _gstin("32", "AABCK1234M"),
        "registration_number": "U40100KL2018PTC111011",
        "address": "18 MG Road, Kochi, Kerala 682001",
        "contact_name": "Thomas Kurien", "contact_email": "thomas@kiranergy.in",
        "contact_phone": "9876500011",
        "bank_holder_name": "Kiran Energy Systems Pvt Ltd", "bank_name": "Federal Bank",
        "account_number": "10000200000011", "bank_code": "FDRL0000011", "bank_country": "India",
    },
    {
        "legal_name": "Luminary Consulting Pvt Ltd", "trade_name": "Luminary",
        "country": "India", "tax_id": _gstin("07", "AABCL2345N"),
        "registration_number": "U74140DL2020PTC111012",
        "address": "35 Vasant Kunj, New Delhi 110070",
        "contact_name": "Nisha Kapoor", "contact_email": "nisha@luminaryconsult.in",
        "contact_phone": "9876500012",
        "bank_holder_name": "Luminary Consulting Pvt Ltd", "bank_name": "Axis Bank",
        "account_number": "50200000000012", "bank_code": "UTIB0000012", "bank_country": "India",
    },
    {
        "legal_name": "Meridian Steel Pvt Ltd", "trade_name": "Meridian Steel",
        "country": "India", "tax_id": _gstin("20", "AABCM3456O"),
        "registration_number": "U27100JH2015PTC111013",
        "address": "7 Main Road, Jamshedpur, Jharkhand 831001",
        "contact_name": "Ajay Singh", "contact_email": "ajay@meridiansteel.in",
        "contact_phone": "9876500013",
        "bank_holder_name": "Meridian Steel Pvt Ltd", "bank_name": "SBI",
        "account_number": "30000000000013", "bank_code": "SBIN0000013", "bank_country": "India",
    },
    {
        "legal_name": "NovaCraft Pvt Ltd", "trade_name": "NovaCraft",
        "country": "India", "tax_id": _gstin("21", "AABCN4567P"),
        "registration_number": "U32300OR2019PTC111014",
        "address": "11 Saheed Nagar, Bhubaneswar, Odisha 751007",
        "contact_name": "Sunita Swain", "contact_email": "sunita@novacraft.in",
        "contact_phone": "9876500014",
        "bank_holder_name": "NovaCraft Pvt Ltd", "bank_name": "UCO Bank",
        "account_number": "00000000000014", "bank_code": "UCBA0000014", "bank_country": "India",
    },
    {
        "legal_name": "Orion Infra Pvt Ltd", "trade_name": "Orion Infra",
        "country": "India", "tax_id": _gstin("04", "AABCO5678Q"),
        "registration_number": "U45400PB2017PTC111015",
        "address": "22 Sector 17, Chandigarh 160017",
        "contact_name": "Gurpreet Sandhu", "contact_email": "gurpreet@orioninfra.in",
        "contact_phone": "9876500015",
        "bank_holder_name": "Orion Infra Pvt Ltd", "bank_name": "HDFC Bank",
        "account_number": "50100100000015", "bank_code": "HDFC0001015", "bank_country": "India",
    },
    # --- 5 pending India vendors (bad GSTIN checksum → tax_bad_checksum) ---
    {
        "legal_name": "Apex Cloud Solutions Pvt Ltd", "trade_name": "Apex Cloud",
        "country": "India", "tax_id": _gstin("07", "AABCA1111X")[:-1] + "9",  # bad checksum
        "registration_number": "U72900DL2016PTC222001",
        "address": "1 Cyber Hub, Gurugram, Haryana 122002",
        "contact_name": "Vinod Kumar", "contact_email": "vinod@apexcloud.in",
        "contact_phone": "9876600001",
        "bank_holder_name": "Apex Cloud Solutions Pvt Ltd", "bank_name": "HDFC Bank",
        "account_number": "50100200000001", "bank_code": "HDFC0002001", "bank_country": "India",
    },
    {
        "legal_name": "Beta Retail Pvt Ltd", "trade_name": "Beta Retail",
        "country": "India", "tax_id": _gstin("27", "AABCB2222Y")[:-1] + "9",  # bad checksum
        "registration_number": "U52100MH2018PTC222002",
        "address": "2 Phoenix Mills, Mumbai, Maharashtra 400013",
        "contact_name": "Riya Shah", "contact_email": "riya@betaretail.in",
        "contact_phone": "9876600002",
        "bank_holder_name": "Beta Retail Pvt Ltd", "bank_name": "ICICI Bank",
        "account_number": "001200000020", "bank_code": "ICIC0002002", "bank_country": "India",
    },
    {
        "legal_name": "Cobalt Pharma Pvt Ltd", "trade_name": "Cobalt Pharma",
        "country": "India", "tax_id": _gstin("29", "AABCC3333Z")[:-1] + "9",  # bad checksum
        "registration_number": "U24231KA2019PTC222003",
        "address": "3 Whitefield, Bengaluru, Karnataka 560066",
        "contact_name": "Ravi Hegde", "contact_email": "ravi@cobaltpharma.in",
        "contact_phone": "9876600003",
        "bank_holder_name": "Cobalt Pharma Pvt Ltd", "bank_name": "Axis Bank",
        "account_number": "50200000000030", "bank_code": "UTIB0000030", "bank_country": "India",
    },
    {
        "legal_name": "DataStream Analytics Pvt Ltd", "trade_name": "DataStream",
        "country": "India", "tax_id": _gstin("33", "AABCD4444A")[:-1] + "9",  # bad checksum
        "registration_number": "U72900TN2017PTC222004",
        "address": "4 OMR Road, Chennai, Tamil Nadu 600097",
        "contact_name": "Sathya Krishnan", "contact_email": "sathya@datastream.in",
        "contact_phone": "9876600004",
        "bank_holder_name": "DataStream Analytics Pvt Ltd", "bank_name": "Kotak Mahindra Bank",
        "account_number": "1234500000040", "bank_code": "KKBK0000040", "bank_country": "India",
    },
    {
        "legal_name": "EarthMover Constructions Pvt Ltd", "trade_name": "EarthMover",
        "country": "India", "tax_id": _gstin("09", "AABCE5555B")[:-1] + "9",  # bad checksum
        "registration_number": "U45200UP2020PTC222005",
        "address": "5 Gomti Nagar, Lucknow, Uttar Pradesh 226010",
        "contact_name": "Ashish Verma", "contact_email": "ashish@earthmover.in",
        "contact_phone": "9876600005",
        "bank_holder_name": "EarthMover Constructions Pvt Ltd", "bank_name": "SBI",
        "account_number": "30000000000050", "bank_code": "SBIN0000050", "bank_country": "India",
    },
    # --- 3 non-India vendors (country_unsupported → pending) ---
    {
        "legal_name": "Global Tech Corp", "trade_name": "Global Tech",
        "country": "USA", "tax_id": "82-1234567",
        "registration_number": "EIN-821234567",
        "address": "1 Silicon Valley Blvd, San Jose, CA 95101",
        "contact_name": "John Smith", "contact_email": "john@globaltech.us",
        "contact_phone": "4155550001",
        "bank_holder_name": "Global Tech Corp", "bank_name": "Chase Bank",
        "account_number": "1000000001", "bank_code": "CHASUS33", "bank_country": "USA",
    },
    {
        "legal_name": "Euro Supplies GmbH", "trade_name": "Euro Supplies",
        "country": "Germany", "tax_id": "DE123456789",
        "registration_number": "HRB12345",
        "address": "10 Unter den Linden, Berlin 10117",
        "contact_name": "Hans Müller", "contact_email": "hans@eurosupplies.de",
        "contact_phone": "4930550001",
        "bank_holder_name": "Euro Supplies GmbH", "bank_name": "Deutsche Bank",
        "account_number": "DE89370400440532013000", "bank_code": "DEUTDEDB", "bank_country": "Germany",
    },
    {
        "legal_name": "Pacific Rim Trading Ltd", "trade_name": "Pacific Rim",
        "country": "Singapore", "tax_id": "201234567A",
        "registration_number": "201234567A",
        "address": "1 Raffles Place, Singapore 048616",
        "contact_name": "Li Wei", "contact_email": "liwei@pacificrim.sg",
        "contact_phone": "6555000001",
        "bank_holder_name": "Pacific Rim Trading Ltd", "bank_name": "DBS Bank",
        "account_number": "0019999999", "bank_code": "DBSSSGSG", "bank_country": "Singapore",
    },
    # --- 2 blocked/duplicate triggers ---
    {
        "legal_name": "Bluepeak Traders Pvt Ltd", "trade_name": "Bluepeak Traders",
        "country": "India", "tax_id": _gstin("06", "AABCB7890G"),  # matches blocked vendor in registry
        "registration_number": "U51909DL2016PTC111222",
        "address": "Plot 14, Udyog Vihar, Gurugram, Haryana 122015",
        "contact_name": "Sanjay Bhatia", "contact_email": "sanjay@bluepeaktraders.in",
        "contact_phone": "9810011223",
        "bank_holder_name": "Bluepeak Traders Pvt Ltd", "bank_name": "Axis Bank",
        "account_number": "912010011223344", "bank_code": "UTIB0000456", "bank_country": "India",
    },
    {
        "legal_name": "Shadow Clone Pvt Ltd", "trade_name": "Shadow Clone",
        "country": "India", "tax_id": _gstin("09", "AABCN9012E"),  # duplicates Nova Supplies account
        "registration_number": "U46900DL2021PTC333666",
        "address": "99 Sector 18, Noida, Uttar Pradesh 201301",
        "contact_name": "Duplicate User", "contact_email": "dup@shadowclone.in",
        "contact_phone": "9899009999",
        "bank_holder_name": "Shadow Clone Pvt Ltd", "bank_name": "HDFC Bank",
        "account_number": "50100234567890",  # same account as Nova Supplies (duplicate trigger)
        "bank_code": "HDFC0001234", "bank_country": "India",
    },
]


def sample_csv_bytes() -> bytes:
    """Return a downloadable CSV template with 25 sample rows."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=SUBMISSION_FIELDS)
    writer.writeheader()
    for row in _SAMPLE_ROWS:
        writer.writerow({k: row.get(k, "") for k in SUBMISSION_FIELDS})
    return buf.getvalue().encode("utf-8")


def results_csv_bytes(results: list[BatchResult]) -> bytes:
    """Export batch results as a CSV for download."""
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["#", "Reference", "Vendor", "Decision", "Primary Rule", "Time (ms)", "Error"],
    )
    writer.writeheader()
    for r in results:
        writer.writerow(
            {
                "#": r.row_index,
                "Reference": r.reference or "",
                "Vendor": r.vendor_name,
                "Decision": r.status,
                "Primary Rule": r.top_code,
                "Time (ms)": r.elapsed_ms,
                "Error": r.error or "",
            }
        )
    return buf.getvalue().encode("utf-8")
