# VendorFlow — vendor onboarding, from submission to approval

**Case study: PS-2 · Operations — Vendor onboarding.**
A vendor submission arrives with company details, tax registration, a bank letter and an
incorporation certificate. The process reads the documents, cross-checks them against the form and
the existing vendor registry, and returns **approved**, **pending** or **rejected** — with every rule
that fired, the field comparisons behind it, and the reply the vendor should receive.

It runs end to end on real files. Nothing is mocked: PDFs are parsed, GSTIN checksums are computed,
names are matched, and the run is persisted with its full audit trail.

---

## 1. Run it

```bash
python -m venv venv
venv/Scripts/activate          # Windows;  source venv/bin/activate on macOS/Linux
pip install -r requirements.txt
streamlit run app.py
```

No API key required. The whole flow runs offline. If `OPENAI_API_KEY` is set (see `.env.example`),
an OpenAI-compatible model may fill fields the document labels miss — it never overwrites a labelled
value and it never decides anything.

Same pipeline, no UI:

```bash
python -m backend.cli --demo happy_path                    # one prepared scenario, JSON on stdout
python -m backend.cli --demo identity_mismatch --save      # also store it in the audit log
python -m backend.cli my_submission.json                   # your own input, see §7
```

```bash
python -m pytest -q                                        # 45 tests
```

---

## 2. The problem, as I read it

Before a company can pay a vendor, someone has to be reasonably sure the vendor is who they say they
are. In practice a procurement team gets an email with three attachments, and a tired person checks
that the company name is consistent, the tax ID is plausible for the country, and the bank account
belongs to the company — not to the person who filled in the form. When something is missing they
write an email, wait, and try again. The only audit trail is the inbox.

The expensive failure is not "rejected a good vendor". It is **approved a vendor whose bank account
belongs to someone else**, or a vendor re-submitting an already-approved tax ID with new payout
details. So the process is built around identity consistency and payout control, not around speed of
approval.

---

## 3. The process map

Seven stages, always the same order. Each stage returns findings; the decision stage aggregates them.

| # | Stage | What it does | Why it is a separate stage |
|---|-------|--------------|----------------------------|
| 1 | Submission received | Accepts and normalises the form payload | Gives every run a clean, quotable starting state |
| 2 | Documents processed | Parses PDFs/text, classifies each file against its slot, extracts labelled fields | A file in the wrong slot or an unreadable scan is a document problem, not a form problem |
| 3 | Field validation | Completeness, email/phone, GSTIN or EIN format + checksum, CIN format, IFSC/ABA checksum, account number shape | Catches typos and wrong-country formats before comparing anything |
| 4 | Cross-document verification | Compares form values with what each document actually says; compares account holder to the legal entity; checks GST state against the address; checks country consistency | This is where subtle fraud shows up: everything plausible alone, inconsistent together |
| 5 | Duplicate detection | Checks tax ID, CIN and payout account against the vendor registry; enforces the blocklist | Needs the outside world, so it is isolated and auditable |
| 6 | Decision generated | Applies severity ordering | One place decides, so the policy is testable |
| 7 | Notification and audit log | Drafts the vendor message, packages the run for storage | Anything not approved must tell the vendor exactly what to fix |

**Decision policy**

1. Any `reject` finding → **rejected**.
2. Otherwise any `pending` finding → **pending** (a reply is needed).
3. Otherwise → **approved**.
4. `warn` findings are recorded and never block.

A finding carries both an internal message (for the reviewer) and a `vendor_message` (what the
vendor is asked for). The drafted email is built from the vendor messages, so the explanation and
the requested fix can never drift apart.

---

## 4. Rules, by stage

