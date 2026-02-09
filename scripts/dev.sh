#!/usr/bin/env bash
# TextQL development script: builds Docker sandbox, then runs backend + frontend.
# Run from the project root. Requires: docker, uv, node/npm.
#
# Usage: ./scripts/dev.sh [--no-build]
#   --no-build  Skip Docker build and dep install (use after 'make build')

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SKIP_BUILD=false
[[ "${1:-}" == "--no-build" ]] && SKIP_BUILD=true

if ! $SKIP_BUILD; then
  echo "==> Building Docker sandbox image..."
  docker build -t textql-sandbox:py311 backend/sandbox/runner

  echo "==> Installing backend dependencies..."
  (cd "$ROOT/backend" && uv sync)

  echo "==> Installing frontend dependencies..."
  (cd "$ROOT/frontend" && npm install)
fi

echo "==> Starting backend (port 8000) and frontend (port 5173)..."
echo "    Backend:  http://127.0.0.1:8000"
echo "    Frontend: http://127.0.0.1:5173"
echo "    Press Ctrl+C to stop both."
echo ""

cleanup() {
  echo ""
  echo "==> Shutting down..."
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null
  exit 0
}
trap cleanup INT TERM

(cd "$ROOT/backend" && uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000) &
BACKEND_PID=$!
sleep 2

(cd "$ROOT/frontend" && npm run dev) &
FRONTEND_PID=$!

wait
