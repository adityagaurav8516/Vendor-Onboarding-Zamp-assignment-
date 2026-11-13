from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from database.models import ExistingVendor, RunResult

DEFAULT_DB = Path(__file__).resolve().parent / "vendorflow.db"
DB_PATH = Path(os.getenv("VENDORFLOW_DB", str(DEFAULT_DB)))

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    legal_name TEXT NOT NULL DEFAULT '',
    trade_name TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    tax_id TEXT NOT NULL DEFAULT '',
    registration_number TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    contact_name TEXT NOT NULL DEFAULT '',
    contact_email TEXT NOT NULL DEFAULT '',
    contact_phone TEXT NOT NULL DEFAULT '',
    bank_holder_name TEXT NOT NULL DEFAULT '',
    bank_name TEXT NOT NULL DEFAULT '',
    account_number TEXT NOT NULL DEFAULT '',
    bank_code TEXT NOT NULL DEFAULT '',
    bank_country TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL UNIQUE,
    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
    status TEXT NOT NULL,
    machine_status TEXT NOT NULL,
    headline TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    extraction_method TEXT NOT NULL DEFAULT '',
    duplicate_of INTEGER,
    scenario_id TEXT,
    notification_subject TEXT NOT NULL DEFAULT '',
    notification_body TEXT NOT NULL DEFAULT '',
    rule_engine_version TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    slot TEXT NOT NULL,
    filename TEXT NOT NULL,
    parse_status TEXT NOT NULL,
    document_type TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    text_excerpt TEXT NOT NULL DEFAULT '',
    fields_json TEXT NOT NULL DEFAULT '{}',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    content BLOB
);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    stage TEXT NOT NULL,
    message TEXT NOT NULL,
    vendor_message TEXT
);

CREATE TABLE IF NOT EXISTS field_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    submitted TEXT NOT NULL DEFAULT '',
    extracted TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '',
    score TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stage_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '[]',
    elapsed_ms INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_submission ON documents(submission_id);
CREATE INDEX IF NOT EXISTS idx_findings_submission ON findings(submission_id);
CREATE INDEX IF NOT EXISTS idx_checks_submission ON field_checks(submission_id);
CREATE INDEX IF NOT EXISTS idx_stages_submission ON stage_logs(submission_id);
CREATE INDEX IF NOT EXISTS idx_events_submission ON events(submission_id);
CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(status);
CREATE INDEX IF NOT EXISTS idx_vendors_status ON vendors(status);
"""

_READY = False


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    global _READY
    if _READY:
        return
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
    from test_data.seed import ensure_seed

    ensure_seed()
    _READY = True


def _migrate(conn) -> None:
    """Bring an existing database up to the current schema without losing history."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(submissions)")}
    if "rule_engine_version" not in columns:
        # Runs recorded before versioning keep an empty stamp; they are never relabelled.
        conn.execute(
            "ALTER TABLE submissions ADD COLUMN rule_engine_version TEXT NOT NULL DEFAULT ''"
        )


def _row(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def _rows(rows) -> list[dict]:
    return [dict(row) for row in rows]


# ---------------------------------------------------------------- registry


def registry_count() -> int:
    with connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0])


def insert_registry_vendor(**fields) -> int:
    now = _now()
    columns = (
        "legal_name",
        "trade_name",
        "country",
        "tax_id",
        "registration_number",
        "address",
        "contact_name",
        "contact_email",
        "contact_phone",
        "bank_holder_name",
        "bank_name",
        "account_number",
        "bank_code",
        "bank_country",
        "status",
    )
    values = [str(fields.get(column, "") or "") for column in columns]
    with connect() as conn:
        cursor = conn.execute(
            f"INSERT INTO vendors ({', '.join(columns)}, created_at, updated_at) "
            f"VALUES ({', '.join('?' for _ in columns)}, ?, ?)",
            (*values, now, now),
        )
        return int(cursor.lastrowid)


def load_registry() -> list[ExistingVendor]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, legal_name, country, tax_id, registration_number, account_number, "
            "bank_code, contact_email, status FROM vendors ORDER BY id"
        ).fetchall()
    return [
        ExistingVendor(
            id=int(row["id"]),
            legal_name=row["legal_name"],
            country=row["country"],
            tax_id=row["tax_id"],
            registration_number=row["registration_number"],
            account_number=row["account_number"],
            bank_code=row["bank_code"],
            contact_email=row["contact_email"],
            status=row["status"],
        )
        for row in rows
    ]


def list_registry() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, legal_name, country, tax_id, registration_number, account_number, "
            "bank_code, contact_email, status, created_at FROM vendors ORDER BY id"
        ).fetchall()
    return _rows(rows)


# ---------------------------------------------------------------- writes


