#!/usr/bin/env bash
# 보드(Radxa Airbox Q900)에서 Cx-Preflight Edge를 띄운다.
#   ./scripts/run_board.sh              # 서버만 (LLM은 자동 탐지: GenieX 18181 → Rust 8091)
#   LLM=rust ./scripts/run_board.sh     # 보드의 네이티브 LLM 서비스를 함께 기동
# 환경변수: CXPE_HOST(0.0.0.0) CXPE_PORT(8080) CXPE_LLM(auto|geniex|rust|none) LLM_ROOT(../llm-runtime)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PY:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || { echo "venv 없음: python3 -m venv .venv && .venv/bin/pip install -e ."; exit 1; }

if [[ "${LLM:-}" == "rust" ]]; then
  LLM_ROOT="${LLM_ROOT:-$ROOT/../llm-runtime}"
  if [[ -x "$LLM_ROOT/scripts/start_llm.sh" ]]; then
    echo "[board] starting Rust LLM service (NPU1) from $LLM_ROOT"
    (cd "$LLM_ROOT" && ./scripts/start_llm.sh) &
    LLM_PID=$!
    trap 'kill $LLM_PID 2>/dev/null || true' EXIT
    for _ in $(seq 1 60); do curl -sf http://127.0.0.1:8091/healthz >/dev/null && break; sleep 1; done
  else
    echo "[board] start_llm.sh not found under $LLM_ROOT" >&2
  fi
fi

export CXPE_LLM="${CXPE_LLM:-auto}"
echo "[board] LLM probe: geniex=$(curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:18181/v1/models || echo down) rust=$(curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:8091/healthz || echo down)"
exec "$PY" -m uvicorn cxpe.server:app --host "${CXPE_HOST:-0.0.0.0}" --port "${CXPE_PORT:-8080}"
