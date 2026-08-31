"""Human review decision workflow for reconciliation review queues.

The review queue remains an immutable snapshot of engine output. Reviewer
actions are stored separately so the audit trail preserves both the original
recommendation and the human decision.
"""

import argparse
import csv
import os
from datetime import datetime, timezone


VALID_DECISIONS = {"APPROVED", "REJECTED", "OVERRIDDEN"}
DECISION_COLUMNS = [
    "txn_ref",
    "decision",
    "reviewer",
    "notes",
    "override_status",
    "override_match_type",
    "override_reason",
    "decided_at",
]


def _read_decisions(decision_path):
    if not os.path.exists(decision_path):
        return []
    with open(decision_path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def record_decision(decision_path, txn_ref, decision, reviewer, notes="",
                    override_status="", override_match_type="", override_reason=""):
    """Create or replace a reviewer decision for one transaction reference."""
    decision = str(decision).strip().upper()
    txn_ref = str(txn_ref).strip()
    reviewer = str(reviewer).strip()
    if decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be one of: {', '.join(sorted(VALID_DECISIONS))}")
    if not txn_ref:
        raise ValueError("txn_ref cannot be blank")
    if not reviewer:
        raise ValueError("reviewer cannot be blank")
    if decision == "OVERRIDDEN" and str(override_status).upper() not in {"MATCHED", "EXCEPTION"}:
        raise ValueError("override_status must be MATCHED or EXCEPTION for an override")
    if decision != "OVERRIDDEN":
        override_status = override_match_type = override_reason = ""

    rows = [row for row in _read_decisions(decision_path) if row.get("txn_ref") != txn_ref]
    rows.append({
        "txn_ref": txn_ref,
        "decision": decision,
        "reviewer": reviewer,
        "notes": str(notes).strip(),
        "override_status": str(override_status).strip().upper(),
        "override_match_type": str(override_match_type).strip(),
        "override_reason": str(override_reason).strip(),
        "decided_at": datetime.now(timezone.utc).isoformat(),
    })
    os.makedirs(os.path.dirname(os.path.abspath(decision_path)), exist_ok=True)
    with open(decision_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DECISION_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return rows[-1]


def merge_decisions(review_queue_path, decision_path):
    """Return the review queue enriched with the latest reviewer decisions."""
    decisions = {row["txn_ref"]: row for row in _read_decisions(decision_path)}
    with open(review_queue_path, newline="", encoding="utf-8") as handle:
        queue = list(csv.DictReader(handle))
    for row in queue:
        decision = decisions.get(row.get("txn_ref"), {})
        row.update({
            "review_decision": decision.get("decision", "PENDING"),
            "reviewer": decision.get("reviewer", ""),
            "review_notes": decision.get("notes", ""),
            "override_status": decision.get("override_status", ""),
            "decided_at": decision.get("decided_at", ""),
        })
    return queue


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Record a human reconciliation review decision.")
    parser.add_argument("--decision-path", default="output/review_decisions.csv")
    parser.add_argument("--txn-ref", required=True)
    parser.add_argument("--decision", required=True, choices=["approve", "reject", "override"])
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument("--override-status", default="")
    parser.add_argument("--override-match-type", default="")
    parser.add_argument("--override-reason", default="")
    args = parser.parse_args()
    result = record_decision(
        args.decision_path,
        args.txn_ref,
        {"approve": "APPROVED", "reject": "REJECTED", "override": "OVERRIDDEN"}[args.decision],
        args.reviewer,
        args.notes,
        args.override_status,
        args.override_match_type,
        args.override_reason,
    )
    print(f"Recorded {result['decision']} decision for {result['txn_ref']} by {result['reviewer']}.")