def save_run(result: RunResult) -> tuple[int, str]:
    """Persist a completed pipeline run. Returns (submission_id, reference)."""
    submission = result.submission
    now = _now()
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO vendors (legal_name, trade_name, country, tax_id, registration_number, "
            "address, contact_name, contact_email, contact_phone, bank_holder_name, bank_name, "
            "account_number, bank_code, bank_country, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                submission.legal_name,
                submission.trade_name,
                submission.country,
                submission.tax_id,
                submission.registration_number,
                submission.address,
                submission.contact_name,
                submission.contact_email,
                submission.contact_phone,
                submission.bank_holder_name,
                submission.bank_name,
                submission.account_number,
                submission.bank_code,
                submission.bank_country,
                result.status,
                now,
                now,
            ),
        )
        vendor_id = int(cursor.lastrowid)
        cursor = conn.execute(
            "INSERT INTO submissions (reference, vendor_id, status, machine_status, headline, "
            "summary, extraction_method, duplicate_of, scenario_id, notification_subject, "
            "notification_body, rule_engine_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "VF-TEMP",
                vendor_id,
                result.status,
                result.status,
                result.headline,
                result.summary,
                result.extraction_method,
                result.duplicate_of,
                result.scenario_id,
                result.notification_subject,
                result.notification_body,
                result.rule_engine_version,
                now,
            ),
        )
        submission_id = int(cursor.lastrowid)
        reference = f"VF-{submission_id:04d}"
        conn.execute(
            "UPDATE submissions SET reference = ? WHERE id = ?", (reference, submission_id)
        )

        for doc in result.documents:
            conn.execute(
                "INSERT INTO documents (submission_id, slot, filename, parse_status, document_type, "
                "method, confidence, text_excerpt, fields_json, warnings_json, content) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    submission_id,
                    doc.slot,
                    doc.filename,
                    doc.parse_status,
                    doc.document_type,
                    doc.method,
                    float(doc.confidence),
                    doc.text_excerpt,
                    json.dumps(doc.fields),
                    json.dumps(doc.warnings),
                    doc.content,
                ),
            )

        for finding in result.findings:
            conn.execute(
                "INSERT INTO findings (submission_id, code, severity, stage, message, vendor_message) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    submission_id,
                    finding.code,
                    finding.severity,
                    finding.stage,
                    finding.message,
                    finding.vendor_message,
                ),
            )

        for position, check in enumerate(result.checks):
            conn.execute(
                "INSERT INTO field_checks (submission_id, field, submitted, extracted, source, result, "
                "score, position) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    submission_id,
                    check.field,
                    check.submitted,
                    check.extracted,
                    check.source,
                    check.result,
                    check.score,
                    position,
                ),
            )

        for position, stage in enumerate(result.stages):
            conn.execute(
                "INSERT INTO stage_logs (submission_id, key, title, status, summary, details_json, "
                "elapsed_ms, position) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    submission_id,
                    stage.key,
                    stage.title,
                    stage.status,
                    stage.summary,
                    json.dumps(stage.details),
                    stage.elapsed_ms,
                    position,
                ),
            )

        conn.execute(
            "INSERT INTO events (submission_id, event, detail, actor, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                submission_id,
                "submitted",
                f"{reference} processed: {result.status} "
                f"(rules {result.rule_engine_version or 'unversioned'}).",
                "pipeline",
                now,
            ),
        )
    return submission_id, reference


def log_event(submission_id: int, event: str, detail: str, actor: str = "system") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO events (submission_id, event, detail, actor, created_at) VALUES (?, ?, ?, ?, ?)",
            (submission_id, event, detail, actor, _now()),
        )


