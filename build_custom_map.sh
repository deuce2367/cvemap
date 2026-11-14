#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_NAME=""
MAXZOOM=14
PBF_FILE=""
BBOX=""
JAVA_HEAP="8g"

show_help() {
    cat <<EOF
Usage: $0 --output <name> [--maxzoom <zoom>] [--pbf <file.osm.pbf>] [--bbox minlon,minlat,maxlon,maxlat] [--heap 8g]

Builds a custom Protomaps V4 vector basemap (.pmtiles) from raw OpenStreetMap PBF data.
Automatically compiles Planetiler via Docker if not yet built, and places bare output
filenames directly into app/data/ for instant auto-discovery by both the Raster Viewer
and Vector Viewer.

Examples:
  From regional PBF : ./build_custom_map.sh --output monaco --maxzoom 14 --pbf data/sources/monaco-latest.osm.pbf
  Bounding box clip : ./build_custom_map.sh --output lagos --maxzoom 14 --pbf data/sources/africa-latest.osm.pbf --bbox 3.0,6.3,3.6,6.7
  Full planet       : ./build_custom_map.sh --output my_planet --maxzoom 12 --heap 24g
EOF
    exit 1
}

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --output) OUTPUT_NAME="$2"; shift 2 ;;
        --maxzoom) MAXZOOM="$2"; shift 2 ;;
        --pbf) PBF_FILE="$2"; shift 2 ;;
        --bbox) BBOX="$2"; shift 2 ;;
        --heap) JAVA_HEAP="$2"; shift 2 ;;
        -h|--help) show_help ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
done

if [ -z "$OUTPUT_NAME" ]; then
    echo "Error: --output is required."
    show_help
fi

OUTPUT_FILE="$OUTPUT_NAME"
if [[ "$OUTPUT_FILE" != *.pmtiles && "$OUTPUT_FILE" != *.mbtiles ]]; then
    OUTPUT_FILE="${OUTPUT_FILE}.pmtiles"
fi
if [[ "$OUTPUT_FILE" != */* ]]; then
    mkdir -p "${SCRIPT_DIR}/app/data"
    OUTPUT_FILE="app/data/${OUTPUT_FILE}"
fi

PLANETILER_JAR="${SCRIPT_DIR}/basemaps/tiles/target/protomaps-basemap-HEAD-with-deps.jar"
if [ ! -f "$PLANETILER_JAR" ]; then
    echo "==> Planetiler JAR not found. Compiling basemaps/tiles via Maven Docker..."
    docker run --rm \
        -u "$(id -u):$(id -g)" \
        -v "${SCRIPT_DIR}/basemaps/tiles:/usr/src/app" \
        -w /usr/src/app \
        maven:3.9-eclipse-temurin-21 \
        mvn clean package -DskipTests
fi

ARGS=(
    "-Xmx${JAVA_HEAP}" "-jar" "basemaps/tiles/target/protomaps-basemap-HEAD-with-deps.jar"
    "--download"
    "--output=${OUTPUT_FILE}"
    "--maxzoom=${MAXZOOM}"
    "--force"
)

if [ -n "$PBF_FILE" ]; then
    echo "Using specific PBF file: $PBF_FILE"
    ARGS+=("--osm-path=${PBF_FILE}")
else
    echo "No --pbf specified. Using cached planet data (will auto-download if missing)."
    ARGS+=("--area=planet")
fi

if [ -n "$BBOX" ]; then
    echo "Restricting generation to bounding box: $BBOX"
    ARGS+=("--bounds=${BBOX}")
fi

cd "${SCRIPT_DIR}"
echo "Starting Planetiler..."
if command -v java >/dev/null 2>&1; then
    java "${ARGS[@]}"
else
    echo "Local 'java' not found; running Planetiler inside eclipse-temurin:21-jre container..."
    docker run --rm \
        -u "$(id -u):$(id -g)" \
        -v "${SCRIPT_DIR}:/work" \
        -w /work \
        eclipse-temurin:21-jre \
        java "${ARGS[@]}"
fi

echo "Done! Generated ${OUTPUT_FILE}"
