#!/bin/bash
# Daily Aura dashboard data refresh — pulls Looker actuals and updates dashboard_data.json

LOG="/Users/yariv.sagih/Documents/Claude/dashboard_update.log"
CLAUDE="/Users/yariv.sagih/.local/bin/claude"

echo "$(date -Iseconds) — Starting dashboard update" >> "$LOG"

PROMPT='You are a data pipeline agent. Update the Aura team dashboard with live Looker actuals. Follow these steps exactly:

**Step 1 — Authenticate with Looker**
Call mcp__aura-looker__authenticate. If it returns a URL requiring browser login, write "REAUTH_REQUIRED" to /Users/yariv.sagih/Documents/Claude/dashboard_update.log and stop — do not proceed further.

**Step 2 — Query Looker**
Use query_looker_inline with model="daily_dashboard", view="daily_report_dash". Run all queries in parallel where possible.

Common filters for all queries unless noted: {"daily_report_dash.date_quarter": "this quarter"}

--- AURA APPS ---

Query A1 (monthly): fields=["daily_report_dash.date_month", "daily_report_dash.apps_gross_profit"], sorts=["daily_report_dash.date_month asc"]

Query A2 (quarterly + pacing): fields=["daily_report_dash.date_quarter", "daily_report_dash.apps_gross_profit", "daily_report_dash.days_left_quarter", "daily_report_dash.days_left"]

Query A3 (last 14 days daily for run rate): filters={"daily_report_dash.date_date": "14 days"}, fields=["daily_report_dash.date_date", "daily_report_dash.apps_gross_profit"], sorts=["daily_report_dash.date_date asc"]

--- SUPPLY US and SUPPLY GLOBAL ---
Use model="report_generator", view="finance_daily_summary_rg". Filter by date_quarter="this quarter".

Query S1 (monthly, both teams): fields=["finance_daily_summary_rg.team", "finance_daily_summary_rg.date_month", "finance_daily_summary_rg.gross_profit"], filters={"finance_daily_summary_rg.date_quarter": "this quarter"}, sorts=["finance_daily_summary_rg.date_month asc"]

Query S2 (quarterly, both teams): fields=["finance_daily_summary_rg.team", "finance_daily_summary_rg.date_quarter", "finance_daily_summary_rg.gross_profit"], filters={"finance_daily_summary_rg.date_quarter": "this quarter"}

Teams are labeled "US team" (→ Supply US) and "Global team" (→ Supply Global) in the results.

**Step 3 — Calculate Aura apps run rate**
From Query A3: daily_avg = sum(apps_gross_profit) / number of rows.
From Query A2: get days_left_quarter and days_left (days remaining in current month).

Run rate projections:
- current_month_run_rate = current_month_actual + round(daily_avg * days_left)
- Q_run_rate = Q_actual + round(daily_avg * days_left_quarter)
- Completed months: run_rate = actual

**Step 4 — Map Looker values to JSON keys**
Month (from "YYYY-MM"): 01→Jan, 02→Feb, 03→Mar, 04→Apr, 05→May, 06→Jun, 07→Jul, 08→Aug, 09→Sep, 10→Oct, 11→Nov, 12→Dec
Quarter (from date_quarter start month): 01→Q1, 04→Q2, 07→Q3, 10→Q4
Quarter JSON key format: "Q2-2026"

**Step 5 — Read the file**
Read /Users/yariv.sagih/Documents/Claude/dashboard_data.json

**Step 6 — Update the JSON**
In quarters[<current quarter key>]:

teams["Aura apps"]:
- actuals: monthly from A1 (rounded), quarter from A2 (rounded)
- runRate: completed months = actual; current month = current_month_run_rate; quarter = Q_run_rate

teams["Supply US"]:
- actuals: monthly from S1 where team="US team" (rounded), quarter from S2 where team="US team" (rounded)

teams["Supply Global"]:
- actuals: monthly from S1 where team="Global team" (rounded), quarter from S2 where team="Global team" (rounded)

Set lastUpdated to todays ISO date string.

**Step 7 — Write the file**
Write the updated JSON back to /Users/yariv.sagih/Documents/Claude/dashboard_data.json

**Step 8 — Log success**
Append: "<date> | SUCCESS | Aura apps Q actual: <val> | Run rate EoQ: <val> | Supply US Q actual: <val> | Supply Global Q actual: <val>"

Important: Do not modify targets, outlook, brands, excludedBrands, other teams, or other quarters.'

"$CLAUDE" --dangerously-skip-permissions -p "$PROMPT" >> "$LOG" 2>&1
STATUS=$?

if [ $STATUS -ne 0 ]; then
    echo "$(date -Iseconds) — ERROR: Aura/Supply claude exited with status $STATUS" >> "$LOG"
else
    echo "$(date -Iseconds) — Aura/Supply done" >> "$LOG"
fi

# ── Step 2: Demand (US + TLV) via Gmail email report ──────────────────────────
echo "$(date -Iseconds) — Starting Demand update" >> "$LOG"
python3 /Users/yariv.sagih/Documents/Claude/update_demand.py >> "$LOG" 2>&1
STATUS=$?

if [ $STATUS -ne 0 ]; then
    echo "$(date -Iseconds) — ERROR: Demand update exited with status $STATUS" >> "$LOG"
else
    echo "$(date -Iseconds) — Done" >> "$LOG"
fi

# ── Step 3: Push updated data to GitHub Pages ─────────────────────────────────
echo "$(date -Iseconds) — Pushing to GitHub Pages" >> "$LOG"
cd /Users/yariv.sagih/Documents/Claude
git add dashboard_data.json
git commit -m "Dashboard data update $(date +%Y-%m-%d)" >> "$LOG" 2>&1
git push origin main >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    echo "$(date -Iseconds) — ERROR: git push failed" >> "$LOG"
else
    echo "$(date -Iseconds) — GitHub Pages updated" >> "$LOG"
fi
