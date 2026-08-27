# Reconciliation Agent — AI Finance Controller

Built for the **Razorpay Buildathon**, Track 04: *AI Finance Controller*.

## What this does

Matches payment gateway settlement records against a merchant's internal ledger, automatically resolving as many transactions as possible and clearly explaining every one it can't. Built to answer a real, documented problem: merchants often can't tell why a settlement doesn't match what their books expected, with no transparent breakdown of fees, delays, or discrepancies from the gateway side.

Every decision the engine makes is logged with a specific, human-readable reason — nothing is a black box, and nothing is hidden.

## The problem it solves

A payment gateway sends a merchant a settlement file (what was actually paid out). The merchant's own ledger has a separate record of what it expects to receive. These two records rarely match perfectly in the real world:

- Gateway fees are deducted before payout
- Settlements can be delayed by days
- Payments can be partial, duplicated, or simply missing
- Reference IDs can have formatting mismatches (case, whitespace, typos) between systems

Finance teams currently reconcile this by hand. This agent automates that process, and — just as importantly — tells you honestly what it *couldn't* resolve, rather than hiding uncertain cases.

## How it works

**Matching tiers** (checked in order):
1. **Exact match** — same reference, same amount, same date
2. **Fee-adjusted match** — settlement is slightly lower than the ledger amount, within a plausible gateway fee range (0–5%)
3. **Timing-offset match** — amount matches exactly, but settlement lags the ledger date by up to 7 days
4. **Fuzzy reference match** — no exact reference match, but a close textual match exists (typo, case, whitespace difference) using `rapidfuzz`, with amounts confirmed before accepting the link

**Exception types** — anything that can't be confidently matched is labeled with a specific reason:
- `partial_payment` — settlement paid meaningfully less than expected
- `duplicate_settlement` — the same reference appears more than once on the settlement side
- `missing_settlement` — ledger expects a payment that never arrived
- `missing_ledger_entry` — a settlement exists with no corresponding ledger record
- `settlement_exceeds_ledger` — settlement paid *more* than expected (possible refund reversal or correction)
- `excessive_settlement_delay` — settlement arrived more than 7 days late
- `fuzzy_ref_amount_mismatch` — reference IDs are a close textual match, but amounts disagree

## Results

Tested on **two independent synthetic datasets** — a 220-record development set and a separate 55-record set with random data and edge cases the matching logic was never tuned against:

| Batch | Records | Match rate | Verified accuracy vs. ground truth |
|---|---|---|---|
| Batch 1 (development) | 220 | 77.3% | 100% |
| Batch 2 (unseen test) | 55 | 69.1% | 100% |

"Verified accuracy" means every MATCH/EXCEPTION classification the engine made was checked against a secret ground-truth label the engine never had access to — this is what makes the match rate honest rather than cherry-picked.

## Project structure

```
SRC/
  generate_data.py       # synthetic settlement + ledger data generator (batch 1)
  generate_data_test2.py # second, independent unseen test batch
  matcher.py             # core reconciliation engine
  report.py              # generates the audit trail CSV + summary
data/
  settlement.csv, ledger.csv, ground_truth.csv           # batch 1
  settlement_test2.csv, ledger_test2.csv, ground_truth_test2.csv  # batch 2
output/
  reconciliation_report.csv  # full per-record audit trail
  summary.txt                 # headline match rate + exception breakdown
  dashboard.html               # visual report (open directly in a browser)
```

## Running it

```bash
pip install pandas rapidfuzz
python SRC/generate_data.py
python SRC/matcher.py
python SRC/report.py
```

Then open `output/dashboard.html` in any browser to see the visual report.

## Design decisions worth noting

- **Rule-based, not black-box.** Every match/exception decision follows an explicit, inspectable rule with a plain-English reason attached — this was a deliberate choice to satisfy the brief's demand for explainability over raw accuracy.
- **Fuzzy matching is confirmed, not assumed.** A close textual match on reference IDs is only accepted as a real match if the settlement amount also agrees; otherwise it's flagged as `fuzzy_ref_amount_mismatch` for manual review, rather than silently auto-matched.
- **Tested on data it wasn't tuned on.** The second batch includes edge cases (refunds exceeding the ledger amount, long settlement delays) that didn't exist in the first batch, specifically to catch overfitting to the training data.