def apply_admin_decision(
    submission_id: int, status: str, note: str, actor: str, action: str
) -> None:
    if status not in {"approved", "pending", "rejected"}:
        raise ValueError(f"Unsupported review status: {status}")
    now = _now()
    with connect() as conn:
        row = conn.execute(
            "SELECT vendor_id, status FROM submissions WHERE id = ?", (submission_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown submission {submission_id}")
        conn.execute(
            "UPDATE submissions SET status = ? WHERE id = ?", (status, submission_id)
        )
        conn.execute(
            "UPDATE vendors SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, row["vendor_id"]),
        )
        conn.execute(
            "INSERT INTO events (submission_id, event, detail, actor, created_at) VALUES (?, ?, ?, ?, ?)",
            (submission_id, action, note.strip(), actor.strip() or "reviewer", now),
        )


# ---------------------------------------------------------------- reads


def _submission_select() -> str:
    return (
        "SELECT s.id, s.reference, s.status, s.machine_status, s.headline, s.summary, "
        "s.extraction_method, s.duplicate_of, s.scenario_id, s.rule_engine_version, s.created_at, "
        "s.notification_subject, s.notification_body, "
        "v.legal_name, v.country, v.tax_id, v.registration_number, v.contact_email, "
        "v.bank_holder_name, v.bank_name, v.bank_country, "
        "(SELECT COUNT(*) FROM findings f WHERE f.submission_id = s.id "
        " AND f.severity IN ('pending','reject')) AS issue_count, "
        "(SELECT f.message FROM findings f WHERE f.submission_id = s.id "
        " AND f.severity IN ('pending','reject') ORDER BY f.id LIMIT 1) AS top_issue "
        "FROM submissions s JOIN vendors v ON v.id = s.vendor_id"
    )


def list_submissions(statuses: list[str] | None = None, search: str = "") -> list[dict]:
    query = _submission_select()
    clauses: list[str] = []
    params: list = []
    if statuses:
        clauses.append(f"s.status IN ({', '.join('?' for _ in statuses)})")
        params.extend(statuses)
    if search.strip():
        needle = f"%{search.strip().lower()}%"
        clauses.append(
            "(LOWER(v.legal_name) LIKE ? OR LOWER(s.reference) LIKE ? OR LOWER(v.tax_id) LIKE ?)"
        )
        params.extend([needle, needle, needle])
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY s.id DESC"
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return _rows(rows)


def find_by_reference(reference: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            _submission_select() + " WHERE UPPER(s.reference) = ?",
            (reference.strip().upper(),),
        ).fetchone()
    return _row(row)


def get_submission(submission_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            _submission_select() + " WHERE s.id = ?", (submission_id,)
        ).fetchone()
    return _row(row)


def get_submission_bundle(submission_id: int) -> dict | None:
    with connect() as conn:
        submission = conn.execute(
            _submission_select() + " WHERE s.id = ?", (submission_id,)
        ).fetchone()
        if submission is None:
            return None
        vendor = conn.execute(
            "SELECT * FROM vendors WHERE id = ?", (submission["vendor_id"],)
        ).fetchone() if "vendor_id" in submission.keys() else None
        if vendor is None:
            vendor = conn.execute(
                "SELECT v.* FROM vendors v JOIN submissions s ON s.vendor_id = v.id "
                "WHERE s.id = ?",
                (submission_id,),
            ).fetchone()
        documents = conn.execute(
            "SELECT id, slot, filename, parse_status, document_type, method, confidence, "
            "text_excerpt, fields_json, warnings_json, content, LENGTH(content) AS size "
            "FROM documents WHERE submission_id = ? ORDER BY id",
            (submission_id,),
        ).fetchall()
        findings = conn.execute(
            "SELECT code, severity, stage, message, vendor_message FROM findings "
            "WHERE submission_id = ? ORDER BY id",
            (submission_id,),
        ).fetchall()
        checks = conn.execute(
            "SELECT field, submitted, extracted, source, result, score FROM field_checks "
            "WHERE submission_id = ? ORDER BY position, id",
            (submission_id,),
        ).fetchall()
        stages = conn.execute(
            "SELECT key, title, status, summary, details_json, elapsed_ms FROM stage_logs "
            "WHERE submission_id = ? ORDER BY position, id",
            (submission_id,),
        ).fetchall()
        events = conn.execute(
            "SELECT event, detail, actor, created_at FROM events WHERE submission_id = ? "
            "ORDER BY id",
            (submission_id,),
        ).fetchall()
    return {
        "submission": dict(submission),
        "vendor": dict(vendor),
        "documents": [
            {
                **{key: row[key] for key in row.keys() if key not in {"fields_json", "warnings_json"}},
                "fields": json.loads(row["fields_json"] or "{}"),
                "warnings": json.loads(row["warnings_json"] or "[]"),
            }
            for row in documents
        ],
        "findings": _rows(findings),
        "checks": _rows(checks),
        "stages": [
            {
                **{key: row[key] for key in row.keys() if key != "details_json"},
                "details": json.loads(row["details_json"] or "[]"),
            }
            for row in stages
        ],
        "events": _rows(events),
    }


def dashboard_counts() -> dict[str, int]:
    counts = {"total": 0, "approved": 0, "pending": 0, "rejected": 0}
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS total FROM submissions GROUP BY status"
        ).fetchall()
    for row in rows:
        counts[row["status"]] = int(row["total"])
        counts["total"] += int(row["total"])
    return counts


def finding_stats(limit: int = 6) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT code, COUNT(*) AS total, "
            "SUM(CASE WHEN severity IN ('pending','reject') THEN 1 ELSE 0 END) AS blocking "
            "FROM findings GROUP BY code ORDER BY total DESC, code LIMIT ?",
            (limit,),
        ).fetchall()
    return _rows(rows)


def recent_events(limit: int = 8) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT e.event, e.detail, e.actor, e.created_at, s.reference FROM events e "
            "JOIN submissions s ON s.id = e.submission_id ORDER BY e.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return _rows(rows)


def average_run_ms() -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT AVG(total) FROM (SELECT SUM(elapsed_ms) AS total FROM stage_logs "
            "GROUP BY submission_id)"
        ).fetchone()
    return int(row[0] or 0)


def reset_demo_data() -> None:
    global _READY
    with connect() as conn:
        conn.executescript(
            "DELETE FROM events; DELETE FROM stage_logs; DELETE FROM field_checks; "
            "DELETE FROM findings; DELETE FROM documents; DELETE FROM submissions; "
            "DELETE FROM vendors; DELETE FROM sqlite_sequence;"
        )
    _READY = False
    init_db()
