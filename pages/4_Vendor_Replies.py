"""Vendor reply loop — upload missing paper, correct fields, and re-evaluate affected rules."""

from __future__ import annotations

import streamlit as st

from backend.pipeline import REQUIRED_DOCUMENTS
from backend.reply_runner import bundle_to_submission, run_vendor_reply
from database import db
from database.models import DocumentFile, Submission
from frontend import ui
from test_data import synthetic
from test_data.scenarios import SCENARIOS_BY_ID, _MISSING_GST, _pack

st.set_page_config(page_title="Vendor replies · VendorFlow", page_icon="◆", layout="wide")
ui.apply_theme()
ui.sidebar("reply")
db.init_db()

ui.page_header(
    "Vendor replies",
    "Close the loop on pending vendors",
    "When a vendor replies with missing paper or corrected details, re-run only affected rules and inspect the before/after diff.",
)

# ---------------------------------------------------------------- Submission Picker

all_runs = db.list_submissions()
if not all_runs:
    st.markdown(
        '<div class="card"><b>No submissions found.</b>'
        '<p class="note" style="margin:.4rem 0 0;">Run a submission first. Once a vendor has pending issues, they can be resolved here.</p></div>',
        unsafe_allow_html=True,
    )
    if st.button("Run a submission →", type="primary"):
        st.switch_page("pages/1_Vendor_Submission.py")
    st.stop()

filter_col, search_col = st.columns([1, 1.5])
only_pending = filter_col.checkbox("Show only submissions needing a reply", value=True)
runs = [r for r in all_runs if r["status"] == "pending"] if only_pending else all_runs

if not runs:
    if only_pending:
        st.info("No submissions currently need a reply. Showing all submissions instead.")
        runs = all_runs
    else:
        st.stop()

label_map = {
    f"{r['reference']} · {r['legal_name']} · {ui.STATUS_TEXT.get(r['status'], r['status'])}": r["id"]
    for r in runs
}
keys = list(label_map.keys())

target_id = st.session_state.pop("reply_submission_id", None) or st.session_state.get("selected_reply_id")
default_index = 0
if target_id is not None:
    for idx, k in enumerate(keys):
        if label_map[k] == target_id:
            default_index = idx
            break

selected_label = st.selectbox(
    "Select submission to reply to",
    options=keys,
    index=default_index,
    key="reply_submission_picker",
)
submission_id = label_map[selected_label]
st.session_state["selected_reply_id"] = submission_id

bundle = db.get_submission_bundle(submission_id)
if bundle is None:
    st.error("Could not load bundle for the selected submission.")
    st.stop()

original_submission = bundle_to_submission(bundle)
parent_status = bundle["submission"]["status"]
parent_ref = bundle["submission"]["reference"]

# ---------------------------------------------------------------- Issue Context Card

pending_issues = [
    f for f in bundle["findings"] if f.get("severity") in {"pending", "reject"} and f.get("message")
]
docs_on_file = {d["slot"]: d for d in bundle["documents"]}

st.write("")
col_left, col_right = st.columns([1.2, 1])

with col_left:
    st.markdown('<div class="eyebrow">What the vendor was asked to fix</div>', unsafe_allow_html=True)
    if pending_issues:
        issue_items = "".join(
            f'<li style="margin-bottom:.4rem;"><b>{ui.esc(item.get("code", ""))}</b>: '
            f'{ui.esc(item.get("vendor_message") or item.get("message", ""))}'
            f' <span class="chip {item.get("severity")}">{item.get("severity")}</span></li>'
            for item in pending_issues
        )
        st.markdown(
            f'<div class="card" style="border-left:4px solid var(--amber);">'
            f'<ol style="margin:0;padding-left:1.2rem;font-size:.87rem;line-height:1.55;">{issue_items}</ol>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="card" style="border-left:4px solid var(--green);">'
            '<p class="note" style="margin:0;">No blocking issues on this record. You can still upload replacement documents or update fields.</p>'
            '</div>',
            unsafe_allow_html=True,
        )

