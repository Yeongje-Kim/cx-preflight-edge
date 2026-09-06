#!/usr/bin/env bash
# 루프백만 있는 네트워크 네임스페이스에서 전체 세션을 돌린다.
# 외부로 나갈 인터페이스가 존재하지 않는 상태에서 사전검증 → 승인 → 판정 → 보고서가 끝나는지 본다.
set -u
cd "$HOME/cx-preflight-edge" || exit 1

unshare -rnm /bin/bash -s <<'INNER'
set -u
ip link set lo up
mount -t sysfs sysfs /sys 2>/dev/null || echo '(sysfs 재마운트 실패)'
cd "$HOME/cx-preflight-edge"
echo "=== 네임스페이스 안 인터페이스 ==="
ip -o link show | awk '{print $2}' | tr -d ':'
echo "sysfs: $(ls /sys/class/net | tr '
' ' ')"

export CXPE_LLM=none
export CXPE_HOST=127.0.0.1 CXPE_PORT=8080
.venv/bin/python -m uvicorn cxpe.server:app --host 127.0.0.1 --port 8080 --log-level warning &
SRV=$!
for _ in $(seq 1 40); do curl -sf -o /dev/null http://127.0.0.1:8080/api/v1/status && break; sleep 0.5; done

echo "=== 폐쇄망 상태 ==="
curl -sf http://127.0.0.1:8080/api/v1/status

echo
echo "=== 세션 생성 (fail_dropout) ==="
SID=$(curl -sf -X POST http://127.0.0.1:8080/api/v1/sessions -H 'Content-Type: application/json' \
      -d '{"case":"fail_dropout","mode":"golden"}' | .venv/bin/python -c 'import sys,json; print(json.load(sys.stdin)["id"])')
echo "session=$SID"
curl -sf "http://127.0.0.1:8080/api/v1/sessions/$SID/preflight" | head -c 200; echo
curl -sf -X POST "http://127.0.0.1:8080/api/v1/sessions/$SID/approve" -H 'Content-Type: application/json' -d '{"approver":"engineer"}'; echo
curl -sf -X POST "http://127.0.0.1:8080/api/v1/sessions/$SID/run" -H 'Content-Type: application/json' -d '{"speed":0}'; echo
for _ in $(seq 1 60); do
  S=$(curl -sf "http://127.0.0.1:8080/api/v1/sessions/$SID" | .venv/bin/python -c 'import sys,json; d=json.load(sys.stdin); print((d.get("meta",{}).get("summary") or {}).get("overall","-"))')
  [ "$S" != "-" ] && break
  sleep 1
done
echo "=== 종합 판정 ==="
echo "$S"
curl -sf -X POST "http://127.0.0.1:8080/api/v1/sessions/$SID/report" -H 'Content-Type: application/json' -d '{}' >/dev/null && echo "보고서 생성 OK"

echo "=== 세션 종료 시점 폐쇄망 상태 ==="
curl -sf http://127.0.0.1:8080/api/v1/status
echo
kill $SRV 2>/dev/null
wait $SRV 2>/dev/null
INNER
