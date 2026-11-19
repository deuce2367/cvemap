# CVEMAP

Self-contained, offline-ready GIS map server combining **TileServer-GL (Raster & Vector Tile Engine)**, **FastAPI (XYZ & OGC WMS Gateway)**, and **MapLibre GL JS / Leaflet** inside a **single unified Docker image**.

---

## Quickstart (Bootstrap from Scratch)

### 1. Clone the Repository
```bash
git clone <repo-url> cvemap
cd cvemap
```

### 2. Download Map Data
Use `./download_map_data.sh` to fetch ready-to-serve Protomaps `.pmtiles` vector basemaps (auto-bootstraps the `go-pmtiles` CLI if not installed) or Geofabrik `.osm.pbf` extracts. If `--region` is omitted, it defaults to **`planet`**:

```bash
# Detailed guide explaining every option & workflow
./download_map_data.sh --help
./download_map_data.sh --list-regions

# Default: Full global planet basemap (zoom 0-12, ~15 GB)
./download_map_data.sh

# Quick test region (~2 MB, downloads in a few seconds)
./download_map_data.sh --region monaco

# Regional or continental extract
./download_map_data.sh --region sf-bay --maxzoom 14
./download_map_data.sh --region europe --maxzoom 12

# Custom bounding box (minLon,minLat,maxLon,maxLat)
./download_map_data.sh --bbox -77.25,38.79,-76.90,39.02 --maxzoom 14 --name dc_metro

# Verify integrity of any downloaded .pmtiles archive
./download_map_data.sh --verify app/data/planet-z12.pmtiles
```

> Downloads stream over resilient chunked HTTP/1.1 with automatic retry, `.part` staging, byte-level resume across interruptions, and post-download integrity verification. Any `.pmtiles` or vector `.mbtiles` placed under `app/data/` is **automatically discovered** on the fly—no manual configuration required.

### 3. Build the Unified Docker Image (`lite` or `full`)
Both targets run **FastAPI + Xvfb + TileServer-GL** in a single container:

```bash
# Option A: "lite" image (fast build; mounts ./app/data at runtime)
./build_docker.sh --lite

# Option B: "full" image (bakes ./app/data into a self-contained offline image)
./build_docker.sh --full

# Optional: build a "full" image bundling only specific archive(s)
./build_docker.sh --full --include monaco-z15.pmtiles --tag cvemap:monaco
```

*(Or directly via Docker BuildKit: `docker build --target lite -t cvemap:lite .` / `docker build --target full -t cvemap:full .`)*

### 4. Launch & Stop CVEMAP
```bash
# Run cvemap:lite (mounts ./app/data at runtime)
./launch_cvemap.sh

# Or run cvemap:full standalone (no volume mounts required)
./launch_cvemap.sh --full

# Stop the running CVEMAP server container
./stop_cvemap.sh
```

---

## Viewers & Endpoints

| Service / Endpoint | URL | Description |
| :--- | :--- | :--- |
| **Raster Viewer** | `http://localhost:8000/` | Leaflet viewer serving all discovered `.pmtiles` and `.mbtiles` datasets across all 25 self-contained offline Protomaps, OpenMapTiles & Nextzen basemaps over **XYZ** and **OGC WMS** (`EPSG:3857` & `EPSG:4326`) |
| **Vector Viewer** | `http://localhost:8000/modern` | Hardware-accelerated MapLibre GL JS viewer rendering native PMTiles & MVT vector tiles across all 25 offline basemaps |
| **Style JSON Endpoint** | `http://localhost:8000/api/style/{dataset}/{style}.json` | Self-contained offline MapLibre GL JS style JSON (with local fonts, sprites, and vector sources) for any dataset & style |
| **WMS Endpoint** | `http://localhost:8000/wms` | OGC WMS `1.1.1` & `1.3.0` (`GetCapabilities` & `GetMap` in `EPSG:3857` & `EPSG:4326`, supporting `LAYERS={style}` or `LAYERS={dataset}__{style}`) |
| **XYZ Endpoint** | `http://localhost:8000/xyz/{dataset}/{style}/{z}/{x}/{y}.png` | Standard slippy raster PNG tiles for any dataset & style (also `/xyz/{style}/{z}/{x}/{y}.png` for the primary dataset) |
| **WMTS Endpoint** | `http://localhost:8000/tileserver/styles/{dataset}__{style}/wmts.xml` | OGC WMTS capabilities for each dataset & basemap style (also `/tileserver/styles/{style}/wmts.xml`) |
| **TileServer-GL Direct** | `http://localhost:8080/` | Direct TileServer-GL dashboard & endpoints (also proxied on `:8000/tileserver/`) |

---

## Custom OSM PBF Map Generation (Optional)

If you want to generate custom `.pmtiles` from raw OpenStreetMap `.osm.pbf` extracts instead of extracting from Protomaps daily builds:

```bash
# 1. Download a Geofabrik regional PBF (and optional Planetiler source layers)
./download_map_data.sh --osm-pbf europe/monaco --planetiler-sources

# 2. Build custom PMTiles (auto-compiles Planetiler via Docker if needed)
./build_custom_map.sh --output monaco_custom --maxzoom 14 --pbf data/sources/monaco-latest.osm.pbf
```

See [PROTOMAPS_WORKFLOW.md](PROTOMAPS_WORKFLOW.md) for advanced Planetiler customization details.
