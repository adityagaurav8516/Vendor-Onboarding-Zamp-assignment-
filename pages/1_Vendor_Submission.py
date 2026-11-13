"""Run a vendor through the pipeline with a live stage view."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import streamlit as st

from backend import submission_loader
from database import db
from frontend import ui
from test_data.scenarios import SCENARIOS, SCENARIOS_BY_ID, build_documents, to_submission
from database.models import DocumentFile, Submission

DOC_TYPES = ["pdf", "txt", "png", "jpg", "jpeg", "tif", "tiff", "webp", "bmp"]


def _documents_with_uploads(base: list[DocumentFile], uploads: dict) -> list[DocumentFile]:
    """Files attached in the browser replace the path for their slot."""
    by_slot = {doc.slot: doc for doc in base}
    for slot, uploaded in uploads.items():
        if uploaded is not None:
            by_slot[slot] = DocumentFile(
                slot=slot, filename=uploaded.name, content=uploaded.getvalue()
            )
    return [by_slot[slot] for slot in submission_loader.SLOTS if slot in by_slot]


def _json_example() -> str:
    return json.dumps(
        {
            "submission": {
                "legal_name": "Acme Tools Pvt Ltd",
                "country": "India",
                "tax_id": "07AABCA1234C1ZI",
                "registration_number": "U72900DL2015PTC123456",
                "address": "22 Nehru Place, New Delhi, Delhi 110019",
                "contact_name": "Rahul Sharma",
                "contact_email": "rahul@acmetools.in",
                "contact_phone": "9876543210",
                "bank_holder_name": "Acme Tools Pvt Ltd",
                "bank_name": "HDFC Bank",
                "account_number": "50100987654321",
                "bank_code": "HDFC0001234",
                "bank_country": "India",
            },
            "documents": {
                "incorporation": "packs/acme-incorporation.pdf",
                "tax_certificate": "packs/acme-gst.pdf",
                "bank_proof": "packs/acme-cheque.png",
            },
        },
        indent=2,
    )

st.set_page_config(page_title="Run a submission · VendorFlow", page_icon="◆", layout="wide")
ui.apply_theme()
ui.sidebar("run")
db.init_db()

ui.page_header(
    "Run a submission",
    "Feed it a vendor pack",
    "Prepare a scenario or build your own. The pipeline runs live on this machine — documents are "
    "parsed, checked, matched against the registry — and the finished record is saved for the dashboard.",
)

ids = [scenario.id for scenario in SCENARIOS]
preset = st.session_state.pop("preset_scenario", None)
if preset in ids:
    st.session_state["scenario_choice"] = preset
source = st.radio(
    "Source",
    ["Prepared scenario", "My own submission", "Upload a JSON"],
    horizontal=True,
    label_visibility="collapsed",
)

submission = None
documents: list[DocumentFile] = []
scenario_id: str | None = None
run_now = False

if source == "Prepared scenario":
    scenario_id = st.selectbox(
        "Scenario",
        options=ids,
        format_func=lambda key: f"{SCENARIOS_BY_ID[key].name} · expects {ui.STATUS_TEXT[SCENARIOS_BY_ID[key].expected_status]}",
        key="scenario_choice",
    )
    scenario = SCENARIOS_BY_ID[scenario_id]
    detail, preview = st.columns([1.25, 1])
    with detail:
        st.markdown(
            f'<div class="scenario"><div class="txt"><b>{ui.esc(scenario.name)}</b>'
            f"<p>{ui.esc(scenario.description)}</p>"
            f'<p style="margin-top:.6rem;">Rule codes expected: '
            f'<span class="code">{ui.esc(", ".join(scenario.expected_codes) or "none")}</span></p></div>'
            f"<div>{ui.pill(scenario.expected_status)}</div></div>",
            unsafe_allow_html=True,
        )
        doc_rows = "".join(
            f'<div style="font-size:.82rem;color:#4b4438;">· {ui.esc(ui.SLOT_LABEL.get(slot, slot))} '
            f'— {len(lines)} labelled lines</div>'
            for slot, lines in scenario.documents.items()
        )
        st.markdown(
            f'<div class="note" style="margin-top:.8rem;">Documents in this pack<br/>{doc_rows}</div>',
            unsafe_allow_html=True,
        )
    with preview:
        st.markdown('<div class="eyebrow">Submitted form</div>', unsafe_allow_html=True)
        ui.scenario_submission_table(scenario.submission)
    run_now = st.button("Run this submission", type="primary", key="run-scenario")
    if run_now:
        submission = to_submission(scenario)
        documents = build_documents(scenario)

elif source == "My own submission":
    defaults = SCENARIOS_BY_ID["happy_path"].submission
    with st.form("own_submission", clear_on_submit=False):
        st.markdown("**Company**")
        company = st.columns(2)
        legal_name = company[0].text_input("Legal name", defaults["legal_name"], key="f_legal_name")
        trade_name = company[1].text_input("Trade name", defaults["trade_name"], key="f_trade_name")
        country = company[0].selectbox("Country", ["India", "United States"], index=0, key="f_country")
        registration_number = company[1].text_input(
            "Registration number (CIN / state file no.)", defaults["registration_number"], key="f_reg"
        )
        tax_id = company[0].text_input("Tax ID (GSTIN / EIN)", defaults["tax_id"], key="f_tax")
        address = company[1].text_input("Registered address", defaults["address"], key="f_address")

        st.markdown("**Contact**")
        contact = st.columns(3)
        contact_name = contact[0].text_input("Contact name", defaults["contact_name"], key="f_contact")
        contact_email = contact[1].text_input("Contact email", defaults["contact_email"], key="f_email")
        contact_phone = contact[2].text_input("Contact phone", defaults["contact_phone"], key="f_phone")

        st.markdown("**Payout account**")
        bank = st.columns(2)
        bank_holder_name = bank[0].text_input("Account holder", defaults["bank_holder_name"], key="f_holder")
        bank_name = bank[1].text_input("Bank name", defaults["bank_name"], key="f_bank")
        account_number = bank[0].text_input("Account number", defaults["account_number"], key="f_account")
        bank_code = bank[1].text_input("IFSC / routing number", defaults["bank_code"], key="f_code")
        bank_country = bank[0].selectbox("Bank country", ["India", "United States"], index=0, key="f_bank_country")

        st.markdown(
            "**Documents** — PDF, plain text or a scan (PNG, JPG, TIFF). "
            "Leave a slot empty to see the process chase it."
        )
        uploads = st.columns(3)
        upload_incorporation = uploads[0].file_uploader(
            "Incorporation certificate", type=DOC_TYPES, key="u_inc"
        )
        upload_tax = uploads[1].file_uploader("Tax / GST certificate", type=DOC_TYPES, key="u_tax")
        upload_bank = uploads[2].file_uploader("Bank proof", type=DOC_TYPES, key="u_bank")

        submitted = st.form_submit_button("Run this submission", type="primary")

    if submitted:
        submission = Submission(
            legal_name=legal_name,
            trade_name=trade_name,
            country=country,
            tax_id=tax_id,
            registration_number=registration_number,
            address=address,
            contact_name=contact_name,
            contact_email=contact_email,
            contact_phone=contact_phone,
            bank_holder_name=bank_holder_name,
            bank_name=bank_name,
            account_number=account_number,
            bank_code=bank_code,
            bank_country=bank_country,
        )
        for slot, uploaded in (
            ("incorporation", upload_incorporation),
            ("tax_certificate", upload_tax),
            ("bank_proof", upload_bank),
        ):
            if uploaded is not None:
                documents.append(
                    DocumentFile(slot=slot, filename=uploaded.name, content=uploaded.getvalue())
                )
        if submission.legal_name.strip() or submission.tax_id.strip() or documents:
            run_now = True
        else:
            st.error("Add a company name, a tax ID, or at least one document before running.")

else:
    st.markdown("**Submission file**")
    st.caption(
        "Same format as `python -m backend.cli`. Document paths are read from this machine — from "
        "where the app was started — and files attached below replace the path for their slot."
    )
    with st.expander("JSON format"):
        st.code(_json_example(), language="json")

    upload = st.file_uploader("Submission JSON", type=["json"], key="json-submission")
    loaded = None
    if upload is not None:
        try:
            payload = json.loads(upload.getvalue().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            st.error(f"{upload.name} is not readable JSON ({exc.__class__.__name__}).")
            payload = None
        if payload is not None:
            try:
                loaded = submission_loader.load_payload(
                    payload, base_dirs=[Path.cwd()], strict_paths=False
                )
            except submission_loader.SubmissionLoadError as exc:
                st.error(str(exc))
                loaded = None

    if loaded is not None:
        submission = loaded.submission
        st.markdown('<div class="eyebrow">Loaded from the file</div>', unsafe_allow_html=True)
        ui.scenario_submission_table(asdict(submission))

        attachments = st.columns(3)
        attached: dict[str, object] = {}
        for column, slot in zip(attachments, submission_loader.SLOTS):
            attached[slot] = column.file_uploader(
                ui.SLOT_LABEL.get(slot, slot), type=DOC_TYPES, key=f"json-doc-{slot}"
            )

        on_disk = {doc.slot for doc in loaded.documents}
        st.dataframe(
            [
                {
                    "Slot": ui.SLOT_LABEL.get(slot, slot),
                    "Path in the JSON": "found" if slot in on_disk else "not found",
                    "Attached here": "yes" if attached.get(slot) is not None else "—",
                }
                for slot in submission_loader.SLOTS
            ],
            hide_index=True,
        )
        for warning in loaded.warnings:
            st.caption(f"— {warning}")
        if loaded.missing_paths and not any(value is not None for value in attached.values()):
            st.warning(
                "Those paths were not found on this machine. Attach the files above, or the run will "
                "report the documents as missing."
            )

        run_now = st.button("Run this submission", type="primary", key="run-json")
        if run_now:
            documents = _documents_with_uploads(loaded.documents, attached)

if run_now and submission is not None:
    st.write("")
    placeholder = st.empty()
    result = ui.live_run(
        submission,
        documents,
        db.load_registry(),
        scenario_id,
        placeholder,
    )
    submission_id, reference = db.save_run(result)
    st.session_state["record_id"] = submission_id
    st.session_state["last_render_id"] = submission_id
    bundle = db.get_submission_bundle(submission_id)
    if bundle is not None:
        st.success(f"Saved as {reference}. It is on the dashboard and in the audit log.")
        st.write("")
        ui.decision_block(bundle)
        st.write("")
        left, right = st.columns([1.15, 1])
        with left:
            st.markdown('<div class="eyebrow">Why</div>', unsafe_allow_html=True)
            ui.findings_block(bundle["findings"])
        with right:
            st.markdown('<div class="eyebrow">Field comparisons</div>', unsafe_allow_html=True)
            ui.checks_table(bundle["checks"])
        ui.section("Reply to the vendor", "What they receive next", "Written by the process, sent by a human.")
        ui.notification_block(bundle)
        with st.expander("Documents the process read"):
            ui.documents_block(bundle, key_prefix=f"run-{submission_id}")
        st.write("")
        if st.button("Open the full record →", type="primary"):
            st.switch_page("pages/3_Vendor_Details.py")

elif not run_now and st.session_state.get("last_render_id"):
    bundle = db.get_submission_bundle(int(st.session_state["last_render_id"]))
    if bundle is not None:
        st.write("")
        st.caption("Your last run in this session")
        ui.decision_block(bundle)
        st.write("")
        if st.button("Open the full record →", type="primary", key="open-last"):
            st.switch_page("pages/3_Vendor_Details.py")
