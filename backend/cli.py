from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.pipeline import run_pipeline
from backend.submission_loader import SubmissionLoadError, load_json_file
from database import db
from test_data.scenarios import SCENARIOS_BY_ID, build_documents, to_submission
from test_data.seed import seed_vendors


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
    parser.add_argument(
        "input",
        nargs="?",
        help="JSON submission file; document paths resolve from here, then from its folder",
    )
    parser.add_argument("--demo", choices=sorted(SCENARIOS_BY_ID), help="Run a synthetic scenario")
    parser.add_argument("--save", action="store_true", help="Store the run in the local SQLite audit log")
    args = parser.parse_args(argv)

    if args.demo:
        scenario = SCENARIOS_BY_ID[args.demo]
        submission = to_submission(scenario)
        documents = build_documents(scenario)
        scenario_id = scenario.id
    elif args.input:
        try:
            loaded = load_json_file(args.input)
        except SubmissionLoadError as exc:
            raise SystemExit(str(exc)) from exc
        submission = loaded.submission
        documents = loaded.documents
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
