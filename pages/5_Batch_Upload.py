"""Batch Upload — process dozens of vendors at once."""

from __future__ import annotations

import time

import streamlit as st

from backend.batch_runner import (
    BatchResult,
    parse_csv,
    parse_zip,
    results_csv_bytes,
    run_batch,
    sample_csv_bytes,
    BatchParseError,
)
from database import db
from frontend import ui

st.set_page_config(
    page_title="Batch Upload · VendorFlow",
    page_icon="📦",
    layout="wide",
)
ui.apply_theme()
ui.sidebar("batch")

# Auto-clear stale batch summaries if data was reset in the database
if any(k in st.session_state for k in ("csv_batch_results", "zip_batch_results")):
    valid_refs = {s["reference"] for s in db.list_submissions()}
    for key in ("csv_batch_results", "zip_batch_results"):
        res_list = st.session_state.get(key)
        if res_list:
            if not any(r.reference and r.reference in valid_refs for r in res_list):
                st.session_state.pop(key, None)

ui.page_header(
    "Vendor onboarding",
    "Batch upload",
    "Process dozens of vendors at once — upload a CSV or ZIP and run them all with one click.",
)


# ── Helpers ────────────────────────────────────────────────────────────────

STATUS_ICON = {"approved": "✅", "pending": "⚠️", "rejected": "🚫", "error": "💥"}
STATUS_PILL = {"approved": "ok", "pending": "warn", "rejected": "bad", "error": "bad"}


def _status_cell(status: str) -> str:
    icon = STATUS_ICON.get(status, "•")
    cls = STATUS_PILL.get(status, "neutral")
    label = {"approved": "Approved", "pending": "Needs reply", "rejected": "Rejected", "error": "Error"}.get(
        status, status
    )
    return f'<span class="pill {cls}">{icon} {label}</span>'


def _ref_link(result: BatchResult) -> str:
    if result.reference:
        return f'<span class="mono">{ui.esc(result.reference)}</span>'
    return "—"


