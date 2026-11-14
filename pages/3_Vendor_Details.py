"""One submission, end to end: stages, comparisons, documents, message, audit."""

from __future__ import annotations

import json
import time

import streamlit as st

from database import db
from frontend import ui

st.set_page_config(page_title="Vendor record · VendorFlow", page_icon="◆", layout="wide")
ui.apply_theme()
ui.sidebar("record")
db.init_db()

ui.page_header("Vendor record", "The whole run, inspectable")

runs = db.list_submissions()
if not runs:
    st.markdown(
        '<div class="card"><b>Nothing has been processed yet.</b>'
        '<p class="note" style="margin:.4rem 0 0;">Run a submission and its record appears here.</p></div>',
        unsafe_allow_html=True,
    )
    if st.button("Run a submission →", type="primary"):
        st.switch_page("pages/1_Vendor_Submission.py")
    st.stop()

labels = {
    f'{run["reference"]} · {run["legal_name"]} · {ui.STATUS_TEXT.get(run["status"], run["status"])}': run["id"]
    for run in runs
}
current = st.session_state.get("record_id")
keys = list(labels)
default_index = next((index for index, key in enumerate(keys) if labels[key] == current), 0)
picked = st.selectbox("Record", keys, index=default_index, key="record-picker")
submission_id = labels[picked]
st.session_state["record_id"] = submission_id

bundle = db.get_submission_bundle(submission_id)
if bundle is None:
    st.error("That record could not be loaded.")
    st.stop()

submission = bundle["submission"]
vendor = bundle["vendor"]
st.write("")
ui.decision_block(bundle)

if bundle.get("parent"):
    parent = bundle["parent"]
    p_col1, p_col2 = st.columns([3, 1.2])
    p_col1.info(f"🔗 **Linked run**: This record is a vendor reply to **{parent['reference']}** ({ui.STATUS_TEXT.get(parent['status'], parent['status'])}).")
    if p_col2.button(f"View original {parent['reference']} →", key="view-parent"):
        st.session_state["record_id"] = parent["id"]
        st.rerun()

if bundle.get("replies"):
    for rep in bundle["replies"]:
        r_col1, r_col2 = st.columns([3, 1.2])
        r_col1.success(f"↳ **Follow-up reply on file**: **{rep['reference']}** ({ui.STATUS_TEXT.get(rep['status'], rep['status'])}).")
        if r_col2.button(f"View reply {rep['reference']} →", key=f"view-reply-{rep['id']}"):
            st.session_state["record_id"] = rep["id"]
            st.rerun()

st.write("")
if submission["status"] == "pending":
    actions = st.columns([1, 1, 1.3, 1.4])
    if actions[0].button("← Dashboard"):
        st.switch_page("pages/2_Admin_Dashboard.py")
    if actions[1].button("Run another"):
        st.switch_page("pages/1_Vendor_Submission.py")
    if actions[2].button("Process vendor reply →", type="primary"):
        st.session_state["reply_submission_id"] = submission_id
        st.switch_page("pages/4_Vendor_Replies.py")
    download_col = actions[3]
else:
    actions = st.columns([1, 1, 1.4])
    if actions[0].button("← Dashboard"):
        st.switch_page("pages/2_Admin_Dashboard.py")
    if actions[1].button("Run another submission"):
        st.switch_page("pages/1_Vendor_Submission.py")
    download_col = actions[2]
export = {
    "reference": submission["reference"],
    "status": submission["status"],
    "machine_status": submission["machine_status"],
    "headline": submission["headline"],
    "summary": submission["summary"],
    "rule_engine_version": submission.get("rule_engine_version", ""),
    "created_at": submission["created_at"],
    "vendor": vendor,
    "documents": [
        {key: value for key, value in document.items() if key != "content"}
        for document in bundle["documents"]
    ],
    "findings": bundle["findings"],
    "field_checks": bundle["checks"],
    "stages": bundle["stages"],
    "events": bundle["events"],
    "notification": {
        "subject": submission["notification_subject"],
        "body": submission["notification_body"],
    },
}
download_col.download_button(
    "Export this record as JSON",
    data=json.dumps(export, indent=2, default=str),
    file_name=f'{submission["reference"]}.json',
    mime="application/json",
    key="export-json",
)

