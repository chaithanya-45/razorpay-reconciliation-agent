import csv
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "SRC"))

from matcher import load_data, reconcile, validate_data
from report import generate_report
from review import merge_decisions, record_decision
from analytics import analyze_gateway_patterns, detect_anomalies
from threshold_tuning import load_ground_truth, simulate_thresholds, recommend_threshold
from dashboard_enhanced import generate_enhanced_dashboard


class ReconcileTests(unittest.TestCase):
    def run_case(self, ledger_ref, expected_amount, order_date, settlement_ref,
                 paid_amount, settle_date):
        ledger = pd.DataFrame([{
            "ledger_ref": ledger_ref,
            "expected_amount": expected_amount,
            "order_date": pd.Timestamp(order_date),
        }])
        settlement = pd.DataFrame([{
            "settlement_ref": settlement_ref,
            "paid_amount": paid_amount,
            "settle_date": pd.Timestamp(settle_date),
        }])
        return reconcile(settlement, ledger)[0]

    def test_exact_match(self):
        result = self.run_case("TXN-1", 1000, "2026-08-01", "TXN-1", 1000, "2026-08-01")
        self.assertEqual((result.status, result.match_type), ("MATCHED", "exact"))

    def test_fee_adjusted_match(self):
        result = self.run_case("TXN-2", 1000, "2026-08-01", "TXN-2", 980, "2026-08-01")
        self.assertEqual((result.status, result.match_type), ("MATCHED", "fee_adjusted"))

    def test_timing_offset_match(self):
        result = self.run_case("TXN-3", 1000, "2026-08-01", "TXN-3", 1000, "2026-08-07")
        self.assertEqual((result.status, result.match_type), ("MATCHED", "timing_offset"))

    def test_fuzzy_reference_match(self):
        result = self.run_case("TXN-4", 1000, "2026-08-01", "txn-4", 1000, "2026-08-01")
        self.assertEqual((result.status, result.match_type), ("MATCHED", "fuzzy_ref_match"))

    def test_duplicate_settlement_is_exception(self):
        ledger = pd.DataFrame([{
            "ledger_ref": "TXN-5", "expected_amount": 1000,
            "order_date": pd.Timestamp("2026-08-01"),
        }])
        settlement = pd.DataFrame([
            {"settlement_ref": "TXN-5", "paid_amount": 1000, "settle_date": pd.Timestamp("2026-08-01")},
            {"settlement_ref": "TXN-5", "paid_amount": 1000, "settle_date": pd.Timestamp("2026-08-01")},
        ])
        result = reconcile(settlement, ledger)[0]
        self.assertEqual((result.status, result.exception_reason), ("EXCEPTION", "duplicate_settlement"))

    def test_missing_settlement_is_exception(self):
        ledger = pd.DataFrame([{
            "ledger_ref": "TXN-6", "expected_amount": 1000,
            "order_date": pd.Timestamp("2026-08-01"),
        }])
        result = reconcile(pd.DataFrame(columns=["settlement_ref", "paid_amount", "settle_date"]), ledger)[0]
        self.assertEqual((result.status, result.exception_reason), ("EXCEPTION", "missing_settlement"))

    def test_missing_ledger_entry_is_exception(self):
        settlement = pd.DataFrame([{
            "settlement_ref": "TXN-7", "paid_amount": 1000,
            "settle_date": pd.Timestamp("2026-08-01"),
        }])
        result = reconcile(settlement, pd.DataFrame(columns=["ledger_ref", "expected_amount", "order_date"]))[0]
        self.assertEqual((result.status, result.exception_reason), ("EXCEPTION", "missing_ledger_entry"))

    def test_partial_payment_is_exception(self):
        result = self.run_case("TXN-8", 1000, "2026-08-01", "TXN-8", 700, "2026-08-01")
        self.assertEqual((result.status, result.exception_reason), ("EXCEPTION", "partial_payment"))

    def test_overpayment_is_exception(self):
        result = self.run_case("TXN-9", 1000, "2026-08-01", "TXN-9", 1100, "2026-08-01")
        self.assertEqual((result.status, result.exception_reason), ("EXCEPTION", "settlement_exceeds_ledger"))

    def test_excessive_delay_is_exception(self):
        result = self.run_case("TXN-10", 1000, "2026-08-01", "TXN-10", 1000, "2026-08-09")
        self.assertEqual((result.status, result.exception_reason), ("EXCEPTION", "excessive_settlement_delay"))

    def test_invalid_amount_is_rejected(self):
        ledger = pd.DataFrame([{
            "ledger_ref": "TXN-11", "expected_amount": "not-a-number",
            "order_date": pd.Timestamp("2026-08-01"),
        }])
        settlement = pd.DataFrame([{
            "settlement_ref": "TXN-11", "paid_amount": 1000,
            "settle_date": pd.Timestamp("2026-08-01"),
        }])
        with self.assertRaisesRegex(ValueError, "non-numeric amount"):
            validate_data(settlement, ledger)

    def test_exact_match_uses_evidence_based_confidence(self):
        result = self.run_case("TXN-12", 1000, "2026-08-01", "TXN-12", 1000, "2026-08-01")
        self.assertEqual(result.status, "MATCHED")
        self.assertEqual(result.confidence, 100)
        self.assertIsNotNone(result.evidence)
        self.assertGreaterEqual(result.evidence["reference_similarity"], 95)

    def test_exception_includes_candidate_evidence(self):
        result = self.run_case("TXN-13", 1000, "2026-08-01", "TXN-13", 700, "2026-08-01")
        self.assertEqual(result.status, "EXCEPTION")
        self.assertEqual(result.exception_reason, "partial_payment")
        self.assertIsNotNone(result.evidence)
        self.assertIn("amount_gap", result.evidence)
        self.assertIn("candidate_score", result.evidence)

    def test_ambiguous_match_becomes_review_candidate(self):
        ledger = pd.DataFrame([{
            "ledger_ref": "ALPHA-100", "expected_amount": 1000,
            "order_date": pd.Timestamp("2026-08-01"),
        }])
        settlement = pd.DataFrame([
            {"settlement_ref": "ALPHA-200", "paid_amount": 1000, "settle_date": pd.Timestamp("2026-08-01")},
            {"settlement_ref": "ALPHA-300", "paid_amount": 1000, "settle_date": pd.Timestamp("2026-08-01")},
        ])
        result = reconcile(settlement, ledger)[0]
        self.assertTrue(result.review_required)
        self.assertEqual(result.decision_bucket, "REVIEW")
        self.assertIsNotNone(result.candidate_matches)
        self.assertGreaterEqual(len(result.candidate_matches), 2)

    def test_generate_report_exports_review_queue(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = pd.DataFrame([{
                "ledger_ref": "REVIEW-1", "expected_amount": 1000,
                "order_date": pd.Timestamp("2026-08-01"),
            }])
            settlement = pd.DataFrame([
                {"settlement_ref": "REVIEW-2", "paid_amount": 1000, "settle_date": pd.Timestamp("2026-08-01")},
                {"settlement_ref": "REVIEW-3", "paid_amount": 1000, "settle_date": pd.Timestamp("2026-08-01")},
            ])
            generate_report(settlement, ledger, tmpdir)
            review_queue_path = os.path.join(tmpdir, "review_queue.csv")
            self.assertTrue(os.path.exists(review_queue_path))

    def test_review_decision_is_persisted_and_merged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            queue_path = os.path.join(tmpdir, "review_queue.csv")
            decision_path = os.path.join(tmpdir, "review_decisions.csv")
            with open(queue_path, "w", newline="", encoding="utf-8") as handle:
                handle.write("txn_ref,status\nTXN-REVIEW,EXCEPTION\n")

            record_decision(decision_path, "TXN-REVIEW", "approved", "finance-1", "Verified in gateway portal")
            merged = merge_decisions(queue_path, decision_path)

            self.assertEqual(merged[0]["review_decision"], "APPROVED")
            self.assertEqual(merged[0]["reviewer"], "finance-1")
            self.assertEqual(merged[0]["review_notes"], "Verified in gateway portal")

    def test_review_override_requires_target_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(ValueError, "override_status"):
                record_decision(
                    os.path.join(tmpdir, "review_decisions.csv"),
                    "TXN-OVERRIDE", "OVERRIDDEN", "finance-1",
                )

    def test_review_decision_updates_existing_transaction(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            decision_path = os.path.join(tmpdir, "review_decisions.csv")
            record_decision(decision_path, "TXN-UPDATE", "rejected", "finance-1")
            record_decision(decision_path, "TXN-UPDATE", "approved", "finance-2")
            with open(decision_path, newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["decision"], "APPROVED")

    def test_gateway_patterns_analyzes_match_rate(self):
        ledger = pd.DataFrame([
            {"ledger_ref": "TXN-1", "expected_amount": 1000, "order_date": pd.Timestamp("2026-08-01")},
            {"ledger_ref": "TXN-2", "expected_amount": 2000, "order_date": pd.Timestamp("2026-08-01")},
        ])
        settlement = pd.DataFrame([
            {"settlement_ref": "TXN-1", "paid_amount": 1000, "settle_date": pd.Timestamp("2026-08-01")},
        ])
        results = reconcile(settlement, ledger)
        patterns = analyze_gateway_patterns(results)
        self.assertEqual(patterns.total_transactions, 2)
        self.assertEqual(patterns.matched_transactions, 1)
        self.assertEqual(patterns.exception_transactions, 1)
        self.assertAlmostEqual(patterns.match_rate_pct, 50.0, places=1)

    def test_gateway_patterns_calculates_fees(self):
        ledger = pd.DataFrame([
            {"ledger_ref": "TXN-F1", "expected_amount": 1000, "order_date": pd.Timestamp("2026-08-01")},
            {"ledger_ref": "TXN-F2", "expected_amount": 1000, "order_date": pd.Timestamp("2026-08-01")},
        ])
        settlement = pd.DataFrame([
            {"settlement_ref": "TXN-F1", "paid_amount": 980, "settle_date": pd.Timestamp("2026-08-01")},
            {"settlement_ref": "TXN-F2", "paid_amount": 950, "settle_date": pd.Timestamp("2026-08-01")},
        ])
        results = reconcile(settlement, ledger)
        patterns = analyze_gateway_patterns(results)
        self.assertIsNotNone(patterns.avg_fee_pct)
        self.assertIsNotNone(patterns.median_fee_pct)
        self.assertGreater(patterns.avg_fee_pct, 0)

    def test_anomaly_detection_runs_without_error(self):
        ledger = pd.DataFrame([
            {"ledger_ref": "A", "expected_amount": 1000, "order_date": pd.Timestamp("2026-08-01")},
            {"ledger_ref": "B", "expected_amount": 2000, "order_date": pd.Timestamp("2026-08-02")},
        ])
        settlement = pd.DataFrame([
            {"settlement_ref": "A", "paid_amount": 950, "settle_date": pd.Timestamp("2026-08-01")},
            {"settlement_ref": "B", "paid_amount": 2000, "settle_date": pd.Timestamp("2026-08-02")},
        ])
        results = reconcile(settlement, ledger)
        patterns = analyze_gateway_patterns(results)
        anomalies = detect_anomalies(results, patterns)
        # Just verify the pipeline runs without errors
        self.assertIsNotNone(patterns)
        self.assertIsInstance(anomalies, list)

    def test_threshold_tuning_simulates_accuracy_at_different_thresholds(self):
        ledger = pd.DataFrame([
            {"ledger_ref": "H", "expected_amount": 1000, "order_date": pd.Timestamp("2026-08-01")},
            {"ledger_ref": "L", "expected_amount": 2000, "order_date": pd.Timestamp("2026-08-02")},
        ])
        settlement = pd.DataFrame([
            {"settlement_ref": "H", "paid_amount": 1000, "settle_date": pd.Timestamp("2026-08-01")},
            {"settlement_ref": "L", "paid_amount": 1900, "settle_date": pd.Timestamp("2026-08-02")},
        ])
        results = reconcile(settlement, ledger)
        ground_truth = {"H": "MATCHED", "L": "EXCEPTION"}
        simulations = simulate_thresholds(results, ground_truth)
        self.assertGreater(len(simulations), 0)
        self.assertTrue(all("threshold" in s and "accuracy_pct" in s for s in simulations))

    def test_threshold_recommendation_returns_valid_threshold(self):
        simulations = [
            {"threshold": t, "accuracy_pct": 90 + t * 0.1, "coverage_pct": 100 - t, "auto_matched": 100, "review_queue_size": 0, "exceptions": 0, "total_records": 100}
            for t in range(0, 101, 5)
        ]
        rec = recommend_threshold(simulations)
        self.assertIsNotNone(rec)
        self.assertIn("threshold", rec)
        self.assertGreaterEqual(rec["accuracy_pct"], 0)

    def test_enhanced_dashboard_generates_without_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = os.path.join(tmpdir, "reconciliation_report.csv")
            review_path = os.path.join(tmpdir, "review_queue.csv")
            output_path = os.path.join(tmpdir, "dashboard_enhanced.html")
            
            with open(report_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["txn_ref", "status", "match_type", "exception_reason", "ledger_amount", "settlement_amount", "confidence", "severity", "detail"])
                writer.writeheader()
                writer.writerow({"txn_ref": "TXN-1", "status": "MATCHED", "match_type": "exact", "exception_reason": "", "ledger_amount": 1000, "settlement_amount": 1000, "confidence": 100, "severity": "", "detail": "Test"})
            
            with open(review_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["txn_ref"])
                writer.writeheader()
            
            generate_enhanced_dashboard(report_path, review_path, output_path)
            self.assertTrue(os.path.exists(output_path))
            with open(output_path, "r", encoding="utf-8") as f:
                content = f.read()
                self.assertIn("Enhanced Dashboard", content)


if __name__ == "__main__":
    unittest.main()
