# VendorFlow — Autonomous Vendor Onboarding & Verification

A production-grade, autonomous vendor onboarding workflow that turns messy incoming vendor packs (company details, tax registrations, bank proofs, and incorporation certificates) into defensible, audit-trailed decisions: **Approved**, **Needs a reply (Pending)**, or **Rejected**.

VendorFlow reads documents, verifies them against deterministic business rules and cross-checks them against the company's master ERP vendor registry. When errors or missing papers are found, it drafts actionable vendor replies, supports an end-to-end **Vendor Reply Loop** with selective re-evaluation, and provides **Batch Upload** capabilities for processing dozens of submissions at scale.

Everything runs on real files end to end: real PDFs are parsed, Indian GSTIN and US ABA routing checksums are validated, company names are fuzzily matched with suffix normalization, and every run is stamped with an immutable rule-engine version.

---

## 1. Feature Highlights

### ⚡ Autonomous 7-Stage Verification Pipeline
Incoming vendor submissions pass through 7 deterministic, ordered stages:
1. **Submission Received**: Payload normalization and sanitization.
2. **Document Intake & Processing**: Text-layer extraction, slot classification, and fallback OCR.
3. **Field Validation**: Country-specific shape and checksum validation (GSTIN, CIN, EIN, IFSC, ABA routing, email, phone).
4. **Cross-Document Verification**: Compares what the vendor typed against what the official certificates actually prove.
5. **Master Registry & Duplicate Detection**: Checks against the internal ERP database for duplicate tax IDs, reused bank accounts, and sanctioned entities.
6. **Decision Engine**: Strict priority aggregation where `reject` beats `pending`, and warnings never block.
7. **Notification & Audit Trail**: Drafts precise, professional vendor correction emails and packages the run for persistent storage.

### 👁️ Multimodal OCR & Layout Parsing via Google Gemini API
- **Local Text-First**: If a PDF has a native text layer, PyMuPDF reads it instantly offline with zero API calls.
- **Hosted Gemini Flash OCR**: For scanned PDFs or mobile photos (`.png`, `.jpg`, `.webp`), pages without text layers are automatically rasterized at 200/300 DPI and sent to **Google Gemini Flash** via `GEMINI_API_KEY`.
- **Spatial Geometry Layout Parser (`backend/layout_parser.py`)**: Rather than naive string dumping, bounding boxes returned by Gemini are clustered into vertical rows and spatial lines. Key-value pairs are recovered three ways: after colons, across cell gaps, and from stacked labels.
- **Zero-Hallucination Extraction Precedence**: 
  $$\text{Printed Document Labels} \succ \text{Spatial Layout Parser} \succ \text{Model Extractor}$$
  Any external model or layout parser may only fill in blanks—it can never overwrite an explicitly printed document label.

### 🔄 Multi-Turn Vendor Reply Loop (`pages/4_Vendor_Replies.py`)
In real procurement, submissions rarely pass on the first attempt—vendors submit typos, miss documents, or upload individual bank proofs. VendorFlow turns a one-shot checker into a complete multi-turn workflow:
- **Upload Missing or Corrected Paper**: Upload missing documents (e.g., GST certificate) or replace flawed ones without re-uploading unchanged files.
- **In-Place Field Corrections**: Correct flagged form inputs (e.g., GSTIN check digits, bank holder names) with original values pre-filled.
- **Selective Re-evaluation Engine (`backend/reply_runner.py`)**: Re-runs **only** the rules affected by the modified fields or uploaded documents (e.g., re-parsing only the new PDF, skipping duplicate detection if tax ID and bank accounts did not change), avoiding unnecessary OCR latency.
- **Parent-Child Run Lineage**: Reply runs are linked in SQLite via `parent_id` foreign keys, with bidirectional audit logging (`vendor_reply` and `reply_received` events).
- **Interactive Before/After Diff Viewer**: Displays a visual workflow transition banner (`Needs a reply ➔ Approved`), side-by-side field diffs, document action logs (added, replaced, retained), and an itemized checklist showing resolved issues with green checkmarks.

