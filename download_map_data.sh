#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# CVEMAP Map Source Data Downloader
# ==============================================================================
# Friendly bootstrap utility to download ready-to-serve Protomaps PMTiles
# (full planet, named regions, or custom bounding boxes), Legacy Natural Earth
# vector layers, or raw Geofabrik OSM PBFs / Planetiler auxiliary datasets.
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${SCRIPT_DIR}/app/data"
SOURCES_DIR="${SCRIPT_DIR}/data/sources"
GO_PMTILES_VERSION="1.28.1"

MODE="pmtiles"
REGION=""
BBOX=""
MAXZOOM=""
OUTPUT_NAME=""
BUILD_DATE=""
SOURCE_URL=""
OSM_PBF_REGION=""
DRY_RUN=false
LIST_REGIONS=false
FETCH_LEGACY=false
FETCH_PLANETILER_SOURCES=false

# Named region bounding boxes (minLon,minLat,maxLon,maxLat|defaultMaxZoom)
declare -A REGION_PRESETS=(
  ["planet"]="planet|12"
  ["monaco"]="7.409,43.723,7.442,43.753|15"
  ["sf-bay"]="-122.991,37.237,-121.526,38.252|14"
  ["nyc"]="-74.300,40.450,-73.650,40.950|14"
  ["us-northeast"]="-79.800,38.800,-66.900,47.500|13"
  ["us-continental"]="-125.000,24.400,-66.900,49.500|12"
  ["central-america"]="-97.573,-9.773,-37.531,44.255|12"
  ["europe"]="-11.000,35.000,32.000,71.500|12"
  ["uk-ireland"]="-10.800,49.800,2.000,60.900|13"
  ["madagascar"]="43.100,-25.700,50.600,-11.900|14"
  ["japan"]="128.900,30.000,146.000,45.700|13"
  ["australia"]="112.500,-44.000,154.000,-10.000|12"
)

show_help() {
  cat <<'EOF'
CVEMAP Map Data Downloader

Usage:
  ./download_map_data.sh [options]

Examples:
  # 1. Quick smoke-test map (~2 MB, downloads in seconds):
  ./download_map_data.sh --region monaco

  # 2. Regional vector basemap (serves both WebGL Viewer & TileServer-GL raster/WMS):
  ./download_map_data.sh --region sf-bay --maxzoom 14
  ./download_map_data.sh --region europe --maxzoom 12

  # 3. Custom bounding box (minLon,minLat,maxLon,maxLat):
  ./download_map_data.sh --bbox -77.25,38.79,-76.90,39.02 --maxzoom 14 --name dc_metro

  # 4. Full global planet basemap (zoom 0-12, ~15 GB):
  ./download_map_data.sh --region planet --maxzoom 12

  # 5. Verify/download Legacy Natural Earth world vector layers:
  ./download_map_data.sh --legacy-world

  # 6. Download raw Geofabrik OSM PBF + Planetiler sources for custom Java builds:
  ./download_map_data.sh --osm-pbf europe/monaco --planetiler-sources

Modes & Presets:
  --region <name>          Extract a named region preset (see --list-regions)
  --bbox <w,s,e,n>         Extract a custom bounding box (minLon,minLat,maxLon,maxLat)
  --list-regions           List all built-in named region presets and exit
  --legacy-world           Ensure Legacy Viewer world vector layers exist in app/data/legacy/world/
  --osm-pbf <geofabrik>    Download a Geofabrik .osm.pbf (e.g. 'monaco', 'africa/madagascar')
  --planetiler-sources     Download auxiliary datasets into data/sources/ for build_custom_map.sh

PMTiles Extract Options:
  --maxzoom <0-15>         Maximum zoom level (default: 12 for planet/continents, 14 for metro regions)
  --name <filename>        Output name in app/data/webgl/world/ (default: auto-named from region)
  --build-date <YYYYMMDD>  Specific Protomaps daily build date (default: auto-detect latest)
  --source-url <url>       Override remote PMTiles archive URL
  --data-dir <path>        Target CVEMAP data directory (default: ./app/data)
  --dry-run                Show commands that would run without downloading files
  -h, --help               Show this help message
EOF
}

