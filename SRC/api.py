import os
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from matcher import load_data, reconcile
from review import record_decision
from report import generate_report

app = FastAPI(title="AI Finance Controller API", version="1.0.0")


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
