#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# CVEMAP Map Source Data Downloader
# ==============================================================================
# Friendly bootstrap utility to download ready-to-serve Protomaps PMTiles
# (full planet, named regions, or custom bounding boxes) or raw Geofabrik OSM
# PBFs / Planetiler auxiliary datasets for custom vector tile compilation.
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${SCRIPT_DIR}/app/data"
SOURCES_DIR="${SCRIPT_DIR}/data/sources"
GO_PMTILES_VERSION="1.28.1"

REGION=""
BBOX=""
MAXZOOM=""
OUTPUT_NAME=""
BUILD_DATE=""
SOURCE_URL=""
OSM_PBF_REGION=""
DRY_RUN=false
LIST_REGIONS=false
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
==========================

Usage:
  ./download_map_data.sh [options]

  Running './download_map_data.sh' with no region or workflow flags defaults to
  downloading the global Protomaps basemap ('--region planet', zoom 0-12, ~15 GB).

--------------------------------------------------------------------------------
WHICH WORKFLOW SHOULD I CHOOSE?
--------------------------------------------------------------------------------
  1. Protomaps PMTiles Basemap (DEFAULT - Recommended for almost everyone)
     Slices a ready-to-serve '.pmtiles' vector archive directly from Protomaps'
     daily OpenStreetMap builds into 'app/data/' (auto-downloads the lightweight
     'go-pmtiles' CLI if needed; no Java compilation required).
     One '.pmtiles' file powers BOTH viewers across all 13 Protomaps themes:
       - Raster Viewer (/)      : XYZ slippy tiles, OGC WMS, and OGC WMTS
       - Vector Viewer (/modern): Native WebGL MVT rendering via MapLibre GL JS

     Pick your geographic coverage:
       * Default ('planet') : Full world basemap when --region/--bbox is omitted.
       * '--region <name>'  : Pre-configured continent, country, or city extract
                              (e.g. 'monaco' is ~2 MB for a 5-second smoke test).
       * '--bbox <w,s,e,n>' : Custom lon/lat rectangle for a specific area of
                              interest when no built-in preset fits.

  2. Raw OSM PBF + Planetiler ('--osm-pbf' & '--planetiler-sources' - Advanced)
     NOT needed to run CVEMAP. These flags download raw OpenStreetMap '.osm.pbf'
     dumps from Geofabrik and coastline/land auxiliary datasets into
     'data/sources/' so you can compile custom vector tiles from scratch using
     './build_custom_map.sh' (Java/Planetiler). Choose this only if you are
     customizing the vector tile schema itself or have custom '.osm.pbf' inputs.

--------------------------------------------------------------------------------
CHOOSING '--maxzoom' (DETAIL vs. DOWNLOAD SIZE)
--------------------------------------------------------------------------------
  Each +1 zoom level roughly quadruples (~4x) archive size for a given area.
  Viewers still let you zoom in past maxzoom by scaling the highest-zoom tiles.
    z10  Regional / highway overview       (~1.5 GB for full planet)
    z12  City, town & arterial road detail (~15 GB  for full planet - DEFAULT)
    z13  Neighborhood & secondary streets  (default for large countries)
    z14  Street-level & building footprints (default for metro presets; ~60 GB+ planet)
    z15  Finest building & POI detail       (default for tiny presets like monaco)

--------------------------------------------------------------------------------
OPTIONS
--------------------------------------------------------------------------------
  Protomaps PMTiles Options (default workflow):
    --region <name>          Named geographic preset to extract into app/data/.
                             Defaults to 'planet' if neither --region, --bbox,
                             nor another workflow flag is specified.
                             Run --list-regions to see all presets.
    --bbox <w,s,e,n>         Extract a custom bounding box instead of a named
                             preset, formatted as minLon,minLat,maxLon,maxLat in
                             WGS84 degrees (e.g. -77.25,38.79,-76.90,39.02).
    --list-regions           Print all built-in --region presets with their
                             bounding boxes and default maxzoom, then exit.
    --maxzoom <0-15>         Maximum tile zoom level to include. Overrides the
                             preset's default (z12 for planet/continents, z13-15
                             for countries/cities). Lower = much smaller file.
    --name <filename>        Custom output filename in app/data/
                             (e.g. 'dc_metro' -> dc_metro.pmtiles). Defaults to
                             '<region>-z<maxzoom>.pmtiles'.
    --build-date <YYYYMMDD>  Pin a specific Protomaps daily build date instead of
                             auto-detecting the latest published build. Useful
                             for reproducible team/offline builds.
    --source-url <url>       Extract from a custom remote or local PMTiles URL/path
                             instead of https://build.protomaps.com/.

  Advanced Custom Tile Compilation Inputs (for ./build_custom_map.sh):
    --osm-pbf <geofabrik>    Download a raw Geofabrik OpenStreetMap extract into
                             data/sources/ (e.g. 'europe/monaco', 'north-america/us/california').
                             Does NOT immediately appear in CVEMAP until compiled
                             with ./build_custom_map.sh.
    --planetiler-sources     Pre-download auxiliary coastline, water polygon,
                             Natural Earth, and Wikidata QRank archives (~2.5 GB)
                             into data/sources/ required by Planetiler builds.

  General Options:
    --data-dir <path>        Override target CVEMAP runtime data directory
                             (default: ./app/data).
    --dry-run                Print the resolved URLs and commands that would be
                             executed without downloading any files.
    -h, --help               Show this help guide and exit.

