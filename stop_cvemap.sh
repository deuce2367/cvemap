#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# CVEMAP Container Stopper
# ==============================================================================
# Cleanly stops and removes running CVEMAP Docker containers (both unified
# 'cvemap' containers and any legacy split 'cvemap-dev' / 'tileserver' ones).
# ==============================================================================

CONTAINER_NAME="cvemap"
STOP_ALL=false

show_help() {
  cat <<'EOF'
Usage:
  ./stop_cvemap.sh [options]

Description:
  Stops and removes the running CVEMAP container (MapServer + FastAPI + TileServer-GL)
  without touching any map data in ./app/data.

Options:
  --name <name>   Stop a specific container name (default: cvemap)
  --all           Also stop any other containers running 'cvemap:*' images
  -h, --help      Show this help message
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --name)    CONTAINER_NAME="$2"; shift 2 ;;
    --all)     STOP_ALL=true; shift ;;
    -h|--help) show_help; exit 0 ;;
    *) echo "Error: Unknown option '$1'. Run '$0 --help' for usage." >&2; exit 1 ;;
  esac
done

TARGETS=("${CONTAINER_NAME}" "cvemap-dev" "tileserver")

if [ "${STOP_ALL}" = true ]; then
  while IFS= read -r cname; do
    [ -n "${cname}" ] && TARGETS+=("${cname}")
  done < <(docker ps -a --format '{{.Names}} {{.Image}}' | awk '$2 ~ /^cvemap(:|$)/ {print $1}')
fi

# Deduplicate target container names
declare -A SEEN=()
FOUND_ANY=false

for target in "${TARGETS[@]}"; do
  if [ -n "${SEEN[$target]+x}" ]; then
    continue
  fi
  SEEN[$target]=1

  if docker container inspect "${target}" >/dev/null 2>&1; then
    FOUND_ANY=true
    RUNNING="$(docker container inspect -f '{{.State.Running}}' "${target}" 2>/dev/null || echo false)"
    if [ "${RUNNING}" = "true" ]; then
      echo "==> Stopping container '${target}' ..."
      docker stop "${target}" >/dev/null
    fi
    echo "==> Removing container '${target}' ..."
    docker rm "${target}" >/dev/null
  fi
done

if [ "${FOUND_ANY}" = true ]; then
  echo "CVEMAP server stopped."
else
  echo "No CVEMAP containers are currently running."
fi