### 📦 Batch Upload & High-Throughput Processing (`pages/5_Batch_Upload.py`)
Designed for procurement teams onboarding "dozens of new vendors every quarter":
- **CSV Batch Upload**: Upload a CSV where each row represents a vendor submission. Valid synthetic PDFs (incorporation, GST certificate, bank cheque) are auto-generated on the fly from field values, allowing rapid evaluation without assembling separate files. Includes a **one-click downloadable 25-row sample CSV template** covering approved, pending, and rejected variants.
- **ZIP Batch Upload**: Upload a `.zip` archive containing multiple vendor JSON payloads and optional embedded PDF attachments.
- **Live Streamlit Progress Bar**: Real-time progress updates displaying current vendor names, processing milestones, and elapsed times.
- **Aggregated Results & CSV Export**: Displays breakdown metrics (approved/pending/rejected), an interactive results table, and one-click results CSV export.

### 🛡️ Master Vendor Registry & Payout Fraud Prevention
Incoming submissions are cross-referenced against the company's internal ERP Master Registry (`test_data/seed.py` / `database/vendorflow.db`):
- **Payout Redirection Attack Prevention**: An unauthenticated submission matching an existing approved vendor's tax ID with a *different* bank account is immediately **Rejected** (`duplicate_bank_change`).
- **Shared Payout Account Detection**: When a different legal entity submits a bank account already registered to another vendor (e.g., Zenith Components), it triggers a `pending` flag (`shared_bank_account`) naming the owning record.
- **Sanctioned / Blocked Entity Enforcement**: Matches against blocked entities (e.g., Bluepeak Traders) result in immediate rejection (`blocked_vendor`).

### 🏷️ Immutable Rule Engine Versioning
Every evaluation is stamped with an immutable version string (e.g., `rule_engine_version: v1.0.0` in `backend/rule_version.py`). Stored submissions retain the version they were judged under, ensuring regulatory auditability.

---

## 2. Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/adityaxd/Vendor-Onboarding-Zamp-assignment-.git
cd Vendor-Onboarding-Zamp-assignment-

# Set up virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux

# Install dependencies
pip install -r requirements.txt
```

### Environment Configuration (Optional)

VendorFlow runs **100% offline out of the box** using built-in synthetic documents and local PDF text-layer extraction.

To enable multimodal OCR for scanned images and paper photos, create a `.env` file in the project root:

```bash
# Google Gemini API key for multimodal scanned document OCR
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash          # Optional, defaults to gemini-3.5-flash

# Optional: OpenAI-compatible endpoint for secondary text fallback
OPENAI_API_KEY=your_openai_key_here    # Optional
```

### Running the Web Application

```bash
streamlit run app.py
```

Open `http://localhost:8501` in your browser.

### Running CLI Commands

```bash
# Run a prepared demo scenario with JSON output to stdout
python -m backend.cli --demo happy_path

# Run and persist to the SQLite database
python -m backend.cli --demo identity_mismatch --save

# Run against a custom submission JSON file
python -m backend.cli path/to/submission.json --save
```

### Running Automated Tests

```bash
python -m pytest -q
```
*Current test suite: **108 passed, 1 skipped** (the live Gemini scan test is skipped when running in mock offline mode).*

---

## 3. The 7-Stage Process Map

| # | Stage | What It Does | Why It Is Isolated |
|---|---|---|---|
| **1** | **Submission Received** | Normalizes form payload, trims whitespace, standardizes casing. | Creates a clean, referenceable baseline state. |
| **2** | **Document Intake** | Opens files with PyMuPDF, reads text layers, rasterizes scans for Gemini OCR, classifies document slots, and runs spatial layout parsing. | Document readability/classification issues are separated from vendor identity issues. |
| **3** | **Field Validation** | Runs country-specific format checks, mod-36 GSTIN checksum, CIN format, IFSC code bank mapping, and ABA routing checksum. | Catches clerical errors and typos before comparing across documents. |
| **4** | **Cross-Verification** | Compares form inputs against extracted certificate text; verifies bank account holder against entity name; verifies GST state against registered address. | Detects subtle identity fraud and jurisdiction mismatches. |
| **5** | **Duplicate & Registry** | Checks tax ID, registration number, and bank account against the company ERP registry; enforces blocklists. | Isolates stateful database checks for auditability. |
| **6** | **Decision Engine** | Applies deterministic priority rules: `reject` > `pending` > `approved`. | Centralizes policy logic for explainability. |
| **7** | **Notification & Audit** | Drafts customized vendor reply emails and commits all stages, field checks, and document snippets to the database. | Ensures the vendor is told exactly what to fix. |

