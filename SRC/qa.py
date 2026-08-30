"""
qa.py
-----
Settlement Q&A: ask plain-English questions about a reconciliation run and
get a direct answer, without manually digging through the CSV.

HONEST DESIGN NOTE:
  This is a rule-based / pattern-matching query engine, NOT a call to an
  external LLM (Claude, GPT, etc). That's a deliberate choice: it works
  immediately with no API key, no cost, and no external dependency, while
  still directly answering the kinds of questions a finance user would
  actually ask.

  If you want to upgrade this to a true LLM-powered Q&A later, the natural
  place to do it is inside `answer_question()`: instead of the regex/keyword
  dispatch below, you'd send the user's question plus a serialized summary
  of `records` to an LLM API (e.g. api.anthropic.com) and return its answer.
  That requires an API key and has a per-call cost, which is why it isn't
  wired up by default here.

Usage:
    python SRC/qa.py --report output/reconciliation_report.csv
    (then type questions interactively)

    or non-interactively:
    python SRC/qa.py --report output/reconciliation_report.csv --ask "why did TXN1042 fail?"
"""

import argparse
import csv
import re
from collections import Counter, defaultdict


def load_report(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def find_record(records, ref):
    ref = ref.strip().upper()
    for r in records:
        if r["txn_ref"].strip().upper() == ref:
            return r
    return None


def answer_question(question: str, records: list) -> str:
    q = question.strip().lower()

    # --- "why did TXN123 [fail/not match/become an exception]?" ---
    ref_match = re.search(r'\b(txn[a-z0-9\-]*\d+[a-z0-9]*)\b', q, re.IGNORECASE)
    if ref_match and any(w in q for w in ["why", "what happened", "status", "reason"]):
        ref = ref_match.group(1)
        rec = find_record(records, ref)
        if not rec:
            return f"I couldn't find a record with reference '{ref}' in this report."
        if rec["status"] == "MATCHED":
            return (f"{rec['txn_ref']} was MATCHED (type: {rec['match_type']}). "
                    f"{rec['detail']}")
        else:
            return (f"{rec['txn_ref']} is an EXCEPTION (reason: {rec['exception_reason']}). "
                    f"{rec['detail']}")

    # --- "how many exceptions / matches" ---
    if "how many" in q:
        if "exception" in q:
            n = sum(1 for r in records if r["status"] == "EXCEPTION")
            return f"There are {n} exceptions out of {len(records)} total records."
        if "match" in q:
            n = sum(1 for r in records if r["status"] == "MATCHED")
            return f"There are {n} matched records out of {len(records)} total records."
        if "partial" in q:
            n = sum(1 for r in records if r.get("exception_reason") == "partial_payment")
            return f"There are {n} partial payment exceptions."
        if "duplicate" in q:
            n = sum(1 for r in records if r.get("exception_reason") == "duplicate_settlement")
            return f"There are {n} duplicate settlement exceptions."

    # --- "what's the match rate / total records" ---
    if "match rate" in q or "accuracy" in q:
        matched = sum(1 for r in records if r["status"] == "MATCHED")
        rate = matched / len(records) * 100 if records else 0
        return f"The match rate is {rate:.1f}% ({matched} of {len(records)} records matched)."

    if "total" in q and "record" in q:
        return f"There are {len(records)} total records in this report."

    # --- "what are the exception reasons / breakdown" ---
    if "breakdown" in q or ("what" in q and "exception" in q and "reason" in q):
        counts = Counter(r.get("exception_reason") for r in records if r["status"] == "EXCEPTION")
        if not counts:
            return "There are no exceptions in this report."
        lines = [f"  - {reason}: {count}" for reason, count in counts.most_common()]
        return "Exception breakdown:\n" + "\n".join(lines)

    # --- "list all missing settlement / missing ledger" ---
    reason_keywords = {
        "missing_settlement": ["missing settlement", "no settlement"],
        "missing_ledger_entry": ["missing ledger", "no ledger"],
        "duplicate_settlement": ["duplicate"],
        "partial_payment": ["partial payment", "partial"],
        "excessive_settlement_delay": ["delay", "late", "delayed"],
        "settlement_exceeds_ledger": ["overpaid", "exceeds", "overpayment"],
    }
    for reason, keywords in reason_keywords.items():
        if any(kw in q for kw in keywords) and ("list" in q or "show" in q or "which" in q):
            matches = [r["txn_ref"] for r in records if r.get("exception_reason") == reason]
            if not matches:
                return f"No records found with reason '{reason}'."
            return f"{len(matches)} record(s) with reason '{reason}': " + ", ".join(matches[:20]) + \
                   (f" ...and {len(matches)-20} more" if len(matches) > 20 else "")

    # --- total exception amount (business-value question) ---
    if "amount" in q and ("exception" in q or "unresolved" in q):
        total = 0.0
        for r in records:
            if r["status"] == "EXCEPTION":
                try:
                    total += float(r["ledger_amount"] or r["settlement_amount"] or 0)
                except ValueError:
                    pass
        return f"Total amount tied up in unresolved exceptions: ₹{total:,.2f}"

    return ("I couldn't understand that question. Try things like:\n"
            "  - 'why did TXN1042 fail?'\n"
            "  - 'how many exceptions are there?'\n"
            "  - 'what's the match rate?'\n"
            "  - 'show me the exception breakdown'\n"
            "  - 'list all partial payments'\n"
            "  - 'what's the total exception amount?'")


def main():
    parser = argparse.ArgumentParser(description="Ask plain-English questions about a reconciliation report.")
    parser.add_argument("--report", required=True, help="Path to reconciliation_report.csv")
    parser.add_argument("--ask", help="Ask a single question non-interactively and exit")
    args = parser.parse_args()

    records = load_report(args.report)
    print(f"Loaded {len(records)} records from {args.report}\n")

    if args.ask:
        print(answer_question(args.ask, records))
        return

    print("Settlement Q&A -- ask a question (or type 'exit' to quit)")
    while True:
        try:
            q = input("\n> ")
        except (EOFError, KeyboardInterrupt):
            break
        if q.strip().lower() in ("exit", "quit"):
            break
        print(answer_question(q, records))


if __name__ == "__main__":
    main()