| Stage | Code | Severity | Triggers when |
|-------|------|----------|---------------|
| documents | `missing_document` | pending | A required slot has no file |
| documents | `wrong_document` | pending | File content does not match the slot it was uploaded to |
| documents | `unreadable_document` | pending | Could not parse (scanned image, encrypted, corrupt) |
| documents | `extraction_unclear` | pending | File parsed but no labelled field could be read |
| validate | `missing_fields` | pending | One or more form fields are blank |
| validate | `bad_email` / `bad_phone` | pending | Address or number is unusable for the claimed country |
| validate | `free_email` | warn | Gmail/Outlook contact address (common for small vendors) |
| validate | `tax_bad_format` | pending | Not a GSTIN (India) or EIN (US) |
| validate | `tax_bad_checksum` | pending | Right shape, wrong check digit — the usual typo |
| validate | `tax_bad_state` | pending | GSTIN state code is not a real GST state |
| validate | `tax_wrong_regime` | **reject** | GSTIN on a US vendor, or EIN on an Indian vendor |
| validate | `bad_registration` | pending | India: not a 21-character CIN |
| validate | `cin_on_us_vendor` | warn | A CIN-shaped registration on a US vendor |
| validate | `bad_bank_code` | pending | Not an IFSC (India) or failing ABA checksum (US) |
| validate | `bank_name_mismatch` | pending | IFSC bank prefix contradicts the bank name given |
| validate | `ifsc_prefix_unknown` | warn | Valid IFSC format, bank not in the local prefix list |
| validate | `bad_account_number` | pending | Non-digits, or implausible length for the country |
| validate | `country_unsupported` | pending | Outside India/US in this version → human review |
| cross | `identity_name_gap` | pending | Name on a certificate differs from the legal name |
| cross | `identity_id_mismatch` | pending | Tax ID or CIN on a certificate differs from the form |
| cross | `identity_document_mismatch` | pending | Name **and** ID on a certificate belong to another entity |
| cross | `identity_pack_mismatch` | **reject** | Two identity documents both belong to a different entity |
| cross | `doc_field_missing` | pending | Certificate does not show the name or the ID |
| cross | `bank_holder_mismatch` | pending | Payout account is not held in the legal name (e.g. a person) |
| cross | `bank_holder_partial` | pending | Holder is close but not equal after suffix stripping |
| cross | `bank_letter_disagrees` | pending | Form holder and bank letter holder differ |
| cross | `account_number_mismatch` / `bank_code_mismatch` | pending | Bank letter shows a different account or branch |
| cross | `state_address_mismatch` | pending | GSTIN state is not mentioned in the address |
| cross | `cross_border_bank` | pending | Bank country differs from the entity country |
| cross | `jurisdiction_conflict` | pending | Address, documents and tax ID do not sit in one country |
| duplicate | `shared_bank_account` | pending | That payout account is already used by another legal entity |
| duplicate | `similar_name` | warn | Name is near-identical to a record, but the tax ID differs |
| duplicate | `prior_submission` | warn | Same tax ID exists in a non-approved state; judged on its own |
| duplicate | `duplicate_same_account` | pending | Same tax ID already approved with the same account |
| duplicate | `duplicate_bank_change` | **reject** | Same tax ID already approved, submitted with a **different** account |
| duplicate | `blocked_vendor` | **reject** | Tax ID or CIN is on the blocklist |

---

## 5. Edge cases (the five that break the happy path)

All five are prepared packs, runnable from the UI. `pages/2_Admin_Dashboard.py → Edge cases` runs all
of them and shows expected vs actual.

| Scenario | What it tests | Verdict |
|----------|---------------|---------|
| **Missing GST certificate** — company and bank paper present, tax certificate absent | The process must chase one specific document instead of stalling or guessing the tax ID | pending |
| **Bank holder is a person** — PQR Enterprises Pvt Ltd, account held by "Rahul Sharma" | The fraud case that matters. Name similarity is high enough to pass a naive check; the entity is still wrong, and the process says payment would not reach the company | pending |
| **GSTIN fails the checksum** — right shape, wrong final digit | A typo must not read as fraud or as a pass. It asks for confirmation of all 15 characters | pending |
| **Bank account already on file** — a different legal entity submits Zenith's approved account | Rejecting would punish a possible clerical error; approving would let two vendors be paid into one account. It becomes a review item with the owning record named | pending |
| **Blocked tax registration** — tax ID/CIN of a vendor the company already blocked | The blocklist must win regardless of how clean the pack looks | rejected |

Deliberate omission: I did **not** build a "forged document detector". Nothing in synthetic data can
support that claim, and a fake confidence score would be worse than saying it is out of scope (§8).

---

## 6. Using the app

- **Overview** — the process map, the decision policy, and a one-click run for every scenario.
- **Run a submission** — pick a prepared pack or fill in a form and attach your own PDFs/text files.
  The run view draws each stage as it executes, then shows the verdict, the field comparisons, the
  findings, and the drafted vendor email. The run is saved.
- **Dashboard** — history with status filters and search, decision mix, which rules fire most, an
  edge-case self-test, the vendor registry, CSV export, and demo reset.
- **Vendor record** — the full run: replayable timeline, field comparisons, the text the parser read
  from each file, findings, the drafted reply, the audit log, a JSON export, and a reviewer override
  that appends to the audit log and updates the registry.

The registry and the demo packs are synthetic: `test_data/seed.py`, `test_data/scenarios.py`.

---

## 7. Input format (CLI or your own form data)

