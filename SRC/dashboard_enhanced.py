"""
dashboard_enhanced.py
---------------------
Generate an enhanced HTML dashboard with confidence and review queue filters.

This upgrades the static dashboard to include:
- Confidence range slider (0-100%)
- Review queue toggle
- Better sorting and filtering
- Summary stats for each filter combination
"""

import json
import os
import csv
from pathlib import Path


def generate_enhanced_dashboard(reconciliation_report_path, review_queue_path, output_path):
    """Generate an interactive HTML dashboard with advanced filters."""
    
    # Load reconciliation data
    records = []
    with open(reconciliation_report_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            records.append({
                "txn_ref": row.get("txn_ref", ""),
                "status": row.get("status", ""),
                "match_type": row.get("match_type", ""),
                "exception_reason": row.get("exception_reason", ""),
                "ledger_amount": float(row.get("ledger_amount", 0)) if row.get("ledger_amount") else None,
                "settlement_amount": float(row.get("settlement_amount", 0)) if row.get("settlement_amount") else None,
                "confidence": int(row.get("confidence", 0)) if row.get("confidence") else None,
                "severity": row.get("severity", ""),
                "detail": row.get("detail", ""),
            })
    
    # Load review queue
    review_queue_refs = set()
    if os.path.exists(review_queue_path):
        with open(review_queue_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                review_queue_refs.add(row.get("txn_ref", ""))
    
    # Prepare data for JavaScript
    for record in records:
        record["is_review_item"] = record["txn_ref"] in review_queue_refs
    
    data_json = json.dumps(records)
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ReconciliAI — Enhanced Dashboard</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:wght@500;600&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');
  :root {{
    --ink: #1B2A4A;
    --ink-light: #2E4270;
    --paper: #F5F6F3;
    --paper-card: #FFFFFF;
    --line: #DCDDD6;
    --matched: #1D6B3E;
    --matched-bg: #E4F3EA;
    --exception: #B4560B;
    --exception-bg: #FCEEE0;
    --review: #0056b3;
    --review-bg: #E7F1FF;
    --text-secondary: #5B5F6B;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--paper);
    font-family: 'Inter', sans-serif;
    color: var(--ink);
    padding: 40px 24px 80px;
  }}
  .wrap {{ max-width: 1200px; margin: 0 auto; }}
  header {{ margin-bottom: 32px; }}
  .eyebrow {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-secondary);
    margin-bottom: 8px;
  }}
  h1 {{
    font-family: 'Source Serif 4', serif;
    font-weight: 600;
    font-size: 30px;
    margin: 0 0 6px;
    color: var(--ink);
  }}
  .subtitle {{ font-size: 14px; color: var(--text-secondary); }}
  
  .filter-panel {{
    background: var(--paper-card);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 20px;
    margin-bottom: 24px;
  }}
  .filter-group {{
    margin-bottom: 16px;
  }}
  .filter-label {{
    font-size: 12px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-secondary);
    margin-bottom: 8px;
    font-family: 'IBM Plex Mono', monospace;
  }}
  .filter-controls {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
    gap: 16px;
  }}
  input[type="range"] {{
    width: 100%;
  }}
  .confidence-display {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 14px;
    margin-top: 4px;
  }}
  .checkbox-group {{
    display: flex;
    gap: 12px;
    align-items: center;
  }}
  input[type="checkbox"] {{
    cursor: pointer;
  }}
  .stat-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 24px; }}
  .stat-card {{
    background: var(--paper-card);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 16px 18px;
  }}
  .stat-label {{
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--text-secondary); margin-bottom: 6px; font-family: 'IBM Plex Mono', monospace;
  }}
  .stat-value {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 24px; font-weight: 500;
  }}
  .stat-value.matched {{ color: var(--matched); }}
  .stat-value.exception {{ color: var(--exception); }}
  .stat-value.review {{ color: var(--review); }}
  
  .section-label {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--text-secondary); margin: 28px 0 12px;
  }}
  
  table {{ width: 100%; border-collapse: collapse; background: var(--paper-card); border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }}
  thead th {{
    text-align: left; font-family: 'IBM Plex Mono', monospace; font-size: 11px;
    text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-secondary);
    padding: 10px 14px; border-bottom: 1px solid var(--line); background: #FAFAF8;
    cursor: pointer;
  }}
  thead th:hover {{ background: #F0F0ED; }}
  tbody td {{ padding: 10px 14px; font-size: 13px; border-bottom: 1px solid var(--line); vertical-align: top; }}
  tbody tr:last-child td {{ border-bottom: none; }}
  tbody tr:hover {{ background: #FAFAF8; }}
  tbody tr.review-item {{ background: var(--review-bg); }}
  .ref {{ font-family: 'IBM Plex Mono', monospace; font-weight: 500; }}
  .amt {{ font-family: 'IBM Plex Mono', monospace; text-align: right; }}
  .confidence {{
    font-family: 'IBM Plex Mono', monospace;
    font-weight: 500;
  }}
  .confidence.high {{ color: var(--matched); }}
  .confidence.medium {{ color: #F59E0B; }}
  .confidence.low {{ color: var(--exception); }}
  .status-badge {{
    font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 500;
    padding: 3px 8px; border-radius: 4px; display: inline-block;
  }}
  .status-badge.MATCHED {{ background: var(--matched-bg); color: var(--matched); }}
  .status-badge.EXCEPTION {{ background: var(--exception-bg); color: var(--exception); }}
  .review-badge {{ background: var(--review-bg); color: var(--review); }}
  .detail {{ color: var(--text-secondary); font-size: 12px; max-width: 340px; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="eyebrow">ReconciliAI — Intelligent Payment Reconciliation</div>
    <h1>ReconciliAI: Enhanced Dashboard</h1>
    <div class="subtitle">Interactive reconciliation analysis with confidence and review queue filters.</div>
  </header>

  <div class="filter-panel">
    <div class="filter-controls">
      <div class="filter-group">
        <div class="filter-label">Confidence Range</div>
        <input type="range" id="confMin" min="0" max="100" value="0" step="5">
        <div class="confidence-display">Min: <span id="confMinVal">0</span>%</div>
        <input type="range" id="confMax" min="0" max="100" value="100" step="5">
        <div class="confidence-display">Max: <span id="confMaxVal">100</span>%</div>
      </div>
      <div class="filter-group">
        <div class="filter-label">Status</div>
        <div class="checkbox-group">
          <input type="radio" id="statusAll" name="status" value="ALL" checked>
          <label for="statusAll">All</label>
          <input type="radio" id="statusMatched" name="status" value="MATCHED">
          <label for="statusMatched">Matched</label>
          <input type="radio" id="statusException" name="status" value="EXCEPTION">
          <label for="statusException">Exceptions</label>
        </div>
      </div>
      <div class="filter-group">
        <div class="filter-label">Review Queue</div>
        <div class="checkbox-group">
          <input type="checkbox" id="reviewQueueOnly">
          <label for="reviewQueueOnly">Show only review items</label>
        </div>
      </div>
    </div>
  </div>

  <div class="stat-row" id="stats"></div>

  <div class="section-label">Full audit trail (filtered)</div>
  <table>
    <thead>
      <tr>
        <th onclick="sortTable(0)">Ref</th>
        <th>Status</th>
        <th onclick="sortTable(3)">Confidence</th>
        <th onclick="sortTable(4)">Ledger Amt</th>
        <th onclick="sortTable(5)">Settlement Amt</th>
        <th>Detail</th>
      </tr>
    </thead>
    <tbody id="tableBody">
    </tbody>
  </table>
</div>

<script>
const RECORDS = {data_json};
let currentSort = {{ column: 0, ascending: true }};

function updateDisplay() {{
  const confMin = parseInt(document.getElementById('confMin').value);
  const confMax = parseInt(document.getElementById('confMax').value);
  const status = document.querySelector('input[name="status"]:checked').value;
  const reviewOnly = document.getElementById('reviewQueueOnly').checked;

  document.getElementById('confMinVal').textContent = confMin;
  document.getElementById('confMaxVal').textContent = confMax;

  let filtered = RECORDS.filter(r => {{
    if (r.confidence !== null && (r.confidence < confMin || r.confidence > confMax)) return false;
    if (status !== 'ALL' && r.status !== status) return false;
    if (reviewOnly && !r.is_review_item) return false;
    return true;
  }});

  // Update stats
  const matched = filtered.filter(r => r.status === 'MATCHED').length;
  const exceptions = filtered.filter(r => r.status === 'EXCEPTION').length;
  const reviewItems = filtered.filter(r => r.is_review_item).length;
  const matchRate = (matched / (matched + exceptions) * 100).toFixed(1);

  document.getElementById('stats').innerHTML = `
    <div class="stat-card">
      <div class="stat-label">Records Displayed</div>
      <div class="stat-value">${{filtered.length}}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Matched</div>
      <div class="stat-value matched">${{matched}} (${{matchRate}}%)</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Exceptions</div>
      <div class="stat-value exception">${{exceptions}}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Review Queue</div>
      <div class="stat-value review">${{reviewItems}}</div>
    </div>
  `;

  // Update table
  const tbody = document.getElementById('tableBody');
  tbody.innerHTML = filtered.map(r => `
    <tr class="${{r.is_review_item ? 'review-item' : ''}}">
      <td class="ref">${{r.txn_ref}}${{r.is_review_item ? ' 🔍' : ''}}</td>
      <td>
        <span class="status-badge ${{r.status}}">${{r.status === 'MATCHED' ? r.match_type : r.exception_reason}}</span>
      </td>
      <td class="confidence ${{r.confidence >= 85 ? 'high' : r.confidence >= 70 ? 'medium' : 'low'}}">${{r.confidence ?? '—'}}</td>
      <td class="amt">${{r.ledger_amount !== null ? '₹' + r.ledger_amount.toLocaleString('en-IN', {{minimumFractionDigits:2}}) : '—'}}</td>
      <td class="amt">${{r.settlement_amount !== null ? '₹' + r.settlement_amount.toLocaleString('en-IN', {{minimumFractionDigits:2}}) : '—'}}</td>
      <td class="detail">${{r.detail}}</td>
    </tr>
  `).join('');
}}

function sortTable(column) {{
  if (currentSort.column === column) {{
    currentSort.ascending = !currentSort.ascending;
  }} else {{
    currentSort.column = column;
    currentSort.ascending = true;
  }}
  updateDisplay();
}}

document.getElementById('confMin').addEventListener('input', updateDisplay);
document.getElementById('confMax').addEventListener('input', updateDisplay);
document.querySelectorAll('input[name="status"]').forEach(r => r.addEventListener('change', updateDisplay));
document.getElementById('reviewQueueOnly').addEventListener('change', updateDisplay);

updateDisplay();
</script>
</body>
</html>"""
    
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate enhanced reconciliation dashboard.")
    parser.add_argument("--report", default="output/reconciliation_report.csv")
    parser.add_argument("--review-queue", default="output/review_queue.csv")
    parser.add_argument("--output", default="output/dashboard_enhanced.html")
    args = parser.parse_args()
    
    generate_enhanced_dashboard(args.report, args.review_queue, args.output)
    print(f"Enhanced dashboard written to: {args.output}")
