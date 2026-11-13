from __future__ import annotations

import argparse
import json
from dataclasses import fields
from pathlib import Path

from backend.pipeline import run_pipeline
from database import db
from database.models import DocumentFile, Submission
from test_data.scenarios import SCENARIOS_BY_ID, build_documents, to_submission
from test_data.seed import seed_vendors

SLOTS = ("incorporation", "tax_certificate", "bank_proof")


def submission_from_json(data: dict) -> Submission:
    allowed = {item.name for item in fields(Submission)}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise SystemExit(f"Unknown submission field(s): {', '.join(unknown)}")
    return Submission(**{key: str(value) for key, value in data.items()})


def documents_from_json(mapping: dict) -> list[DocumentFile]:
    documents = []
    for slot, raw_path in mapping.items():
        if slot not in SLOTS:
            raise SystemExit(f"Unknown document slot '{slot}'. Use one of {', '.join(SLOTS)}.")
        path = Path(raw_path)
        if not path.is_file():
            raise SystemExit(f"Document not found: {path}")
        documents.append(
            DocumentFile(slot=slot, filename=path.name, content=path.read_bytes())
        )
    return documents


def summarize(result, reference: str | None) -> dict:
    return {
        "reference": reference,
        "scenario": result.scenario_id,
        "status": result.status,
        "headline": result.headline,
        "summary": result.summary,
        "extraction_method": result.extraction_method,
        "rule_engine_version": result.rule_engine_version,
        "checks": [
            {
                "field": check.field,
                "submitted": check.submitted,
                "extracted": check.extracted,
                "source": check.source,
                "result": check.result,
            }
            for check in result.checks
        ],
        "findings": [
            {
                "code": finding.code,
                "severity": finding.severity,
                "stage": finding.stage,
                "message": finding.message,
                "vendor_message": finding.vendor_message,
            }
            for finding in result.findings
        ],
        "required_actions": [
            finding.vendor_message
            for finding in result.findings
            if finding.severity in {"pending", "reject"} and finding.vendor_message
        ],
        "notification": {
            "subject": result.notification_subject,
            "body": result.notification_body,
        },
        "documents": [
            {
                "slot": doc.slot,
                "filename": doc.filename,
                "parse_status": doc.parse_status,
                "document_type": doc.document_type,
                "method": doc.method,
                "confidence": round(doc.confidence, 2),
            }
            for doc in result.documents
        ],
        "stages": [
            {
                "key": stage.key,
                "title": stage.title,
                "status": stage.status,
                "summary": stage.summary,
            }
            for stage in result.stages
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.cli",
        description="Run the VendorFlow decision pipeline without the UI.",
    )
    parser.add_argument("input", nargs="?", help="JSON submission file with optional documents")
    parser.add_argument("--demo", choices=sorted(SCENARIOS_BY_ID), help="Run a synthetic scenario")
    parser.add_argument("--save", action="store_true", help="Store the run in the local SQLite audit log")
    args = parser.parse_args(argv)

    if args.demo:
        scenario = SCENARIOS_BY_ID[args.demo]
        submission = to_submission(scenario)
        documents = build_documents(scenario)
        scenario_id = scenario.id
    elif args.input:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        submission = submission_from_json(payload.get("submission", payload))
        documents = documents_from_json(payload.get("documents", {}))
        scenario_id = None
    else:
        parser.error("Give a JSON file or --demo <scenario>")

    db.init_db()
    result = run_pipeline(submission, documents, db.load_registry(), scenario_id=scenario_id)
    reference = None
    if args.save:
        _submission_id, reference = db.save_run(result)
    else:
        reference = None
    print(json.dumps(summarize(result, reference), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
