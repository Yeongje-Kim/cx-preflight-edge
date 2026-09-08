#!/usr/bin/env bash
# Run the application and a preinstalled, cached NPU model in one isolated namespace.
# The host network and SSH session stay unchanged. No model/runtime install is performed.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" != "--inside" ]]; then
  exec unshare -Urnm bash "$0" --inside "${1:?output directory required}" "${2:?manifest required}"
fi
OUT_DIR="$(realpath -m "$2")"
MANIFEST="$(realpath "$3")"
PYTHON_BIN="${CXPE_PYTHON:-$ROOT_DIR/.venv/bin/python}"
GENIEX_BIN="${CXPE_GENIEX_BIN:-$HOME/.local/bin/geniex}"
mkdir -p "$OUT_DIR"
cd "$ROOT_DIR"
ip link set lo up
mount -t sysfs sysfs /sys
ip -j address > "$OUT_DIR/interfaces.json"
ip -j route show table all > "$OUT_DIR/routes.json"
ip -6 -j route show table all > "$OUT_DIR/routes6.json"
readlink /proc/self/ns/net > "$OUT_DIR/network-namespace.txt"
[[ "$(ls /sys/class/net)" == "lo" ]]
unset CXPE_SKIP_CONNECT_PROBE
export CXPE_LLM=geniex CXPE_GENIEX_URL=http://127.0.0.1:18181
export NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost
RUNTIME_PID=""
SERVER_PID=""
cleanup() {
  [[ -z "$SERVER_PID" ]] || kill "$SERVER_PID" 2>/dev/null || true
  [[ -z "$RUNTIME_PID" ]] || kill "$RUNTIME_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT
"$GENIEX_BIN" --skip-update serve --host 127.0.0.1:18181 > "$OUT_DIR/runtime.log" 2>&1 &
RUNTIME_PID=$!
printf '%s\n' "$RUNTIME_PID" > "$OUT_DIR/runtime-pid.txt"
READY=0
for ((i=0; i<120; i++)); do
  if curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:18181/v1/models > "$OUT_DIR/models.json" 2>/dev/null; then READY=1; break; fi
  kill -0 "$RUNTIME_PID" 2>/dev/null || { cat "$OUT_DIR/runtime.log"; exit 1; }
  sleep 1
done
[[ "$READY" == 1 ]]
"$PYTHON_BIN" -m pytest -o addopts='' -q > "$OUT_DIR/pytest.txt" 2>&1
# Keep going after a corpus failure so lifecycle evidence is still available.
set +e
"$PYTHON_BIN" scripts/evaluate_npu.py --out "$OUT_DIR/evaluation"
EVALUATION_RC=$?
set -e
"$PYTHON_BIN" -m uvicorn cxpe.server:app --host 127.0.0.1 --port 8089 --log-level warning > "$OUT_DIR/server.log" 2>&1 &
SERVER_PID=$!
printf '%s\n' "$SERVER_PID" > "$OUT_DIR/server-pid.txt"
READY=0
for ((i=0; i<40; i++)); do
  if curl --noproxy '*' -fsS --max-time 15 http://127.0.0.1:8089/api/v1/status > "$OUT_DIR/status-start.json" 2>/dev/null; then READY=1; break; fi
  sleep 1
done
[[ "$READY" == 1 ]]
[[ "$(readlink /proc/$RUNTIME_PID/ns/net)" == "$(readlink /proc/$SERVER_PID/ns/net)" ]]
"$PYTHON_BIN" scripts/verify_npu_lifecycle.py --root "$ROOT_DIR" --manifest "$MANIFEST" --out "$OUT_DIR/lifecycle" --runtime-pid "$RUNTIME_PID" --require-offline
curl --noproxy '*' -fsS --max-time 15 http://127.0.0.1:8089/api/v1/status > "$OUT_DIR/status-end.json"
exit "$EVALUATION_RC"
