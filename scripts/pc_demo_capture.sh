#!/usr/bin/env bash
# PC에서 서버를 띄워 데모 케이스를 돌리고 화면을 캡처한다 (PPT용). Chrome/Edge headless 사용.
#   ./scripts/pc_demo_capture.sh [case=fail_start] [port=8087]
set -uo pipefail
CASE="${1:-fail_start}"; PORT="${2:-8087}"; BASE="http://127.0.0.1:$PORT"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
PY="$ROOT/.venv/Scripts/python"; [[ -x "$PY" ]] || PY="$ROOT/.venv/bin/python"
mkdir -p docs/screens
export CXPE_SKIP_CONNECT_PROBE=1 CXPE_LLM=none PYTHONIOENCODING=utf-8
"$PY" -m uvicorn cxpe.server:app --host 127.0.0.1 --port "$PORT" > /tmp/cxpe_pc.log 2>&1 &
SRV=$!; trap 'kill $SRV 2>/dev/null' EXIT
for _ in $(seq 1 40); do curl -sf "$BASE/api/v1/status" >/dev/null && break; sleep 0.5; done
J() { "$PY" -c "import sys,json; d=json.load(sys.stdin); print($1)"; }
SID=$(curl -sf -X POST "$BASE/api/v1/sessions" -H 'Content-Type: application/json' -d "{\"case\":\"$CASE\",\"mode\":\"golden\"}" | J "d['id']")
echo "session $SID"
if [[ "$CASE" != "preflight_warn" ]]; then
  curl -sf -X POST "$BASE/api/v1/sessions/$SID/approve" -H 'Content-Type: application/json' -d '{"approver":"commissioning engineer"}' >/dev/null
  curl -sf -X POST "$BASE/api/v1/sessions/$SID/run" -H 'Content-Type: application/json' -d '{"speed":0}' >/dev/null
  for _ in $(seq 1 60); do ST=$(curl -sf "$BASE/api/v1/sessions/$SID" | J "d['meta']['summary'].get('status')"); [[ "$ST" == "finished" ]] && break; sleep 0.5; done
  curl -sf "$BASE/api/v1/sessions/$SID" | J "'overall', d['meta']['summary'].get('overall'), 'report', d['has_report']"
fi
BROWSER=""
for c in "/c/Program Files/Google/Chrome/Application/chrome.exe" "/c/Program Files (x86)/Google/Chrome/Application/chrome.exe" "/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" "/c/Program Files/Microsoft/Edge/Application/msedge.exe"; do
  [[ -x "$c" ]] && { BROWSER="$c"; break; }
done
if [[ -z "$BROWSER" ]]; then echo "no chrome/edge found; server log: /tmp/cxpe_pc.log"; exit 0; fi
OUT="$(cd docs/screens && pwd -W 2>/dev/null || pwd)"
"$BROWSER" --headless=new --disable-gpu --hide-scrollbars --window-size=1600,1900 --virtual-time-budget=8000 \
  --screenshot="$OUT/live_${CASE}.png" "$BASE/?session=$SID" 2>/dev/null
"$BROWSER" --headless=new --disable-gpu --hide-scrollbars --window-size=1600,1000 --virtual-time-budget=6000 \
  --screenshot="$OUT/history.png" "$BASE/history" 2>/dev/null
ls -la docs/screens/
