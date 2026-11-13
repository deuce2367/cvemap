#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# CVEMAP Unified Container Launcher
# ==============================================================================
# Starts a single Docker container running MapServer, FastAPI, and TileServer-GL.
# Defaults to 'cvemap:lite' with ./app/data mounted at runtime, or '--full' to
# run 'cvemap:full' completely standalone with baked-in map data.
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="lite"
IMAGE=""
CONTAINER_NAME="cvemap"
PORT="8000"
TS_PORT="8080"
DATA_DIR="${SCRIPT_DIR}/app/data"
STOP_ONLY=false

show_help() {
  cat <<'EOF'
Usage:
  ./launch_cvemap.sh [options]

Options:
  --lite                Run 'cvemap:lite' mounting --data-dir at runtime [default]
  --full                Run 'cvemap:full' using baked-in map data (no host mount)
  --image <image:tag>   Override Docker image to run
  --data-dir <path>     Host data directory to mount in --lite mode (default: ./app/data)
  --port <port>         Host port for CVEMAP Web/WMS/XYZ UI (default: 8000)
  --ts-port <port>      Host port for direct TileServer-GL access (default: 8080)
  --name <name>         Container name (default: cvemap)
  --stop                Stop and remove running CVEMAP containers and exit
  -h, --help            Show this help message
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --lite)      MODE="lite"; shift ;;
    --full)      MODE="full"; shift ;;
    --image)     IMAGE="$2"; shift 2 ;;
    --data-dir)  DATA_DIR="$2"; shift 2 ;;
    --port)      PORT="$2"; shift 2 ;;
    --ts-port)   TS_PORT="$2"; shift 2 ;;
    --name)      CONTAINER_NAME="$2"; shift 2 ;;
    --stop)      STOP_ONLY=true; shift ;;
    -h|--help)   show_help; exit 0 ;;
    *) echo "Error: Unknown option '$1'. Run '$0 --help' for usage." >&2; exit 1 ;;
  esac
done

# Stop any existing unified or legacy split containers
if [ "${STOP_ONLY}" = true ]; then
  "${SCRIPT_DIR}/stop_cvemap.sh" --name "${CONTAINER_NAME}"
  exit 0
fi

docker stop "${CONTAINER_NAME}" cvemap-dev tileserver 2>/dev/null || true
docker rm "${CONTAINER_NAME}" cvemap-dev tileserver 2>/dev/null || true

if [ -z "${IMAGE}" ]; then
  IMAGE="cvemap:${MODE}"
fi

if ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  echo "Image '${IMAGE}' not found locally. Building it now..."
  "${SCRIPT_DIR}/build_docker.sh" "--${MODE}" --tag "${IMAGE}"
fi

DOCKER_ARGS=(
  "-d"
  "--name" "${CONTAINER_NAME}"
  "-p" "${PORT}:8000"
  "-p" "${TS_PORT}:8080"
)

if [ "${MODE}" = "lite" ]; then
  mkdir -p "${DATA_DIR}"
  DOCKER_ARGS+=("-v" "$(cd "${DATA_DIR}" && pwd):/app/data")
fi

echo "==> Starting ${CONTAINER_NAME} (${IMAGE}) ..."
docker run "${DOCKER_ARGS[@]}" "${IMAGE}" >/dev/null

echo "All services started in single container '${CONTAINER_NAME}'!"
echo "  - Legacy Raster & WMS Viewer : http://localhost:${PORT}/"
echo "  - Modern WebGL Vector Viewer : http://localhost:${PORT}/modern"
echo "  - Admin Dashboard            : http://localhost:${PORT}/admin"
echo "  - TileServer-GL Endpoints    : http://localhost:${TS_PORT}/"
