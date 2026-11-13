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
    (
        "documents",
        "Documents processed",
        "PDFs and text files are parsed, classified and read for labelled fields; pages without a "
        "text layer are OCRed and their key/value pairs recovered from layout.",
    ),
    ("validate", "Field validation", "Format, checksum and completeness rules run on every submitted field."),
    ("cross", "Cross-document verification", "The form is compared with what the documents actually say."),
    ("duplicate", "Duplicate detection", "Tax ID, CIN and payout account are checked against the vendor registry."),
    ("decision", "Decision generated", "Severity is aggregated: reject beats pending, warnings never block."),
    ("audit", "Notification and audit log", "A vendor reply is drafted and the whole run is stored."),
)

CSS = """
<style>
:root{
  --paper:#f4f0e7; --paper-2:#ece6d9; --card:#fbf9f4; --ink:#1c1915; --ink-2:#4b4438; --muted:#6b6152;
  --line:rgba(28,25,21,.12); --line-2:rgba(28,25,21,.2);
  --green:#0f6e56; --green-d:#0b5a45; --green-soft:#ddefe5;
  --amber:#a86b1f; --amber-soft:#faeed2; --red:#a3372c; --red-soft:#f9e1dc;
  --shadow:0 1px 2px rgba(28,25,21,.05),0 6px 20px -12px rgba(28,25,21,.18);
  --shadow-up:0 2px 4px rgba(28,25,21,.06),0 12px 28px -14px rgba(28,25,21,.28);
  --serif:Georgia,"Times New Roman",serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
}

/* ---------- base ---------- */
.stApp{background:var(--paper);}
[data-testid="stHeader"]{background:transparent;}
[data-testid="stSidebarNav"]{display:none;}
.block-container{padding-top:2.2rem;padding-bottom:4rem;max-width:1180px;animation:rise .35s ease both;}
@keyframes rise{from{opacity:0;transform:translateY(6px);}to{opacity:1;transform:none;}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important;}}
h1,h2,h3,h4,[data-testid="stMarkdownContainer"] h1,[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3{font-family:var(--serif)!important;color:var(--ink);letter-spacing:-.018em;font-weight:700;}
a{color:var(--green);}
p{line-height:1.6;}
:focus-visible{outline:2px solid var(--green);outline-offset:2px;}
::selection{background:var(--green-soft);}

/* ---------- sidebar ---------- */
section[data-testid="stSidebar"]{background:var(--paper-2);border-right:1px solid var(--line);}
[data-testid="stSidebar"] .block-container{padding-top:1.4rem;animation:none;}
[data-testid="stSidebar"] .stButton{margin-bottom:-.35rem;}
[data-testid="stSidebar"] .stButton button{justify-content:flex-start;background:transparent;border:1px solid transparent;
  color:var(--ink-2);font-weight:500;padding:.5rem .8rem;border-radius:10px;box-shadow:none;transition:background .15s,color .15s;}
[data-testid="stSidebar"] .stButton button>div{justify-content:flex-start;width:100%;}
[data-testid="stSidebar"] .stButton button p{text-align:left;}
[data-testid="stSidebar"] .stButton button:hover:not(:disabled){background:rgba(15,110,86,.09);color:var(--green-d);border-color:transparent;}
[data-testid="stSidebar"] .stButton button:disabled{background:var(--green);color:#fff;opacity:1;font-weight:600;
  box-shadow:0 4px 12px -6px rgba(15,110,86,.6);cursor:default;}
[data-testid="stSidebar"] .stButton button:disabled p{color:#fff;}
.brandrow{display:flex;align-items:center;gap:.65rem;}
.logo{width:34px;height:34px;border-radius:10px;background:linear-gradient(140deg,var(--green),#0b4a3a);color:#fff;
  display:grid;place-items:center;font-family:var(--serif);font-size:1.1rem;font-weight:700;box-shadow:0 4px 10px -4px rgba(15,110,86,.7);}
.brand{font-family:var(--serif);font-size:1.28rem;letter-spacing:-.01em;line-height:1.1;}
.brandsub{font-size:.66rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-top:.15rem;}
.sidenav{font-size:.7rem;color:var(--muted);margin:1.5rem 0 .6rem;letter-spacing:.14em;text-transform:uppercase;font-weight:700;}
.sidefoot{font-size:.72rem;color:var(--muted);line-height:1.55;border-top:1px solid var(--line);margin-top:1.6rem;padding-top:.9rem;}

/* ---------- typography helpers ---------- */
.eyebrow{font-size:.68rem;font-weight:700;letter-spacing:.18em;text-transform:uppercase;color:var(--green);}
.lede{font-size:1.04rem;color:var(--ink-2);max-width:70ch;margin:0;}
.phead{margin-bottom:1.4rem;padding-bottom:1.2rem;border-bottom:1px solid var(--line);}
.phead h1{font-size:2.3rem;margin:.25rem 0 .55rem;line-height:1.1;}
.sechead{margin:2.4rem 0 1rem;}
.sechead h2{margin:.25rem 0 .3rem;font-size:1.4rem;}
.note{font-size:.82rem;color:var(--muted);line-height:1.6;}
.mono{font-family:var(--mono);font-size:.8rem;}
.code{font-family:var(--mono);font-size:.72rem;background:#efe9dc;border-radius:6px;padding:.1rem .45rem;color:#5c5346;}

/* ---------- surfaces ---------- */
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px 20px;box-shadow:var(--shadow);height:100%;}
.card .eyebrow{color:var(--muted);}
.hero{position:relative;overflow:hidden;background:linear-gradient(135deg,#fbf9f4 0%,#f3ecdd 100%);border:1px solid var(--line);
  border-radius:24px;padding:40px 42px 36px;box-shadow:var(--shadow);}
.hero:after{content:"";position:absolute;right:-90px;top:-90px;width:320px;height:320px;border-radius:50%;
  background:radial-gradient(circle,rgba(15,110,86,.16),transparent 68%);pointer-events:none;}
.hero h1{font-size:2.8rem;line-height:1.08;margin:.5rem 0 .9rem;position:relative;}
.hero .lede{position:relative;}
.statgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:20px 0 8px;}
.stat{position:relative;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px 20px 16px;
  box-shadow:var(--shadow);overflow:hidden;transition:transform .18s,box-shadow .18s;}
.stat:before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--line-2);}
.stat.ok:before{background:var(--green);} .stat.warn:before{background:#c99a3f;} .stat.bad:before{background:var(--red);}
.stat.accent:before{background:var(--ink);}
.stat:hover{transform:translateY(-2px);box-shadow:var(--shadow-up);}
.stat .n{font-family:var(--serif);font-size:2.1rem;line-height:1;letter-spacing:-.02em;}
.stat.ok .n{color:var(--green);} .stat.warn .n{color:var(--amber);} .stat.bad .n{color:var(--red);}
.stat .l{font-size:.68rem;letter-spacing:.13em;text-transform:uppercase;color:var(--muted);margin-top:.55rem;font-weight:600;}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;}
.step{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;height:100%;
  box-shadow:var(--shadow);transition:transform .18s,box-shadow .18s,border-color .18s;}
.step:hover{transform:translateY(-2px);box-shadow:var(--shadow-up);border-color:rgba(15,110,86,.35);}
.step .num{display:inline-grid;place-items:center;width:28px;height:28px;border-radius:8px;background:var(--green-soft);
  color:var(--green-d);font-size:.74rem;letter-spacing:.04em;font-weight:700;}
.step b{display:block;margin:.6rem 0 .3rem;font-size:.98rem;}
.step p{margin:0;font-size:.84rem;color:var(--muted);line-height:1.55;}
.rule{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;height:100%;
  border-top:4px solid var(--line-2);box-shadow:var(--shadow);}
.rule.ok{border-top-color:var(--green);} .rule.warn{border-top-color:#c99a3f;} .rule.bad{border-top-color:var(--red);}
.rule b{display:block;margin-bottom:.4rem;font-family:var(--serif);font-size:1.1rem;}
.rule ul{margin:.3rem 0 0;padding-left:1.05rem;font-size:.85rem;color:var(--ink-2);line-height:1.7;}
.rule li::marker{color:var(--muted);}

/* ---------- badges ---------- */
.pill{display:inline-flex;align-items:center;gap:.4rem;padding:.22rem .7rem;border-radius:999px;font-size:.68rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;white-space:nowrap;}
.pill:before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;}
.pill.ok{background:var(--green-soft);color:var(--green-d);} .pill.warn{background:var(--amber-soft);color:#8a5a12;}
.pill.bad{background:var(--red-soft);color:#96281d;} .pill.neutral{background:#ece5d8;color:#5c5346;}
.chip{font-size:.64rem;letter-spacing:.05em;text-transform:uppercase;font-weight:700;padding:.14rem .5rem;border-radius:6px;background:#ece5d8;color:#5c5346;white-space:nowrap;}
.chip.pass{background:var(--green-soft);color:var(--green-d);} .chip.attention,.chip.pending{background:var(--amber-soft);color:#8a5a12;}
.chip.stop,.chip.reject{background:var(--red-soft);color:#96281d;} .chip.warn{background:#ece5d8;color:#5c5346;}

/* ---------- pipeline stages ---------- */
.stages{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:12px 10px 14px;box-shadow:var(--shadow);}
.livehead{display:flex;justify-content:space-between;font-size:.74rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);padding:4px 12px 0;font-weight:700;}
.bar{height:6px;background:#e6dfd2;border-radius:999px;overflow:hidden;margin:9px 12px 8px;}
.bar>span{display:block;height:100%;background:linear-gradient(90deg,var(--green),#17956f);border-radius:999px;transition:width .3s ease;}
.stage{position:relative;display:grid;grid-template-columns:22px 1fr;gap:8px;padding:10px 12px;border-radius:12px;animation:rise .3s ease both;}
.stage+.stage{margin-top:2px;}
.stage:not(:last-child):before{content:"";position:absolute;left:22px;top:26px;bottom:-12px;width:2px;background:var(--line);}
.stage:hover{background:rgba(28,25,21,.03);}
.stage .dot{position:relative;z-index:1;width:11px;height:11px;border-radius:50%;background:#cdc4b3;margin-top:5px;margin-left:5px;box-shadow:0 0 0 3px var(--card);}
.stage.pass .dot{background:var(--green);} .stage.attention .dot{background:#c99a3f;}
.stage.stop .dot{background:var(--red);} .stage.queued{opacity:.4;animation:none;}
.stage .head{display:flex;align-items:center;gap:.5rem;font-size:.93rem;font-weight:600;}
.stage .ms{margin-left:auto;font-variant-numeric:tabular-nums;font-size:.7rem;color:var(--muted);font-weight:400;}
.stage .sum{margin:.2rem 0 0;font-size:.85rem;color:var(--ink-2);line-height:1.5;}
.stage .det{margin:.35rem 0 0;padding-left:1rem;font-size:.76rem;color:var(--muted);line-height:1.55;}
.stage .det li{margin-bottom:.12rem;word-break:break-word;}

/* ---------- decision + findings ---------- */
.verdict{background:var(--card);border:1px solid var(--line);border-left:6px solid var(--green);border-radius:8px 18px 18px 8px;padding:24px 28px;box-shadow:var(--shadow);}
.verdict.pending{border-left-color:#c99a3f;background:linear-gradient(135deg,#fdf7ea,#fbf9f4);}
.verdict.rejected{border-left-color:var(--red);background:linear-gradient(135deg,#fbf0ed,#fbf9f4);}
.verdict.approved{background:linear-gradient(135deg,#eef7f1,#fbf9f4);}
.verdict h2{margin:.7rem 0 .45rem;font-size:1.6rem;line-height:1.2;}
.verdict p{margin:0;color:var(--ink-2);font-size:.96rem;}
.vmeta{display:flex;flex-wrap:wrap;gap:.45rem;margin-top:1.1rem;}
.vmeta span{font-size:.72rem;color:var(--muted);background:rgba(28,25,21,.05);border-radius:999px;padding:.2rem .65rem;}
.vrow{display:flex;align-items:center;gap:.7rem;}
.ref{font-family:var(--mono);font-size:.78rem;color:var(--muted);}
.dl{display:grid;grid-template-columns:170px 1fr;gap:0;font-size:.86rem;background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;}
.dl dt,.dl dd{padding:.5rem .9rem;border-top:1px solid var(--line);margin:0;}
.dl dt:first-of-type,.dl dt:first-of-type+dd{border-top:none;}
.dl dt{color:var(--muted);background:rgba(28,25,21,.025);} .dl dd{word-break:break-word;}
.finding{display:flex;gap:.75rem;align-items:flex-start;padding:.85rem 1rem;margin-bottom:.5rem;background:var(--card);
  border:1px solid var(--line);border-radius:12px;font-size:.87rem;line-height:1.55;}
.finding .msg{flex:1;}
.finding .vendor{margin:.35rem 0 0;font-size:.8rem;color:var(--muted);}
.mail{background:#fffdf7;border:1px solid var(--line);border-radius:14px;padding:20px 24px;font-size:.88rem;line-height:1.65;box-shadow:var(--shadow);}
.mail .subj{font-weight:700;margin-bottom:.8rem;padding-bottom:.7rem;border-bottom:1px solid var(--line);font-family:var(--serif);font-size:1rem;}
.mail pre{white-space:pre-wrap;font-family:inherit;margin:0;font-size:.88rem;line-height:1.65;background:none;padding:0;}
.scenario{display:flex;gap:1rem;align-items:flex-start;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px 18px;
  box-shadow:var(--shadow);transition:border-color .15s;}
.scenario:hover{border-color:rgba(15,110,86,.35);}
.scenario+.scenario{margin-top:.5rem;}
.scenario .txt{flex:1;}
.scenario b{font-size:.95rem;}
.scenario p{margin:.25rem 0 0;font-size:.83rem;color:var(--muted);}

/* ---------- dashboard mix ---------- */
.mix{display:flex;height:12px;border-radius:999px;overflow:hidden;background:#e6dfd2;margin:.9rem 0 .8rem;gap:2px;}
.mix span{display:block;height:100%;}
.legend{display:flex;flex-wrap:wrap;gap:.4rem 1.2rem;font-size:.82rem;color:var(--ink-2);}
.legend i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:.4rem;}
.legend b{font-variant-numeric:tabular-nums;}
.rowline{display:flex;justify-content:space-between;align-items:center;gap:1rem;padding:.45rem 0;border-top:1px dashed var(--line);font-size:.85rem;}
.rowline:first-child{border-top:none;}

/* ---------- streamlit widgets ---------- */
.stButton button,.stDownloadButton button,.stFormSubmitButton button{border-radius:10px;font-weight:600;border:1px solid var(--line-2);
  background:var(--card);color:var(--ink);transition:transform .12s,box-shadow .15s,background .15s,border-color .15s;box-shadow:0 1px 2px rgba(28,25,21,.06);}
.stButton button:hover,.stDownloadButton button:hover,.stFormSubmitButton button:hover{border-color:var(--green);color:var(--green-d);transform:translateY(-1px);box-shadow:0 4px 12px -6px rgba(28,25,21,.3);}
.stButton button:active,.stDownloadButton button:active{transform:translateY(0);}
button[data-testid="stBaseButton-primary"],button[data-testid="stBaseButton-primaryFormSubmit"]{background:var(--green);border-color:var(--green);color:#fff;
  box-shadow:0 6px 16px -8px rgba(15,110,86,.8);}
button[data-testid="stBaseButton-primary"]:hover,button[data-testid="stBaseButton-primaryFormSubmit"]:hover{background:var(--green-d);border-color:var(--green-d);color:#fff;}
button[data-testid="stBaseButton-primary"] p,button[data-testid="stBaseButton-primaryFormSubmit"] p{color:#fff;}
[data-testid="stMetric"]{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px 18px;box-shadow:var(--shadow);}
[data-testid="stMetricLabel"] p{font-size:.7rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);font-weight:600;}
[data-testid="stMetricValue"]{font-family:var(--serif);letter-spacing:-.02em;}
[data-baseweb="tab-list"]{gap:.4rem;border-bottom:1px solid var(--line);}
button[data-baseweb="tab"]{border-radius:10px 10px 0 0;padding:.6rem 1rem;font-weight:600;color:var(--muted);transition:background .15s,color .15s;}
button[data-baseweb="tab"]:hover{background:rgba(15,110,86,.07);color:var(--green-d);}
button[data-baseweb="tab"][aria-selected="true"]{color:var(--green-d);}
[data-baseweb="tab-highlight"]{background:var(--green);height:3px;border-radius:3px 3px 0 0;}
[data-baseweb="tab-border"]{background:transparent;}
[data-baseweb="input"],[data-baseweb="select"]>div,[data-baseweb="textarea"]{background:var(--card)!important;border-radius:10px!important;border-color:var(--line-2)!important;}
[data-baseweb="input"]:focus-within,[data-baseweb="select"]>div:focus-within{border-color:var(--green)!important;box-shadow:0 0 0 3px rgba(15,110,86,.15);}
[data-testid="stWidgetLabel"] p{font-size:.82rem;font-weight:600;color:var(--ink-2);}
[data-testid="stForm"]{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px 24px;box-shadow:var(--shadow);}
[data-testid="stExpander"]{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);overflow:hidden;}
[data-testid="stExpander"] summary:hover{background:rgba(28,25,21,.03);}
[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:14px;overflow:hidden;box-shadow:var(--shadow);}
[data-testid="stFileUploaderDropzone"]{background:var(--card);border:1.5px dashed var(--line-2);border-radius:14px;transition:border-color .15s,background .15s;}
[data-testid="stFileUploaderDropzone"]:hover{border-color:var(--green);background:#f6faf7;}
[data-testid="stAlert"]{border-radius:12px;}
[data-testid="stRadio"] [role="radiogroup"]{gap:.5rem;}
[data-testid="stRadio"] label{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:.35rem 1rem;transition:border-color .15s,background .15s;}
[data-testid="stRadio"] label:hover{border-color:var(--green);}
[data-testid="stRadio"] label:has(input:checked){background:var(--green-soft);border-color:var(--green);}
[data-testid="stRadio"] label>div:first-child{display:none;}

@media (max-width:900px){
  .statgrid{grid-template-columns:repeat(2,1fr);}
  .grid3{grid-template-columns:1fr;}
  .hero{padding:26px;}
  .hero h1{font-size:2.1rem;}
  .phead h1{font-size:1.9rem;}
  .dl{grid-template-columns:1fr;}
  .dl dt{border-bottom:none;padding-bottom:0;}
  .scenario{flex-direction:column;}
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


def page_header(kicker: str, title: str, subtitle: str = "") -> None:
    st.markdown(
        f'<div class="phead"><div class="eyebrow">{esc(kicker)}</div>'
        f"<h1>{esc(title)}</h1>" + (f'<p class="lede">{esc(subtitle)}</p>' if subtitle else "") + "</div>",
        unsafe_allow_html=True,
    )


def section(kicker: str, title: str, subtitle: str = "") -> None:
    sub = f'<p class="note">{esc(subtitle)}</p>' if subtitle else ""
    st.markdown(
        f'<div class="sechead"><div class="eyebrow">{esc(kicker)}</div>'
        f'<h2>{esc(title)}</h2>{sub}</div>',
        unsafe_allow_html=True,
    )


def stat_grid(items: list[tuple[str, str]], tones: tuple[str, ...] = ("accent", "ok", "warn", "bad")) -> None:
    cells = "".join(
        f'<div class="stat {tones[index] if index < len(tones) else ""}"><div class="n">{esc(value)}</div>'
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
        st.markdown(
            '<div class="brandrow"><div class="logo">V</div><div><div class="brand">VendorFlow</div>'
            '<div class="brandsub">Vendor onboarding</div></div></div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="sidenav">Workspace</div>', unsafe_allow_html=True)
        for key, label, path in links:
            if key == active:
                st.button(label, key=f"nav-now-{key}", disabled=True, width="stretch")
            elif st.button(label, key=f"nav-{key}", width="stretch"):
                st.switch_page(path)


def decision_mix(counts: dict, registry_size: int, avg_ms: int) -> None:
    """Segmented approved / pending / rejected bar with a legend."""
    total = counts["total"]
    segments = (
        ("approved", "Approved", "#0f6e56"),
        ("pending", "Needs a reply", "#c99a3f"),
        ("rejected", "Rejected", "#a3372c"),
    )
    bar = "".join(
        f'<span style="width:{counts[key] / total * 100:.2f}%;background:{colour};" title="{label}: {counts[key]}"></span>'
        for key, label, colour in segments
        if counts[key]
    )
    legend = "".join(
        f'<span><i style="background:{colour}"></i>{label} <b>{counts[key]}</b></span>'
        for key, label, colour in segments
    )
    st.markdown(
        f'<div class="card"><div class="eyebrow">Decision mix</div><div class="mix">{bar}</div>'
        f'<div class="legend">{legend}</div>'
        f'<p class="note" style="margin:.8rem 0 0;">Avg pipeline time {avg_ms} ms · registry {registry_size} vendors · '
        f"{total} decision(s) on record</p></div>",
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
        f'rules: {esc(submission.get("rule_engine_version") or "unversioned")}',
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
