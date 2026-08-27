"""
matcher.py
----------
Core reconciliation engine.

Takes two independent record sets (settlement + ledger) that use DIFFERENT
column names and DIFFERENT semantics for "amount" (gross vs net), and tries
to link each ledger entry to its corresponding settlement entry.

Design: rule-based, deterministic, and fully explainable -- every decision
the engine makes is logged with a reason. No black-box scoring.

Matching tiers (checked in this order, first match wins):
  1. EXACT        -> same ref id, same amount, same date
  2. FEE_ADJUSTED  -> same ref id, settlement amount is slightly less than
                      ledger amount, within a plausible gateway fee range (0-5%)
  3. TIMING_OFFSET -> same ref id, same amount, settlement date is up to
                      7 days after ledger date (delayed settlement)
  4. Everything else that shares a ref id but doesn't fit the above -> EXCEPTION
  5. Ref ids with no counterpart at all on the other side -> EXCEPTION

NOTE ON DUPLICATES:
  If a settlement ref id appears MORE THAN ONCE, that is itself flagged as
  an exception (duplicate_settlement) regardless of whether the amounts match,
  because a merchant's ledger only expects to be paid once per transaction.
"""

import pandas as pd
from dataclasses import dataclass, field
from typing import Optional


MAX_FEE_PCT = 0.05        # gateway fees assumed to be within 0-5% of gross amount
MAX_TIMING_OFFSET_DAYS = 7  # settlement can legitimately lag ledger by up to a week


@dataclass
class MatchResult:
    txn_ref: str
    status: str            # "MATCHED" or "EXCEPTION"
    match_type: Optional[str] = None     # exact / fee_adjusted / timing_offset
    exception_reason: Optional[str] = None
    ledger_amount: Optional[float] = None
    settlement_amount: Optional[float] = None
    detail: str = ""


def load_data(settlement_path: str, ledger_path: str):
    settlement = pd.read_csv(settlement_path)
    ledger = pd.read_csv(ledger_path)
    settlement["settle_date"] = pd.to_datetime(settlement["settle_date"])
    ledger["order_date"] = pd.to_datetime(ledger["order_date"])
    return settlement, ledger


