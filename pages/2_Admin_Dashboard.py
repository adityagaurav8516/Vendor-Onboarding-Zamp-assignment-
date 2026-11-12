"""Portfolio view: every run, its status, and what the rules are catching."""

from __future__ import annotations

import csv
import io

import streamlit as st

from backend.pipeline import run_pipeline
from database import db
from frontend import ui
from test_data.scenarios import SCENARIOS, build_documents, to_submission
from test_data.seed import seed_vendors

st.set_page_config(page_title="Dashboard · VendorFlow", page_icon="◆", layout="wide")
ui.apply_theme()
ui.sidebar("dashboard")
db.init_db()

ui.page_header(
    "Dashboard",
    "Every submission, and what it decided",
    "History, current status and the output of each run — the audit trail procurement did not have "
    "when this lived in an inbox.",
)

runs = db.list_submissions()
counts = db.dashboard_counts()
registry = db.list_registry()
avg_ms = db.average_run_ms()
approval = f"{counts['approved'] / counts['total'] * 100:.0f}%" if counts["total"] else "—"

top = st.columns([1, 1, 1, 1, 1])
top[0].metric("Total runs", counts["total"])
top[1].metric("Approved", counts["approved"])
top[2].metric("Needs a reply", counts["pending"])
top[3].metric("Rejected", counts["rejected"])
top[4].metric("Approval rate", approval, help="Share of saved runs that ended approved.")

st.write("")
left, right = st.columns([1.4, 1])
with left:
    if counts["total"]:
        bar = "".join(
            f'<span style="width:{counts[key] / counts["total"] * 100:.2f}%;background:{colour};"></span>'
            for key, colour in (("approved", "#0f6e56"), ("pending", "#c99a3f"), ("rejected", "#a3372c"))
            if counts[key]
        )
        st.markdown(
            f'<div class="card"><div class="eyebrow">Decision mix</div>'
            f'<div class="bar" style="margin:.7rem 0 .5rem;height:9px;">{bar}</div>'
            f'<div class="note">Avg pipeline time {avg_ms} ms · registry {len(registry)} vendors · '
            f'{counts["total"]} decision(s) on record</div></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="card"><b>No runs yet.</b>'
            '<p class="note" style="margin:.4rem 0 0;">Run a submission and it lands here with its '
            "decision, findings and vendor message.</p></div>",
            unsafe_allow_html=True,
        )
with right:
    stats = db.finding_stats(5)
    if stats:
        rows = "".join(
            f'<div style="display:flex;justify-content:space-between;gap:1rem;padding:.28rem 0;font-size:.85rem;">'
            f'<span class="code">{ui.esc(item["code"])}</span>'
            f'<span class="mono">{item["total"]}</span></div>'
            for item in stats
        )
        st.markdown(
            f'<div class="card"><div class="eyebrow">Rules firing most often</div>'
            f'<div style="margin-top:.5rem;">{rows}</div></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="card"><div class="eyebrow">Rules firing most often</div>'
            '<p class="note" style="margin:.5rem 0 0;">Nothing has fired yet.</p></div>',
            unsafe_allow_html=True,
        )

tab_runs, tab_edges, tab_registry, tab_reset = st.tabs(
    ["Runs", "Edge cases", "Vendor registry", "Demo data"]
)

# --------------------------------------------------------------------- runs

with tab_runs:
    st.write("")
    filters = st.columns([1.4, 1.2, 2])
    status_filter = filters[0].multiselect(
        "Status",
        ["approved", "pending", "rejected"],
        default=["approved", "pending", "rejected"],
        format_func=lambda value: ui.STATUS_TEXT[value],
    )
    search = filters[1].text_input("Search", placeholder="Name, reference or tax ID")
    filters[2].write("")
    if filters[2].button("Run a submission →"):
        st.switch_page("pages/1_Vendor_Submission.py")

    rows = db.list_submissions(status_filter or None, search)
    if not rows:
        st.markdown('<p class="note">No runs match this filter.</p>', unsafe_allow_html=True)
    else:
        table = [
            {
                "Reference": row["reference"],
                "Vendor": row["legal_name"],
                "Country": row["country"],
                "Decision": ui.STATUS_TEXT.get(row["status"], row["status"]),
                "Rules fired": row["issue_count"],
                "Primary reason": row["top_issue"] or "—",
                "Processed": row["created_at"],
            }
            for row in rows
        ]
        event = st.dataframe(
            table,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="runs-table",
            column_config={
                "Decision": st.column_config.TextColumn(width="small"),
                "Rules fired": st.column_config.NumberColumn(width="small"),
                "Primary reason": st.column_config.TextColumn(width="large"),
            },
        )
        selection = getattr(event, "selection", None)
        selected = list(selection["rows"]) if selection else []
        controls = st.columns([3, 1.2, 1.2])
        if selected:
            picked = rows[selected[0]]
            controls[0].caption(f"{picked['reference']} selected · {picked['legal_name']}")
            if controls[1].button("Open record →", type="primary", key="open-run"):
                st.session_state["record_id"] = picked["id"]
                st.switch_page("pages/3_Vendor_Details.py")
        else:
            controls[0].caption("Select a row to open its full record.")
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(table[0].keys()))
        writer.writeheader()
        writer.writerows(table)
        controls[2].download_button(
            "Export CSV",
            data=buffer.getvalue(),
            file_name="vendorflow-runs.csv",
            mime="text/csv",
            key="runs-csv",
        )

    activity = db.recent_events(6)
    if activity:
        with st.expander("Recent audit events"):
            for item in activity:
                st.markdown(
                    f'<div style="font-size:.84rem;padding:.25rem 0;">'
                    f'<span class="code">{ui.esc(item["reference"])}</span> '
                    f'<b>{ui.esc(item["event"])}</b> — {ui.esc(item["detail"])} '
                    f'<span class="note">({ui.esc(item["actor"])}, {ui.esc(item["created_at"])})</span></div>',
                    unsafe_allow_html=True,
                )

