"""The version of the decision rules that produced a run.

Every run stores the value below in SQLite, so a historical audit record keeps
the rules it was judged by even after this file changes. Nothing reads the
stored version back into a decision: it is evidence, not configuration.

Bump policy:

* **major** — a new `reject` rule, a severity change, or a change to the
  decision ordering.
* **minor** — a new `pending` or `warn` rule, or a new format/checksum rule.
* **patch** — message wording, labels, or thresholds that cannot change a
  verdict.

The same value is stamped into the audit event of every saved run, so a record
can always be tied to the code that evaluated it.
"""

from __future__ import annotations

RULE_ENGINE_VERSION = "v1.0.0"
RULESET_NAME = "vendorflow-rules"


def describe() -> str:
    """Human-readable stamp, e.g. ``vendorflow-rules v1.0.0``."""
    return f"{RULESET_NAME} {RULE_ENGINE_VERSION}"
