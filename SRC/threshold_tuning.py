"""
threshold_tuning.py
-------------------
Confidence threshold tuning against ground-truth data.

Simulates different confidence thresholds to find the optimal balance between
coverage (% of transactions auto-matched) and accuracy (% of matches correct).

Usage:
    python threshold_tuning.py --ground-truth data/ground_truth_test2.csv \
      --settlement data/settlement_test2.csv --ledger data/ledger_test2.csv
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))

from matcher import load_data, reconcile


def load_ground_truth(path):
    """Load ground truth labels (expected outcome for each transaction)."""
    truth = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            txn_id = row.get("txn_id") or row.get("txn_ref")
            expected = row.get("expected_outcome") or row.get("expected_status", "EXCEPTION")
            truth[txn_id] = expected.upper()
    return truth


def simulate_thresholds(results, ground_truth):
    """Simulate reconciliation accuracy at different confidence thresholds.
    
    Returns a list of (threshold, stats) tuples showing coverage and accuracy.
    """
    simulations = []
    
    for threshold in range(0, 101, 5):  # 0, 5, 10, ..., 100
        matched_by_engine = [r for r in results if r.status == "MATCHED"]
        exceptions_by_engine = [r for r in results if r.status == "EXCEPTION"]
        
        # At this threshold, keep matches with confidence >= threshold
        # Everything below threshold goes to review (treated as unresolved)
        passed_confidence = [r for r in matched_by_engine if r.confidence and r.confidence >= threshold]
        flagged_for_review = [r for r in matched_by_engine if r.confidence and r.confidence < threshold]
        
        # Evaluate accuracy
        correct = 0
        total = 0
        for r in passed_confidence:
            truth_label = ground_truth.get(r.txn_ref, "EXCEPTION")
            if truth_label == "MATCHED":
                correct += 1
            total += 1
        
        for r in exceptions_by_engine:
            truth_label = ground_truth.get(r.txn_ref, "EXCEPTION")
            if truth_label == "EXCEPTION":
                correct += 1
            total += 1
        
        accuracy = (correct / total * 100) if total > 0 else 0
        coverage = (len(passed_confidence) + len(exceptions_by_engine)) / len(results) * 100 if results else 0
        review_queue_size = len(flagged_for_review)
        
        simulations.append({
            "threshold": threshold,
            "coverage_pct": round(coverage, 1),
            "accuracy_pct": round(accuracy, 1),
            "auto_matched": len(passed_confidence),
            "review_queue_size": review_queue_size,
            "exceptions": len(exceptions_by_engine),
            "total_records": len(results),
        })
    
    return simulations


def recommend_threshold(simulations):
    """Recommend an optimal confidence threshold.
    
    Balances accuracy and coverage: prefer high accuracy with reasonable coverage.
    """
    # Prefer accuracy >= 95% and coverage >= 60%
    good_thresholds = [s for s in simulations if s["accuracy_pct"] >= 95 and s["coverage_pct"] >= 60]
    
    if good_thresholds:
        # Among good thresholds, pick the one with highest coverage (broadest application)
        best = max(good_thresholds, key=lambda s: s["coverage_pct"])
        return best
    
    # Fallback: highest accuracy that covers at least 50%
    high_accuracy = [s for s in simulations if s["coverage_pct"] >= 50]
    if high_accuracy:
        best = max(high_accuracy, key=lambda s: s["accuracy_pct"])
        return best
    
    # Final fallback: default to 85% (balance)
    return [s for s in simulations if s["threshold"] == 85][0]


def export_tuning_report(simulations, recommendation, output_path):
    """Export threshold tuning analysis to JSON."""
    report = {
        "simulations": simulations,
        "recommendation": {
            "threshold": recommendation["threshold"],
            "coverage_pct": recommendation["coverage_pct"],
            "accuracy_pct": recommendation["accuracy_pct"],
            "reasoning": f"At {recommendation['threshold']}% confidence, "
                        f"{recommendation['coverage_pct']:.1f}% of transactions are auto-matched "
                        f"with {recommendation['accuracy_pct']:.1f}% accuracy. "
                        f"{recommendation['review_queue_size']} transactions require human review."
        }
    }
    
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Simulate confidence thresholds and recommend tuning."
    )
    parser.add_argument("--ground-truth", required=True, help="Path to ground truth CSV")
    parser.add_argument("--settlement", required=True, help="Path to settlement CSV")
    parser.add_argument("--ledger", required=True, help="Path to ledger CSV")
    parser.add_argument("--output-dir", default="output", help="Output directory for report")
    
    args = parser.parse_args()
    
    print("Loading data...")
    settlement, ledger = load_data(args.settlement, args.ledger)
    ground_truth = load_ground_truth(args.ground_truth)
    
    print(f"Reconciling {len(ledger)} ledger records against {len(settlement)} settlement records...")
    results = reconcile(settlement, ledger)
    
    print(f"Simulating confidence thresholds...")
    simulations = simulate_thresholds(results, ground_truth)
    
    print("\nThreshold Analysis:")
    print("-" * 80)
    print(f"{'Threshold':<12} {'Coverage':<12} {'Accuracy':<12} {'Auto':<10} {'Review':<10}")
    print("-" * 80)
    for sim in simulations:
        print(f"{sim['threshold']:<12} {sim['coverage_pct']:<12} {sim['accuracy_pct']:<12} "
              f"{sim['auto_matched']:<10} {sim['review_queue_size']:<10}")
    
    recommendation = recommend_threshold(simulations)
    print("\nRECOMMENDATION:")
    print(f"  Confidence threshold: {recommendation['threshold']}%")
    print(f"  Coverage: {recommendation['coverage_pct']:.1f}% ({recommendation['auto_matched']} auto-matched)")
    print(f"  Accuracy: {recommendation['accuracy_pct']:.1f}%")
    print(f"  Review queue: {recommendation['review_queue_size']} records need human review")
    
    output_path = os.path.join(args.output_dir, "threshold_tuning.json")
    export_tuning_report(simulations, recommendation, output_path)
    print(f"\nFull report written to: {output_path}")