# --------------------------------------------------------------- edge cases

with tab_edges:
    st.write("")
    st.markdown(
        '<p class="note">The prepared packs, run through the real pipeline without saving anything, '
        "against the seed registry so run history cannot shift the expected verdict. Expected against "
        "actual is the quickest proof the rules behave.</p>",
        unsafe_allow_html=True,
    )
    if st.button("Run all edge cases", type="primary", key="run-edges"):
        results = []
        for scenario in SCENARIOS:
            result = run_pipeline(
                to_submission(scenario),
                build_documents(scenario),
                seed_vendors(),
                scenario_id=scenario.id,
            )
            codes = {finding.code for finding in result.findings}
            results.append(
                {
                    "id": scenario.id,
                    "name": scenario.name,
                    "expected": scenario.expected_status,
                    "actual": result.status,
                    "codes": sorted(codes),
                    "missing": sorted(set(scenario.expected_codes) - codes),
                    "status": result.status,
                }
            )
        st.session_state["edge_results"] = results

    results = st.session_state.get("edge_results")
    if results:
        rows = [
            {
                "Scenario": item["name"],
                "Expected": ui.STATUS_TEXT[item["expected"]],
                "Actual": ui.STATUS_TEXT[item["actual"]],
                "Pass": "yes" if item["actual"] == item["expected"] and not item["missing"] else "no",
                "Rule codes fired": ", ".join(item["codes"]) or "none",
            }
            for item in results
        ]
        st.dataframe(rows, hide_index=True)
        failures = [item for item in results if item["actual"] != item["expected"] or item["missing"]]
        if failures:
            for failure in failures:
                st.error(f"{failure['name']}: expected {failure['expected']}, got {failure['actual']}.")
        else:
            st.success("All prepared scenarios produced their expected verdict.")
    else:
        for scenario in SCENARIOS:
            st.markdown(
                f'<div class="scenario"><div class="txt"><b>{ui.esc(scenario.name)}</b>'
                f'<p>{ui.esc(scenario.description)}</p></div>'
                f'<div>{ui.pill(scenario.expected_status)}</div></div>',
                unsafe_allow_html=True,
            )

# ------------------------------------------------------------------ registry

with tab_registry:
    st.write("")
    st.markdown(
        '<p class="note">The procurement side of the problem: vendors already known, with their '
        "status. Duplicate detection and the blocklist read from this table; saving a reviewer "
        "verdict writes back to it.</p>",
        unsafe_allow_html=True,
    )
    needle = st.text_input("Filter the registry", placeholder="Name, tax ID or CIN", key="registry-search")
    visible = [
        vendor
        for vendor in registry
        if not needle.strip()
        or needle.strip().lower()
        in f'{vendor["legal_name"]} {vendor["tax_id"]} {vendor["registration_number"]}'.lower()
    ]
    st.dataframe(
        [
            {
                "ID": vendor["id"],
                "Legal name": vendor["legal_name"],
                "Country": vendor["country"],
                "Tax ID": vendor["tax_id"],
                "CIN": vendor["registration_number"],
                "Account": f'…{str(vendor["account_number"])[-4:]}',
                "Status": ui.STATUS_TEXT.get(vendor["status"], vendor["status"]),
            }
            for vendor in visible
        ],
        hide_index=True,
    )
    st.caption(f"{len(visible)} of {len(registry)} vendors shown.")

# ---------------------------------------------------------------------- reset

with tab_reset:
    st.write("")
    st.markdown(
        '<p class="note">Empties the run history and reseeds the three vendor records the demo '
        "scenarios depend on. Useful right before a walkthrough.</p>",
        unsafe_allow_html=True,
    )
    confirm = st.checkbox("I understand this deletes all saved runs", key="reset-confirm")
    if st.button("Reset demo data", disabled=not confirm, key="reset-data"):
        db.reset_demo_data()
        for key in ("record_id", "last_render_id", "edge_results", "runs-table"):
            st.session_state.pop(key, None)
        st.success("Demo data reset. The registry is back to its three seed records.")
        st.rerun()