--------------------------------------------------------------------------------
EXAMPLES
--------------------------------------------------------------------------------
  # 1. Default: Full global planet basemap (zoom 0-12, ~15 GB):
  ./download_map_data.sh

  # 2. Lighter global planet basemap (zoom 0-10, ~1.5 GB):
  ./download_map_data.sh --maxzoom 10

  # 3. Fast smoke-test extract (~2 MB, finishes in seconds):
  ./download_map_data.sh --region monaco

  # 4. High-detail metro or regional basemap:
  ./download_map_data.sh --region sf-bay --maxzoom 14
  ./download_map_data.sh --region europe --maxzoom 12

  # 5. Custom bounding box (Washington, D.C. metro area):
  ./download_map_data.sh --bbox -77.25,38.79,-76.90,39.02 --maxzoom 14 --name dc_metro

  # 6. Advanced: Fetch raw OSM PBF + Planetiler inputs for custom Java builds:
  ./download_map_data.sh --osm-pbf europe/monaco --planetiler-sources
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

download_osm_pbf() {
  local region_slug="${OSM_PBF_REGION%.osm.pbf}"
  region_slug="${region_slug%-latest}"
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

PMTILES_OPT_SET=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --region)              REGION="$2"; PMTILES_OPT_SET=true; shift 2 ;;
    --bbox)                BBOX="$2"; PMTILES_OPT_SET=true; shift 2 ;;
    --maxzoom)             MAXZOOM="$2"; PMTILES_OPT_SET=true; shift 2 ;;
    --name|--output)       OUTPUT_NAME="$2"; PMTILES_OPT_SET=true; shift 2 ;;
    --build-date)          BUILD_DATE="$2"; PMTILES_OPT_SET=true; shift 2 ;;
    --source-url)          SOURCE_URL="$2"; PMTILES_OPT_SET=true; shift 2 ;;
    --data-dir)            DATA_DIR="$2"; shift 2 ;;
    --osm-pbf)             OSM_PBF_REGION="$2"; shift 2 ;;
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

DID_OTHER_WORK=false

if [ -n "$OSM_PBF_REGION" ]; then
  download_osm_pbf
  DID_OTHER_WORK=true
fi

if [ "$FETCH_PLANETILER_SOURCES" = true ]; then
  download_planetiler_sources
  DID_OTHER_WORK=true
fi

# Default region to 'planet' when running the PMTiles workflow without --region or --bbox
if [ "$PMTILES_OPT_SET" = true ] || [ "$DID_OTHER_WORK" = false ]; then
  if [ -z "$REGION" ] && [ -z "$BBOX" ]; then
    REGION="planet"
    echo "==> No --region or --bbox specified; defaulting to '--region planet'."
  fi
fi

# Run PMTiles extraction when requested or by default
if [ -n "$REGION" ] || [ -n "$BBOX" ]; then
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

  mkdir -p "${DATA_DIR}"
  DEST_PATH="${DATA_DIR}/${OUTPUT_NAME}"

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

cat <<EOF

Next steps:
  1. Build & run the 'lite' image (mounts ${DATA_DIR} at runtime):
       ./build_docker.sh --lite
       ./launch_cvemap.sh

  2. Or build a self-contained 'full' image with map data baked in:
       ./build_docker.sh --full
       ./launch_cvemap.sh --full

  3. Stop the running server anytime:
       ./stop_cvemap.sh
EOF