def _summary_counts(results: list[BatchResult]) -> dict:
    counts = {"approved": 0, "pending": 0, "rejected": 0, "error": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    counts["total"] = len(results)
    return counts


def _render_summary_table(results: list[BatchResult]) -> None:
    rows_html = ""
    for r in results:
        rows_html += (
            f"<tr>"
            f"<td style='text-align:center;color:var(--muted);font-size:.82rem;'>{r.row_index}</td>"
            f"<td>{_ref_link(r)}</td>"
            f"<td>{ui.esc(r.vendor_name)}</td>"
            f"<td>{_status_cell(r.status)}</td>"
            f"<td><span class='code'>{ui.esc(r.top_code)}</span></td>"
            f"<td style='text-align:right;font-variant-numeric:tabular-nums;font-size:.85rem;'>{r.elapsed_ms}</td>"
            f"<td style='font-size:.8rem;color:var(--muted);'>{ui.esc(r.error or '')}</td>"
            f"</tr>"
        )
    table_html = f"""
<div style="overflow-x:auto;">
<table style="width:100%;border-collapse:collapse;font-size:.87rem;">
  <thead>
    <tr style="border-bottom:2px solid var(--line);">
      <th style="text-align:center;padding:.5rem .75rem;color:var(--muted);font-size:.7rem;letter-spacing:.12em;text-transform:uppercase;">#</th>
      <th style="text-align:left;padding:.5rem .75rem;color:var(--muted);font-size:.7rem;letter-spacing:.12em;text-transform:uppercase;">Reference</th>
      <th style="text-align:left;padding:.5rem .75rem;color:var(--muted);font-size:.7rem;letter-spacing:.12em;text-transform:uppercase;">Vendor</th>
      <th style="text-align:left;padding:.5rem .75rem;color:var(--muted);font-size:.7rem;letter-spacing:.12em;text-transform:uppercase;">Decision</th>
      <th style="text-align:left;padding:.5rem .75rem;color:var(--muted);font-size:.7rem;letter-spacing:.12em;text-transform:uppercase;">Primary Rule</th>
      <th style="text-align:right;padding:.5rem .75rem;color:var(--muted);font-size:.7rem;letter-spacing:.12em;text-transform:uppercase;">Time (ms)</th>
      <th style="text-align:left;padding:.5rem .75rem;color:var(--muted);font-size:.7rem;letter-spacing:.12em;text-transform:uppercase;">Error</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>
</div>
"""
    st.markdown(table_html, unsafe_allow_html=True)


def _render_counts(counts: dict) -> None:
    ui.stat_grid(
        [
            (str(counts["total"]), "Total submitted"),
            (str(counts["approved"]), "Approved"),
            (str(counts["pending"]), "Needs reply"),
            (str(counts["rejected"]), "Rejected"),
        ],
        tones=("accent", "ok", "warn", "bad"),
    )


# ── Tabs ───────────────────────────────────────────────────────────────────

tab_csv, tab_zip = st.tabs(["📄 Upload CSV", "🗜 Upload ZIP"])


# ══════════════════════════════════════════════════════════════════════════
# CSV tab
# ══════════════════════════════════════════════════════════════════════════

with tab_csv:
    st.markdown(
        '<div class="sechead"><div class="eyebrow">Step 1</div>'
        "<h2>Download the template</h2>"
        '<p class="note">Each row is one vendor submission. '
        "Documents are auto-generated from the field values — no PDFs needed.</p></div>",
        unsafe_allow_html=True,
    )

    st.download_button(
        "⬇ Download CSV template (25 sample rows)",
        data=sample_csv_bytes(),
        file_name="vendorflow_batch_template.csv",
        mime="text/csv",
        key="dl-csv-template",
    )

    with st.expander("Required CSV columns"):
        cols = [
            "legal_name", "trade_name", "country", "tax_id", "registration_number",
            "address", "contact_name", "contact_email", "contact_phone",
            "bank_holder_name", "bank_name", "account_number", "bank_code", "bank_country",
        ]
        st.code(", ".join(cols), language="text")
        st.markdown(
            '<p class="note">All columns are required. '
            "Rows with a bad GSTIN checksum or unsupported country will be <em>pending</em>; "
            "matched blocked vendors will be <em>rejected</em>.</p>",
            unsafe_allow_html=True,
        )

    st.markdown(
        '<div class="sechead"><div class="eyebrow">Step 2</div>'
        "<h2>Upload your CSV</h2></div>",
        unsafe_allow_html=True,
    )

    csv_file = st.file_uploader(
        "Drop a .csv file here",
        type=["csv"],
        key="csv-uploader",
        label_visibility="collapsed",
    )

    csv_items: list[dict] | None = None

    if csv_file is not None:
        try:
            csv_items = parse_csv(csv_file.read())
            st.success(f"✓ {len(csv_items)} vendor rows parsed successfully.")

            # Preview table
            with st.expander(f"Preview (first 5 of {len(csv_items)} rows)", expanded=True):
                preview = [
                    {
                        "Legal name": r.get("legal_name", ""),
                        "Country": r.get("country", ""),
                        "Tax ID": r.get("tax_id", ""),
                        "Bank": r.get("bank_name", ""),
                        "Account": r.get("account_number", ""),
                    }
                    for r in csv_items[:5]
                ]
                st.dataframe(preview, hide_index=True)

        except BatchParseError as exc:
            st.error(f"⚠ Could not parse the CSV: {exc}")
            csv_items = None

    if csv_items:
        st.markdown(
            '<div class="sechead"><div class="eyebrow">Step 3</div>'
            "<h2>Run the batch</h2></div>",
            unsafe_allow_html=True,
        )

        if st.button(
            f"▶ Run batch — {len(csv_items)} vendors",
            type="primary",
            key="run-csv-batch",
        ):
            existing = db.load_registry()
            progress_bar = st.progress(0)
            status_text = st.empty()
            live_results = st.empty()

            partial: list[BatchResult] = []

            def on_csv_progress(done: int, total: int, name: str, status) -> None:
                pct = done / total if total else 0
                progress_bar.progress(pct)
                if status is None:
                    status_text.markdown(
                        f'<p class="note">Processing **{done + 1}** of **{total}**: {ui.esc(name)}…</p>',
                        unsafe_allow_html=True,
                    )
                else:
                    partial.append(None)  # placeholder; real appending happens in run_batch
                    icon = STATUS_ICON.get(status, "•")
                    status_text.markdown(
                        f'<p class="note">{icon} Finished **{done}** of **{total}**: '
                        f"{ui.esc(name)} → **{status}**</p>",
                        unsafe_allow_html=True,
                    )

            t0 = time.perf_counter()
            csv_results = run_batch(csv_items, source="csv", existing=existing, on_progress=on_csv_progress)
            elapsed = time.perf_counter() - t0

            progress_bar.progress(1.0)
            status_text.markdown(
                f'<p class="note">✅ Batch complete — {len(csv_results)} vendors in {elapsed:.1f} s</p>',
                unsafe_allow_html=True,
            )
            st.session_state["csv_batch_results"] = csv_results

    # Show results
    if "csv_batch_results" in st.session_state:
        results: list[BatchResult] = st.session_state["csv_batch_results"]
        counts = _summary_counts(results)
        st.markdown(
            '<div class="sechead"><div class="eyebrow">Results</div>'
            "<h2>Batch summary</h2></div>",
            unsafe_allow_html=True,
        )
        _render_counts(counts)
        _render_summary_table(results)

        st.markdown("<br>", unsafe_allow_html=True)
        col_dl, col_dash, col_clear = st.columns([1, 1, 1])
        with col_dl:
            st.download_button(
                "⬇ Download results as CSV",
                data=results_csv_bytes(results),
                file_name="vendorflow_batch_results.csv",
                mime="text/csv",
                key="dl-csv-results",
            )
        with col_dash:
            if st.button("View all in Dashboard →", key="csv-to-dash"):
                st.switch_page("pages/2_Admin_Dashboard.py")
        with col_clear:
            if st.button("🗑 Clear batch summary", key="clear-csv-summary"):
                st.session_state.pop("csv_batch_results", None)
                st.rerun()


# ══════════════════════════════════════════════════════════════════════════
# ZIP tab
# ══════════════════════════════════════════════════════════════════════════

with tab_zip:
    st.markdown(
        '<div class="sechead"><div class="eyebrow">Step 1</div>'
        "<h2>Prepare your ZIP</h2>"
        '<p class="note">Each vendor is a JSON file inside the ZIP. '
        "PDFs are optional — missing documents are auto-generated from field values.</p></div>",
        unsafe_allow_html=True,
    )

    st.code(
        """my_batch.zip
├── vendor_01.json        ← required
├── vendor_02.json
├── docs/
│   ├── vendor_01_inc.pdf ← optional
│   └── vendor_01_tax.pdf
└── ...

# vendor_01.json format:
{
  "submission": {
    "legal_name": "...",
    "trade_name": "...",
    "country": "India",
    "tax_id": "...",
    "registration_number": "...",
    "address": "...",
    "contact_name": "...",
    "contact_email": "...",
    "contact_phone": "...",
    "bank_holder_name": "...",
    "bank_name": "...",
    "account_number": "...",
    "bank_code": "...",
    "bank_country": "India"
  },
  "documents": {
    "incorporation":   "docs/vendor_01_inc.pdf",
    "tax_certificate": "docs/vendor_01_tax.pdf",
    "bank_proof":      "docs/vendor_01_bank.pdf"
  }
}""",
        language="text",
    )

    st.markdown(
        '<div class="sechead"><div class="eyebrow">Step 2</div>'
        "<h2>Upload your ZIP</h2></div>",
        unsafe_allow_html=True,
    )

    zip_file = st.file_uploader(
        "Drop a .zip file here",
        type=["zip"],
        key="zip-uploader",
        label_visibility="collapsed",
    )

    zip_items: list[dict] | None = None

    if zip_file is not None:
        try:
            zip_items = parse_zip(zip_file.read())
            st.success(f"✓ {len(zip_items)} vendor JSON file(s) found in ZIP.")

            # Preview
            with st.expander(f"Detected files ({len(zip_items)})", expanded=True):
                rows = []
                for item in zip_items:
                    sub = item["submission"]
                    n_docs = len(item.get("doc_map", {}))
                    rows.append(
                        {
                            "File": item["_name"],
                            "Legal name": sub.get("legal_name", ""),
                            "Country": sub.get("country", ""),
                            "Embedded docs": n_docs,
                        }
                    )
                st.dataframe(rows, hide_index=True)

        except BatchParseError as exc:
            st.error(f"⚠ Could not parse the ZIP: {exc}")
            zip_items = None

    if zip_items:
        st.markdown(
            '<div class="sechead"><div class="eyebrow">Step 3</div>'
            "<h2>Run the batch</h2></div>",
            unsafe_allow_html=True,
        )

        if st.button(
            f"▶ Run batch — {len(zip_items)} vendors",
            type="primary",
            key="run-zip-batch",
        ):
            existing = db.load_registry()
            progress_bar = st.progress(0)
            status_text = st.empty()

            def on_zip_progress(done: int, total: int, name: str, status) -> None:
                pct = done / total if total else 0
                progress_bar.progress(pct)
                if status is None:
                    status_text.markdown(
                        f'<p class="note">Processing **{done + 1}** of **{total}**: {ui.esc(name)}…</p>',
                        unsafe_allow_html=True,
                    )
                else:
                    icon = STATUS_ICON.get(status, "•")
                    status_text.markdown(
                        f'<p class="note">{icon} Finished **{done}** of **{total}**: '
                        f"{ui.esc(name)} → **{status}**</p>",
                        unsafe_allow_html=True,
                    )

            t0 = time.perf_counter()
            zip_results = run_batch(zip_items, source="zip", existing=existing, on_progress=on_zip_progress)
            elapsed = time.perf_counter() - t0

            progress_bar.progress(1.0)
            status_text.markdown(
                f'<p class="note">✅ Batch complete — {len(zip_results)} vendors in {elapsed:.1f} s</p>',
                unsafe_allow_html=True,
            )
            st.session_state["zip_batch_results"] = zip_results

    # Show results
    if "zip_batch_results" in st.session_state:
        results: list[BatchResult] = st.session_state["zip_batch_results"]
        counts = _summary_counts(results)
        st.markdown(
            '<div class="sechead"><div class="eyebrow">Results</div>'
            "<h2>Batch summary</h2></div>",
            unsafe_allow_html=True,
        )
        _render_counts(counts)
        _render_summary_table(results)

        st.markdown("<br>", unsafe_allow_html=True)
        col_dl, col_dash, col_clear = st.columns([1, 1, 1])
        with col_dl:
            st.download_button(
                "⬇ Download results as CSV",
                data=results_csv_bytes(results),
                file_name="vendorflow_zip_batch_results.csv",
                mime="text/csv",
                key="dl-zip-results",
            )
        with col_dash:
            if st.button("View all in Dashboard →", key="zip-to-dash"):
                st.switch_page("pages/2_Admin_Dashboard.py")
        with col_clear:
            if st.button("🗑 Clear batch summary", key="clear-zip-summary"):
                st.session_state.pop("zip_batch_results", None)
                st.rerun()
