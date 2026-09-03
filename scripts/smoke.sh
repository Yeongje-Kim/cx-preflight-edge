#!/usr/bin/env bash
# 보드 스모크: 서버·LLM·폐쇄망 상태·3케이스 재생을 순서대로 확인한다.
#   ./scripts/smoke.sh [http://127.0.0.1:8080]
set -uo pipefail
BASE="${1:-http://127.0.0.1:8080}"
J() { python3 -c "import sys,json; d=json.load(sys.stdin); print($1)"; }
echo "== status"; curl -sf "$BASE/api/v1/status" | J "'offline', d['offline']['is_offline'], d['offline']['offline_score'], 'llm', d['llm']"
for CASE in preflight_warn pass fail_start; do
  echo "== case $CASE"
  SID=$(curl -sf -X POST "$BASE/api/v1/sessions" -H 'Content-Type: application/json' -d "{\"case\":\"$CASE\",\"mode\":\"golden\"}" | J "d['id']")
  PF=$(curl -sf "$BASE/api/v1/sessions/$SID/preflight"); echo "$PF" | J "'preflight errors', d.get('n_error'), 'warn', d.get('n_warn')"
  if [[ "$CASE" == "preflight_warn" ]]; then
    curl -s -X POST "$BASE/api/v1/sessions/$SID/approve" -H 'Content-Type: application/json' -d '{"approver":"smoke"}' | J "'approve(expect 409):', d.get('detail', d)"
    continue
  fi
  curl -sf -X POST "$BASE/api/v1/sessions/$SID/approve" -H 'Content-Type: application/json' -d '{"approver":"smoke"}' | J "'approved', d['approved']"
  curl -sf -X POST "$BASE/api/v1/sessions/$SID/run" -H 'Content-Type: application/json' -d '{"speed":0}' | J "'run', d"
  for _ in $(seq 1 120); do
    ST=$(curl -sf "$BASE/api/v1/sessions/$SID" | J "d['meta']['summary'].get('status')")
    [[ "$ST" == "finished" || "$ST" == "error" ]] && break; sleep 1
  done
  curl -sf "$BASE/api/v1/sessions/$SID" | J "'overall', d['meta']['summary'].get('overall'), 'label', d['meta']['summary'].get('label_overall'), 'tx', d['meta']['offline'].get('tx_delta_bytes'), 'report', d['has_report'], 'timings', d['meta']['timings']"
done
