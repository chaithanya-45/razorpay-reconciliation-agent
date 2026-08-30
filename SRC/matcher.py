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

NOTE ON CURRENCY / GST (added):
  Real settlement/ledger pairs aren't always in the same currency, and a
  merchant's ledger sometimes stores a GST-inclusive amount while the
  gateway settles the GST-exclusive amount. Both are normalized to a common
  basis (INR, pre-tax) before the existing matching tiers run, so the core
  matching logic above didn't need to change -- only what "ledger_amt" and
  "settle_amt" mean going into it.
"""

import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
from rapidfuzz import fuzz


MAX_FEE_PCT = 0.05          # gateway fees assumed to be within 0-5% of gross amount
MAX_TIMING_OFFSET_DAYS = 7  # settlement can legitimately lag ledger by up to a week
FUZZY_REF_THRESHOLD = 90    # rapidfuzz similarity score (0-100) to treat two ref ids as "the same, likely a typo"

GST_RATE = 0.18             # standard India GST rate, used to strip GST-inclusive ledger amounts
GST_TOLERANCE = 0.01        # 1% wiggle room around the exact GST math (rounding differences)

# Fixed exchange rates to INR, used only to VALIDATE cross-currency records are
# reasonably close after conversion -- not a live FX feed. In a real system
# this would call a rates API; here it's intentionally simple and inspectable.
FX_TO_INR = {
    "INR": 1.0,
    "USD": 83.0,
    "EUR": 90.0,
    "GBP": 105.0,
}


@dataclass
class MatchResult:
    txn_ref: str
    status: str            # "MATCHED" or "EXCEPTION"
    match_type: Optional[str] = None     # exact / fee_adjusted / timing_offset / fuzzy_ref_match
    exception_reason: Optional[str] = None
    ledger_amount: Optional[float] = None
    settlement_amount: Optional[float] = None
    detail: str = ""
    severity: Optional[str] = None            # HIGH / MEDIUM / LOW -- how urgent this exception is
    recommended_action: Optional[str] = None  # a concrete next step for a finance user


# Business rules for how urgent an exception is and what a finance user
# should actually do about it. Keeping this as a lookup table (rather than
# scattering severity logic through every branch of reconcile()) makes it
# easy to see and adjust the business judgment calls in one place.
EXCEPTION_ACTIONS = {
    "duplicate_settlement": {
        "action": "Verify which settlement entry is genuine before reconciling; "
                   "do not release funds twice. Escalate to the payment gateway if unclear.",
        "base_severity": "HIGH",  # risk of double-paying, always urgent regardless of amount
    },
    "missing_settlement": {
        "action": "Follow up with the payment gateway on why this settlement never arrived; "
                   "check for a failed payout or a data feed gap.",
        "base_severity": "MEDIUM",
    },
    "missing_ledger_entry": {
        "action": "Confirm this sale was recorded correctly in the merchant's books; "
                   "may indicate a booking-system sync issue rather than a payment issue.",
        "base_severity": "MEDIUM",
    },
    "partial_payment": {
        "action": "Confirm with the gateway whether this was an intentional partial "
                   "settlement (e.g. a dispute hold) or an error requiring a follow-up payment.",
        "base_severity": "MEDIUM",
    },
    "settlement_exceeds_ledger": {
        "action": "Investigate the source of the extra funds -- could be a refund reversal, "
                   "a correction credit, or a gateway error. Do not assume it's a bonus.",
        "base_severity": "MEDIUM",
    },
    "excessive_settlement_delay": {
        "action": "Usually resolves on its own; flag for follow-up only if it exceeds the "
                   "gateway's stated maximum settlement window.",
        "base_severity": "LOW",
    },
    "unknown_currency": {
        "action": "Add this currency to the supported conversion table, or confirm it's not "
                   "a data entry error, before this can be reconciled automatically.",
        "base_severity": "MEDIUM",
    },
    "fuzzy_ref_amount_mismatch": {
        "action": "Manually confirm whether the two references really refer to the same "
                   "transaction before treating this as resolved.",
        "base_severity": "MEDIUM",
    },
    "unexplained_mismatch": {
        "action": "Needs manual review -- doesn't fit any recognized pattern.",
        "base_severity": "MEDIUM",
    },
}


def _amount_at_stake(result: "MatchResult") -> float:
    for amt in (result.ledger_amount, result.settlement_amount):
        if amt is not None:
            return abs(amt)
    return 0.0


def enrich_with_severity_and_action(results: list) -> list:
    """Adds a business-facing severity level and recommended next action to
    every exception. Matches are left as-is (severity=None) since they don't
    need a business decision -- only exceptions do.

    Severity combines the exception TYPE's inherent risk (e.g. a duplicate is
    always risky, regardless of amount) with the AMOUNT at stake (a ₹50
    partial payment matters less than a ₹5,00,000 one).
    """
    for r in results:
        if r.status != "EXCEPTION":
            continue
        rule = EXCEPTION_ACTIONS.get(r.exception_reason, {
            "action": "Needs manual review.",
            "base_severity": "MEDIUM",
        })
        amount = _amount_at_stake(r)

        # Escalate severity if a large amount is involved, regardless of type
        if amount >= 100000:
            severity = "HIGH"
        elif amount >= 10000:
            # don't downgrade an already-HIGH-risk type (like duplicates) to MEDIUM
            severity = "HIGH" if rule["base_severity"] == "HIGH" else "MEDIUM"
        else:
            severity = rule["base_severity"]

        r.severity = severity
        r.recommended_action = rule["action"]

    return results


def load_data(settlement_path: str, ledger_path: str):
    settlement = pd.read_csv(settlement_path)
    ledger = pd.read_csv(ledger_path)
    validate_data(settlement, ledger)
    settlement["settle_date"] = pd.to_datetime(settlement["settle_date"], errors="raise")
    ledger["order_date"] = pd.to_datetime(ledger["order_date"], errors="raise")

    # Backward-compatible currency/GST support: older datasets won't have
    # these columns at all. Default to INR / GST-exclusive so existing
    # behavior is completely unchanged when the columns are absent.
    if "currency" not in settlement.columns:
        settlement["currency"] = "INR"
    if "currency" not in ledger.columns:
        ledger["currency"] = "INR"
    if "gst_inclusive" not in ledger.columns:
        ledger["gst_inclusive"] = False

    return settlement, ledger


def validate_data(settlement: pd.DataFrame, ledger: pd.DataFrame) -> None:
    required_columns = {
        "settlement": {"settlement_ref", "paid_amount", "settle_date"},
        "ledger": {"ledger_ref", "expected_amount", "order_date"},
    }
    for name, frame in (("settlement", settlement), ("ledger", ledger)):
        missing = required_columns[name] - set(frame.columns)
        if missing:
            raise ValueError(f"{name} file is missing required columns: {sorted(missing)}")
        if frame.empty:
            raise ValueError(f"{name} file contains no records")

        ref_column = "settlement_ref" if name == "settlement" else "ledger_ref"
        amount_column = "paid_amount" if name == "settlement" else "expected_amount"
        if frame[ref_column].isna().any() or frame[ref_column].astype(str).str.strip().eq("").any():
            raise ValueError(f"{name} file contains a blank reference in {ref_column}")
        if pd.to_numeric(frame[amount_column], errors="coerce").isna().any():
            raise ValueError(f"{name} file contains a non-numeric amount in {amount_column}")
        date_column = "settle_date" if name == "settlement" else "order_date"
        if pd.to_datetime(frame[date_column], errors="coerce").isna().any():
            raise ValueError(f"{name} file contains an invalid date in {date_column}")


def normalize_to_inr(amount: float, currency: str) -> Optional[float]:
    """Convert an amount to INR using a fixed reference rate table.
    Returns None if the currency isn't recognized (treated as its own exception)."""
    rate = FX_TO_INR.get(str(currency).upper())
    if rate is None:
        return None
    return round(amount * rate, 2)


