"""
generate_data.py
-----------------
Generates two synthetic datasets that simulate a real-world reconciliation scenario:

1. settlement.csv  -> what the PAYMENT GATEWAY says it paid out to the merchant
2. ledger.csv      -> what the MERCHANT's internal books expect to receive

The two datasets are deliberately NOT perfectly aligned. We inject known,
labeled mismatch types so that:
  (a) we can compute an honest match rate against ground truth
  (b) we know exactly which records SHOULD end up as exceptions, and why

This "ground truth" is saved separately (ground_truth.csv) so it is NEVER
used by the matching engine itself -- only by us, afterwards, to grade
the engine's own reported match rate against reality.
"""

import csv
import random
import uuid
from datetime import datetime, timedelta

random.seed(42)  # reproducible for Day 1; we'll generate a second, unseen batch later for real testing

NUM_BASE_RECORDS = 220  # scaled up for a stronger throughput demonstration

OUTPUT_DIR = "data"


def random_date(start, end):
    delta = end - start
    return start + timedelta(days=random.randint(0, delta.days), hours=random.randint(0, 23))


def make_base_transaction(i, base_date):
    txn_id = f"TXN{1000 + i}"
    amount = round(random.uniform(200, 15000), 2)
    fee_pct = random.choice([0.02, 0.023, 0.025])  # gateway fee varies by plan
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
    base_date = datetime(2026, 7, 1)
    settlement_rows = []
    ledger_rows = []
    ground_truth = []  # txn_id -> expected outcome label, for our own grading later

    records = [make_base_transaction(i, base_date) for i in range(NUM_BASE_RECORDS)]

    # Partition records into mismatch categories so we know exactly what we injected
    n = len(records)
    idx = list(range(n))
    random.shuffle(idx)

    clean_match_idx      = set(idx[0:105])   # 105 records: perfect match, should match cleanly
    fee_only_idx         = set(idx[105:135])  # 30 records: ledger expects gross, settlement pays net (fee diff) -> should still match (explainable diff)
    timing_offset_idx    = set(idx[135:160])  # 25 records: settlement date is a few days after ledger date -> should still match
    fuzzy_ref_idx        = set(idx[160:170])  # 10 records: settlement ref has a formatting typo -> should still match via fuzzy logic
    partial_payment_idx  = set(idx[170:186])  # 16 records: settlement paid LESS than expected -> should be EXCEPTION (partial payment)
    duplicate_idx        = set(idx[186:196])  # 10 records: settlement has a duplicate entry -> should be EXCEPTION (duplicate)
    missing_in_settle_idx= set(idx[196:208])  # 12 records: exists in ledger, missing from settlement -> should be EXCEPTION (missing settlement)
    missing_in_ledger_idx= set(idx[208:220])  # 12 records: exists in settlement, missing from ledger -> should be EXCEPTION (missing ledger entry / unrecorded sale)

    for i, r in enumerate(records):
        txn_id = r["txn_id"]

        # ---- LEDGER SIDE ----
        # Merchant's own books always expect the GROSS amount (before gateway fee)
        if i not in missing_in_ledger_idx:
            ledger_rows.append({
                "ledger_ref": txn_id,
                "expected_amount": r["gross_amount"],
                "order_date": r["date"].strftime("%Y-%m-%d"),
            })

        # ---- SETTLEMENT SIDE ----
        if i in clean_match_idx:
            settlement_rows.append({
                "settlement_ref": txn_id,
                "paid_amount": r["gross_amount"],  # exact, no fee deducted in this synthetic slice for a clean 1:1 case
                "settle_date": r["date"].strftime("%Y-%m-%d"),
            })
            ground_truth.append((txn_id, "MATCH", "exact"))

        elif i in fee_only_idx:
            settlement_rows.append({
                "settlement_ref": txn_id,
                "paid_amount": r["net_amount"],  # gateway deducted its fee
                "settle_date": r["date"].strftime("%Y-%m-%d"),
            })
            ground_truth.append((txn_id, "MATCH", "fee_adjusted"))

        elif i in timing_offset_idx:
            offset_date = r["date"] + timedelta(days=random.randint(2, 5))
            settlement_rows.append({
                "settlement_ref": txn_id,
                "paid_amount": r["gross_amount"],
                "settle_date": offset_date.strftime("%Y-%m-%d"),
            })
            ground_truth.append((txn_id, "MATCH", "timing_offset"))

        elif i in fuzzy_ref_idx:
            # inject a realistic formatting typo into the settlement-side ref.
            # Only case/whitespace/hyphen variants are used -- these always
            # normalize back to the original txn_id, so they can never
            # accidentally collide with another real transaction's ID
            # (unlike a digit-shift typo, which risks landing on a sequential
            # neighbor ID that already exists as its own transaction).
            typo_variants = [
                txn_id.lower(),                      # case difference
                txn_id + " ",                         # trailing whitespace
                txn_id.replace("TXN", "TXN-"),        # stray hyphen
            ]
            settlement_ref = random.choice(typo_variants)
            settlement_rows.append({"settlement_ref": settlement_ref, "paid_amount": r["gross_amount"],
                                     "settle_date": r["date"].strftime("%Y-%m-%d")})
            ground_truth.append((txn_id, "MATCH", "fuzzy_ref_match"))

        elif i in partial_payment_idx:
            partial = round(r["gross_amount"] * random.uniform(0.4, 0.8), 2)
            settlement_rows.append({
                "settlement_ref": txn_id,
                "paid_amount": partial,
                "settle_date": r["date"].strftime("%Y-%m-%d"),
            })
            ground_truth.append((txn_id, "EXCEPTION", "partial_payment"))

        elif i in duplicate_idx:
            settlement_rows.append({
                "settlement_ref": txn_id,
                "paid_amount": r["gross_amount"],
                "settle_date": r["date"].strftime("%Y-%m-%d"),
            })
            # duplicate entry with a different internal settlement id but same ref
            settlement_rows.append({
                "settlement_ref": txn_id,
                "paid_amount": r["gross_amount"],
                "settle_date": r["date"].strftime("%Y-%m-%d"),
            })
            ground_truth.append((txn_id, "EXCEPTION", "duplicate_settlement"))

        elif i in missing_in_settle_idx:
            # no settlement row created at all -> ledger expects money that never arrived
            ground_truth.append((txn_id, "EXCEPTION", "missing_settlement"))

        elif i in missing_in_ledger_idx:
            settlement_rows.append({
                "settlement_ref": txn_id,
                "paid_amount": r["gross_amount"],
                "settle_date": r["date"].strftime("%Y-%m-%d"),
            })
            ground_truth.append((txn_id, "EXCEPTION", "missing_ledger_entry"))

    random.shuffle(settlement_rows)
    random.shuffle(ledger_rows)

    with open(f"{OUTPUT_DIR}/settlement.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["settlement_ref", "paid_amount", "settle_date"])
        writer.writeheader()
        writer.writerows(settlement_rows)

    with open(f"{OUTPUT_DIR}/ledger.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["ledger_ref", "expected_amount", "order_date"])
        writer.writeheader()
        writer.writerows(ledger_rows)

    with open(f"{OUTPUT_DIR}/ground_truth.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["txn_id", "expected_outcome", "reason"])
        writer.writerows(ground_truth)

    print(f"Generated {len(settlement_rows)} settlement rows, {len(ledger_rows)} ledger rows, "
          f"{len(ground_truth)} ground-truth labels.")
    print("Breakdown of injected scenarios:")
    print(f"  clean_match:        {len(clean_match_idx)}")
    print(f"  fee_only:           {len(fee_only_idx)}")
    print(f"  timing_offset:      {len(timing_offset_idx)}")
    print(f"  fuzzy_ref_typo:     {len(fuzzy_ref_idx)}")
    print(f"  partial_payment:    {len(partial_payment_idx)}")
    print(f"  duplicate:          {len(duplicate_idx)}")
    print(f"  missing_in_settle:  {len(missing_in_settle_idx)}")
    print(f"  missing_in_ledger:  {len(missing_in_ledger_idx)}")


if __name__ == "__main__":
    generate()