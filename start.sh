#!/usr/bin/env bash
# Start all three services. Ctrl-C stops everything.
set -euo pipefail
cd "$(dirname "$0")"

./check-env.sh || exit 1

cleanup() { echo; echo "stopping…"; kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "→ engine   http://localhost:8000/docs"
(cd engine && ./.venv/bin/python -m uvicorn app.main:app --port 8000 --log-level warning) &

sleep 3
echo "→ bff      http://localhost:4000/api/v1/health"
(cd server && npm start) &

sleep 2
echo "→ frontend http://localhost:5173"
(cd frontend && npm run dev) &

wait
