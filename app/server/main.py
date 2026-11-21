import concurrent.futures
import functools
import io
import logging
import math
import os
import sqlite3
import urllib.error
import urllib.request
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image

from server.tileserver_sync import (
    ALL_STYLES,
    OPENMAPTILES_STYLE_DEFS,
    OPENMAPTILES_STYLES,
    TILESERVER_STYLES,
    build_browser_style_json,
    discover_vector_datasets,
    format_style_title,
    inspect_pmtiles,
    sanitize_tileserver_id,
    select_primary_pmtiles,
    sync_tileserver_runtime,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="CVEMAP")

DATA_DIR = os.path.abspath("data")
TILESERVER_TEMPLATE_DIR = (
    "/app/tileserver"
    if os.path.isdir("/app/tileserver")
    else os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tileserver"))
)
TILESERVER_INTERNAL_URL = os.environ.get("TILESERVER_INTERNAL_URL", "http://127.0.0.1:8080").rstrip("/")

ORIGIN_SHIFT = 20037508.342789244
WORLD_SPAN = 2.0 * ORIGIN_SHIFT
MAX_MERCATOR_LAT = 85.05112878

_TILE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8)


def _sync_if_needed() -> str:
    try:
        sync_tileserver_runtime(data_dir=DATA_DIR, reload_process=True)
    except Exception as exc:
        logger.warning("TileServer runtime sync skipped: %s", exc)
    return select_primary_pmtiles(DATA_DIR)


@app.on_event("startup")
def startup_event() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    _sync_if_needed()


