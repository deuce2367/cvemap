# Protomaps V4 Custom Map Generation Workflow

This guide explains how to generate highly optimized, minimized Protomaps Vector Tiles (PMTiles) using the modern **Protomaps V4 Schema**.

By generating the map from scratch using our customized Java profile (`planetiler`), we ensure:
1. **Perfect compatibility** with all 13 bundled Protomaps themes (`Protomaps Light`, `Protomaps Dark`, `Protomaps Bio`, `Protomaps Seafoam`, `Protomaps Sol`, etc.).
2. **Minimal file size** by stripping non-English labels, dropping extraneous metadata like `wikidata`, and slightly increasing the simplification tolerance.

## The Utility (`build_custom_map.sh`)

We provide an all-in-one utility that supports building from the cached global planet file, or from a specific regional PBF file that you provide.

**Syntax:**
```bash
./build_custom_map.sh --output <name> --maxzoom <zoom> [--pbf <file.osm.pbf>] [--bbox minlon,minlat,maxlon,maxlat]
```

### 1. Generating a Full Planet Map
To build a full planet map, provide the output name and the maximum zoom level. By omitting `--pbf`, it defaults to using the global planet file (and will auto-download the ~75GB `planet.osm.pbf` if it isn't already cached).

```bash
# Generates a full planet file up to zoom 12
./build_custom_map.sh --output my_planet --maxzoom 12
```

### 2. Generating from a Specific PBF File
If you have already downloaded a smaller regional file (e.g., from Geofabrik) to avoid pulling down the entire 75GB planet, pass it directly:

```bash
# Process a regional PBF
./build_custom_map.sh --output madagascar --maxzoom 14 --pbf data/sources/madagascar-latest.osm.pbf
```

### 3. Restricting to a Bounding Box
You can stack `--bbox` on top of any workflow to clip the output vector tiles to a specific rectangular region:

```bash
# Process only the Lagos region from an Africa PBF
./build_custom_map.sh --output lagos --maxzoom 14 --pbf data/sources/africa-latest.osm.pbf --bbox 3.0,6.3,3.6,6.7
```

## Automatic Discovery in CVEMAP
1. Bare output names (e.g. `--output lagos`) are written directly into `app/data/lagos.pmtiles`.
2. Refresh your browser—CVEMAP automatically inspects the PMTiles v3 header (`minZoom`, `maxZoom`, bounds, and schema version) and registers the archive in both the **Vector Viewer** (`/modern`) and **TileServer-GL** (**Raster Viewer** at `/`)!

## Bootstrapping in a New Environment

You do **not** need Java or Maven installed locally on your host:
- If `basemaps/tiles/target/protomaps-basemap-HEAD-with-deps.jar` is missing, `./build_custom_map.sh` automatically compiles it inside a `maven:3.9-eclipse-temurin-21` Docker container.
- If `java` is not installed locally, `./build_custom_map.sh` automatically runs Planetiler inside `eclipse-temurin:21-jre`.
- Alternatively, if you just want a ready-made global or regional basemap without compiling from raw OSM PBFs, run `./download_map_data.sh --help`!

### Launching CVEMAP
To build and launch the unified container (FastAPI + TileServer-GL):
```bash
# Build 'lite' image (mounts ./app/data at runtime) and launch:
./build_docker.sh --lite
./launch_cvemap.sh

# Or build 'full' image (bakes map data inside the image):
./build_docker.sh --full
./launch_cvemap.sh --full
```
- **Raster Viewer (XYZ & WMS)**: `http://localhost:8000/`
- **Vector Viewer (WebGL MVT)**: `http://localhost:8000/modern`
- **TileServer-GL Direct**: `http://localhost:8080/`