with col_right:
    st.markdown('<div class="eyebrow">Documents currently on file</div>', unsafe_allow_html=True)
    doc_chips = []
    for slot, label in REQUIRED_DOCUMENTS:
        if slot in docs_on_file:
            d = docs_on_file[slot]
            status_chip = f'<span class="chip pass">on file · {ui.esc(d["filename"])}</span>'
        else:
            status_chip = '<span class="chip stop">missing</span>'
        doc_chips.append(
            f'<div style="display:flex;justify-content:space-between;align-items:center;padding:.35rem 0;'
            f'border-bottom:1px dashed var(--line);font-size:.85rem;">'
            f'<span>{ui.esc(label)}</span>{status_chip}</div>'
        )
    st.markdown(
        f'<div class="card">{"".join(doc_chips)}</div>',
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------- Quick Demo Presets

scenario_id = bundle["submission"].get("scenario_id")
preset_filled = False

st.write("")
st.markdown("---")
st.markdown('<div class="eyebrow">Vendor reply input</div>', unsafe_allow_html=True)

# Helper button to auto-fill for testing common scenarios
quick_action_col, _ = st.columns([1.5, 2])
if scenario_id in {"missing_gst", "invalid_gstin", "identity_mismatch"}:
    preset_label = {
        "missing_gst": "⚡ Quick-fill: Attach missing GST certificate",
        "invalid_gstin": "⚡ Quick-fill: Correct GSTIN checksum typo",
        "identity_mismatch": "⚡ Quick-fill: Correct bank account holder to company",
    }.get(scenario_id, "⚡ Quick-fill correction")

    if quick_action_col.button(preset_label, help="Auto-fills the fix for this demo scenario"):
        st.session_state["preset_applied"] = scenario_id
        st.rerun()

applied_preset = st.session_state.get("preset_applied")
if applied_preset and applied_preset != scenario_id:
    # Reset if user switched to another scenario
    st.session_state.pop("preset_applied", None)
    applied_preset = None

# ---------------------------------------------------------------- Reply Form

tab_docs, tab_fields = st.tabs(["1. Upload missing / replacement documents", "2. Correct form fields"])

new_documents: dict[str, DocumentFile] = {}

with tab_docs:
    st.write("")
    st.markdown('<p class="note">Upload any missing documents or corrected replacements. Slots you leave empty will reuse the existing parsed documents without re-running OCR.</p>', unsafe_allow_html=True)

    doc_cols = st.columns(3)

    # Slot 1: incorporation
    with doc_cols[0]:
        st.markdown("**Certificate of Incorporation**")
        inc_status = "On file" if "incorporation" in docs_on_file else "⚠️ Missing"
        st.caption(f"Status: {inc_status}")
        inc_file = st.file_uploader(
            "Upload replacement incorporation doc",
            type=["pdf", "txt", "png", "jpg", "jpeg"],
            key=f"rep-inc-{submission_id}",
            label_visibility="collapsed",
        )
        if inc_file is not None:
            new_documents["incorporation"] = DocumentFile(
                slot="incorporation",
                filename=inc_file.name,
                content=inc_file.getvalue(),
            )

    # Slot 2: tax_certificate
    with doc_cols[1]:
        st.markdown("**GST / Tax Certificate**")
        tax_status = "On file" if "tax_certificate" in docs_on_file else "⚠️ Missing"
        st.caption(f"Status: {tax_status}")
        tax_file = st.file_uploader(
            "Upload replacement GST cert",
            type=["pdf", "txt", "png", "jpg", "jpeg"],
            key=f"rep-tax-{submission_id}",
            label_visibility="collapsed",
        )
        if tax_file is not None:
            new_documents["tax_certificate"] = DocumentFile(
                slot="tax_certificate",
                filename=tax_file.name,
                content=tax_file.getvalue(),
            )
        elif applied_preset == "missing_gst":
            # Preset fix for missing GST scenario
            pack = _pack(_MISSING_GST)
            gst_bytes = synthetic.render_pdf(pack["tax_certificate"])
            new_documents["tax_certificate"] = DocumentFile(
                slot="tax_certificate",
                filename="sundara-gst-certificate.pdf",
                content=gst_bytes,
            )
            st.success("✓ Attached missing GST certificate from preset.")
        elif applied_preset == "invalid_gstin":
            from test_data.scenarios import _BAD_GSTIN, _gstin
            valid_gst = _gstin("07", "AABCH5678K")
            pack = _pack(_BAD_GSTIN, tax_id=valid_gst)
            tax_bytes = synthetic.render_pdf(pack["tax_certificate"])
            new_documents["tax_certificate"] = DocumentFile(
                slot="tax_certificate",
                filename="helios-tax-fixed.pdf",
                content=tax_bytes,
            )
            st.success("✓ Attached updated GST certificate matching corrected GSTIN.")

    # Slot 3: bank_proof
    with doc_cols[2]:
        st.markdown("**Bank proof (Cheque / Letter)**")
        bank_status = "On file" if "bank_proof" in docs_on_file else "⚠️ Missing"
        st.caption(f"Status: {bank_status}")
        bank_file = st.file_uploader(
            "Upload replacement bank proof",
            type=["pdf", "txt", "png", "jpg", "jpeg"],
            key=f"rep-bank-{submission_id}",
            label_visibility="collapsed",
        )
        if bank_file is not None:
            new_documents["bank_proof"] = DocumentFile(
                slot="bank_proof",
                filename=bank_file.name,
                content=bank_file.getvalue(),
            )
        elif applied_preset == "identity_mismatch":
            from test_data.scenarios import _HOLDER_MISMATCH
            pack = _pack(_HOLDER_MISMATCH, holder=original_submission.legal_name)
            bank_bytes = synthetic.render_pdf(pack["bank_proof"])
            new_documents["bank_proof"] = DocumentFile(
                slot="bank_proof",
                filename="pqr-bank-proof-fixed.pdf",
                content=bank_bytes,
            )
            st.success("✓ Attached matching bank letter in company name.")

with tab_fields:
    st.write("")
    st.markdown('<p class="note">Modify any incorrect form fields below. Rules for unchanged fields will not be re-evaluated.</p>', unsafe_allow_html=True)

    # Initial values (overridden if quick-fill preset was triggered)
    init_tax_id = original_submission.tax_id
    init_bank_holder = original_submission.bank_holder_name

    if applied_preset == "invalid_gstin":
        # Correct typo in checksum: replace check digit 'A' with '5'
        init_tax_id = original_submission.tax_id[:-1] + "5"
    elif applied_preset == "identity_mismatch":
        # Correct bank holder to company name
        init_bank_holder = original_submission.legal_name

    f_col1, f_col2 = st.columns(2)

    with f_col1:
        st.markdown("##### Company details")
        c_name = st.text_input("Legal name", value=original_submission.legal_name, key=f"f-name-{submission_id}")
        c_trade = st.text_input("Trade name", value=original_submission.trade_name, key=f"f-trade-{submission_id}")
        c_country = st.text_input("Country", value=original_submission.country, key=f"f-country-{submission_id}")
        c_tax_id = st.text_input("Tax ID (GSTIN / EIN)", value=init_tax_id, key=f"f-tax-{submission_id}")
        c_reg_no = st.text_input("Registration number (CIN / state reg)", value=original_submission.registration_number, key=f"f-reg-{submission_id}")
        c_addr = st.text_area("Registered address", value=original_submission.address, key=f"f-addr-{submission_id}")

    with f_col2:
        st.markdown("##### Banking & Contact details")
        c_holder = st.text_input("Account holder name", value=init_bank_holder, key=f"f-holder-{submission_id}")
        c_bank = st.text_input("Bank name", value=original_submission.bank_name, key=f"f-bank-{submission_id}")
        c_account = st.text_input("Account number", value=original_submission.account_number, key=f"f-acc-{submission_id}")
        c_code = st.text_input("Bank code (IFSC / routing)", value=original_submission.bank_code, key=f"f-code-{submission_id}")
        c_bcountry = st.text_input("Bank country", value=original_submission.bank_country, key=f"f-bcountry-{submission_id}")

        st.markdown("##### Contact")
        c_cname = st.text_input("Contact name", value=original_submission.contact_name, key=f"f-cname-{submission_id}")
        c_email = st.text_input("Contact email", value=original_submission.contact_email, key=f"f-email-{submission_id}")
        c_phone = st.text_input("Contact phone", value=original_submission.contact_phone, key=f"f-phone-{submission_id}")

updated_submission = Submission(
    legal_name=c_name,
    trade_name=c_trade,
    country=c_country,
    tax_id=c_tax_id,
    registration_number=c_reg_no,
    address=c_addr,
    contact_name=c_cname,
    contact_email=c_email,
    contact_phone=c_phone,
    bank_holder_name=c_holder,
    bank_name=c_bank,
    account_number=c_account,
    bank_code=c_code,
    bank_country=c_bcountry,
)

# ---------------------------------------------------------------- Run Button

st.write("")
action_col1, action_col2 = st.columns([1.5, 2])
submit_reply = action_col1.button("Submit vendor reply & re-evaluate affected rules →", type="primary")

if submit_reply:
    st.session_state.pop("preset_applied", None)
    with st.spinner("Re-evaluating affected rules..."):
        result, diff = run_vendor_reply(
            parent_bundle=bundle,
            updated_submission=updated_submission,
            new_documents=new_documents,
            existing=db.load_registry(),
        )
        new_id, new_ref = db.save_run(result)
        diff.reply_id = new_id
        diff.reply_reference = new_ref
        st.session_state["last_diff"] = diff
        st.session_state["last_result_id"] = new_id
        st.session_state["last_bundle"] = db.get_submission_bundle(new_id)

# ---------------------------------------------------------------- Diff Display

diff = st.session_state.get("last_diff")
if diff is not None and diff.parent_id == submission_id:
    st.write("")
    st.markdown("---")
    ui.section("Before / after diff", "Workflow evaluation results", "Inspect the transition from the original submission to this vendor reply.")

    # 1. Big Transition Banner
    ui.diff_banner(diff)

    # 2. Stat Grid
    resolved_count = len(diff.resolved_findings)
    remaining_count = len(diff.remaining_findings)
    new_count = len(diff.new_findings)
    re_evaluated_rules_count = len(diff.affected_rules)
    preserved_rules_count = len(diff.skipped_rules)

    diff_tones = (
        "ok" if diff.after_status == "approved" else "warn",
        "ok",
        "warn" if remaining_count else "ok",
        "accent",
    )
    ui.stat_grid(
        [
            (f"{ui.STATUS_TEXT.get(diff.after_status, diff.after_status)}", "New Decision"),
            (f"{resolved_count} resolved", "Issues cleared"),
            (f"{remaining_count} remaining", "Issues left"),
            (f"{re_evaluated_rules_count} re-run / {preserved_rules_count} kept", "Rules selective execution"),
        ],
        tones=diff_tones,
    )

    # 3. Diff Details Tabs
    t_findings, t_fields, t_docs, t_rules = st.tabs(
        ["Findings diff", "Field changes", "Document changes", "Selective execution audit"]
    )

    with t_findings:
        st.write("")
        f_left, f_right = st.columns(2)
        with f_left:
            st.markdown("##### Resolved findings (✓ Fixed)")
            if diff.resolved_findings:
                for f in diff.resolved_findings:
                    st.markdown(
                        f'<div class="resolved-item"><span class="check-icon">✓</span>'
                        f'<div><b>{ui.esc(f.code)}</b> ({f.stage}): {ui.esc(f.message)}</div></div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown('<p class="note">No previously flagged findings were resolved in this run.</p>', unsafe_allow_html=True)

        with f_right:
            st.markdown("##### Remaining / New findings")
            if diff.remaining_findings:
                for f in diff.remaining_findings:
                    st.markdown(
                        f'<div class="remaining-item"><span>⚠️</span>'
                        f'<div><b>{ui.esc(f.code)}</b> ({f.stage}): {ui.esc(f.message)}</div></div>',
                        unsafe_allow_html=True,
                    )
            if diff.new_findings:
                for f in diff.new_findings:
                    st.markdown(
                        f'<div class="new-item"><span>ℹ️</span>'
                        f'<div><b>{ui.esc(f.code)}</b> ({f.stage}): {ui.esc(f.message)}</div></div>',
                        unsafe_allow_html=True,
                    )
            if not diff.remaining_findings and not diff.new_findings:
                st.markdown(
                    '<div class="card" style="border-left:4px solid var(--green);">'
                    '<p class="note" style="margin:0;">All blocking findings are resolved. The pack is clean!</p></div>',
                    unsafe_allow_html=True,
                )

    with t_fields:
        st.write("")
        if diff.field_diffs:
            field_table = [
                {
                    "Field": d.label,
                    "Before (Original Run)": d.old_value or "—",
                    "After (Vendor Reply)": d.new_value or "—",
                }
                for d in diff.field_diffs
            ]
            st.dataframe(field_table, hide_index=True)
        else:
            st.markdown('<p class="note">No form fields were changed in this reply.</p>', unsafe_allow_html=True)

    with t_docs:
        st.write("")
        doc_table = [
            {
                "Document Slot": d.label,
                "Action": d.action.upper(),
                "Before": d.old_filename or "Missing",
                "After": d.new_filename or "Missing",
            }
            for d in diff.document_diffs
        ]
        st.dataframe(doc_table, hide_index=True)

    with t_rules:
        st.write("")
        st.markdown("##### Rules selectively re-evaluated:")
        if diff.affected_rules:
            for rule in diff.affected_rules:
                st.markdown(f"— **Re-evaluated**: {ui.esc(rule)}")
        st.write("")
        st.markdown("##### Rules preserved from original run (0 ms, 0 redundant extraction):")
        if diff.skipped_rules:
            for rule in diff.skipped_rules:
                st.markdown(f"— <span style='color:var(--muted);'>Preserved: {ui.esc(rule)}</span>", unsafe_allow_html=True)

    # 4. Next Action Buttons
    st.write("")
    nav_col1, nav_col2, _ = st.columns([1.2, 1.2, 2])
    if nav_col1.button("Open updated record in Vendor Record →", type="primary"):
        st.session_state["record_id"] = diff.reply_id
        st.switch_page("pages/3_Vendor_Details.py")
    if nav_col2.button("Return to dashboard"):
        st.switch_page("pages/2_Admin_Dashboard.py")