tab_run, tab_checks, tab_docs, tab_decision, tab_audit = st.tabs(
    ["Run timeline", "Field comparisons", "Documents", "Decision & reply", "Audit log"]
)

# ----------------------------------------------------------------- timeline

with tab_run:
    st.write("")
    placeholder = st.empty()
    if st.button("Replay this run", key="replay"):
        done: dict[str, dict] = {}
        stages = bundle["stages"]
        for index, stage in enumerate(stages, start=1):
            done[stage["key"]] = stage
            time.sleep(0.3)
            placeholder.markdown(
                ui.live_html(done, index, len(stages), finished=index == len(stages)),
                unsafe_allow_html=True,
            )
    else:
        placeholder.markdown(ui.timeline_html(bundle["stages"]), unsafe_allow_html=True)

# ------------------------------------------------------------------- checks

with tab_checks:
    st.write("")
    ui.checks_table(bundle["checks"])

# -------------------------------------------------------------------- docs

with tab_docs:
    ui.documents_block(bundle, key_prefix=f"record-{submission_id}")

# ---------------------------------------------------------------- decision

with tab_decision:
    st.write("")
    left, right = st.columns([1.15, 1])
    with left:
        st.markdown('<div class="eyebrow">Findings</div>', unsafe_allow_html=True)
        ui.findings_block(bundle["findings"])
    with right:
        st.markdown('<div class="eyebrow">What the vendor is asked for</div>', unsafe_allow_html=True)
        required = [
            finding["vendor_message"]
            for finding in bundle["findings"]
            if finding["severity"] in {"pending", "reject"} and finding["vendor_message"]
        ]
        if required:
            items = "".join(
                f'<li style="margin-bottom:.45rem;">{ui.esc(item)}</li>' for item in required
            )
            st.markdown(
                f'<div class="card"><ol style="margin:0;padding-left:1.1rem;font-size:.86rem;'
                f'line-height:1.55;">{items}</ol></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="card"><p class="note" style="margin:0;">Nothing. This pack is approved '
                "as it stands.</p></div>",
                unsafe_allow_html=True,
            )
    st.write("")
    st.markdown('<div class="eyebrow">Reply drafted for the vendor</div>', unsafe_allow_html=True)
    ui.notification_block(bundle)

# --------------------------------------------------------------------- audit

with tab_audit:
    st.write("")
    for event in bundle["events"]:
        st.markdown(
            f'<div style="font-size:.85rem;padding:.35rem 0;border-bottom:1px dashed rgba(28,25,21,.12);">'
            f'<b>{ui.esc(event["event"])}</b> — {ui.esc(event["detail"])} '
            f'<span class="note">· {ui.esc(event["actor"])} · {ui.esc(event["created_at"])}</span></div>',
            unsafe_allow_html=True,
        )

    st.write("")
    st.markdown("**Reviewer verdict**")
    with st.form("review-verdict"):
        form = st.columns([1, 1, 2])
        verdict = form[0].selectbox(
            "Verdict",
            ["approved", "pending", "rejected"],
            index=["approved", "pending", "rejected"].index(submission["status"])
            if submission["status"] in {"approved", "pending", "rejected"}
            else 1,
            format_func=lambda value: ui.STATUS_TEXT[value],
            key="verdict-status",
        )
        reviewer = form[1].text_input("Reviewer", "Procurement reviewer", key="verdict-actor")
        note = form[2].text_input(
            "Reason for the change",
            placeholder="What did you check, and what changed your mind?",
            key="verdict-note",
        )
        if st.form_submit_button("Save verdict", type="primary"):
            if not note.strip():
                st.error("Add a short reason so the audit log is useful later.")
            else:
                db.apply_admin_decision(
                    submission_id, verdict, note, reviewer, "reviewer_verdict"
                )
                st.success("Verdict saved to the audit log.")
                st.rerun()
