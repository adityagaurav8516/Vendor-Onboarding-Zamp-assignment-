"""VendorFlow — vendor onboarding."""

from __future__ import annotations

import streamlit as st

from database import db
from frontend import ui

st.set_page_config(page_title="VendorFlow · Vendor onboarding", page_icon="◆", layout="wide")
ui.apply_theme()
ui.sidebar("home")
db.init_db()

counts = db.dashboard_counts()

st.markdown(
    '<div class="hero"><h1>Vendor onboarding, decided.</h1>'
    '<p class="lede">Submit a vendor pack. Get an approved, pending or rejected decision with the reasons.</p></div>',
    unsafe_allow_html=True,
)

left, right, _spacer = st.columns([1.1, 1, 1.2])
if left.button("Run a submission", type="primary", width="stretch"):
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
