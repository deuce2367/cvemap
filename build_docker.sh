#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# CVEMAP Unified Docker Image Builder
# ==============================================================================
# Builds the single-container CVEMAP image (FastAPI + TileServer-GL) in either
# 'lite' (mount data at runtime) or 'full' (map data baked in) mode.
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

TARGET="lite"
BUILD_BOTH=false
IMAGE_REPO="cvemap"
CUSTOM_TAG=""
DATA_SRC="app/data"
INCLUDE_ARCHIVES=""
CVEMAP_UID="${CVEMAP_UID:-4038}"
CVEMAP_GID="${CVEMAP_GID:-1000}"

show_help() {
  cat <<'EOF'
CVEMAP Docker Builder

Usage:
  ./build_docker.sh [options]

Targets:
  --lite                   Build 'cvemap:lite' (mount app/data at runtime) [default]
  --full                   Build 'cvemap:full' (bundles map data inside image)
  --all                    Build both 'cvemap:lite' and 'cvemap:full'

Options for --full:
  --data-dir <rel-path>    Relative directory in repo to bundle into /app/data (default: app/data)
  --include <f1,f2,...>    Stage only specific .pmtiles/.mbtiles filenames into the
                           full image instead of copying every archive in app/data/

General Options:
  --tag <image:tag>        Override output image tag
  --uid <uid>              Container cvemap user UID (default: 4038)
  --gid <gid>              Container cvemap group GID (default: 1000)
  -h, --help               Show this help message

Examples:
  # Build lightweight runtime image (fast, mounts ./app/data at runtime):
  ./build_docker.sh --lite

  # Build full self-contained offline image bundling all of ./app/data:
  ./build_docker.sh --full

  # Build full image bundling only a specific PMTiles archive:
  ./build_docker.sh --full --include monaco-z15.pmtiles --tag cvemap:monaco
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --lite)      TARGET="lite"; shift ;;
    --full)      TARGET="full"; shift ;;
    --all)       BUILD_BOTH=true; shift ;;
    --data-dir)  DATA_SRC="$2"; shift 2 ;;
    --include)   INCLUDE_ARCHIVES="$2"; shift 2 ;;
    --tag)       CUSTOM_TAG="$2"; shift 2 ;;
    --uid)       CVEMAP_UID="$2"; shift 2 ;;
    --gid)       CVEMAP_GID="$2"; shift 2 ;;
    -h|--help)   show_help; exit 0 ;;
    *) echo "Error: Unknown option '$1'. Run '$0 --help' for usage." >&2; exit 1 ;;
  esac
done

STAGE_DIR=""
cleanup_stage() {
  if [ -n "${STAGE_DIR}" ] && [ -d "${STAGE_DIR}" ]; then
    rm -rf "${STAGE_DIR}"
  fi
}
trap cleanup_stage EXIT

prepare_full_data_src() {
  if [ -z "${INCLUDE_ARCHIVES}" ]; then
    echo "${DATA_SRC}"
    return
  fi

  STAGE_DIR=".build_stage_data"
  rm -rf "${STAGE_DIR}"
  mkdir -p "${STAGE_DIR}"

  IFS=',' read -ra FILES <<< "${INCLUDE_ARCHIVES}"
  for raw_item in "${FILES[@]}"; do
    item="$(echo "${raw_item}" | xargs)"
    [ -z "${item}" ] && continue
    found="$(find "${DATA_SRC}" -type f -name "${item}" | head -n 1 || true)"
    if [ -z "${found}" ]; then
      echo "Error: Archive '${item}' not found under ${DATA_SRC}." >&2
      exit 1
    fi
    rel="${found#${DATA_SRC}/}"
    mkdir -p "${STAGE_DIR}/$(dirname "${rel}")"
    echo "==> Staging ${found} -> ${STAGE_DIR}/${rel}" >&2
    cp -L "${found}" "${STAGE_DIR}/${rel}"
  done

  echo "${STAGE_DIR}"
}

build_lite() {
  local tag="${1:-${IMAGE_REPO}:lite}"
  echo "==> Building unified lite image (${tag}) [cvemap ${CVEMAP_UID}:${CVEMAP_GID}] ..."
  DOCKER_BUILDKIT=1 docker build \
    --target lite \
    --build-arg "CVEMAP_UID=${CVEMAP_UID}" \
    --build-arg "CVEMAP_GID=${CVEMAP_GID}" \
    -t "${tag}" \
    -f Dockerfile \
    .
  echo "==> Built ${tag}"
}

build_full() {
  local tag="${1:-${IMAGE_REPO}:full}"
  local src
  src="$(prepare_full_data_src)"
  echo "==> Building unified full image (${tag}) from data source '${src}' [cvemap ${CVEMAP_UID}:${CVEMAP_GID}] ..."
  DOCKER_BUILDKIT=1 docker build \
    --target full \
    --build-arg "DATA_SRC=${src}" \
    --build-arg "CVEMAP_UID=${CVEMAP_UID}" \
    --build-arg "CVEMAP_GID=${CVEMAP_GID}" \
    -t "${tag}" \
    -f Dockerfile \
    .
  echo "==> Built ${tag}"
}

if [ "${BUILD_BOTH}" = true ]; then
  build_lite "${IMAGE_REPO}:lite"
  build_full "${CUSTOM_TAG:-${IMAGE_REPO}:full}"
elif [ "${TARGET}" = "full" ]; then
  build_full "${CUSTOM_TAG:-${IMAGE_REPO}:full}"
else
  build_lite "${CUSTOM_TAG:-${IMAGE_REPO}:lite}"
fi
