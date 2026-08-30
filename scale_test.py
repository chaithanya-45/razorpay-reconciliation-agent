"""
scale_test.py
-------------
Generates a large (10,000+ record) synthetic dataset and benchmarks the
reconciliation engine's throughput on it. This demonstrates the engine
scales well beyond the 50+ record minimum in the brief.

Run from the project root:
    python scale_test.py

Requires SRC/matcher.py to be importable (adjusts sys.path automatically).
"""

import sys
import os
import time
import random
import csv
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "SRC"))

random.seed(7)

N = 10000
base_date = datetime(2026, 1, 1)


def generate_scale_dataset():
    settlement_rows = []
    ledger_rows = []

    for i in range(N):
        txn_id = f"TXNS{100000+i}"
        amount = round(random.uniform(100, 50000), 2)
        fee = round(amount * random.choice([0.02, 0.025]), 2)
        d = base_date + timedelta(days=random.randint(0, 60))

        ledger_rows.append({
            "ledger_ref": txn_id,
            "expected_amount": amount,
            "order_date": d.strftime("%Y-%m-%d"),
        })

        r = random.random()
        if r < 0.75:  # clean or fee-adjusted match
            pay = amount if r < 0.5 else round(amount - fee, 2)
            settlement_rows.append({
                "settlement_ref": txn_id, "paid_amount": pay,
                "settle_date": d.strftime("%Y-%m-%d"),
            })
        elif r < 0.85:  # timing offset
            offset_date = d + timedelta(days=random.randint(1, 6))
            settlement_rows.append({
                "settlement_ref": txn_id, "paid_amount": amount,
                "settle_date": offset_date.strftime("%Y-%m-%d"),
            })
        elif r < 0.95:  # partial payment (exception)
            partial = round(amount * random.uniform(0.4, 0.8), 2)
            settlement_rows.append({
                "settlement_ref": txn_id, "paid_amount": partial,
                "settle_date": d.strftime("%Y-%m-%d"),
            })
        # else (5% of records): no settlement row at all -> missing_settlement exception

    os.makedirs("data", exist_ok=True)
    with open("data/settlement_scale.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["settlement_ref", "paid_amount", "settle_date"])
        w.writeheader()
        w.writerows(settlement_rows)

    with open("data/ledger_scale.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ledger_ref", "expected_amount", "order_date"])
        w.writeheader()
        w.writerows(ledger_rows)

    return len(ledger_rows), len(settlement_rows)


def run_benchmark():
    from matcher import load_data, reconcile

    n_ledger, n_settlement = generate_scale_dataset()
    print(f"Generated {n_ledger} ledger rows, {n_settlement} settlement rows.")

    start = time.time()
    settlement, ledger = load_data("data/settlement_scale.csv", "data/ledger_scale.csv")
    load_time = time.time() - start

    start = time.time()
    results = reconcile(settlement, ledger)
    reconcile_time = time.time() - start

    matched = sum(1 for r in results if r.status == "MATCHED")
    exceptions = sum(1 for r in results if r.status == "EXCEPTION")
    missing_settlement = sum(1 for r in results if r.exception_reason == "missing_settlement")

    print(f"\n=== PERFORMANCE ===")
    print(f"Load time:      {load_time:.3f}s")
    print(f"Reconcile time: {reconcile_time:.3f}s")
    print(f"Total records:  {len(results)}")
    print(f"Throughput:     {len(results)/reconcile_time:.0f} records/second")
    print(f"\nMatched:    {matched} ({matched/len(results)*100:.1f}%)")
    print(f"Exceptions: {exceptions} ({exceptions/len(results)*100:.1f}%)")

    print(f"\n=== SCALING NOTE ===")
    print(f"Unmatched-side records entering the fuzzy-matching pass: ~{missing_settlement}")
    print("Fuzzy matching only compares within this unmatched subset, not the full dataset --")
    print("this is why throughput stays high as total record count grows, as long as the")
    print("proportion of missing/unmatched records stays in a realistic range (e.g. under ~10%).")


if __name__ == "__main__":
    run_benchmark()
