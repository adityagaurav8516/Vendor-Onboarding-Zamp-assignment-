"""VendorFlow — PS-2 vendor onboarding decision process."""

from __future__ import annotations

import streamlit as st

from database import db
from frontend import ui
from test_data.scenarios import SCENARIOS

st.set_page_config(page_title="VendorFlow · Vendor onboarding", page_icon="◆", layout="wide")
ui.apply_theme()
ui.sidebar("home")
db.init_db()

counts = db.dashboard_counts()

st.markdown(
    f'<div class="hero"><div class="eyebrow">AI Solutions Analyst · Case study · PS-2</div>'
    f"<h1>A vendor submission goes in.<br/>A reasoned decision comes out.</h1>"
    f'<p class="lede">Company details, tax registration, a bank letter and an incorporation '
    f"certificate arrive as one pack. Seven stages read the documents, check them against the "
    f"vendor registry, and produce <b>approved</b>, <b>pending</b> or <b>rejected</b> — with every "
    f"rule that fired, the field comparisons behind it, and the vendor&rsquo;s next message already "
    f"written. The process runs here, on real files, not in a mockup.</p></div>",
    unsafe_allow_html=True,
)

st.write("")

left, right, _spacer = st.columns([1.1, 1, 1.2])
if left.button("Run the happy path", type="primary", width="stretch"):
    st.session_state["preset_scenario"] = "happy_path"
    st.switch_page("pages/1_Vendor_Submission.py")
if right.button("Open the dashboard", width="stretch"):
    st.switch_page("pages/2_Admin_Dashboard.py")

ui.stat_grid(
    [
        (str(counts["total"]), "Runs processed"),
        (str(counts["approved"]), "Approved"),
        (str(counts["pending"]), "Need a reply"),
        (str(counts["rejected"]), "Rejected"),
    ]
)

ui.section(
    "The process",
    "Seven stages, always in the same order",
    "The model reads documents. Code decides. A stage that needs a human stops the run with a reason attached.",
)
steps = "".join(
    f'<div class="step"><div class="num">{index:02d}</div><b>{ui.esc(title)}</b>'
    f"<p>{ui.esc(description)}</p></div>"
    for index, (_key, title, description) in enumerate(ui.STAGE_PLAN, start=1)
)
st.markdown(f'<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;">{steps}</div>', unsafe_allow_html=True)

ui.section(
    "The policy",
    "Three outcomes, one ordering rule",
    "Reject beats pending. Pending beats approve. A warning is recorded and never blocks.",
)
rules = st.columns(3)
rules[0].markdown(
    '<div class="rule ok"><b>Approve</b><ul>'
    "<li>Legal name matches on every document</li>"
    "<li>GSTIN or EIN passes format and checksum</li>"
    "<li>Bank holder is the legal entity</li>"
    "<li>Tax ID is not already approved elsewhere</li>"
    "</ul></div>",
    unsafe_allow_html=True,
)
rules[1].markdown(
    '<div class="rule warn"><b>Needs a reply</b><ul>'
    "<li>Document missing, unreadable, or in the wrong slot</li>"
    "<li>Name, tax ID or account differs across documents</li>"
    "<li>Checksum typo, wrong country format</li>"
    "<li>Payout account shared with another vendor</li>"
    "<li>Bank in another country from the entity</li>"
    "</ul></div>",
    unsafe_allow_html=True,
)
rules[2].markdown(
    '<div class="rule bad"><b>Reject</b><ul>'
    "<li>Tax regime contradicts the claimed country</li>"
    "<li>Blocked tax registration or CIN</li>"
    "<li>Approved vendor resubmitted with new bank details</li>"
    "<li>Identity pack belongs to a different entity</li>"
    "</ul></div>",
    unsafe_allow_html=True,
)

ui.section(
    "Edge cases",
    "What breaks the happy path — and what the process does about it",
    "Each one is a prepared input pack. Open it on the run page, or start it from here.",
)
for scenario in SCENARIOS:
    if scenario.id == "happy_path":
        continue
    row = st.columns([6, 1.2])
    with row[0]:
        st.markdown(
            f'<div class="scenario"><div class="txt"><b>{ui.esc(scenario.name)}</b>'
            f"<p>{ui.esc(scenario.description)}</p></div>"
            f'<div style="text-align:right;">{ui.pill(scenario.expected_status)}</div></div>',
            unsafe_allow_html=True,
        )
    with row[1]:
        if st.button("Run this", key=f"home-run-{scenario.id}", width="stretch"):
            st.session_state["preset_scenario"] = scenario.id
            st.switch_page("pages/1_Vendor_Submission.py")

ui.section("Under the hood", "Built to be defended in an interview")
notes = st.columns(3)
notes[0].markdown(
    '<div class="rule"><b>Rules own the verdict</b><ul>'
    "<li>Every severity is set in Python, never by a model</li>"
    "<li>A cited document can only raise a question, never force a pass</li>"
    "<li>Same input, same output, every run</li>"
    "</ul></div>",
    unsafe_allow_html=True,
)
notes[1].markdown(
    '<div class="rule"><b>The model fills gaps only</b><ul>'
    "<li>Labelled fields are parsed first and win</li>"
    "<li>An optional model may fill blanks, never overwrite</li>"
    "<li>No key needed: the whole flow runs offline</li>"
    "</ul></div>",
    unsafe_allow_html=True,
)
notes[2].markdown(
    '<div class="rule"><b>Every run is evidence</b><ul>'
    "<li>Stages, findings, field comparisons and documents stored</li>"
    "<li>Reviewer verdicts append to the audit log</li>"
    "<li>Same pipeline behind the UI and the CLI</li>"
    "</ul></div>",
    unsafe_allow_html=True,
)

st.write("")
st.markdown(
    '<p class="note">Synthetic vendors and documents only. The registry, the packs and the '
    "rules are all in the repo — <span class=\"mono\">python -m backend.cli --demo happy_path</span> "
    "runs the same decision without the interface.</p>",
    unsafe_allow_html=True,
)
