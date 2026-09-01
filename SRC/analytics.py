"""
analytics.py
-------------
Gateway analytics and anomaly detection for reconciliation results.

Tracks patterns in matched transactions (fee rates, settlement timing, currency
usage) and detects anomalies that may warrant threshold adjustments or manual
investigation.

Usage:
    from matcher import load_data, reconcile
    from analytics import analyze_gateway_patterns, detect_anomalies
    
    results = reconcile(settlement, ledger)
    patterns = analyze_gateway_patterns(results)
    anomalies = detect_anomalies(results, patterns)
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional, List, Dict
import statistics


@dataclass
class GatewayPattern:
    """Summary of gateway transaction patterns."""
    total_transactions: int
    matched_transactions: int
    exception_transactions: int
    match_rate_pct: float
    
    avg_fee_pct: Optional[float]
    median_fee_pct: Optional[float]
    min_fee_pct: Optional[float]
    max_fee_pct: Optional[float]
    stddev_fee_pct: Optional[float]
    
    avg_settlement_delay_days: Optional[float]
    median_settlement_delay_days: Optional[float]
    max_settlement_delay_days: Optional[int]
    
    primary_currencies: List[str]
    currency_distribution: Dict[str, int]
    
    exception_rate_by_reason: Dict[str, float]
    exception_severity_distribution: Dict[str, int]


@dataclass
class Anomaly:
    """A detected unusual transaction pattern."""
    txn_ref: str
    anomaly_type: str  # "unusual_fee", "excessive_delay", "high_confidence_exception", etc.
    severity: str  # "LOW", "MEDIUM", "HIGH"
    score: float  # 0-100
    details: str


def analyze_gateway_patterns(results: list) -> GatewayPattern:
    """Analyze aggregate patterns in reconciliation results."""
    matched = [r for r in results if r.status == "MATCHED"]
    exceptions = [r for r in results if r.status == "EXCEPTION"]
    
    # Fee analysis (fee_adjusted matches only)
    fee_matches = [r for r in matched if r.match_type == "fee_adjusted"]
    fees_pct = []
    if fee_matches:
        for r in fee_matches:
            if r.ledger_amount and r.settlement_amount and r.ledger_amount != 0:
                fee_pct = (r.ledger_amount - r.settlement_amount) / r.ledger_amount * 100
                fees_pct.append(max(0.0, min(5.0, fee_pct)))  # clamp to 0-5%
    
    avg_fee = statistics.mean(fees_pct) if fees_pct else None
    median_fee = statistics.median(fees_pct) if fees_pct else None
    min_fee = min(fees_pct) if fees_pct else None
    max_fee = max(fees_pct) if fees_pct else None
    stddev_fee = statistics.stdev(fees_pct) if len(fees_pct) > 1 else None
    
    # Timing analysis (timing_offset matches only)
    timing_matches = [r for r in matched if r.match_type == "timing_offset"]
    delays_days = []
    if timing_matches:
        for r in timing_matches:
            if hasattr(r, 'evidence') and r.evidence and 'date_similarity' in r.evidence:
                delay = round((r.evidence.get('date_similarity', 0.0) * 7))
                delays_days.append(delay)
    
    avg_delay = statistics.mean(delays_days) if delays_days else None
    median_delay = statistics.median(delays_days) if delays_days else None
    max_delay = max(delays_days) if delays_days else None
    
    # Currency analysis
    currency_count = defaultdict(int)
    for r in results:
        if hasattr(r, 'evidence') and r.evidence and 'currency' in r.evidence:
            currency_count[r.evidence['currency']] += 1
    
    primary_currencies = sorted(currency_count.keys(), key=lambda c: currency_count[c], reverse=True)[:5]
    
    # Exception analysis
    exception_reasons = defaultdict(int)
    exception_severities = defaultdict(int)
    for r in exceptions:
        exception_reasons[r.exception_reason] += 1
        if r.severity:
            exception_severities[r.severity] += 1
    
    exception_rate_by_reason = {
        reason: (count / len(results) * 100) if results else 0
        for reason, count in exception_reasons.items()
    }
    
    return GatewayPattern(
        total_transactions=len(results),
        matched_transactions=len(matched),
        exception_transactions=len(exceptions),
        match_rate_pct=len(matched) / len(results) * 100 if results else 0,
        avg_fee_pct=round(avg_fee, 2) if avg_fee is not None else None,
        median_fee_pct=round(median_fee, 2) if median_fee is not None else None,
        min_fee_pct=round(min_fee, 2) if min_fee is not None else None,
        max_fee_pct=round(max_fee, 2) if max_fee is not None else None,
        stddev_fee_pct=round(stddev_fee, 2) if stddev_fee is not None else None,
        avg_settlement_delay_days=round(avg_delay, 1) if avg_delay is not None else None,
        median_settlement_delay_days=round(median_delay, 1) if median_delay is not None else None,
        max_settlement_delay_days=int(max_delay) if max_delay is not None else None,
        primary_currencies=primary_currencies,
        currency_distribution=dict(currency_count),
        exception_rate_by_reason=exception_rate_by_reason,
        exception_severity_distribution=dict(exception_severities),
    )


def detect_anomalies(results: list, patterns: GatewayPattern) -> List[Anomaly]:
    """Detect unusual transactions that deviate from gateway patterns."""
    anomalies = []
    
    # Baseline thresholds based on patterns
    avg_fee = patterns.avg_fee_pct or 2.0
    max_acceptable_fee = min(5.0, avg_fee * 1.5)  # 50% above average or 5%, whichever is lower
    
    avg_delay = patterns.avg_settlement_delay_days or 3.0
    max_acceptable_delay = max(7, avg_delay * 2.0)
    
    # Check each matched transaction for unusual fees
    for r in results:
        if r.status == "MATCHED" and r.match_type == "fee_adjusted":
            if r.ledger_amount and r.settlement_amount and r.ledger_amount != 0:
                fee_pct = (r.ledger_amount - r.settlement_amount) / r.ledger_amount * 100
                if fee_pct > max_acceptable_fee:
                    anomalies.append(Anomaly(
                        txn_ref=r.txn_ref,
                        anomaly_type="unusual_fee",
                        severity="MEDIUM" if fee_pct > 4.0 else "LOW",
                        score=min(100, (fee_pct / 5.0) * 100),
                        details=f"Fee {fee_pct:.2f}% exceeds typical pattern ({avg_fee:.2f}%)"
                    ))
        
        # Check exceptions with high confidence (may indicate threshold issues)
        if r.status == "EXCEPTION" and r.confidence and r.confidence >= 85:
            anomalies.append(Anomaly(
                txn_ref=r.txn_ref,
                anomaly_type="high_confidence_exception",
                severity="MEDIUM",
                score=r.confidence,
                details=f"Unambiguous exception: {r.exception_reason} ({r.confidence}% confidence)"
            ))
        
        # Check for excessive delays in timing-offset matches
        if r.status == "MATCHED" and r.match_type == "timing_offset":
            if hasattr(r, 'evidence') and r.evidence:
                date_sim = r.evidence.get('date_similarity', 1.0)
                delay_days = round((1.0 - date_sim) * 7)  # inverse of similarity
                if delay_days > max_acceptable_delay:
                    anomalies.append(Anomaly(
                        txn_ref=r.txn_ref,
                        anomaly_type="excessive_delay",
                        severity="LOW" if delay_days < 14 else "MEDIUM",
                        score=min(100, (delay_days / 14.0) * 100),
                        details=f"Settlement delayed {delay_days} days (typical: {avg_delay:.1f} days)"
                    ))
    
    return sorted(anomalies, key=lambda a: (-a.score, a.txn_ref))


def export_analytics_report(patterns: GatewayPattern, anomalies: List[Anomaly], 
                            output_path: str) -> None:
    """Write gateway patterns and anomalies to a JSON report."""
    report = {
        "patterns": {
            "total_transactions": patterns.total_transactions,
            "matched_transactions": patterns.matched_transactions,
            "exception_transactions": patterns.exception_transactions,
            "match_rate_pct": patterns.match_rate_pct,
            "fees": {
                "avg_pct": patterns.avg_fee_pct,
                "median_pct": patterns.median_fee_pct,
                "min_pct": patterns.min_fee_pct,
                "max_pct": patterns.max_fee_pct,
                "stddev_pct": patterns.stddev_fee_pct,
            },
            "settlement_timing": {
                "avg_delay_days": patterns.avg_settlement_delay_days,
                "median_delay_days": patterns.median_settlement_delay_days,
                "max_delay_days": patterns.max_settlement_delay_days,
            },
            "currencies": {
                "primary": patterns.primary_currencies,
                "distribution": patterns.currency_distribution,
            },
            "exceptions": {
                "rate_by_reason": patterns.exception_rate_by_reason,
                "severity_distribution": patterns.exception_severity_distribution,
            },
        },
        "anomalies": [
            {
                "txn_ref": a.txn_ref,
                "type": a.anomaly_type,
                "severity": a.severity,
                "score": a.score,
                "details": a.details,
            }
            for a in anomalies
        ],
    }
    
    import os
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from matcher import load_data, reconcile
    
    settlement, ledger = load_data("data/settlement.csv", "data/ledger.csv")
    results = reconcile(settlement, ledger)
    
    patterns = analyze_gateway_patterns(results)
    anomalies = detect_anomalies(results, patterns)
    
    print(f"Gateway patterns:")
    print(f"  Match rate: {patterns.match_rate_pct:.1f}%")
    print(f"  Avg fee: {patterns.avg_fee_pct or 'N/A'}%")
    print(f"  Avg settlement delay: {patterns.avg_settlement_delay_days or 'N/A'} days")
    print(f"  Anomalies detected: {len(anomalies)}")
    
    export_analytics_report(patterns, anomalies, "output/gateway_analytics.json")
    print(f"Report written to: output/gateway_analytics.json")
