"""
generate_data_test2.py
------------------------
A SECOND, independent synthetic dataset -- different random seed, and a few
NEW edge cases that were not present in the original training/dev dataset.

Purpose: prove the matching engine's rules generalize, rather than being
quietly tuned to fit the first dataset's exact numbers. This is the honest
test the brief asks for ("one cherry-picked match proves nothing").

New edge cases added in this batch that batch 1 did NOT have:
  - refund_after_settlement: settlement amount is HIGHER than ledger amount
    (e.g. a top-up or correction was added) -> should be an exception,
    since our current rules only expect settlement <= ledger (fees deducted).
  - large_timing_offset: settlement is delayed by MORE than 7 days
    -> should correctly fall into 'excessive_settlement_delay'.
  - near_boundary_fee: settlement fee is right at ~5% (the edge of our
    MAX_FEE_PCT threshold) to test the boundary condition.

We deliberately do NOT change matcher.py's rules before running this --
that's the whole point of the test.
"""

import csv
import random
from datetime import datetime, timedelta

random.seed(999)  # DIFFERENT seed from batch 1 (which used seed 42)

NUM_BASE_RECORDS = 55
OUTPUT_DIR = "/home/claude/reconai/data"


def random_date(start, end):
    delta = end - start
    return start + timedelta(days=random.randint(0, delta.days), hours=random.randint(0, 23))


def make_base_transaction(i, base_date):
    txn_id = f"TXN2{1000 + i}"
    amount = round(random.uniform(150, 20000), 2)
    fee_pct = random.choice([0.018, 0.02, 0.022, 0.025])
    fee = round(amount * fee_pct, 2)
    net_amount = round(amount - fee, 2)
    txn_date = random_date(base_date, base_date + timedelta(days=30))
    return {
        "txn_id": txn_id,
        "gross_amount": amount,
        "fee": fee,
        "net_amount": net_amount,
        "date": txn_date,
    }


