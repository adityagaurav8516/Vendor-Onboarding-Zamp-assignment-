"""Shared theme, layout helpers and result renderers for the VendorFlow app."""

from __future__ import annotations

import html
import time

import streamlit as st

from backend.pipeline import run_pipeline

STATUS_TEXT = {"approved": "Approved", "pending": "Needs a reply", "rejected": "Rejected"}
STATUS_CLASS = {"approved": "ok", "pending": "warn", "rejected": "bad"}
STAGE_LABEL = {"pass": "passed", "attention": "needs attention", "stop": "stopped"}

SLOT_LABEL = {
    "incorporation": "Incorporation certificate",
    "tax_certificate": "Tax / GST certificate",
    "bank_proof": "Bank proof",
}

FIELD_LABEL = {
    "legal_name": "Legal name",
    "trade_name": "Trade name",
    "country": "Country",
    "tax_id": "Tax ID",
    "registration_number": "Registration number",
    "address": "Registered address",
    "contact_name": "Contact",
    "contact_email": "Email",
    "contact_phone": "Phone",
    "bank_holder_name": "Account holder",
    "bank_name": "Bank",
    "account_number": "Account number",
    "bank_code": "Bank code (IFSC / routing)",
    "bank_country": "Bank country",
    "account_holder": "Account holder",
    "document_type": "Document type",
}

DOC_FIELDS = (
    "legal_name",
    "tax_id",
    "registration_number",
    "country",
    "address",
    "account_holder",
    "bank_name",
    "account_number",
    "bank_code",
    "document_type",
)

RESULT_TEXT = {
    "match": "match",
    "partial": "close",
    "mismatch": "mismatch",
    "missing": "not compared",
}

# The seven stages the pipeline always runs, in order.
STAGE_PLAN = (
    ("intake", "Submission received", "The form payload is accepted and normalised."),
    ("documents", "Documents processed", "PDFs and text files are parsed, classified and read for labelled fields."),
    ("validate", "Field validation", "Format, checksum and completeness rules run on every submitted field."),
    ("cross", "Cross-document verification", "The form is compared with what the documents actually say."),
    ("duplicate", "Duplicate detection", "Tax ID, CIN and payout account are checked against the vendor registry."),
    ("decision", "Decision generated", "Severity is aggregated: reject beats pending, warnings never block."),
    ("audit", "Notification and audit log", "A vendor reply is drafted and the whole run is stored."),
)

