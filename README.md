# CVEMAP

Self-contained, offline-ready GIS map server combining **MapServer (WMS & XYZ)**, **FastAPI**, **MapLibre GL JS (WebGL Vector / PMTiles)**, and **TileServer-GL (CPU Vector-to-Raster Renderer)** inside a **single unified Docker image**.

---

## Quickstart (Bootstrap from Scratch)

### 1. Clone the Repository
```bash
git clone <repo-url> cvemap
cd cvemap
```

### 2. Download Map Data
Use `./download_map_data.sh` to fetch ready-to-serve Protomaps `.pmtiles` vector basemaps (auto-bootstraps the `go-pmtiles` CLI if not installed), legacy Natural Earth vector layers, or Geofabrik `.osm.pbf` extracts:

```bash
# See all options and built-in region presets
./download_map_data.sh --help
./download_map_data.sh --list-regions

# Quick test region (~2 MB, downloads in a few seconds)
./download_map_data.sh --region monaco

# Regional or continental extract
./download_map_data.sh --region sf-bay --maxzoom 14
./download_map_data.sh --region europe --maxzoom 12

# Custom bounding box (minLon,minLat,maxLon,maxLat)
./download_map_data.sh --bbox -77.25,38.79,-76.90,39.02 --maxzoom 14 --name dc_metro

# Full global basemap (zoom 0-12, ~15 GB)
./download_map_data.sh --region planet --maxzoom 12
```

> Any `.pmtiles`, `.mbtiles`, `.shp`, `.fgb`, or `.geojson` placed under `app/data/` is **automatically discovered** on startup—no manual `config.json` editing required.

### 3. Build the Unified Docker Image (`lite` or `full`)
Both targets run **all software** (MapServer CGI, FastAPI, Xvfb, and TileServer-GL) in one container:

```bash
# Option A: "lite" image (fast build; mounts ./app/data at runtime)
./build_docker.sh --lite

# Option B: "full" image (bakes ./app/data into a self-contained offline image)
./build_docker.sh --full

# Optional: build a "full" image bundling only specific archive(s) + legacy world vectors
./build_docker.sh --full --include monaco-z15.pmtiles --tag cvemap:monaco
```

*(Or directly via Docker BuildKit: `docker build --target lite -t cvemap:lite .` / `docker build --target full -t cvemap:full .`)*

### 4. Launch CVEMAP
```bash
# Run cvemap:lite (mounts ./app/data at runtime)
./launch_cvemap.sh

# Or run cvemap:full standalone (no volume mounts required)
./launch_cvemap.sh --full
```

---

## Endpoints & Viewers

| Service / Endpoint | URL | Description |
| :--- | :--- | :--- |
| **Legacy Raster & WMS Viewer** | `http://localhost:8000/` | Leaflet viewer supporting both **XYZ** and **MapServer WMS** across Shapefiles, FlatGeobuf, MBTiles, and TileServer-GL styles |
| **Modern WebGL Vector Viewer** | `http://localhost:8000/modern` | Hardware-accelerated MapLibre GL JS viewer serving PMTiles & MVT with 13 offline Protomaps styles |
| **Admin Dashboard** | `http://localhost:8000/admin` | Layer styling, choropleths, labels, ordering, and Mapfile inspector (`admin` / `password`) |
| **WMS Endpoint** | `http://localhost:8000/wms/{map_id}` | Standard OGC WMS (`EPSG:3857` & `EPSG:4326`) for QGIS, ArcGIS, Leaflet, OpenLayers |
| **XYZ Endpoint** | `http://localhost:8000/xyz/{map_id}/{layer}/{z}/{x}/{y}.png` | Pre-rendered or MapServer/TileServer raster XYZ tiles |
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