def generate():
    base_date = datetime(2026, 8, 1)
    settlement_rows = []
    ledger_rows = []
    ground_truth = []

    records = [make_base_transaction(i, base_date) for i in range(NUM_BASE_RECORDS)]
    n = len(records)
    idx = list(range(n))
    random.shuffle(idx)

    clean_match_idx       = set(idx[0:22])
    fee_only_idx          = set(idx[22:30])
    near_boundary_fee_idx = set(idx[30:33])   # NEW: fee right at the 5% edge
    timing_offset_idx     = set(idx[33:38])
    large_timing_idx      = set(idx[38:41])   # NEW: delay > 7 days
    partial_payment_idx   = set(idx[41:45])
    duplicate_idx         = set(idx[45:47])
    missing_in_settle_idx = set(idx[47:50])
    missing_in_ledger_idx = set(idx[50:52])
    refund_after_idx      = set(idx[52:55])   # NEW: settlement > ledger amount

    for i, r in enumerate(records):
        txn_id = r["txn_id"]

        if i not in missing_in_ledger_idx:
            ledger_rows.append({
                "ledger_ref": txn_id,
                "expected_amount": r["gross_amount"],
                "order_date": r["date"].strftime("%Y-%m-%d"),
            })

        if i in clean_match_idx:
            settlement_rows.append({"settlement_ref": txn_id, "paid_amount": r["gross_amount"],
                                     "settle_date": r["date"].strftime("%Y-%m-%d")})
            ground_truth.append((txn_id, "MATCH", "exact"))

        elif i in fee_only_idx:
            settlement_rows.append({"settlement_ref": txn_id, "paid_amount": r["net_amount"],
                                     "settle_date": r["date"].strftime("%Y-%m-%d")})
            ground_truth.append((txn_id, "MATCH", "fee_adjusted"))

        elif i in near_boundary_fee_idx:
            # fee at exactly 4.9% -- should still squeak into fee_adjusted (< 5% threshold)
            near_boundary_amt = round(r["gross_amount"] * (1 - 0.049), 2)
            settlement_rows.append({"settlement_ref": txn_id, "paid_amount": near_boundary_amt,
                                     "settle_date": r["date"].strftime("%Y-%m-%d")})
            ground_truth.append((txn_id, "MATCH", "fee_adjusted_boundary"))

        elif i in timing_offset_idx:
            offset_date = r["date"] + timedelta(days=random.randint(2, 6))
            settlement_rows.append({"settlement_ref": txn_id, "paid_amount": r["gross_amount"],
                                     "settle_date": offset_date.strftime("%Y-%m-%d")})
            ground_truth.append((txn_id, "MATCH", "timing_offset"))

        elif i in large_timing_idx:
            offset_date = r["date"] + timedelta(days=random.randint(10, 20))
            settlement_rows.append({"settlement_ref": txn_id, "paid_amount": r["gross_amount"],
                                     "settle_date": offset_date.strftime("%Y-%m-%d")})
            ground_truth.append((txn_id, "EXCEPTION", "excessive_settlement_delay"))

        elif i in partial_payment_idx:
            partial = round(r["gross_amount"] * random.uniform(0.3, 0.75), 2)
            settlement_rows.append({"settlement_ref": txn_id, "paid_amount": partial,
                                     "settle_date": r["date"].strftime("%Y-%m-%d")})
            ground_truth.append((txn_id, "EXCEPTION", "partial_payment"))

        elif i in duplicate_idx:
            settlement_rows.append({"settlement_ref": txn_id, "paid_amount": r["gross_amount"],
                                     "settle_date": r["date"].strftime("%Y-%m-%d")})
            settlement_rows.append({"settlement_ref": txn_id, "paid_amount": r["gross_amount"],
                                     "settle_date": r["date"].strftime("%Y-%m-%d")})
            ground_truth.append((txn_id, "EXCEPTION", "duplicate_settlement"))

        elif i in missing_in_settle_idx:
            ground_truth.append((txn_id, "EXCEPTION", "missing_settlement"))

        elif i in missing_in_ledger_idx:
            settlement_rows.append({"settlement_ref": txn_id, "paid_amount": r["gross_amount"],
                                     "settle_date": r["date"].strftime("%Y-%m-%d")})
            ground_truth.append((txn_id, "EXCEPTION", "missing_ledger_entry"))

        elif i in refund_after_idx:
            # settlement HIGHER than ledger -- a new pattern batch 1 never had
            higher_amt = round(r["gross_amount"] * random.uniform(1.05, 1.2), 2)
            settlement_rows.append({"settlement_ref": txn_id, "paid_amount": higher_amt,
                                     "settle_date": r["date"].strftime("%Y-%m-%d")})
            ground_truth.append((txn_id, "EXCEPTION", "settlement_exceeds_ledger"))

    random.shuffle(settlement_rows)
    random.shuffle(ledger_rows)

    with open(f"{OUTPUT_DIR}/settlement_test2.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["settlement_ref", "paid_amount", "settle_date"])
        writer.writeheader()
        writer.writerows(settlement_rows)

    with open(f"{OUTPUT_DIR}/ledger_test2.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["ledger_ref", "expected_amount", "order_date"])
        writer.writeheader()
        writer.writerows(ledger_rows)

    with open(f"{OUTPUT_DIR}/ground_truth_test2.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["txn_id", "expected_outcome", "reason"])
        writer.writerows(ground_truth)

    print(f"Generated batch 2: {len(settlement_rows)} settlement rows, {len(ledger_rows)} ledger rows, "
          f"{len(ground_truth)} ground-truth labels.")
    print("Includes NEW edge cases not present in batch 1:")
    print(f"  near_boundary_fee (4.9% fee):        {len(near_boundary_fee_idx)}")
    print(f"  large_timing_offset (>7 days delay):  {len(large_timing_idx)}")
    print(f"  settlement_exceeds_ledger (refund):   {len(refund_after_idx)}")


if __name__ == "__main__":
    generate()