CSS = """
<style>
:root{
  --paper:#f3efe6; --card:#faf7f1; --ink:#1c1915; --muted:#6f6555;
  --line:rgba(28,25,21,.12); --green:#0f6e56; --amber:#a86b1f; --red:#a3372c;
}
.stApp{background:var(--paper);}
[data-testid="stHeader"]{background:transparent;}
[data-testid="stSidebarNav"]{display:none;}
section[data-testid="stSidebar"]{background:#ece6d9;border-right:1px solid var(--line);}
[data-testid="stSidebar"] .block-container{padding-top:1.4rem;}
.block-container{padding-top:2rem;padding-bottom:4rem;max-width:1180px;}
h1,h2,h3,h4{font-family:Georgia,"Times New Roman",serif;color:var(--ink);letter-spacing:-.015em;}
h1{margin:.2rem 0 .5rem;}
a{color:var(--green);}
p{line-height:1.6;}
.eyebrow{font-size:.68rem;font-weight:700;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);}
.lede{font-size:1.02rem;color:#4b4438;max-width:70ch;margin:0;}
.phead{margin-bottom:1.2rem;}
.phead h1{font-size:2.2rem;}
.brand{font-family:Georgia,serif;font-size:1.32rem;letter-spacing:-.01em;}
.brandsub{font-size:.72rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);}
.sidenav{font-size:.86rem;color:var(--muted);margin:1.2rem 0 .4rem;letter-spacing:.08em;text-transform:uppercase;}
.sidefoot{font-size:.72rem;color:var(--muted);line-height:1.5;border-top:1px solid var(--line);margin-top:1.4rem;padding-top:.8rem;}
.navnow{background:var(--green);color:#fff;border-radius:10px;padding:.42rem .7rem;font-size:.88rem;font-weight:600;margin-bottom:.2rem;}
.hero{background:linear-gradient(135deg,#faf7f1 0%,#f5efe3 100%);border:1px solid var(--line);border-radius:22px;padding:34px 36px 30px;box-shadow:0 1px 2px rgba(28,25,21,.05);}
.hero h1{font-size:2.7rem;line-height:1.08;margin:.4rem 0 .7rem;}
.statgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0 8px;}
.stat{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px 18px;}
.stat .n{font-family:Georgia,serif;font-size:1.85rem;line-height:1;letter-spacing:-.02em;}
.stat .l{font-size:.68rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-top:.45rem;}
.stat.accent .n{color:var(--green);}
.step{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px 17px;height:100%;}
.step .num{font-size:.68rem;letter-spacing:.16em;color:var(--green);font-weight:700;}
.step b{display:block;margin:.35rem 0 .25rem;font-size:.96rem;}
.step p{margin:0;font-size:.83rem;color:var(--muted);line-height:1.55;}
.rule{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;height:100%;border-top:4px solid var(--line);}
.rule.ok{border-top-color:var(--green);} .rule.warn{border-top-color:var(--amber);} .rule.bad{border-top-color:var(--red);}
.rule b{display:block;margin-bottom:.4rem;}
.rule ul{margin:.3rem 0 0;padding-left:1.05rem;font-size:.84rem;color:#4b4438;line-height:1.65;}
.pill{display:inline-flex;align-items:center;gap:.35rem;padding:.18rem .6rem;border-radius:999px;font-size:.68rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;}
.pill.ok{background:#ddefe5;color:#0b5a45;} .pill.warn{background:#faeed2;color:#8a5a12;}
.pill.bad{background:#f9e1dc;color:#96281d;} .pill.neutral{background:#ece5d8;color:#5c5346;}
.chip{font-size:.64rem;letter-spacing:.05em;text-transform:uppercase;font-weight:700;padding:.1rem .45rem;border-radius:5px;background:#ece5d8;color:#5c5346;}
.chip.pass{background:#ddefe5;color:#0b5a45;} .chip.attention{background:#faeed2;color:#8a5a12;}
.chip.stop{background:#f9e1dc;color:#96281d;} .chip.warn{background:#ece5d8;color:#5c5346;}
.code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.72rem;background:#efe9dc;border-radius:6px;padding:.08rem .4rem;color:#5c5346;}
.stages{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:10px 8px 12px;}
.livehead{display:flex;justify-content:space-between;font-size:.74rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);padding:4px 12px 0;font-weight:600;}
.bar{height:5px;background:#e6dfd2;border-radius:999px;overflow:hidden;margin:8px 12px 6px;}
.bar>span{display:block;height:100%;background:var(--green);border-radius:999px;}
.stage{display:grid;grid-template-columns:22px 1fr;gap:8px;padding:10px 12px;border-radius:12px;}
.stage+.stage{margin-top:2px;}
.stage .dot{width:9px;height:9px;border-radius:50%;background:#cdc4b3;margin-top:6px;margin-left:5px;}
.stage.pass .dot{background:var(--green);} .stage.attention .dot{background:var(--amber);}
.stage.stop .dot{background:var(--red);} .stage.queued{opacity:.38;}
.stage .head{display:flex;align-items:center;gap:.4rem;font-size:.92rem;font-weight:600;}
.stage .ms{margin-left:auto;font-variant-numeric:tabular-nums;font-size:.7rem;color:var(--muted);font-weight:400;}
.stage .sum{margin:.18rem 0 0;font-size:.84rem;color:#4b4438;line-height:1.5;}
.stage .det{margin:.35rem 0 0;padding-left:1rem;font-size:.75rem;color:var(--muted);line-height:1.55;}
.stage .det li{margin-bottom:.12rem;word-break:break-word;}
.verdict{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--green);border-radius:6px 16px 16px 6px;padding:22px 24px;}
.verdict.pending{border-left-color:var(--amber);background:#fdf7ea;}
.verdict.rejected{border-left-color:var(--red);background:#fbf0ed;}
.verdict h2{margin:.55rem 0 .4rem;font-size:1.55rem;}
.verdict p{margin:0;color:#4b4438;font-size:.95rem;}
.vmeta{display:flex;flex-wrap:wrap;gap:.5rem 1.1rem;margin-top:1rem;font-size:.74rem;color:var(--muted);letter-spacing:.02em;}
.vrow{display:flex;align-items:center;gap:.6rem;}
.ref{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.78rem;color:var(--muted);}
.dl{display:grid;grid-template-columns:190px 1fr;gap:.35rem 1rem;font-size:.86rem;}
.dl dt{color:var(--muted);} .dl dd{margin:0;word-break:break-word;}
.finding{display:flex;gap:.6rem;align-items:flex-start;padding:.7rem 0;border-top:1px dashed var(--line);font-size:.87rem;line-height:1.55;}
.finding:first-child{border-top:none;}
.finding .msg{flex:1;}
.finding .vendor{margin:.3rem 0 0;font-size:.8rem;color:var(--muted);}
.mail{background:#fffdf7;border:1px solid var(--line);border-radius:12px;padding:18px 20px;font-size:.88rem;line-height:1.65;}
.mail .subj{font-weight:700;margin-bottom:.7rem;padding-bottom:.6rem;border-bottom:1px solid var(--line);}
.mail pre{white-space:pre-wrap;font-family:inherit;margin:0;font-size:.88rem;line-height:1.65;}
.note{font-size:.8rem;color:var(--muted);line-height:1.55;}
.scenario{display:flex;gap:1rem;align-items:flex-start;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 18px;}
.scenario+.scenario{margin-top:.5rem;}
.scenario .txt{flex:1;}
.scenario b{font-size:.94rem;}
.scenario p{margin:.2rem 0 0;font-size:.83rem;color:var(--muted);}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.8rem;}
@media (max-width:900px){
  .statgrid{grid-template-columns:repeat(2,1fr);}
  .hero{padding:24px;}
  .hero h1{font-size:2.1rem;}
}
</style>
"""


