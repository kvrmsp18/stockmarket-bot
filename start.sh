#!/bin/sh
set -eu
mkdir -p data
python scripts/worker.py &
WORKER_PID=$!
trap 'kill "$WORKER_PID" 2>/dev/null || true' INT TERM EXIT
exec streamlit run app.py --server.address=0.0.0.0 --server.port="${PORT:-8501}"