---

## 4. Complete Rule Catalog

| Stage | Rule Code | Severity | Description |
|:---|:---|:---|:---|
| **documents** | `missing_document` | `pending` | A required slot (incorporation, tax, or bank proof) has no attached file. |
| **documents** | `wrong_document` | `pending` | Uploaded document content does not match the slot. |
| **documents** | `unreadable_document` | `pending` | File is unparseable, password-encrypted, or corrupt. |
| **documents** | `extraction_unclear` | `pending` | Document parsed but no recognizable vendor fields were recovered. |
| **documents** | `ocr_low_confidence` | `warn` | Scanned page mean OCR confidence is below 0.75; flagged for human review. |
| **validate** | `missing_fields` | `pending` | One or more mandatory form fields are blank. |
| **validate** | `bad_email` / `bad_phone` | `pending` | Contact information is malformed or invalid for the country. |
| **validate** | `free_email` | `warn` | Contact uses a public domain (Gmail, Yahoo, Outlook). |
| **validate** | `tax_bad_format` | `pending` | Identifier does not match GSTIN (India) or EIN (US) shape. |
| **validate** | `tax_bad_checksum` | `pending` | GSTIN has valid 15-char structure but fails the mod-36 checksum (typo). |
| **validate** | `tax_bad_state` | `pending` | GSTIN state code does not correspond to a valid Indian state/UT. |
| **validate** | `tax_wrong_regime` | **`reject`** | GSTIN submitted for a US entity or EIN submitted for an Indian entity. |
| **validate** | `bad_registration` | `pending` | Indian entity does not provide a valid 21-character CIN. |
| **validate** | `cin_on_us_vendor` | `warn` | US vendor provides an Indian CIN-shaped registration number. |
| **validate** | `bad_bank_code` | `pending` | Malformed IFSC (India) or failed ABA routing checksum (US). |
| **validate** | `bank_name_mismatch` | `pending` | IFSC 4-letter bank prefix contradicts the declared bank name. |
| **validate** | `ifsc_prefix_unknown` | `warn` | IFSC structure is valid but prefix is not in the recognized bank list. |
| **validate** | `bad_account_number` | `pending` | Account number contains non-digits or implausible length for country. |
| **validate** | `country_unsupported` | `pending` | Vendor country is outside India/US; queued for manual procurement review. |
| **cross** | `identity_name_gap` | `pending` | Name on incorporation/tax certificate differs from the form legal name. |
| **cross** | `identity_id_mismatch` | `pending` | Tax ID or CIN on certificate differs from the form input. |
| **cross** | `identity_document_mismatch` | `pending` | Name and ID on a certificate belong to an entirely different entity. |
| **cross** | `identity_pack_mismatch` | **`reject`** | Multiple identity documents belong to an unrelated third party. |
| **cross** | `doc_field_missing` | `pending` | Required certificate fails to state the entity name or identifier. |
| **cross** | `bank_holder_mismatch` | `pending` | Payout account holder is an individual person rather than the legal company. |
| **cross** | `bank_holder_partial` | `pending` | Account holder is close but differs after legal suffix stripping. |
| **cross** | `bank_letter_disagrees` | `pending` | Form account holder differs from the name extracted from bank proof. |
| **cross** | `account_number_mismatch` | `pending` | Account number on the bank proof disagrees with the form input. |
| **cross** | `bank_code_mismatch` | `pending` | Bank code on the bank letter disagrees with the form input. |
| **cross** | `state_address_mismatch` | `pending` | State indicated in the GSTIN is not mentioned in the registered address. |
| **cross** | `cross_border_bank` | `pending` | Bank account country differs from the entity country. |
| **cross** | `jurisdiction_conflict` | `pending` | Address, identity papers, and tax IDs belong to conflicting jurisdictions. |
| **duplicate** | `shared_bank_account` | `pending` | Payout account is already registered to a different legal entity. |
| **duplicate** | `similar_name` | `warn` | Legal name is nearly identical to an existing record, but tax ID differs. |
| **duplicate** | `prior_submission` | `warn` | Same tax ID already exists in non-approved state; judged independently. |
| **duplicate** | `duplicate_same_account` | `pending` | Vendor already approved with identical payout details; no new record needed. |
| **duplicate** | `duplicate_bank_change` | **`reject`** | Vendor already approved, submitted with a **different** bank account (redirection attack). |
| **duplicate** | `blocked_vendor` | **`reject`** | Tax ID or CIN matches an entity on the company blocklist. |