list_regions() {
  echo "Available --region presets:"
  printf "  %-18s %-36s %s\n" "REGION" "BOUNDING BOX (W,S,E,N)" "DEFAULT MAXZOOM"
  printf "  %-18s %-36s %s\n" "------" "----------------------" "---------------"
  for key in $(printf "%s\n" "${!REGION_PRESETS[@]}" | sort); do
    IFS='|' read -r bbox def_z <<< "${REGION_PRESETS[$key]}"
    printf "  %-18s %-36s z%s\n" "$key" "$bbox" "$def_z"
  done
}

run_cmd() {
  if [ "$DRY_RUN" = true ]; then
    echo "[dry-run] $*"
  else
    "$@"
  fi
}

ensure_pmtiles_cli() {
  if [ -x "${SCRIPT_DIR}/pmtiles" ]; then
    echo "${SCRIPT_DIR}/pmtiles"
    return
  fi
  if [ -x "${SCRIPT_DIR}/bin/pmtiles" ]; then
    echo "${SCRIPT_DIR}/bin/pmtiles"
    return
  fi
  if command -v pmtiles >/dev/null 2>&1; then
    command -v pmtiles
    return
  fi

  echo "==> Bootstrapping go-pmtiles v${GO_PMTILES_VERSION} CLI into ./bin/pmtiles ..." >&2
  local os arch archive_url
  os="$(uname -s)"
  arch="$(uname -m)"
  case "${os}" in
    Linux)  os="Linux" ;;
    Darwin) os="Darwin" ;;
    *) echo "Error: Unsupported OS '${os}' for automatic go-pmtiles download." >&2; exit 1 ;;
  esac
  case "${arch}" in
    x86_64|amd64)  arch="x86_64" ;;
    aarch64|arm64) arch="arm64" ;;
    *) echo "Error: Unsupported architecture '${arch}'." >&2; exit 1 ;;
  esac

  archive_url="https://github.com/protomaps/go-pmtiles/releases/download/v${GO_PMTILES_VERSION}/go-pmtiles_${GO_PMTILES_VERSION}_${os}_${arch}.tar.gz"
  if [ "$DRY_RUN" = true ]; then
    echo "[dry-run] curl -fSL ${archive_url} | tar -xz -C ${SCRIPT_DIR}/bin pmtiles" >&2
    echo "${SCRIPT_DIR}/bin/pmtiles"
    return
  fi

  mkdir -p "${SCRIPT_DIR}/bin"
  curl -fSL "${archive_url}" | tar -xz -C "${SCRIPT_DIR}/bin" pmtiles
  chmod +x "${SCRIPT_DIR}/bin/pmtiles"
  echo "${SCRIPT_DIR}/bin/pmtiles"
}

resolve_latest_protomaps_url() {
  if [ -n "$SOURCE_URL" ]; then
    echo "$SOURCE_URL"
    return
  fi
  if [ -n "$BUILD_DATE" ]; then
    echo "https://build.protomaps.com/${BUILD_DATE}.pmtiles"
    return
  fi
  echo "==> Resolving latest Protomaps daily build metadata..." >&2
  local latest_key
  latest_key="$(curl -fsSL https://build-metadata.protomaps.dev/builds.json | grep -oE '"key":"[0-9]{8}\.pmtiles"' | tail -n 1 | cut -d'"' -f4 || true)"
  if [ -z "$latest_key" ]; then
    echo "Error: Could not auto-detect latest Protomaps build. Pass --build-date YYYYMMDD." >&2
    exit 1
  fi
  echo "https://build.protomaps.com/${latest_key}"
}

