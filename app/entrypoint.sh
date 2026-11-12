#!/usr/bin/env bash
set -euo pipefail

# Allow running arbitrary commands (e.g., `docker run -it cvemap:lite bash`)
if [ "$#" -gt 0 ] && [[ "${1}" != -* ]]; then
  exec "$@"
fi

CVEMAP_PORT="${CVEMAP_PORT:-8000}"
TILESERVER_PORT="${TILESERVER_PORT:-8080}"
export DISPLAY="${DISPLAY:-:99}"
export TILESERVER_INTERNAL_URL="${TILESERVER_INTERNAL_URL:-http://127.0.0.1:${TILESERVER_PORT}}"

rm -f /tmp/.X99-lock /tmp/tileserver.pid /tmp/uvicorn.pid 2>/dev/null || true

cleanup() {
  local code=$?
  trap - SIGTERM SIGINT EXIT
  echo "Shutting down CVEMAP services..."
  for pid_var in "${UVICORN_PID:-}" "${TILESERVER_PID:-}" "${XVFB_PID:-}"; do
    if [ -n "${pid_var}" ] && kill -0 "${pid_var}" 2>/dev/null; then
      kill -TERM "${pid_var}" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
  exit "${code}"
}
trap cleanup SIGTERM SIGINT EXIT

echo "==> Starting Xvfb virtual framebuffer on ${DISPLAY} ..."
Xvfb "${DISPLAY}" -screen 0 1024x768x24 -nolisten unix \
  2> >(grep -vE "(Could not resolve keysym|XKEYBOARD keymap compiler|xkbcomp are not fatal)" >&2) &
XVFB_PID=$!

cd /app
mkdir -p /app/data/legacy /app/data/webgl/world

echo "==> Preparing TileServer-GL runtime configuration..."
RUNTIME_TS_CFG="$(python3 -m server.tileserver_sync \
  --data-dir /app/data \
  --template-dir /app/tileserver \
  --runtime-dir /tmp/tileserver)"

echo "==> Starting TileServer-GL on port ${TILESERVER_PORT} ..."
node /usr/src/app/ \
  -c "${RUNTIME_TS_CFG}" \
  -p "${TILESERVER_PORT}" \
  --ignore-missing-files &
TILESERVER_PID=$!
echo "${TILESERVER_PID}" > /tmp/tileserver.pid

echo "==> Starting CVEMAP MapServer & FastAPI on port ${CVEMAP_PORT} ..."
python3 -m uvicorn server.main:app --host 0.0.0.0 --port "${CVEMAP_PORT}" &
UVICORN_PID=$!
echo "${UVICORN_PID}" > /tmp/uvicorn.pid

echo "=================================================================="
echo " CVEMAP Unified Server Ready"
echo "   Legacy Raster / WMS Viewer : http://localhost:${CVEMAP_PORT}/"
echo "   Modern WebGL Vector Viewer : http://localhost:${CVEMAP_PORT}/modern"
echo "   Admin Dashboard            : http://localhost:${CVEMAP_PORT}/admin"
echo "   TileServer-GL Direct       : http://localhost:${TILESERVER_PORT}/"
echo "=================================================================="

wait -n "${TILESERVER_PID}" "${UVICORN_PID}"
