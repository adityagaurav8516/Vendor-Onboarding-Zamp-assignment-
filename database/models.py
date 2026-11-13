from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Submission:
    legal_name: str = ""
    trade_name: str = ""
    country: str = ""
    tax_id: str = ""
    registration_number: str = ""
    address: str = ""
    contact_name: str = ""
    contact_email: str = ""
    contact_phone: str = ""
    bank_holder_name: str = ""
    bank_name: str = ""
    account_number: str = ""
    bank_code: str = ""
    bank_country: str = ""


@dataclass
class DocumentFile:
    slot: str
    filename: str
    content: bytes


@dataclass
class ExistingVendor:
    id: int
    legal_name: str
    country: str
    tax_id: str
    registration_number: str
    account_number: str
    bank_code: str
    contact_email: str
    status: str


@dataclass
class Finding:
    code: str
    severity: str
    stage: str
    message: str
    vendor_message: str | None = None


@dataclass
class FieldCheck:
    field: str
    submitted: str
    extracted: str
    source: str
    result: str
    score: str = ""


@dataclass
class StageLog:
    key: str
    title: str
    status: str
    summary: str
    details: list[str] = field(default_factory=list)
    elapsed_ms: int = 0


@dataclass
class ParsedDocument:
    slot: str
    filename: str
    parse_status: str
    text_excerpt: str
    document_type: str
    fields: dict
    method: str
    confidence: float
    warnings: list[str] = field(default_factory=list)
    content: bytes = b""


@dataclass
class RunResult:
    status: str
    headline: str
    summary: str
    findings: list[Finding]
    stages: list[StageLog]
    checks: list[FieldCheck]
    notification_subject: str
    notification_body: str
    extraction_method: str
    duplicate_of: int | None
    documents: list[ParsedDocument]
    submission: Submission
    scenario_id: str | None = None
    rule_engine_version: str = ""
