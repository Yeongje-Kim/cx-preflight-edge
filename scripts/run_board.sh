#!/usr/bin/env bash
# 보드(Radxa Fogwise AIRbox Q900 (Qualcomm IQ-9075 / QCS9075))에서 Cx-Preflight Edge를 띄운다.
#   ./scripts/run_board.sh          # 서버 기동. LLM 백엔드는 자동 탐지한다(GenieX 18181 → 네이티브 8091)
#
# 온디바이스 LLM 런타임은 보드에 미리 설치되어 있어야 한다. 이 저장소는 런타임을 포함하지 않고
# 로컬 HTTP로 호출만 한다. 런타임을 함께 띄우려면 CXPE_LLM_START 에 기동 명령을 넣는다.
#
# 환경변수
#   CXPE_HOST(0.0.0.0) CXPE_PORT(8080)
#   CXPE_LLM(auto|geniex|native|none)
#   CXPE_GENIEX_URL(http://127.0.0.1:18181) CXPE_NATIVE_URL(http://127.0.0.1:8091)
#   CXPE_LLM_START  선택. 지정하면 서버 기동 전에 이 명령을 백그라운드로 실행하고 8091 헬스체크를 기다린다.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PY:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || { echo "venv 없음: python3 -m venv .venv && .venv/bin/pip install -e ."; exit 1; }

if [[ -n "${CXPE_LLM_START:-}" ]]; then
  echo "[board] starting local LLM runtime: $CXPE_LLM_START"
  bash -c "$CXPE_LLM_START" &
  LLM_PID=$!
  trap 'kill $LLM_PID 2>/dev/null || true' EXIT
  for _ in $(seq 1 60); do curl -sf http://127.0.0.1:8091/healthz >/dev/null && break; sleep 1; done
fi

export CXPE_LLM="${CXPE_LLM:-auto}"
echo "[board] LLM probe: geniex=$(curl -sf -o /dev/null -w '%{http_code}' \
  "${CXPE_GENIEX_URL:-http://127.0.0.1:18181}/v1/models" || echo down) native=$(curl -sf -o /dev/null -w '%{http_code}' \
  "${CXPE_NATIVE_URL:-http://127.0.0.1:8091}/healthz" || echo down)"
exec "$PY" -m uvicorn cxpe.server:app --host "${CXPE_HOST:-0.0.0.0}" --port "${CXPE_PORT:-8080}"