```json
{
  "submission": {
    "legal_name": "ABC Technologies Pvt Ltd",
    "country": "India",
    "tax_id": "07AABCA1234C1Z3",
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

Document slots are `incorporation`, `tax_certificate`, `bank_proof`. `.pdf` and `.txt` are accepted.
Text extraction relies on the document carrying a text layer with `Label: value` lines — which is why
the demo packs are labelled that way.

---

## 8. Design decisions, assumptions, and what I left out

**Rules decide; the model reads.** Every severity and every verdict is set in Python. The optional
model may only fill a field the labelled parser missed, and any disagreement is logged as a warning
while the labelled value is kept. That keeps verdicts reproducible and explainable — the two things
an auditor asks for. The cost is that genuinely messy scans need OCR, which this version does not do.

**Name matching, not equality.** Legal names are compared after lowercasing, removing punctuation and
stripping company suffixes (Pvt Ltd, LLP, Inc, GmbH…), then scored with a token-sort ratio: ≥92 match,
≥78 partial, below that mismatch. That is why "ABC Technologies Private Limited" matches "ABC
Technologies Pvt Ltd" but a person's name does not.

**Tax IDs are verified, not pattern-matched.** GSTIN: 15-character structure, valid state code, and
the official mod-36 check digit. EIN: 9 digits with an IRS-assigned prefix. IFSC: 11-character shape
plus a local bank-prefix list for the name cross-check; US routing numbers must pass the ABA checksum.

**Duplicate semantics.** Same tax ID + same account → "already approved", pending (no second record).
Same tax ID + different account → rejected, because changing an approved vendor's payout from an
unauthenticated submission is the classic payout-redirect attack. Same account under a *different*
legal entity → pending, never rejected: it is as likely to be a data-entry error as fraud, and the
reviewer is told which record owns the account.

**Assumptions** (worth stating out loud in the demo):

- India-first, US-supported; anything else goes to a human rather than guessing rules.
- The submission is unauthenticated, so it can never change an approved vendor's bank details.
- A bank in another country, with everything else consistent, is a question for a human, not an
  automatic rejection.
- Amounts, POs and payment terms are out of scope: PS-2 is identity, not a three-way match.
- Documents are synthetic. A real deployment would add OCR for scans, a document-template registry,
  and a call-out to a GST verification API.

**Not built, on purpose:** OCR for scanned images, government tax-API lookups, e-signature on the
vendor reply, and multi-user accounts. Each is a clear next step, none was needed to make the process
real.

---

## 9. Demo video script (5 minutes)

| Time | Show | Say |
|------|------|-----|
| 0:00 | Overview page | The problem in one line: a vendor email must become a decision someone can defend |
| 0:30 | The seven stages + policy | Why rules decide and the model only reads; the four severity levels |
| 1:15 | Run the happy path live | Name the stage as it lands; point at the field comparisons and the drafted email |
| 2:30 | Dashboard | History, decision mix, which rule fires most, CSV export |
| 3:00 | Run "Bank holder is a person" | The name is close enough to fool a naive check; the account holder is the finding |
| 3:50 | Open the record → timeline + audit + override | Everything is inspectable; a human can override, and the override is logged |
| 4:30 | Run "Blocked tax registration" | A clean-looking pack still gets rejected; the blocklist wins |
| 4:50 | Close | What I would add next: OCR, tax-API verification |

Before recording: **Dashboard → Demo data → Reset demo data**, so the history starts clean and the
two runs you record are the only ones on screen.

---

## 10. Submission checklist

- [x] Process that actually runs, end to end, on real input files
- [x] Live run view (each stage as it executes) and a dashboard (history, status, outputs)
- [x] Clear output: approved / pending / rejected, with reasons and the vendor's next message
- [x] Five deliberately designed edge cases, prepared as runnable packs
- [x] Audit trail: stages, findings, comparisons, documents, reviewer overrides, JSON export
- [x] Tests: 45 passing (`python -m pytest -q`)
- [ ] Deployed link — `streamlit run app.py` locally, or push this repo to Streamlit Community Cloud
      (entrypoint `app.py`, secrets: none required)
- [ ] Five-minute demo video — script above

---

## 11. Layout

```
app.py                      Overview
pages/1_Vendor_Submission.py  Run view (live stages)
pages/2_Admin_Dashboard.py    Dashboard, edge-case self-test, registry
pages/3_Vendor_Details.py     One record: timeline, checks, documents, audit
backend/                    pipeline, validators, cross-verification, duplicates, decisions
database/                   SQLite schema and repository
test_data/                  seed registry and the six prepared packs
tests/                      pytest suite
frontend/                   theme and renderers
```