---

## 5. Prepared Edge Cases

All scenarios are pre-packaged and executable with one click from **Admin Dashboard → Test scenarios**:

| Scenario ID | Test Pack Name | Core Challenge Tested | Expected Decision | Key Rule Fired |
|---|---|---|---|---|
| `happy_path` | **Happy Path** | Clean pack: all documents present, valid GSTIN, matching names, clear registry. | **Approved** | *(None)* |
| `missing_gst` | **Missing GST Certificate** | Incorporation and cheque present, GST certificate omitted. | **Needs a reply** | `missing_document` |
| `identity_mismatch` | **Bank Holder Is a Person** | Entity is *PQR Enterprises Pvt Ltd*, account held by individual *Rahul Sharma*. | **Needs a reply** | `bank_holder_mismatch` |
| `invalid_gstin` | **GSTIN Typo** | Valid 15-character GSTIN structure with corrupted check digit. | **Needs a reply** | `tax_bad_checksum` |
| `duplicate_bank_account` | **Shared Bank Account** | New entity submits bank account already approved for *Zenith Components*. | **Needs a reply** | `shared_bank_account` |
| `blocked_vendor` | **Blocked Vendor** | Clean documents, but tax registration belongs to blocklisted *Bluepeak Traders*. | **Rejected** | `blocked_vendor` |

---

## 6. Application Tour & Screen Layout

1. **Overview (`app.py`)**:
   - Hero summary, live stats from SQLite database, and direct navigation buttons into single submission, vendor replies, batch upload, and dashboard.
2. **Run a Submission (`pages/1_Vendor_Submission.py`)**:
   - Pick a prepared demo scenario, upload your own JSON payload, or fill out the form manually.
   - Paced live stage execution view (~0.3s per stage) showing real-time logs, field comparisons, and drafted email notifications.
3. **Vendor Replies (`pages/4_Vendor_Replies.py`)**:
   - Multi-turn reply processor for pending submissions.
   - Replace or upload missing files, adjust form fields, re-run affected rules selectively, and inspect before/after diffs.
4. **Batch Upload (`pages/5_Batch_Upload.py`)**:
   - High-throughput CSV or ZIP ingestion with live progress bar and downloadable results.
5. **Admin Dashboard (`pages/2_Admin_Dashboard.py`)**:
   - Full submission history with multi-status filters and search.
   - Decision mix charts and rule frequency analytics.
   - Test scenarios self-test harness.
   - **Master Vendor Registry**: View the 3 baseline ERP vendors (Zenith, Orbit, Bluepeak) used for duplicate checks.
   - Safe Demo Data Reset.
6. **Vendor Record (`pages/3_Vendor_Details.py`)**:
   - Complete audit trail: replayable timeline, extracted text snippets, side-by-side field checks, document downloads, and reviewer manual overrides.

---

## 7. Submission & Document Formats

### Single Vendor JSON Payload

```json
{
  "submission": {
    "legal_name": "ABC Technologies Pvt Ltd",
    "trade_name": "ABC Tech",
    "country": "India",
    "tax_id": "07AABCA1234C1ZH",
    "registration_number": "U72900DL2015PTC123456",
    "address": "22 Nehru Place, New Delhi, Delhi 110019",
    "contact_name": "Rahul Sharma",
    "contact_email": "rahul@abctech.in",
    "contact_phone": "9876543210",
    "bank_holder_name": "ABC Technologies Pvt Ltd",
    "bank_name": "HDFC Bank",
    "account_number": "50100987654321",
    "bank_code": "HDFC0001234",
    "bank_country": "India"
  },
  "documents": {
    "incorporation": "packs/abc-incorporation.pdf",
    "tax_certificate": "packs/abc-gst.pdf",
    "bank_proof": "packs/abc-cheque.pdf"
  }
}
```

### Batch Upload CSV Format

A flat CSV where headers match `Submission` attributes. Missing document attachments are automatically rendered as synthetic PDFs:

