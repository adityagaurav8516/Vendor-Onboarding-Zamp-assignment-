"""Load a submission JSON — shared by the CLI and the Run page.

The file is the same shape in both places::

    {
      "submission": {"legal_name": "...", "country": "India", ...},
      "documents": {"incorporation": "packs/inc.pdf", "bank_proof": "packs/cheque.pdf"}
    }

Document paths are resolved against the directories given by the caller (the
working directory, then the JSON file's own folder, in the CLI). The pipeline
never sees a path: files are read into bytes here, so an unreadable path becomes
a warning or a clear error instead of a stack trace inside the run.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path

from database.models import DocumentFile, Submission

SLOTS = ("incorporation", "tax_certificate", "bank_proof")


class SubmissionLoadError(ValueError):
    """Raised when a submission payload cannot be used as it stands."""


@dataclass
class LoadedSubmission:
    submission: Submission
    documents: list[DocumentFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_paths: list[str] = field(default_factory=list)

    @property
    def slots(self) -> set[str]:
        return {doc.slot for doc in self.documents}


def coerce_text(value) -> str:
    """Text form of a JSON/CSV value: null is empty, whole numbers lose a trailing ``.0``."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def submission_from_payload(data: dict) -> Submission:
    if not isinstance(data, dict):
        raise SubmissionLoadError("'submission' must be a JSON object.")
    allowed = {item.name for item in fields(Submission)}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise SubmissionLoadError(f"Unknown submission field(s): {', '.join(unknown)}")
    return Submission(**{key: coerce_text(value) for key, value in data.items()})


def load_payload(
    payload,
    base_dirs=None,
    strict_paths: bool = False,
) -> LoadedSubmission:
    """Turn a parsed JSON payload into a submission and its document bytes."""
    if not isinstance(payload, dict):
        raise SubmissionLoadError("The submission file must contain a JSON object.")

    if "submission" in payload:
        unknown = sorted(set(payload) - {"submission", "documents"})
        if unknown:
            raise SubmissionLoadError(
                f"Unknown top-level field(s): {', '.join(unknown)}. "
                "Use only 'submission' and 'documents'."
            )
        submission_data = payload.get("submission")
        documents_data = payload.get("documents", {})
    else:
        # Bare form: submission fields at the top level, documents alongside.
        submission_data = {key: value for key, value in payload.items() if key != "documents"}
        documents_data = payload.get("documents", {})

    if submission_data is None:
        submission_data = {}
    if not isinstance(documents_data, dict):
        raise SubmissionLoadError(
            "'documents' must be a JSON object mapping a document slot to a file path."
        )

    submission = submission_from_payload(submission_data)
    documents, warnings, missing = _load_documents(
        documents_data, [Path(base) for base in (base_dirs or [Path.cwd()])], strict_paths
    )
    return LoadedSubmission(
        submission=submission,
        documents=documents,
        warnings=warnings,
        missing_paths=missing,
    )


def load_json_file(path: str | Path, base_dirs=None, strict_paths: bool = True) -> LoadedSubmission:
    import json

    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SubmissionLoadError(f"Could not read {source}: {exc.__class__.__name__}") from exc
    except json.JSONDecodeError as exc:
        raise SubmissionLoadError(f"{source} is not valid JSON: {exc.msg}") from exc
    directories = [Path(base) for base in (base_dirs or [Path.cwd(), source.resolve().parent])]
    return load_payload(payload, base_dirs=directories, strict_paths=strict_paths)


def _load_documents(mapping: dict, base_dirs: list[Path], strict: bool):
    documents: list[DocumentFile] = []
    warnings: list[str] = []
    missing: list[str] = []
    for slot, raw in mapping.items():
        if slot not in SLOTS:
            raise SubmissionLoadError(
                f"Unknown document slot '{slot}'. Use one of {', '.join(SLOTS)}."
            )
        if not isinstance(raw, str) or not raw.strip():
            raise SubmissionLoadError(
                f"Document path for '{slot}' must be a non-empty string."
            )
        resolved = _resolve(raw, base_dirs)
        if resolved is None:
            missing.append(raw.strip())
            warnings.append(f"{slot}: no file at '{raw.strip()}'")
            if strict:
                raise SubmissionLoadError(f"Document not found: {raw.strip()}")
            continue
        try:
            content = resolved.read_bytes()
        except OSError as exc:
            warnings.append(f"{slot}: could not read '{resolved}' ({exc.__class__.__name__})")
            if strict:
                raise SubmissionLoadError(f"Document could not be read: {resolved}") from exc
            continue
        documents.append(DocumentFile(slot=slot, filename=resolved.name, content=content))
    return documents, warnings, missing


def _resolve(raw: str, base_dirs: list[Path]) -> Path | None:
    candidate = Path(raw.strip()).expanduser()
    if candidate.is_file():
        return candidate
    if candidate.is_absolute():
        return None
    for base in base_dirs:
        nested = Path(base) / raw.strip()
        if nested.is_file():
            return nested
    return None
