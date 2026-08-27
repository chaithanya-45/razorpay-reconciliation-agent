"""Evaluate reconciliation outcomes against an independent ground-truth CSV."""

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from matcher import load_data, reconcile


def load_ground_truth(path):
    with open(path, newline="", encoding="utf-8") as file:
        return {
            row["txn_id"]: (row["expected_outcome"], row["reason"])
            for row in csv.DictReader(file)
        }


def evaluate(settlement_path, ledger_path, ground_truth_path):
    settlement, ledger = load_data(settlement_path, ledger_path)
    results = reconcile(settlement, ledger)
    truth = load_ground_truth(ground_truth_path)
    predictions = {
        result.txn_ref: "MATCH" if result.status == "MATCHED" else "EXCEPTION"
        for result in results
    }

    missing = sorted(set(truth) - set(predictions))
    unexpected = sorted(set(predictions) - set(truth))
    mismatches = sorted(
        ref for ref in set(truth) & set(predictions)
        if truth[ref][0] != predictions[ref]
    )
    accuracy = (
        sum(truth[ref][0] == predictions[ref] for ref in truth if ref in predictions)
        / len(truth)
        * 100
        if truth else 0
    )

    print(f"Records evaluated : {len(truth)}")
    print(f"Predictions made  : {len(predictions)}")
    print(f"Outcome accuracy  : {accuracy:.1f}%")
    print(f"Missing predictions: {len(missing)}")
    print(f"Unexpected records: {len(unexpected)}")
    print(f"Outcome mismatches: {len(mismatches)}")
    if mismatches:
        print("Mismatched references:")
        for ref in mismatches:
            print(f"  {ref}: expected {truth[ref][0]}, got {predictions[ref]}")

    if missing or unexpected or mismatches:
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate reconciliation outcomes against ground truth.")
    parser.add_argument("--settlement", required=True, help="Path to the settlement CSV")
    parser.add_argument("--ledger", required=True, help="Path to the ledger CSV")
    parser.add_argument("--ground-truth", required=True, help="Path to the ground-truth CSV")
    args = parser.parse_args()
    raise SystemExit(evaluate(args.settlement, args.ledger, args.ground_truth))