def reconcile(settlement: pd.DataFrame, ledger: pd.DataFrame) -> list[MatchResult]:
    results: list[MatchResult] = []

    # Step 1: detect duplicate settlement refs up front -- these are always exceptions
    settlement_counts = settlement["settlement_ref"].value_counts()
    duplicate_refs = set(settlement_counts[settlement_counts > 1].index)

    ledger_by_ref = {row["ledger_ref"]: row for _, row in ledger.iterrows()}
    settlement_by_ref = {}
    for _, row in settlement.iterrows():
        settlement_by_ref.setdefault(row["settlement_ref"], []).append(row)

    all_refs = set(ledger_by_ref.keys()) | set(settlement_by_ref.keys())

    for ref in sorted(all_refs):
        in_ledger = ref in ledger_by_ref
        in_settlement = ref in settlement_by_ref

        # --- Case: duplicate settlement entries for this ref ---
        if ref in duplicate_refs:
            results.append(MatchResult(
                txn_ref=ref,
                status="EXCEPTION",
                exception_reason="duplicate_settlement",
                ledger_amount=float(ledger_by_ref[ref]["expected_amount"]) if in_ledger else None,
                settlement_amount=float(settlement_by_ref[ref][0]["paid_amount"]) if in_settlement else None,
                detail=f"{settlement_counts[ref]} settlement entries found for this reference "
                       f"(expected exactly 1). Needs manual review to determine which is valid."
            ))
            continue

        # --- Case: missing on one side ---
        if in_ledger and not in_settlement:
            results.append(MatchResult(
                txn_ref=ref,
                status="EXCEPTION",
                exception_reason="missing_settlement",
                ledger_amount=float(ledger_by_ref[ref]["expected_amount"]),
                settlement_amount=None,
                detail="Ledger expects this transaction but no matching settlement was received. "
                       "Possible payment failure, delayed payout beyond window, or gateway data gap."
            ))
            continue

        if in_settlement and not in_ledger:
            settle_row = settlement_by_ref[ref][0]
            results.append(MatchResult(
                txn_ref=ref,
                status="EXCEPTION",
                exception_reason="missing_ledger_entry",
                ledger_amount=None,
                settlement_amount=float(settle_row["paid_amount"]),
                detail="Settlement received but no corresponding ledger entry exists. "
                       "Possible unrecorded sale, or a booking-system sync issue."
            ))
            continue

        # --- Case: present on both sides -- try to match ---
        ledger_row = ledger_by_ref[ref]
        settle_row = settlement_by_ref[ref][0]

        ledger_amt = float(ledger_row["expected_amount"])
        settle_amt = float(settle_row["paid_amount"])
        ledger_date = ledger_row["order_date"]
        settle_date = settle_row["settle_date"]

        amt_diff = round(ledger_amt - settle_amt, 2)
        amt_diff_pct = amt_diff / ledger_amt if ledger_amt else 0
        date_diff_days = (settle_date - ledger_date).days

        # Tier 1: EXACT
        if abs(amt_diff) < 0.01 and date_diff_days == 0:
            results.append(MatchResult(
                txn_ref=ref, status="MATCHED", match_type="exact",
                ledger_amount=ledger_amt, settlement_amount=settle_amt,
                detail="Amount and date match exactly."
            ))
            continue

        # Tier 2: FEE_ADJUSTED (settlement is slightly less, within plausible fee range)
        if 0 <= amt_diff_pct <= MAX_FEE_PCT and date_diff_days == 0:
            results.append(MatchResult(
                txn_ref=ref, status="MATCHED", match_type="fee_adjusted",
                ledger_amount=ledger_amt, settlement_amount=settle_amt,
                detail=f"Settlement is {amt_diff_pct*100:.2f}% below ledger amount "
                       f"(₹{amt_diff:.2f}), consistent with a gateway fee deduction."
            ))
            continue

        # Tier 3: TIMING_OFFSET (exact amount, settlement date lags)
        if abs(amt_diff) < 0.01 and 0 < date_diff_days <= MAX_TIMING_OFFSET_DAYS:
            results.append(MatchResult(
                txn_ref=ref, status="MATCHED", match_type="timing_offset",
                ledger_amount=ledger_amt, settlement_amount=settle_amt,
                detail=f"Amount matches exactly; settlement lagged ledger by {date_diff_days} day(s), "
                       f"within the normal settlement window."
            ))
            continue

        # Fallback: doesn't fit any known clean pattern -> exception, but with the numbers shown
        if amt_diff < 0:
            reason = "settlement_exceeds_ledger"
            detail = (f"Settlement amount (₹{settle_amt:.2f}) is ₹{-amt_diff:.2f} HIGHER than the "
                      f"ledger amount (₹{ledger_amt:.2f}) -- gateway paid more than the merchant's "
                      f"books expected. Possible refund reversal, correction credit, or duplicate top-up.")
        elif amt_diff_pct > MAX_FEE_PCT:
            reason = "partial_payment"
            detail = (f"Settlement amount (₹{settle_amt:.2f}) is ₹{amt_diff:.2f} "
                      f"({amt_diff_pct*100:.1f}%) below the ledger amount (₹{ledger_amt:.2f}) -- "
                      f"too large to be a normal gateway fee. Likely a partial payment or refund.")
        elif date_diff_days > MAX_TIMING_OFFSET_DAYS:
            reason = "excessive_settlement_delay"
            detail = (f"Settlement date is {date_diff_days} days after the ledger order date, "
                      f"beyond the normal {MAX_TIMING_OFFSET_DAYS}-day settlement window.")
        else:
            reason = "unexplained_mismatch"
            detail = (f"Amount differs by ₹{amt_diff:.2f} ({amt_diff_pct*100:.1f}%) and date differs "
                      f"by {date_diff_days} day(s) -- doesn't fit a known pattern.")

        results.append(MatchResult(
            txn_ref=ref, status="EXCEPTION", exception_reason=reason,
            ledger_amount=ledger_amt, settlement_amount=settle_amt,
            detail=detail
        ))

    return results


if __name__ == "__main__":
    settlement, ledger = load_data(
        "/home/claude/reconai/data/settlement.csv",
        "/home/claude/reconai/data/ledger.csv",
    )
    results = reconcile(settlement, ledger)

    matched = [r for r in results if r.status == "MATCHED"]
    exceptions = [r for r in results if r.status == "EXCEPTION"]

    print(f"Total records processed: {len(results)}")
    print(f"Matched:    {len(matched)}  ({len(matched)/len(results)*100:.1f}%)")
    print(f"Exceptions: {len(exceptions)}  ({len(exceptions)/len(results)*100:.1f}%)")
    print()
    print("Exception breakdown:")
    from collections import Counter
    reason_counts = Counter(r.exception_reason for r in exceptions)
    for reason, count in reason_counts.most_common():
        print(f"  {reason}: {count}")