download_legacy_world() {
  local world_dir="${DATA_DIR}/legacy/world"
  mkdir -p "${world_dir}"
  if [ -f "${world_dir}/geoBoundariesCGAZ_ADM0.shp" ] || [ -f "${world_dir}/ne_110m_admin_0_countries.shp" ]; then
    echo "==> Legacy world vector layers already present in ${world_dir}."
    return
  fi
  echo "==> Downloading Natural Earth 1:110m Admin 0 Countries & Populated Places into ${world_dir} ..."
  local countries_url="https://naciscdn.org/naturalearth/110m/cultural/ne_110m_admin_0_countries.zip"
  local places_url="https://naciscdn.org/naturalearth/50m/cultural/ne_50m_populated_places_simple.zip"
  if [ "$DRY_RUN" = true ]; then
    echo "[dry-run] curl -fSL ${countries_url} -> ${world_dir}"
    echo "[dry-run] curl -fSL ${places_url} -> ${world_dir}"
    return
  fi
  local tmp_zip
  for zip_url in "${countries_url}" "${places_url}"; do
    tmp_zip="$(mktemp /tmp/ne_legacy_XXXXXX.zip)"
    curl -fSL "${zip_url}" -o "${tmp_zip}"
    python3 -m zipfile -e "${tmp_zip}" "${world_dir}"
    rm -f "${tmp_zip}"
  done
  echo "==> Legacy world vector layers ready in ${world_dir}."
}

download_osm_pbf() {
  local region_slug="${OSM_PBF_REGION%.osm.pbf}"
  region_slug="${region_slug%-latest}"
  # Convenience alias if 'monaco' passed instead of 'europe/monaco'
  if [ "$region_slug" = "monaco" ]; then
    region_slug="europe/monaco"
  elif [ "$region_slug" = "madagascar" ]; then
    region_slug="africa/madagascar"
  fi
  local base_name
  base_name="$(basename "${region_slug}")-latest.osm.pbf"
  local url="https://download.geofabrik.de/${region_slug}-latest.osm.pbf"
  mkdir -p "${SOURCES_DIR}"
  echo "==> Downloading Geofabrik OSM PBF: ${url}"
  echo "    Destination: ${SOURCES_DIR}/${base_name}"
  run_cmd curl -fL --progress-bar "${url}" -o "${SOURCES_DIR}/${base_name}"
  echo "==> Saved ${SOURCES_DIR}/${base_name}"
}

download_planetiler_sources() {
  mkdir -p "${SOURCES_DIR}"
  echo "==> Downloading auxiliary Planetiler datasets into ${SOURCES_DIR} ..."
  local -a urls=(
    "https://naciscdn.org/naturalearth/packages/natural_earth_vector.gpkg.zip"
    "https://osmdata.openstreetmap.de/download/water-polygons-split-3857.zip"
    "https://osmdata.openstreetmap.de/download/land-polygons-split-3857.zip"
    "https://qrank.wmcloud.org/download/qrank.csv.gz"
  )
  for u in "${urls[@]}"; do
    local fname
    fname="$(basename "$u")"
    if [ -f "${SOURCES_DIR}/${fname}" ]; then
      echo "    [skip] ${fname} already exists."
    else
      echo "    Fetching ${fname} ..."
      run_cmd curl -fL --progress-bar "$u" -o "${SOURCES_DIR}/${fname}"
    fi
  done
}

# Show help if invoked with no arguments
if [ "$#" -eq 0 ]; then
  show_help
  exit 0
fi

while [ "$#" -gt 0 ]; do
  case "$1" in
    --region)              REGION="$2"; shift 2 ;;
    --bbox)                BBOX="$2"; shift 2 ;;
    --maxzoom)             MAXZOOM="$2"; shift 2 ;;
    --name|--output)       OUTPUT_NAME="$2"; shift 2 ;;
    --build-date)          BUILD_DATE="$2"; shift 2 ;;
    --source-url)          SOURCE_URL="$2"; shift 2 ;;
    --data-dir)            DATA_DIR="$2"; shift 2 ;;
    --osm-pbf)             OSM_PBF_REGION="$2"; shift 2 ;;
    --legacy-world)        FETCH_LEGACY=true; shift ;;
    --planetiler-sources)  FETCH_PLANETILER_SOURCES=true; shift ;;
    --list-regions)        LIST_REGIONS=true; shift ;;
    --dry-run)             DRY_RUN=true; shift ;;
    -h|--help)             show_help; exit 0 ;;
    *) echo "Error: Unknown argument '$1'. Run '$0 --help' for usage." >&2; exit 1 ;;
  esac