# ------------------------------------------------------------------ helpers


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def apply_theme() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def pill(status: str, label: str | None = None) -> str:
    return (
        f'<span class="pill {STATUS_CLASS.get(status, "neutral")}">'
        f'{esc(label or STATUS_TEXT.get(status, status))}</span>'
    )


def page_header(kicker: str, title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="phead"><div class="eyebrow">{esc(kicker)}</div>'
        f"<h1>{esc(title)}</h1><p class=\"lede\">{esc(subtitle)}</p></div>",
        unsafe_allow_html=True,
    )


def section(kicker: str, title: str, subtitle: str = "") -> None:
    sub = f'<p class="note">{esc(subtitle)}</p>' if subtitle else ""
    st.markdown(
        f'<div style="margin:2rem 0 .8rem;"><div class="eyebrow">{esc(kicker)}</div>'
        f'<h2 style="margin:.25rem 0 .3rem;font-size:1.4rem;">{esc(title)}</h2>{sub}</div>',
        unsafe_allow_html=True,
    )


def stat_grid(items: list[tuple[str, str]]) -> None:
    cells = "".join(
        f'<div class="stat{" accent" if index == 0 else ""}"><div class="n">{esc(value)}</div>'
        f'<div class="l">{esc(label)}</div></div>'
        for index, (value, label) in enumerate(items)
    )
    st.markdown(f'<div class="statgrid">{cells}</div>', unsafe_allow_html=True)


def sidebar(active: str) -> None:
    links = (
        ("home", "Overview", "app.py"),
        ("run", "Run a submission", "pages/1_Vendor_Submission.py"),
        ("dashboard", "Dashboard", "pages/2_Admin_Dashboard.py"),
        ("record", "Vendor record", "pages/3_Vendor_Details.py"),
    )
    with st.sidebar:
        st.markdown('<div class="brand">VendorFlow</div>', unsafe_allow_html=True)
        st.markdown('<div class="brandsub">Vendor onboarding · PS-2</div>', unsafe_allow_html=True)
        st.markdown('<div class="sidenav">Workspace</div>', unsafe_allow_html=True)
        for key, label, path in links:
            if key == active:
                st.button(label, key=f"nav-now-{key}", disabled=True, width="stretch")
            elif st.button(label, key=f"nav-{key}", width="stretch"):
                st.switch_page(path)
        st.markdown(
            '<div class="sidefoot">Rules decide, code executes. '
            "Every run is stored with its findings, comparisons and vendor reply.</div>",
            unsafe_allow_html=True,
        )


def stage_rows(stages: list[dict]) -> list[tuple[str, dict]]:
    by_key = {stage["key"]: stage for stage in stages}
    return [(key, by_key[key]) for key, _title, _desc in STAGE_PLAN if key in by_key]