@app.middleware("http")
async def disable_sprite_and_theme_cache(request: Request, call_next):
    response = await call_next(request)
    p = request.url.path
    if p.startswith("/static/vendor/protomaps-assets/sprites/") or p.startswith("/static/vendor/openglobus/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


os.makedirs(DATA_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")


@app.get("/", response_class=HTMLResponse)
def raster_viewer() -> HTMLResponse:
    index_path = os.path.abspath("static/index.html")
    with open(index_path, "r") as f:
        return HTMLResponse(
            content=f.read(),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )


@app.get("/modern", response_class=HTMLResponse)
def vector_viewer() -> HTMLResponse:
    modern_path = os.path.abspath("static/modern.html")
    with open(modern_path, "r") as f:
        return HTMLResponse(
            content=f.read(),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )


@app.get("/globe", response_class=HTMLResponse)
@app.get("/openglobus", response_class=HTMLResponse)
def globe_viewer() -> HTMLResponse:
    globe_path = os.path.abspath("static/globe.html")
    with open(globe_path, "r") as f:
        return HTMLResponse(
            content=f.read(),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )


@app.get("/api/maps")
def api_get_maps() -> JSONResponse:
    primary_path = _sync_if_needed()
    datasets = discover_vector_datasets(DATA_DIR)

    primary_dataset = None
    if primary_path and os.path.isfile(primary_path):
        rel_primary = os.path.relpath(primary_path, DATA_DIR)
        primary_dataset = next((d for d in datasets if d["path"] == rel_primary), None)
        if primary_dataset is None:
            pm_info = inspect_pmtiles(primary_path)
            base_name = os.path.splitext(os.path.basename(primary_path))[0]
            primary_dataset = {
                "name": base_name,
                "ts_key": sanitize_tileserver_id(base_name),
                "path": rel_primary,
                "minZoom": pm_info["minZoom"],
                "maxZoom": pm_info["maxZoom"],
                "bounds": pm_info.get("bounds"),
                "pmap_prefix": pm_info["pmap_prefix"],
            }
    if primary_dataset is None and datasets:
        primary_dataset = datasets[0]

    sorted_all = sorted(ALL_STYLES, key=lambda s: format_style_title(s))
    return JSONResponse(
        content={
            "datasets": datasets,
            "primary_dataset": primary_dataset,
            "tileserver_styles": TILESERVER_STYLES,
            "openmaptiles_styles": [
                {"id": st_id, "title": meta["title"]}
                for st_id, meta in OPENMAPTILES_STYLE_DEFS.items()
            ],
            "all_styles": [
                {"id": st_id, "title": format_style_title(st_id)}
                for st_id in sorted_all
            ],
        }
    )


@app.get("/api/style/{dataset}/{style}.json")
def api_get_browser_style(dataset: str, style: str, request: Request) -> JSONResponse:
    base_origin = str(request.base_url).rstrip("/")
    style_obj = build_browser_style_json(
        data_dir=DATA_DIR,
        tileserver_template_dir=TILESERVER_TEMPLATE_DIR,
        dataset_name=dataset,
        style_id=style,
        base_origin=base_origin,
    )
    if style_obj is None:
        raise HTTPException(status_code=404, detail="No vector dataset or style available")
    return JSONResponse(
        content=style_obj,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


def _resolve_tileserver_style(style: str, dataset: str | None = None) -> str:
    datasets = discover_vector_datasets(DATA_DIR)
    if not dataset and "__" in style:
        dataset, style = style.split("__", 1)

    st = style if style in ALL_STYLES else "light"

    if dataset:
        ds = next(
            (d for d in datasets if d["name"] == dataset or d.get("ts_key") == dataset),
            None,
        )
        if ds:
            return f"{ds['ts_key']}__{st}"

    return st


def proxy_internal_url(url: str) -> Response:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CVEMAP-Proxy/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            ctype = resp.headers.get("Content-Type", "application/octet-stream")
            return Response(content=body, media_type=ctype)
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=exc.code, detail=exc.reason)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"TileServer upstream unavailable: {exc}")


@app.get("/tileserver/{path:path}")
def tileserver_proxy(path: str, request: Request) -> Response:
    qs = f"?{request.url.query}" if request.url.query else ""
    return proxy_internal_url(f"{TILESERVER_INTERNAL_URL}/{path}{qs}")


@app.get("/xyz/{style}/{z}/{x}/{y}.png")
def xyz_style_endpoint(style: str, z: int, x: int, y: int, dataset: str | None = None) -> Response:
    ts_style = _resolve_tileserver_style(style, dataset=dataset)
    num_tiles = max(1, 1 << z)
    return proxy_internal_url(f"{TILESERVER_INTERNAL_URL}/styles/{ts_style}/{z}/{x % num_tiles}/{y}.png")


@app.get("/xyz/{dataset}/{style}/{z}/{x}/{y}.png")
def xyz_dataset_style_endpoint(dataset: str, style: str, z: int, x: int, y: int) -> Response:
    ts_style = _resolve_tileserver_style(style, dataset=dataset)
    num_tiles = max(1, 1 << z)
    return proxy_internal_url(f"{TILESERVER_INTERNAL_URL}/styles/{ts_style}/{z}/{x % num_tiles}/{y}.png")


# ==============================================================================
# Pure-Python OGC WMS 1.1.1 / 1.3.0 Endpoint (backed by TileServer-GL)
# ==============================================================================


@functools.lru_cache(maxsize=512)
def _fetch_tile_bytes_cached(style: str, z: int, x: int, y: int) -> bytes:
    url = f"{TILESERVER_INTERNAL_URL}/styles/{style}/{z}/{x}/{y}.png"
    req = urllib.request.Request(url, headers={"User-Agent": "CVEMAP-WMS/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def _fetch_tile_bytes(style: str, z: int, x: int, y: int) -> bytes | None:
    try:
        return _fetch_tile_bytes_cached(style, z, x, y)
    except Exception as exc:
        logger.debug("WMS tile fetch failed for %s/%d/%d/%d: %s", style, z, x, y, exc)
        return None


def _lon_to_mercator_x(lon: float) -> float:
    return (lon / 180.0) * ORIGIN_SHIFT


def _lat_to_mercator_y(lat: float) -> float:
    clamped = max(-MAX_MERCATOR_LAT, min(MAX_MERCATOR_LAT, lat))
    rad = math.radians(clamped)
    return math.log(math.tan(math.pi / 4.0 + rad / 2.0)) * (ORIGIN_SHIFT / math.pi)


def _lat_to_global_py(lat: float, total_px: float) -> float:
    clamped = max(-MAX_MERCATOR_LAT, min(MAX_MERCATOR_LAT, lat))
    rad = math.radians(clamped)
    y_norm = (1.0 - math.log(math.tan(rad) + (1.0 / math.cos(rad))) / math.pi) * 0.5
    return y_norm * total_px


def _build_tile_mosaic(style: str, z: int, tx_min: int, ty_min: int, tx_max: int, ty_max: int) -> Image.Image:
    num_tiles = 1 << z
    cols = tx_max - tx_min + 1
    rows = ty_max - ty_min + 1
    mosaic = Image.new("RGBA", (cols * 256, rows * 256), (0, 0, 0, 0))

    coords: list[tuple[int, int, int, int]] = []
    for ty in range(ty_min, ty_max + 1):
        if ty < 0 or ty >= num_tiles:
            continue
        for tx in range(tx_min, tx_max + 1):
            coords.append((tx, ty, tx % num_tiles, ty))

    def load_one(item: tuple[int, int, int, int]) -> tuple[int, int, bytes | None]:
        tx, ty, wx, wy = item
        return tx, ty, _fetch_tile_bytes(style, z, wx, wy)

    for tx, ty, raw in _TILE_EXECUTOR.map(load_one, coords):
        if raw:
            try:
                tile_im = Image.open(io.BytesIO(raw)).convert("RGBA")
                mosaic.paste(tile_im, ((tx - tx_min) * 256, (ty - ty_min) * 256))
            except Exception:
                pass

    return mosaic


def _render_wms_getmap(
    style: str,
    srs: str,
    version: str,
    bbox: tuple[float, float, float, float],
    width: int,
    height: int,
    img_format: str,
) -> Response:
    width = max(1, min(4096, width))
    height = max(1, min(4096, height))
    srs_upper = srs.upper()

    if srs_upper in ("EPSG:4326", "CRS:84"):
        # WMS 1.3.0 with EPSG:4326 uses (minLat, minLon, maxLat, maxLon) axis order
        if version.startswith("1.3") and srs_upper == "EPSG:4326":
            min_lat, min_lon, max_lat, max_lon = bbox
        else:
            min_lon, min_lat, max_lon, max_lat = bbox

        if max_lon <= min_lon or max_lat <= min_lat:
            raise HTTPException(status_code=400, detail="Invalid WMS BBOX")

        min_x = _lon_to_mercator_x(min_lon)
        max_x = _lon_to_mercator_x(max_lon)
        res_x = (max_x - min_x) / float(width)
        res_y_eq = ((max_lat - min_lat) * (ORIGIN_SHIFT / 180.0)) / float(height)
        target_res = max(min(res_x, res_y_eq), 1e-9)
        z = int(round(math.log2(WORLD_SPAN / (256.0 * target_res))))
        z = max(0, min(18, z))
        total_px = float(256 * (1 << z))

        px_left = ((min_x + ORIGIN_SHIFT) / WORLD_SPAN) * total_px
        px_right = ((max_x + ORIGIN_SHIFT) / WORLD_SPAN) * total_px
        py_top = _lat_to_global_py(max_lat, total_px)
        py_bottom = _lat_to_global_py(min_lat, total_px)

        tx_min = math.floor(px_left / 256.0)
        tx_max = math.floor(max(px_left, px_right - 1e-6) / 256.0)
        ty_min = max(0, math.floor(py_top / 256.0))
        ty_max = min((1 << z) - 1, math.floor(max(py_top, py_bottom - 1e-6) / 256.0))

        # Safety cap on extreme zoomed-out high-res requests
        while (tx_max - tx_min + 1) * (ty_max - ty_min + 1) > 64 and z > 0:
            z -= 1
            total_px = float(256 * (1 << z))
            px_left = ((min_x + ORIGIN_SHIFT) / WORLD_SPAN) * total_px
            px_right = ((max_x + ORIGIN_SHIFT) / WORLD_SPAN) * total_px
            py_top = _lat_to_global_py(max_lat, total_px)
            py_bottom = _lat_to_global_py(min_lat, total_px)
            tx_min = math.floor(px_left / 256.0)
            tx_max = math.floor(max(px_left, px_right - 1e-6) / 256.0)
            ty_min = max(0, math.floor(py_top / 256.0))
            ty_max = min((1 << z) - 1, math.floor(max(py_top, py_bottom - 1e-6) / 256.0))

        mosaic = _build_tile_mosaic(style, z, tx_min, ty_min, tx_max, ty_max)
        mosaic_h = float(mosaic.height)
        src_x0 = px_left - tx_min * 256.0
        src_x1 = px_right - tx_min * 256.0

        bands = min(height, 64)
        mesh = []
        for i in range(bands):
            y0 = int(round(i * height / bands))
            y1 = int(round((i + 1) * height / bands))
            if y1 <= y0:
                continue
            lat0 = max_lat - (y0 / float(height)) * (max_lat - min_lat)
            lat1 = max_lat - (y1 / float(height)) * (max_lat - min_lat)
            sy0 = max(0.5, min(mosaic_h - 0.5, _lat_to_global_py(lat0, total_px) - ty_min * 256.0))
            sy1 = max(0.5, min(mosaic_h - 0.5, _lat_to_global_py(lat1, total_px) - ty_min * 256.0))
            mesh.append(((0, y0, width, y1), (src_x0, sy0, src_x0, sy1, src_x1, sy1, src_x1, sy0)))

        out_img = mosaic.transform((width, height), Image.Transform.MESH, mesh, Image.Resampling.BILINEAR)
    else:
        # EPSG:3857 / EPSG:900913 Spherical Mercator
        min_x, min_y, max_x, max_y = bbox
        if max_x <= min_x or max_y <= min_y:
            raise HTTPException(status_code=400, detail="Invalid WMS BBOX")

        res_x = (max_x - min_x) / float(width)
        z = int(round(math.log2(WORLD_SPAN / (256.0 * max(res_x, 1e-9)))))
        z = max(0, min(18, z))
        total_px = float(256 * (1 << z))

        px_left = ((min_x + ORIGIN_SHIFT) / WORLD_SPAN) * total_px
        px_right = ((max_x + ORIGIN_SHIFT) / WORLD_SPAN) * total_px
        py_top = ((ORIGIN_SHIFT - max_y) / WORLD_SPAN) * total_px
        py_bottom = ((ORIGIN_SHIFT - min_y) / WORLD_SPAN) * total_px

        tx_min = math.floor(px_left / 256.0)
        tx_max = math.floor(max(px_left, px_right - 1e-6) / 256.0)
        ty_min = math.floor(py_top / 256.0)
        ty_max = math.floor(max(py_top, py_bottom - 1e-6) / 256.0)

        while (tx_max - tx_min + 1) * (ty_max - ty_min + 1) > 64 and z > 0:
            z -= 1
            total_px = float(256 * (1 << z))
            px_left = ((min_x + ORIGIN_SHIFT) / WORLD_SPAN) * total_px
            px_right = ((max_x + ORIGIN_SHIFT) / WORLD_SPAN) * total_px
            py_top = ((ORIGIN_SHIFT - max_y) / WORLD_SPAN) * total_px
            py_bottom = ((ORIGIN_SHIFT - min_y) / WORLD_SPAN) * total_px
            tx_min = math.floor(px_left / 256.0)
            tx_max = math.floor(max(px_left, px_right - 1e-6) / 256.0)
            ty_min = math.floor(py_top / 256.0)
            ty_max = math.floor(max(py_top, py_bottom - 1e-6) / 256.0)

        mosaic = _build_tile_mosaic(style, z, tx_min, ty_min, tx_max, ty_max)
        src_x0 = px_left - tx_min * 256.0
        src_y0 = py_top - ty_min * 256.0
        src_x1 = px_right - tx_min * 256.0
        src_y1 = py_bottom - ty_min * 256.0

        # Fast pixel-exact path when WMS BBOX aligns 1:1 with Web Mercator tile grid
        ix0, iy0 = int(round(src_x0)), int(round(src_y0))
        if (
            abs(src_x0 - ix0) < 0.05
            and abs(src_y0 - iy0) < 0.05
            and abs((src_x1 - src_x0) - width) < 0.1
            and abs((src_y1 - src_y0) - height) < 0.1
        ):
            out_img = mosaic.crop((ix0, iy0, ix0 + width, iy0 + height))
        else:
            out_img = mosaic.transform(
                (width, height),
                Image.Transform.EXTENT,
                (src_x0, src_y0, src_x1, src_y1),
                Image.Resampling.BILINEAR,
            )

    buf = io.BytesIO()
    if "jpeg" in img_format.lower() or "jpg" in img_format.lower():
        out_img.convert("RGB").save(buf, format="JPEG", quality=90)
        media_type = "image/jpeg"
    else:
        out_img.save(buf, format="PNG")
        media_type = "image/png"

    return Response(content=buf.getvalue(), media_type=media_type)


def _dataset_bbox_3857(bounds: list[float] | None) -> tuple[float, float, float, float, float, float, float, float]:
    if bounds and len(bounds) == 4 and bounds[0] < bounds[2] and bounds[1] < bounds[3]:
        w, s, e, n = (
            max(-180.0, min(180.0, float(bounds[0]))),
            max(-MAX_MERCATOR_LAT, min(MAX_MERCATOR_LAT, float(bounds[1]))),
            max(-180.0, min(180.0, float(bounds[2]))),
            max(-MAX_MERCATOR_LAT, min(MAX_MERCATOR_LAT, float(bounds[3]))),
        )
        return (
            w,
            s,
            e,
            n,
            _lon_to_mercator_x(w),
            _lat_to_mercator_y(s),
            _lon_to_mercator_x(e),
            _lat_to_mercator_y(n),
        )
    return (
        -180.0,
        -MAX_MERCATOR_LAT,
        180.0,
        MAX_MERCATOR_LAT,
        -ORIGIN_SHIFT,
        -ORIGIN_SHIFT,
        ORIGIN_SHIFT,
        ORIGIN_SHIFT,
    )


def _collect_wms_layer_entries() -> list[tuple[str, str, list[float] | None]]:
    datasets = discover_vector_datasets(DATA_DIR)
    entries: list[tuple[str, str, list[float] | None]] = []
    sorted_all = sorted(ALL_STYLES, key=lambda s: format_style_title(s))

    # Default top-level convenience style names (all 17 offline styles)
    default_bounds = datasets[0].get("bounds") if datasets else None
    for st in sorted_all:
        entries.append((st, format_style_title(st), default_bounds))

    # Per-dataset explicit layer names (<ts_key>__<style>) across all 17 styles
    for ds in datasets:
        ts_key = ds["ts_key"]
        ds_name = ds["name"]
        ds_bounds = ds.get("bounds")
        for st in sorted_all:
            entries.append((f"{ts_key}__{st}", f"{ds_name} - {format_style_title(st)}", ds_bounds))

    return entries


def _render_wms_capabilities(request: Request, version: str) -> Response:
    base_url = xml_escape(str(request.url).split("?")[0])
    layer_entries = _collect_wms_layer_entries()

    if version.startswith("1.3"):
        layer_blocks = []
        for lyr_name, lyr_title, lyr_bounds in layer_entries:
            w, s, e, n, mx0, my0, mx1, my1 = _dataset_bbox_3857(lyr_bounds)
            geo_s = -90.0 if s <= -85.0 else s
            geo_n = 90.0 if n >= 85.0 else n
            layer_blocks.append(
                f"""      <Layer queryable="0">
        <Name>{xml_escape(lyr_name)}</Name>
        <Title>{xml_escape(lyr_title)}</Title>
        <CRS>EPSG:3857</CRS>
        <CRS>EPSG:4326</CRS>
        <CRS>CRS:84</CRS>
        <EX_GeographicBoundingBox>
          <westBoundLongitude>{w:.6f}</westBoundLongitude>
          <eastBoundLongitude>{e:.6f}</eastBoundLongitude>
          <southBoundLatitude>{geo_s:.6f}</southBoundLatitude>
          <northBoundLatitude>{geo_n:.6f}</northBoundLatitude>
        </EX_GeographicBoundingBox>
        <BoundingBox CRS="EPSG:3857" minx="{mx0:.2f}" miny="{my0:.2f}" maxx="{mx1:.2f}" maxy="{my1:.2f}"/>
        <BoundingBox CRS="EPSG:4326" minx="{geo_s:.6f}" miny="{w:.6f}" maxx="{geo_n:.6f}" maxy="{e:.6f}"/>
        <BoundingBox CRS="CRS:84" minx="{w:.6f}" miny="{geo_s:.6f}" maxx="{e:.6f}" maxy="{geo_n:.6f}"/>
      </Layer>"""
            )
        layers_xml = "\n".join(layer_blocks)
        xml_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<WMS_Capabilities version="1.3.0" xmlns="http://www.opengis.net/wms" xmlns:xlink="http://www.w3.org/1999/xlink">
  <Service>
    <Name>WMS</Name>
    <Title>CVEMAP Raster WMS</Title>
    <OnlineResource xlink:type="simple" xlink:href="{base_url}"/>
  </Service>
  <Capability>
    <Request>
      <GetCapabilities>
        <Format>text/xml</Format>
        <DCPType><HTTP><Get><OnlineResource xlink:type="simple" xlink:href="{base_url}?"/></Get></HTTP></DCPType>
      </GetCapabilities>
      <GetMap>
        <Format>image/png</Format>
        <Format>image/jpeg</Format>
        <DCPType><HTTP><Get><OnlineResource xlink:type="simple" xlink:href="{base_url}?"/></Get></HTTP></DCPType>
      </GetMap>
    </Request>
    <Exception><Format>XML</Format></Exception>
    <Layer>
      <Title>CVEMAP Basemaps</Title>
      <CRS>EPSG:3857</CRS>
      <CRS>EPSG:4326</CRS>
      <CRS>CRS:84</CRS>
{layers_xml}
    </Layer>
  </Capability>
</WMS_Capabilities>"""
        return Response(content=xml_body, media_type="text/xml")

    layer_blocks_111 = []
    for lyr_name, lyr_title, lyr_bounds in layer_entries:
        w, s, e, n, mx0, my0, mx1, my1 = _dataset_bbox_3857(lyr_bounds)
        geo_s = -90.0 if s <= -85.0 else s
        geo_n = 90.0 if n >= 85.0 else n
        layer_blocks_111.append(
            f"""      <Layer queryable="0">
        <Name>{xml_escape(lyr_name)}</Name>
        <Title>{xml_escape(lyr_title)}</Title>
        <SRS>EPSG:3857</SRS>
        <SRS>EPSG:4326</SRS>
        <LatLonBoundingBox minx="{w:.6f}" miny="{geo_s:.6f}" maxx="{e:.6f}" maxy="{geo_n:.6f}"/>
        <BoundingBox SRS="EPSG:3857" minx="{mx0:.2f}" miny="{my0:.2f}" maxx="{mx1:.2f}" maxy="{my1:.2f}"/>
        <BoundingBox SRS="EPSG:4326" minx="{w:.6f}" miny="{geo_s:.6f}" maxx="{e:.6f}" maxy="{geo_n:.6f}"/>
      </Layer>"""
        )
    layers_xml = "\n".join(layer_blocks_111)
    xml_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE WMT_MS_Capabilities SYSTEM "http://schemas.opengis.net/wms/1.1.1/WMS_MS_Capabilities.dtd">
<WMT_MS_Capabilities version="1.1.1">
  <Service>
    <Name>OGC:WMS</Name>
    <Title>CVEMAP Raster WMS</Title>
    <OnlineResource xmlns:xlink="http://www.w3.org/1999/xlink" xlink:type="simple" xlink:href="{base_url}"/>
  </Service>
  <Capability>
    <Request>
      <GetCapabilities>
        <Format>application/vnd.ogc.wms_xml</Format>
        <DCPType><HTTP><Get><OnlineResource xmlns:xlink="http://www.w3.org/1999/xlink" xlink:type="simple" xlink:href="{base_url}?"/></Get></HTTP></DCPType>
      </GetCapabilities>
      <GetMap>
        <Format>image/png</Format>
        <Format>image/jpeg</Format>
        <DCPType><HTTP><Get><OnlineResource xmlns:xlink="http://www.w3.org/1999/xlink" xlink:type="simple" xlink:href="{base_url}?"/></Get></HTTP></DCPType>
      </GetMap>
    </Request>
    <Exception><Format>application/vnd.ogc.se_xml</Format></Exception>
    <Layer>
      <Title>CVEMAP Basemaps</Title>
      <SRS>EPSG:3857</SRS>
      <SRS>EPSG:4326</SRS>
      <LatLonBoundingBox minx="-180" miny="-90" maxx="180" maxy="90"/>
{layers_xml}
    </Layer>
  </Capability>
</WMT_MS_Capabilities>"""
    return Response(content=xml_body, media_type="text/xml")


def _handle_wms_request(request: Request) -> Response:
    params = {k.upper(): v for k, v in request.query_params.items()}
    req_type = params.get("REQUEST", "GetCapabilities").strip().upper()
    version = params.get("VERSION", "1.1.1").strip()

    if req_type == "GETCAPABILITIES":
        return _render_wms_capabilities(request, version)

    if req_type != "GETMAP":
        raise HTTPException(status_code=400, detail=f"Unsupported WMS REQUEST: {req_type}")

    dataset_param = params.get("DATASET", "").strip() or None
    layers_raw = params.get("LAYERS", "light").strip()
    requested = [s.strip() for s in layers_raw.split(",") if s.strip()]
    chosen_layer = requested[-1] if requested else "light"
    style = _resolve_tileserver_style(chosen_layer, dataset=dataset_param)

    srs = params.get("CRS") or params.get("SRS") or "EPSG:3857"
    bbox_raw = params.get("BBOX", "")
    try:
        b_parts = [float(x) for x in bbox_raw.split(",")]
        if len(b_parts) != 4:
            raise ValueError("BBOX requires 4 comma-separated floats")
        bbox = (b_parts[0], b_parts[1], b_parts[2], b_parts[3])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or missing BBOX parameter")

    try:
        width = int(float(params.get("WIDTH", "256")))
        height = int(float(params.get("HEIGHT", "256")))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid WIDTH or HEIGHT parameter")

    img_format = params.get("FORMAT", "image/png")
    return _render_wms_getmap(style, srs, version, bbox, width, height, img_format)


@app.get("/wms")
def wms_root_endpoint(request: Request) -> Response:
    return _handle_wms_request(request)


# ==============================================================================
# Vector MBTiles MVT Endpoint (for OpenMapTiles / Planetiler .mbtiles archives)
# ==============================================================================


def _serve_mvt_tile(layer: str, z: int, x: int, y: int) -> Response:
    datasets = discover_vector_datasets(DATA_DIR)
    target = next((d for d in datasets if d["name"] == layer and d.get("is_mbtiles")), None)
    if not target:
        raise HTTPException(status_code=404, detail="Vector MBTiles dataset not found")

    mbtiles_path = os.path.join(DATA_DIR, target["path"])
    if not os.path.isfile(mbtiles_path):
        raise HTTPException(status_code=404, detail="MBTiles file missing")

    num_tiles = max(1, 1 << z)
    tms_y = num_tiles - 1 - y
    conn = sqlite3.connect(f"file:{mbtiles_path}?mode=ro", uri=True)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
        (z, x % num_tiles, tms_y),
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return Response(
            content=row[0],
            media_type="application/x-protobuf",
            headers={"Content-Encoding": "gzip", "Access-Control-Allow-Origin": "*"},
        )
    if 0 <= z <= int(target.get("maxZoom", 14)):
        return Response(status_code=204, headers={"Access-Control-Allow-Origin": "*"})
    raise HTTPException(status_code=404, detail="Vector tile not found")


@app.get("/mvt/{layer}/{z}/{x}/{y}.pbf")
def mvt_endpoint(layer: str, z: int, x: int, y: int) -> Response:
    return _serve_mvt_tile(layer, z, x, y)
