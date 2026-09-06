#!/usr/bin/env bash
# LLM 추출이 도는 동안 cxpe 프로세스와 LLM 서버가 여는 TCP 상대 주소를 모두 수집한다.
# 루프백 외 주소가 하나라도 잡히면 폐쇄망 주장이 깨진다.
set -u
cd "$HOME/cx-preflight-edge" || exit 1
OUT=/tmp/cxpe_sockets.txt
: > "$OUT"

# WAN 인터페이스 송신 바이트 시작값
WAN=$(ip route show default | awk '{print $5}' | head -1)
TX0=$(cat /sys/class/net/$WAN/statistics/tx_bytes)

.venv/bin/python -m cxpe.cli extract --md data/plan_n1_cooling.md --llm auto \
  --out sessions/_extract2 --golden-fallback > /tmp/cxpe_extract2.log 2>&1 &
PY=$!

while kill -0 $PY 2>/dev/null; do
  ss -tnp 2>/dev/null | grep -E "python|geniex" >> "$OUT"
  sleep 0.4
done
wait $PY
RC=$?

TX1=$(cat /sys/class/net/$WAN/statistics/tx_bytes)

echo "=== 추출 결과 ==="
tail -2 /tmp/cxpe_extract2.log
echo "=== 관측된 TCP 상대 주소 (cxpe python / geniex) ==="
awk '{print $5}' "$OUT" | sort -u
echo "=== 루프백 아닌 상대 주소 ==="
awk '{print $5}' "$OUT" | sort -u | grep -v -E "^127\.0\.0\.1:|^\[::1\]:" || echo "(없음)"
echo "=== 샘플 수 ==="
wc -l < "$OUT"
echo "=== WAN($WAN) 송신 바이트 증가 (SSH 관리 트래픽 포함) ==="
echo "$((TX1 - TX0)) bytes"
exit $RC