def strip_gst(ledger_amount: float, gst_inclusive: bool) -> float:
    """If the ledger records a GST-inclusive amount, back out the GST portion
    so it's comparable to the (GST-exclusive) settlement amount from the gateway."""
    if gst_inclusive:
        return round(ledger_amount / (1 + GST_RATE), 2)
    return ledger_amount


def reconcile(settlement: pd.DataFrame, ledger: pd.DataFrame) -> list[MatchResult]:
    results: list[MatchResult] = []

    # Step 1: detect duplicate settlement refs up front -- these are always exceptions
    settlement_counts = settlement["settlement_ref"].value_counts()
    duplicate_refs = set(settlement_counts[settlement_counts > 1].index)

    ledger_by_ref = {row["ledger_ref"]: row for _, row in ledger.iterrows()}
    settlement_by_ref = {}
    for _, row in settlement.iterrows():
        settlement_by_ref.setdefault(row["settlement_ref"], []).append(row)

    # --- Fuzzy ref-id resolution pass ---
    # Real-world settlement files and merchant ledgers sometimes use slightly
    # different formatting for the same reference (extra whitespace, case
    # differences, a stray character from manual re-entry). Before treating
    # a ref as "missing" on one side, check for a close fuzzy match on the
    # other side. If found, we treat them as the SAME transaction but flag
    # it distinctly so the fuzzy link itself is visible in the audit trail.
    fuzzy_ref_map = {}  # ledger_ref -> matched settlement_ref (only when NOT an exact match)
    unmatched_ledger_refs = [r for r in ledger_by_ref if r not in settlement_by_ref]
    unmatched_settlement_refs = [r for r in settlement_by_ref if r not in ledger_by_ref]

    for l_ref in unmatched_ledger_refs:
        best_score = 0
        best_match = None
        for s_ref in unmatched_settlement_refs:
            score = fuzz.ratio(str(l_ref).strip().upper(), str(s_ref).strip().upper())
            if score > best_score:
                best_score = score
                best_match = s_ref
        if best_match and best_score >= FUZZY_REF_THRESHOLD:
            fuzzy_ref_map[l_ref] = (best_match, best_score)

    # Settlement refs that got consumed by a fuzzy match should not ALSO be
    # evaluated on their own in the main loop below (that would double-count
    # the same underlying transaction as two separate records).
    consumed_settlement_refs = {matched_ref for matched_ref, _ in fuzzy_ref_map.values()}

    all_refs = (set(ledger_by_ref.keys()) | set(settlement_by_ref.keys())) - consumed_settlement_refs

    for ref in sorted(all_refs):
        in_ledger = ref in ledger_by_ref
        in_settlement = ref in settlement_by_ref

        # --- Case: fuzzy-linked ref (close but not exact match on the other side) ---
        if in_ledger and not in_settlement and ref in fuzzy_ref_map:
            matched_settlement_ref, score = fuzzy_ref_map[ref]
            settle_row = settlement_by_ref[matched_settlement_ref][0]
            ledger_amt = float(ledger_by_ref[ref]["expected_amount"])
            settle_amt = float(settle_row["paid_amount"])
            amt_diff = round(ledger_amt - settle_amt, 2)
            if abs(amt_diff) < 0.01:
                results.append(MatchResult(
                    txn_ref=ref, status="MATCHED", match_type="fuzzy_ref_match",
                    ledger_amount=ledger_amt, settlement_amount=settle_amt,
                    detail=f"No exact settlement ref found, but '{matched_settlement_ref}' is a "
                           f"{score:.0f}% textual match to ledger ref '{ref}' and amounts agree exactly. "
                           f"Likely a formatting difference (case, whitespace, or a manual re-entry typo)."
                ))
            else:
                results.append(MatchResult(
                    txn_ref=ref, status="EXCEPTION", exception_reason="fuzzy_ref_amount_mismatch",
                    ledger_amount=ledger_amt, settlement_amount=settle_amt,
                    detail=f"Settlement ref '{matched_settlement_ref}' is a {score:.0f}% textual match "
                           f"to ledger ref '{ref}', but amounts differ by ₹{amt_diff:.2f}. "
                           f"Needs manual confirmation this is really the same transaction."
                ))
            continue

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

        ledger_currency = str(ledger_row.get("currency", "INR"))
        settle_currency = str(settle_row.get("currency", "INR"))
        gst_inclusive = bool(ledger_row.get("gst_inclusive", False))

        raw_ledger_amt = float(ledger_row["expected_amount"])
        raw_settle_amt = float(settle_row["paid_amount"])

        # Step A: strip GST from the ledger side if it's stored GST-inclusive,
        # so both sides are compared on the same (pre-tax) basis.
        ledger_amt_pretax = strip_gst(raw_ledger_amt, gst_inclusive)

        # Step B: normalize both sides to INR for comparison if currencies differ.
        if ledger_currency.upper() != settle_currency.upper():
            ledger_amt_inr = normalize_to_inr(ledger_amt_pretax, ledger_currency)
            settle_amt_inr = normalize_to_inr(raw_settle_amt, settle_currency)
            if ledger_amt_inr is None or settle_amt_inr is None:
                unknown_ccy = ledger_currency if ledger_amt_inr is None else settle_currency
                results.append(MatchResult(
                    txn_ref=ref, status="EXCEPTION", exception_reason="unknown_currency",
                    ledger_amount=raw_ledger_amt, settlement_amount=raw_settle_amt,
                    detail=f"Currency '{unknown_ccy}' is not in the supported conversion table "
                           f"({', '.join(FX_TO_INR.keys())}). Cannot safely compare amounts."
                ))
                continue
            ledger_amt, settle_amt = ledger_amt_inr, settle_amt_inr
            currency_note = (f" (converted from {ledger_currency}->INR and {settle_currency}->INR "
                              f"using reference rates for comparison)")
        else:
            ledger_amt, settle_amt = ledger_amt_pretax, raw_settle_amt
            currency_note = "" if ledger_currency.upper() == "INR" else f" (both sides in {ledger_currency})"

        gst_note = " (GST stripped from ledger amount before comparison)" if gst_inclusive else ""

        ledger_date = ledger_row["order_date"]
        settle_date = settle_row["settle_date"]

        amt_diff = round(ledger_amt - settle_amt, 2)
        amt_diff_pct = amt_diff / ledger_amt if ledger_amt else 0
        date_diff_days = (settle_date - ledger_date).days

        # Tier 1: EXACT
        if abs(amt_diff) < 0.01 and date_diff_days == 0:
            results.append(MatchResult(
                txn_ref=ref, status="MATCHED", match_type="exact",
                ledger_amount=raw_ledger_amt, settlement_amount=raw_settle_amt,
                detail=f"Amount and date match exactly.{currency_note}{gst_note}"
            ))
            continue

        # Tier 2: FEE_ADJUSTED (settlement is slightly less, within plausible fee range)
        if 0 <= amt_diff_pct <= MAX_FEE_PCT and date_diff_days == 0:
            results.append(MatchResult(
                txn_ref=ref, status="MATCHED", match_type="fee_adjusted",
                ledger_amount=raw_ledger_amt, settlement_amount=raw_settle_amt,
                detail=f"Settlement is {amt_diff_pct*100:.2f}% below ledger amount "
                       f"(₹{amt_diff:.2f}), consistent with a gateway fee deduction.{currency_note}{gst_note}"
            ))
            continue

        # Tier 3: TIMING_OFFSET (exact amount, settlement date lags)
        if abs(amt_diff) < 0.01 and 0 < date_diff_days <= MAX_TIMING_OFFSET_DAYS:
            results.append(MatchResult(
                txn_ref=ref, status="MATCHED", match_type="timing_offset",
                ledger_amount=raw_ledger_amt, settlement_amount=raw_settle_amt,
                detail=f"Amount matches exactly; settlement lagged ledger by {date_diff_days} day(s), "
                       f"within the normal settlement window.{currency_note}{gst_note}"
            ))
            continue

        # Fallback: doesn't fit any known clean pattern -> exception, but with the numbers shown
        if amt_diff < 0:
            reason = "settlement_exceeds_ledger"
            detail = (f"Settlement amount (₹{settle_amt:.2f}) is ₹{-amt_diff:.2f} HIGHER than the "
                      f"ledger amount (₹{ledger_amt:.2f}) -- gateway paid more than the merchant's "
                      f"books expected. Possible refund reversal, correction credit, or duplicate top-up."
                      f"{currency_note}{gst_note}")
        elif amt_diff_pct > MAX_FEE_PCT:
            reason = "partial_payment"
            detail = (f"Settlement amount (₹{settle_amt:.2f}) is ₹{amt_diff:.2f} "
                      f"({amt_diff_pct*100:.1f}%) below the ledger amount (₹{ledger_amt:.2f}) -- "
                      f"too large to be a normal gateway fee. Likely a partial payment or refund."
                      f"{currency_note}{gst_note}")
        elif date_diff_days > MAX_TIMING_OFFSET_DAYS:
            reason = "excessive_settlement_delay"
            detail = (f"Settlement date is {date_diff_days} days after the ledger order date, "
                      f"beyond the normal {MAX_TIMING_OFFSET_DAYS}-day settlement window.")
        else:
            reason = "unexplained_mismatch"
            detail = (f"Amount differs by ₹{amt_diff:.2f} ({amt_diff_pct*100:.1f}%) and date differs "
                      f"by {date_diff_days} day(s) -- doesn't fit a known pattern.{currency_note}{gst_note}")

        results.append(MatchResult(
            txn_ref=ref, status="EXCEPTION", exception_reason=reason,
            ledger_amount=raw_ledger_amt, settlement_amount=raw_settle_amt,
            detail=detail
        ))

    return enrich_with_severity_and_action(results)


if __name__ == "__main__":
    settlement, ledger = load_data(
        "data/settlement.csv",
        "data/ledger.csv",
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