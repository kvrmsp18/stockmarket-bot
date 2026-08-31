#!/bin/sh
set -eu

mkdir -p data reports

worker_loop() {
  while :; do
    echo "[worker-supervisor] starting worker"
    python scripts/worker.py || true
    echo "[worker-supervisor] worker exited; restarting in 5 seconds"
    sleep 5
  done
}

worker_loop &
SUPERVISOR_PID=$!

cleanup() {
  kill "$SUPERVISOR_PID" 2>/dev/null || true
  pkill -TERM -P "$SUPERVISOR_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

exec streamlit run app.py --server.address=0.0.0.0 --server.port="${PORT:-8501}"