```csv
legal_name,trade_name,country,tax_id,registration_number,address,contact_name,contact_email,contact_phone,bank_holder_name,bank_name,account_number,bank_code,bank_country
Aurora Dynamics Pvt Ltd,Aurora,India,07AABCA1234C1ZS,U72900DL2015PTC111001,"11 Nehru Place, New Delhi 110019",Arjun Mehta,arjun@aurora.in,9876500001,Aurora Dynamics Pvt Ltd,HDFC Bank,50100100000001,HDFC0001001,India
```

---

## 8. Repository Layout

```
Vendor-Onboarding-Zamp-assignment-/
├── app.py                      # Main landing page & overview metrics
├── pages/
│   ├── 1_Vendor_Submission.py   # Interactive single submission & live stage visualizer
│   ├── 2_Admin_Dashboard.py     # Portfolio analytics, edge cases, registry & reset
│   ├── 3_Vendor_Details.py      # Detailed audit trail, document views & reviewer overrides
│   ├── 4_Vendor_Replies.py      # Vendor Reply Loop: file replacement, diff viewer
│   └── 5_Batch_Upload.py        # High-throughput batch processing from CSV or ZIP
├── backend/
│   ├── pipeline.py              # Main 7-stage synchronous execution pipeline
│   ├── field_validator.py       # Checksum, shape, regex, and country validations
│   ├── cross_verification.py    # Cross-document field matching & consistency logic
│   ├── duplicate_detector.py    # Master registry check, duplicate accounts, blocklists
│   ├── decision_engine.py       # Priority ordering (reject > pending > approved)
│   ├── reply_runner.py          # Selective re-evaluation engine & before/after diff logic
│   ├── batch_runner.py          # CSV/ZIP parsing & synthetic document generator
│   ├── document_parser.py       # PyMuPDF text reader & OCR routing
│   ├── layout_parser.py         # Spatial geometry-aware label/value reconstructor
│   ├── ocr_engine.py            # Google Gemini Flash API vision adapter
│   ├── ai_extractor.py          # Field extraction precedence & secondary fallback
│   ├── names.py                 # Suffix-tolerant fuzzy string token-sort scoring
│   ├── rule_version.py          # Rule engine version stamping (v1.0.0)
│   ├── notification_generator.py# Vendor correction email generator
│   ├── submission_loader.py     # JSON submission schema parser
│   └── cli.py                   # Command-line interface
├── database/
│   ├── db.py                    # SQLite repository, schema migrations, and queries
│   └── models.py                # Dataclasses (Submission, RunResult, ReplyDiff, etc.)
├── frontend/
│   └── ui.py                    # Shared styles, layout components, and HTML renderers
├── test_data/
│   ├── scenarios.py             # The 6 prepared runnable edge-case packs
│   ├── seed.py                  # ERP Master Registry baseline data (Zenith, Orbit, Bluepeak)
│   └── synthetic.py             # ReportLab synthetic PDF generation helpers
└── tests/
    ├── test_batch_runner.py     # 21 unit & integration tests for batch processing
    ├── test_reply_runner.py     # Unit tests for selective re-runs and diff computation
    ├── test_pipeline.py         # Pipeline contract and invariant tests
    ├── test_validation.py       # Checksum, regex, and field validation tests
    ├── test_ocr.py              # PyMuPDF, Gemini adapter, and layout parser tests
    └── test_cli.py              # Command-line interface tests
```

---

## 9. Submission Verification & Deliverables Checklist

- [x] **Autonomous Verification Pipeline**: Deterministic 7-stage engine covering Indian and US vendor packs.
- [x] **Multimodal OCR & Layout Geometry**: Google Gemini Flash integration with spatial row and label clustering.
- [x] **Multi-Turn Vendor Reply Loop**: Document replacement, field adjustments, selective rule re-evaluation, and visual diff viewer.
- [x] **Batch Ingestion**: High-throughput CSV/ZIP batch upload with synthetic document generation and real-time progress bar.
- [x] **Zero-Hallucination Guarantee**: Strict precedence where printed document labels always overrule external models.
- [x] **Defensible Audit Trail**: Stamped rule-engine versioning, full stage logs, field comparison scores, reviewer manual overrides.
- [x] **Comprehensive Test Suite**: 108 tests passing (`python -m pytest -q`).
- [x] **Offline-First Resilience**: Entire application runs offline without external API keys.