def _stage_row(key: str, title: str, description: str, stage: dict | None) -> str:
    queued = stage is None
    status = (stage or {}).get("status", "")
    label = STAGE_LABEL.get(status, "")
    chip = f'<span class="chip {status}">{label}</span>' if label and not queued else ""
    ms = f'<span class="ms">{stage["elapsed_ms"]} ms</span>' if stage and not queued else '<span class="ms">queued</span>'
    summary = stage["summary"] if stage else description
    details = ""
    if stage:
        lines = stage.get("details") or []
        items = "".join(f"<li>{esc(text)}</li>" for text in lines[:5])
        extra = f"<li>+{len(lines) - 5} more</li>" if len(lines) > 5 else ""
        if items:
            details = f'<ul class="det">{items}{extra}</ul>'
    klass = "queued" if queued else status
    return (
        f'<div class="stage {klass}"><span class="dot"></span><div>'
        f'<div class="head"><span>{esc(title)}</span>{chip}{ms}</div>'
        f'<p class="sum">{esc(summary)}</p>{details}</div></div>'
    )


def live_html(done: dict[str, dict], completed: int, total: int, finished: bool = False) -> str:
    rows = "".join(
        _stage_row(key, title, description, done.get(key))
        for key, title, description in STAGE_PLAN
    )
    pct = int(round(completed / total * 100)) if total else 0
    head = "Run complete" if finished else f"Running · stage {min(completed + 1, total)} of {total}"
    return (
        f'<div class="stages"><div class="livehead"><span>{esc(head)}</span>'
        f'<span>{pct}%</span></div><div class="bar"><span style="width:{pct}%"></span></div>'
        f"{rows}</div>"
    )


def timeline_html(stages: list[dict]) -> str:
    by_key = {stage["key"]: stage for stage in stages}
    rows = "".join(
        _stage_row(key, title, description, by_key.get(key))
        for key, title, description in STAGE_PLAN
    )
    total = sum(int(stage.get("elapsed_ms") or 0) for stage in stages)
    failures = sum(1 for stage in stages if stage.get("status") in {"attention", "stop"})
    head = f"{len(stages)} stages · {total} ms · {failures} flagged"
    return (
        f'<div class="stages"><div class="livehead"><span>{esc(head)}</span>'
        f'<span>{esc("replayable")}</span></div>{rows}</div>'
    )


def live_run(submission, documents, existing, scenario_id, placeholder, delay: float = 0.32):
    """Execute the pipeline while drawing each stage as it completes."""
    done: dict[str, dict] = {}

    def on_stage(stage, index: int, total: int) -> None:
        time.sleep(delay)
        done[stage.key] = {
            "key": stage.key,
            "title": stage.title,
            "status": stage.status,
            "summary": stage.summary,
            "details": stage.details,
            "elapsed_ms": stage.elapsed_ms,
        }
        placeholder.markdown(live_html(done, index, total), unsafe_allow_html=True)

    placeholder.markdown(live_html({}, 0, len(STAGE_PLAN)), unsafe_allow_html=True)
    result = run_pipeline(submission, documents, existing, scenario_id=scenario_id, on_stage=on_stage)
    placeholder.markdown(live_html(done, len(STAGE_PLAN), len(STAGE_PLAN), finished=True), unsafe_allow_html=True)
    return result


# ------------------------------------------------------------------ records


def decision_block(bundle: dict) -> None:
    submission = bundle["submission"]
    vendor = bundle["vendor"]
    status = submission["status"]
    meta = [
        esc(vendor["legal_name"]),
        esc(vendor["country"] or "country not given"),
        f'{len(bundle["documents"])} document(s)',
        f'extraction: {esc(submission["extraction_method"] or "none")}',
        esc(submission["created_at"]),
    ]
    if submission["duplicate_of"]:
        meta.append(f'linked record {esc(submission["duplicate_of"])}')
    if submission["status"] != submission["machine_status"]:
        meta.append(f'machine decision was {esc(STATUS_TEXT.get(submission["machine_status"], submission["machine_status"]))}')
    st.markdown(
        f'<div class="verdict {status}"><div class="vrow">{pill(status)}'
        f'<span class="ref">{esc(submission["reference"])}</span></div>'
        f'<h2>{esc(submission["headline"])}</h2>'
        f'<p>{esc(submission["summary"])}</p>'
        f'<div class="vmeta">{"".join(f"<span>{item}</span>" for item in meta)}</div></div>',
        unsafe_allow_html=True,
    )