done

if [ "$LIST_REGIONS" = true ]; then
  list_regions
  exit 0
fi

DID_WORK=false

if [ "$FETCH_LEGACY" = true ]; then
  download_legacy_world
  DID_WORK=true
fi

if [ -n "$OSM_PBF_REGION" ]; then
  download_osm_pbf
  DID_WORK=true
fi

if [ "$FETCH_PLANETILER_SOURCES" = true ]; then
  download_planetiler_sources
  DID_WORK=true
fi

# Handle PMTiles extraction if --region, --bbox, --source-url, or --build-date specified
if [ -n "$REGION" ] || [ -n "$BBOX" ] || [ -n "$SOURCE_URL" ] || [ -n "$BUILD_DATE" ]; then
  DID_WORK=true
  DEF_ZOOM=14
  if [ -n "$REGION" ]; then
    if [ -z "${REGION_PRESETS[$REGION]+x}" ]; then
      echo "Error: Unknown region preset '${REGION}'." >&2
      list_regions >&2
      exit 1
    fi
    IFS='|' read -r preset_bbox preset_zoom <<< "${REGION_PRESETS[$REGION]}"
    DEF_ZOOM="$preset_zoom"
    if [ -z "$BBOX" ] && [ "$preset_bbox" != "planet" ]; then
      BBOX="$preset_bbox"
    fi
  fi

  FINAL_ZOOM="${MAXZOOM:-$DEF_ZOOM}"
  if [ -z "$OUTPUT_NAME" ]; then
    if [ -n "$REGION" ]; then
      OUTPUT_NAME="${REGION}-z${FINAL_ZOOM}.pmtiles"
    else
      OUTPUT_NAME="custom-z${FINAL_ZOOM}.pmtiles"
    fi
  fi
  if [[ "$OUTPUT_NAME" != *.pmtiles ]]; then
    OUTPUT_NAME="${OUTPUT_NAME}.pmtiles"
  fi

  DEST_DIR="${DATA_DIR}/webgl/world"
  DEST_PATH="${DEST_DIR}/${OUTPUT_NAME}"
  mkdir -p "${DEST_DIR}"

  PMTILES_BIN="$(ensure_pmtiles_cli)"
  REMOTE_URL="$(resolve_latest_protomaps_url)"

  EXTRACT_ARGS=("extract" "${REMOTE_URL}" "${DEST_PATH}" "--maxzoom=${FINAL_ZOOM}")
  if [ -n "$BBOX" ]; then
    EXTRACT_ARGS+=("--bbox=${BBOX}")
  fi

  echo "=================================================================="
  echo " Protomaps PMTiles Extraction"
  echo "   Source  : ${REMOTE_URL}"
  echo "   Region  : ${REGION:-custom}"
  [ -n "$BBOX" ] && echo "   BBox    : ${BBOX}"
  echo "   MaxZoom : ${FINAL_ZOOM}"
  echo "   Output  : ${DEST_PATH}"
  echo "=================================================================="

  run_cmd "${PMTILES_BIN}" "${EXTRACT_ARGS[@]}"
  echo ""
  echo "==> Map archive ready: ${DEST_PATH}"
fi

if [ "$DID_WORK" = false ]; then
  echo "Nothing selected to download."
  show_help
  exit 1
fi

cat <<EOF

Next steps:
  1. Build & run the 'lite' image (mounts ${DATA_DIR} at runtime):
       ./build_docker.sh --lite
       ./launch_cvemap.sh

  2. Or build a self-contained 'full' image with map data baked in:
       ./build_docker.sh --full
       ./launch_cvemap.sh --full
EOF
