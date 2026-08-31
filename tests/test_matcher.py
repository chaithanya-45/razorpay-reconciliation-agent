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


if __name__ == "__main__":
    unittest.main()