def findings_block(findings: list[dict], empty: str = "Every rule passed. Nothing needs a reply.") -> None:
    order = {"reject": 0, "pending": 1, "warn": 2}
    ordered = sorted(findings, key=lambda item: (order.get(item["severity"], 3), item["code"]))
    if not ordered:
        st.markdown(f'<p class="note">{esc(empty)}</p>', unsafe_allow_html=True)
        return
    rows = []
    for finding in ordered:
        severity = finding["severity"]
        label = {"reject": "rejecting", "pending": "needs a reply", "warn": "note"}.get(severity, severity)
        vendor = (
            f'<p class="vendor">Vendor is told: {esc(finding["vendor_message"])}</p>'
            if finding.get("vendor_message")
            else ""
        )
        rows.append(
            f'<div class="finding"><span class="chip {severity}">{esc(label)}</span>'
            f'<div class="msg">{esc(finding["message"])} '
            f'<span class="code">{esc(finding["code"])}</span>{vendor}</div></div>'
        )
    st.markdown(f'<div>{"".join(rows)}</div>', unsafe_allow_html=True)


def checks_rows(checks: list[dict]) -> list[dict]:
    return [
        {
            "Field": check["field"],
            "On the form": check["submitted"] or "—",
            "On the document": check["extracted"] or "—",
            "Source": check["source"],
            "Result": RESULT_TEXT.get(check["result"], check["result"]),
            "Score": check["score"] or "",
        }
        for check in checks
    ]


def checks_table(checks: list[dict]) -> None:
    if not checks:
        st.markdown('<p class="note">No document values were readable, so nothing could be compared.</p>', unsafe_allow_html=True)
        return
    st.dataframe(
        checks_rows(checks),
        hide_index=True,
        column_config={
            "Result": st.column_config.TextColumn("Result", width="small"),
            "Score": st.column_config.TextColumn("Score", width="small"),
        },
    )


def notification_block(bundle: dict) -> None:
    submission = bundle["submission"]
    subject = submission["notification_subject"]
    body = submission["notification_body"]
    st.markdown(
        f'<div class="mail"><div class="subj">{esc(subject)}</div><pre>{esc(body)}</pre></div>',
        unsafe_allow_html=True,
    )
    st.download_button(
        "Download as text",
        data=f"Subject: {subject}\n\n{body}",
        file_name=f"{submission['reference']}-vendor-message.txt",
        mime="text/plain",
        key=f"msg-{submission['reference']}",
    )
    st.markdown(
        '<p class="note">Drafted by the process, not sent by it. Nothing leaves this machine.</p>',
        unsafe_allow_html=True,
    )


def documents_block(bundle: dict, key_prefix: str = "doc") -> None:
    documents = bundle["documents"]
    if not documents:
        st.markdown('<p class="note">No documents were attached to this submission.</p>', unsafe_allow_html=True)
        return
    for index, doc in enumerate(documents):
        label = SLOT_LABEL.get(doc["slot"], doc["slot"])
        status = "parsed" if doc["parse_status"] == "parsed" else "unreadable"
        with st.expander(f'{label} · {doc["filename"]} · {status}', expanded=(len(documents) == 1)):
            meta = st.columns(4)
            meta[0].caption(f'Slot  \n**{label}**')
            meta[1].caption(f'Read as  \n**{doc["document_type"]}**')
            meta[2].caption(f'Method  \n**{doc["method"] or "none"}**')
            meta[3].caption(f'Confidence  \n**{float(doc["confidence"]):.2f}**')
            fields = [
                {"Field": FIELD_LABEL.get(name, name), "Value": doc["fields"].get(name) or "—"}
                for name in DOC_FIELDS
                if name in doc["fields"]
            ]
            if fields:
                st.dataframe(fields, hide_index=True)
            else:
                st.markdown('<p class="note">No labelled fields were found in this file.</p>', unsafe_allow_html=True)
            for warning in doc.get("warnings") or []:
                st.caption(f"— {warning}")
            if doc.get("text_excerpt"):
                st.code(doc["text_excerpt"][:1200], language="text")
            if doc.get("content"):
                st.download_button(
                    "Download the original",
                    data=doc["content"],
                    file_name=doc["filename"],
                    mime="application/pdf" if doc["filename"].lower().endswith(".pdf") else "text/plain",
                    key=f'{key_prefix}-{index}-{doc["filename"]}',
                )


def scenario_submission_table(submission: dict) -> None:
    rows = [
        f'<dt>{esc(FIELD_LABEL[key])}</dt><dd>{esc(value or "—")}</dd>'
        for key, value in submission.items()
        if key in FIELD_LABEL
    ]
    st.markdown(f'<dl class="dl">{"".join(rows)}</dl>', unsafe_allow_html=True)
