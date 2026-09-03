#!/usr/bin/env bash
# wan_off.sh가 저장한 기본 경로를 복구한다.
set -euo pipefail
STATE="${STATE:-/tmp/cxpe_wan_state}"
[[ -f "$STATE" ]] || { echo "no saved state at $STATE"; exit 1; }
while read -r line; do [[ -n "$line" ]] && sudo ip route add $line || true; done < "$STATE"
echo "[wan_on] default route now:"; ip route show default || echo "(none)"
