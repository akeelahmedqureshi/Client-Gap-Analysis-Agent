#!/usr/bin/env bash
# End-to-end API smoke test: register -> upload CSV -> import -> analyze (all gates pre-approved)
# -> wait -> check report, evidence and changes.  Needs curl and python3.
#
#   BASE=http://localhost:8000 CSV=../examples/clients.csv ./scripts/api_smoke_test.sh
#
# Against the offline demo (scripts/demo_server.py) use the default CSV and PROJECT="ABC Patient Management".
# Against a real deployment it creates a NEW organization — use a test instance, or set
# EMAIL/PASSWORD of an existing analyst account with LOGIN=1.
set -euo pipefail
BASE=${BASE:-http://localhost:8000}
CSV=${CSV:-$(dirname "$0")/../../examples/clients.csv}
PROJECT=${PROJECT:-ABC Patient Management}
EMAIL=${EMAIL:-smoke$(date +%s)@example.com}
PASSWORD=${PASSWORD:-smoke-test-password-1}
TIMEOUT=${TIMEOUT:-900}

json() { python3 -c "import sys, json; d = json.load(sys.stdin); print($1)"; }
step() { printf '\n== %s\n' "$*"; }

step "health"
curl -fsS "$BASE/api/health"; echo

if [[ "${LOGIN:-0}" == "1" ]]; then
  step "login $EMAIL"
  TOKEN=$(curl -fsS -X POST "$BASE/api/auth/login" -H 'Content-Type: application/json' \
          -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" | json 'd["access_token"]')
else
  step "register $EMAIL (new organization)"
  TOKEN=$(curl -fsS -X POST "$BASE/api/auth/register" -H 'Content-Type: application/json' \
          -d "{\"organization\":\"Smoke Test\",\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" | json 'd["access_token"]')
fi
AUTH=(-H "Authorization: Bearer $TOKEN")

step "upload $CSV"
UPLOAD=$(curl -fsS "${AUTH[@]}" -F "file=@$CSV;type=text/csv" "$BASE/api/uploads")
echo "$UPLOAD" | json '"valid=%s records=%d errors=%s" % (d["valid"], len(d["records"]), d.get("errors"))'
UPLOAD_ID=$(echo "$UPLOAD" | json 'd["id"]')

step "import"
curl -fsS "${AUTH[@]}" -H 'Content-Type: application/json' -d '{}' "$BASE/api/uploads/$UPLOAD_ID/import" \
  | json '"created %d project(s)" % len(d["created_projects"])'
PROJECT_ID=$(curl -fsS "${AUTH[@]}" "$BASE/api/projects" \
  | PROJECT="$PROJECT" python3 -c "import sys, json, os; print(next(p['id'] for p in json.load(sys.stdin) if p['name'] == os.environ['PROJECT']))")
echo "project $PROJECT_ID ($PROJECT)"

step "approval preview"
GATES=$(curl -fsS "${AUTH[@]}" "$BASE/api/runs/approval-preview?project_id=$PROJECT_ID" | json 'json.dumps([g["gate"] for g in d])')
echo "$GATES"

step "start analysis (pre-approving $GATES)"
RUN_ID=$(curl -fsS "${AUTH[@]}" -H 'Content-Type: application/json' \
         -d "{\"project_id\":\"$PROJECT_ID\",\"approve_gates\":$GATES}" "$BASE/api/runs" | json 'd["run_id"]')
echo "run $RUN_ID"

step "wait for completion (timeout ${TIMEOUT}s)"
START=$(date +%s)
while :; do
  RUN=$(curl -fsS "${AUTH[@]}" "$BASE/api/runs/$RUN_ID")
  STATUS=$(echo "$RUN" | json 'd["status"]')
  printf '  %s  %s\n' "$STATUS" "$(echo "$RUN" | json '" ".join(f"{k}={v}" for k, v in d["agents"].items() if v != "completed")')"
  [[ "$STATUS" == completed* || "$STATUS" == failed || "$STATUS" == awaiting_approval ]] && break
  (( $(date +%s) - START > TIMEOUT )) && { echo "timed out"; exit 1; }
  sleep 5
done
[[ "$STATUS" == completed* ]] || { echo "run ended as $STATUS"; echo "$RUN" | json 'd.get("error")'; exit 1; }

step "results"
curl -fsS "${AUTH[@]}" "$BASE/api/runs/$RUN_ID/agents/gap_analysis" | json '"gaps: %d" % len(d["result"]["data"]["gaps"])'
curl -fsS "${AUTH[@]}" "$BASE/api/runs/$RUN_ID/agents/opportunity_prioritization" \
  | json '"top recommendations: " + "; ".join(r["feature"] for r in d["result"]["data"]["recommendations"][:5])'
curl -fsS "${AUTH[@]}" "$BASE/api/runs/$RUN_ID/evidence" | json '"evidence records: %d (all with a source URL: %s)" % (len(d), all(e["source_url"] for e in d))'
curl -fsS "${AUTH[@]}" "$BASE/api/runs/$RUN_ID/report.md" -o "report-$RUN_ID.md" && echo "report saved: report-$RUN_ID.md ($(wc -c < "report-$RUN_ID.md") bytes)"
if curl -fsS "${AUTH[@]}" "$BASE/api/runs/$RUN_ID/report.pdf" -o "report-$RUN_ID.pdf" 2>/dev/null; then
  echo "PDF saved: report-$RUN_ID.pdf"
else
  echo "PDF export unavailable (needs the browser extra + Chromium on the server)"
fi
curl -fsS "${AUTH[@]}" "$BASE/api/runs/$RUN_ID/changes" | json '"changes vs previous run: %s" % d["summary"]'
step "OK — run $STATUS"
