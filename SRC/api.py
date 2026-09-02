import csv
import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from matcher import load_data, reconcile
from review import record_decision
from report import generate_report

app = FastAPI(title="AI Finance Controller API", version="1.0.0")


def _read_review_queue(path: str):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class ReconcileRequest(BaseModel):
    settlement_path: str = Field(..., description="Path to settlement CSV")
    ledger_path: str = Field(..., description="Path to ledger CSV")
    output_dir: str = Field(default="output", description="Directory for generated outputs")


class ReviewDecisionRequest(BaseModel):
    txn_ref: str = Field(..., min_length=1)
    decision: str = Field(..., description="approve, reject, or override")
    reviewer: str = Field(..., min_length=1)
    notes: str = Field(default="")
    decision_path: str = Field(default="output/review_decisions.csv")
    override_status: Optional[str] = Field(default=None)
    override_match_type: Optional[str] = Field(default=None)
    override_reason: Optional[str] = Field(default=None)


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "ai-finance-controller"}


@app.post("/reconcile")
def reconcile_endpoint(payload: ReconcileRequest):
    os.makedirs(payload.output_dir, exist_ok=True)
    settlement, ledger = load_data(payload.settlement_path, payload.ledger_path)
    results = reconcile(settlement, ledger)
    match_count = sum(1 for r in results if r.status == "MATCHED")
    match_rate = (match_count / len(results) * 100) if results else 0.0
    generate_report(payload.settlement_path, payload.ledger_path, payload.output_dir)
    return {
        "total_records": len(results),
        "matched_records": match_count,
        "match_rate_pct": round(match_rate, 2),
        "output_dir": payload.output_dir,
    }


@app.get("/review-queue")
def review_queue_endpoint(path: str = "output/review_queue.csv"):
    rows = _read_review_queue(path)
    return rows


@app.get("/review-ui", response_class=HTMLResponse)
def review_ui_endpoint():
    return """
    <html>
      <head>
        <title>Review Queue</title>
        <style>
          body { font-family: Arial, sans-serif; margin: 32px; }
          table { border-collapse: collapse; width: 100%; margin-top: 18px; }
          th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
          th { background: #f4f4f4; }
          .badge { background: #eaf3ff; padding: 3px 8px; border-radius: 5px; }
        </style>
      </head>
      <body>
        <h1>Review Queue</h1>
        <p>Use the review queue to approve, reject, or override exceptions.</p>
        <table id="queue-table">
          <thead>
            <tr>
              <th>Txn Ref</th>
              <th>Status</th>
              <th>Decision Bucket</th>
              <th>Match Type</th>
              <th>Exception Reason</th>
              <th>Confidence</th>
              <th>Severity</th>
              <th>Recommendation</th>
            </tr>
          </thead>
          <tbody></tbody>
        </table>
        <script>
          fetch('/review-queue?path=output/review_queue.csv')
            .then(res => res.json())
            .then(rows => {
              const tbody = document.querySelector('#queue-table tbody');
              if (!rows.length) {
                tbody.innerHTML = '<tr><td colspan="8">No review records available.</td></tr>';
                return;
              }
              tbody.innerHTML = rows.map(row => `
                <tr>
                  <td>${row.txn_ref || ''}</td>
                  <td><span class="badge">${row.status || ''}</span></td>
                  <td>${row.decision_bucket || ''}</td>
                  <td>${row.match_type || ''}</td>
                  <td>${row.exception_reason || ''}</td>
                  <td>${row.confidence || ''}</td>
                  <td>${row.severity || ''}</td>
                  <td>${row.recommended_action || ''}</td>
                </tr>
              `).join('');
            })
            .catch(() => {
              document.querySelector('#queue-table tbody').innerHTML = '<tr><td colspan="8">Unable to load review queue.</td></tr>';
            });
        </script>
      </body>
    </html>
    """


@app.post("/review-decisions")
def review_decision_endpoint(payload: ReviewDecisionRequest):
    decision = record_decision(
        payload.decision_path,
        payload.txn_ref,
        {"approve": "APPROVED", "reject": "REJECTED", "override": "OVERRIDDEN"}[payload.decision.lower()],
        payload.reviewer,
        payload.notes,
        payload.override_status or "",
        payload.override_match_type or "",
        payload.override_reason or "",
    )
    return {"decision": decision}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
