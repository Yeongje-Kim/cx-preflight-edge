#!/usr/bin/env bash
# 데모용 WAN 차단: 기본 경로를 제거하고 상태를 기록한다. 복구는 wan_on.sh.
# LAN 직결(노트북 ↔ 보드)은 그대로 남으므로 브라우저 접속은 유지된다.
set -euo pipefail
STATE="${STATE:-/tmp/cxpe_wan_state}"
ip route show default > "$STATE" || true
echo "[wan_off] saved default routes:"; cat "$STATE" || true
while read -r line; do [[ -n "$line" ]] && sudo ip route del $line || true; done < "$STATE"
echo "[wan_off] default route now:"; ip route show default || echo "(none)"
echo "[wan_off] probe:"; (getent hosts example.com && echo "DNS still resolves (cache?)") || echo "DNS blocked"
(timeout 2 bash -c 'cat < /dev/null > /dev/tcp/1.1.1.1/443' && echo "TCP still open") || echo "TCP blocked"
