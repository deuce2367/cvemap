import copy
import glob
import gzip
import json
import logging
import os
import re
import signal
import sqlite3
import struct
import subprocess

import urllib.parse

logger = logging.getLogger(__name__)

TILESERVER_STYLES = [
    "bio",
    "black",
    "black_and_white",
    "dark",
    "dusk_rose",
    "flat",
    "grayscale",
    "iris_bloom",
    "light",
    "rainforest",
    "seafoam",
    "sol",
    "white",
]

OPENMAPTILES_STYLE_DEFS: dict[str, dict[str, str]] = {
    "fiord": {
        "title": "Fiord",
        "vendor_dir": "fiord-color",
        "sprite": "fiord-color/sprite",
        "browser_sprite": "fiord-color/sprites/sprite",
    },
    "maptiler_basic": {
        "title": "MapTiler Basic",
        "vendor_dir": "maptiler-basic",
        "sprite": "maptiler-basic/sprite",
        "browser_sprite": "maptiler-basic/sprites/sprite",
    },
    "osm_bright": {
        "title": "OSM Bright (Colorful)",
        "vendor_dir": "carto-color",
        "sprite": "carto-color/sprite",
        "browser_sprite": "carto-color/sprites/sprite",
    },
    "osm_liberty": {
        "title": "OSM Liberty (Classic OSM)",
        "vendor_dir": "osm-liberty",
        "sprite": "osm-liberty/osm-liberty",
        "browser_sprite": "osm-liberty/sprites/osm-liberty",
    },
    "osm_openmap": {
        "title": "OSM OpenMap",
        "vendor_dir": "osm-openmap",
        "sprite": "osm-openmap/sprite",
        "browser_sprite": "osm-openmap/sprites/sprite",
    },
    "tron": {
        "title": "Tron",
        "vendor_dir": "nextzen-tron",
        "sprite": "nextzen-tron/sprite",
        "browser_sprite": "nextzen-tron/sprites/sprite",
    },
}

OPENMAPTILES_STYLES = list(OPENMAPTILES_STYLE_DEFS.keys())
ALL_STYLES = TILESERVER_STYLES + OPENMAPTILES_STYLES

_HEADER_CACHE: dict[str, tuple[float, int, dict]] = {}


def sanitize_tileserver_id(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    return cleaned or "dataset"


def make_dataset_style_id(dataset_name: str, style_id: str) -> str:
    return f"{sanitize_tileserver_id(dataset_name)}__{style_id}"


def format_style_title(style_id: str) -> str:
    if style_id in OPENMAPTILES_STYLE_DEFS:
        return OPENMAPTILES_STYLE_DEFS[style_id]["title"]
    if style_id == "black_and_white":
        return "Protomaps Black & White"
    return "Protomaps " + style_id.replace("_", " ").title()


def _is_ignored_data_path(path: str, data_dir: str) -> bool:
    rel = os.path.relpath(path, data_dir)
    parts = rel.split(os.sep)
    return any(p.startswith(".") or p == "legacy" for p in parts)


def migrate_data_directory(data_dir: str) -> None:
    """Flatten legacy `webgl/` archive subdirectories into `data_dir` and prune obsolete MapServer artifacts."""
    if not os.path.isdir(data_dir):
        return

    # 1. Move any .pmtiles / .mbtiles nested under obsolete `webgl/` up to top-level `data_dir`
    webgl_dir = os.path.join(data_dir, "webgl")
    if os.path.isdir(webgl_dir):
        for ext in ("*.pmtiles", "*.mbtiles"):
            for src in sorted(glob.glob(os.path.join(webgl_dir, "**", ext), recursive=True)):
                if not os.path.isfile(src):
                    continue
                dst = os.path.join(data_dir, os.path.basename(src))
                if not os.path.exists(dst):
                    try:
                        os.replace(src, dst)
                        logger.info("Migrated %s -> %s", src, dst)
                    except OSError as exc:
                        logger.warning("Could not migrate %s: %s", src, exc)
        # Prune empty directories under webgl/
        for root, dirs, files in os.walk(webgl_dir, topdown=False):
            if not dirs and not files:
                try:
                    os.rmdir(root)
                except OSError:
                    pass

    # 2. Remove obsolete MapServer generated files (.map, .gdal_wms_*.xml, old MapServer config.json)
    for pattern in ("*.map", ".gdal_wms_*.xml"):
        for stale in glob.glob(os.path.join(data_dir, pattern)):
            try:
                os.remove(stale)
            except OSError:
                pass

    old_cfg = os.path.join(data_dir, "config.json")
    if os.path.isfile(old_cfg):
        try:
            with open(old_cfg, "r") as f:
                parsed = json.load(f)
            if isinstance(parsed, dict) and "maps" in parsed:
                os.remove(old_cfg)
        except Exception:
            pass

    # 3. Remove empty legacy/ directories if present
    legacy_dir = os.path.join(data_dir, "legacy")
    if os.path.isdir(legacy_dir):
        for root, dirs, files in os.walk(legacy_dir, topdown=False):
            if not dirs and not files:
                try:
                    os.rmdir(root)
                except OSError:
                    pass


def inspect_pmtiles(filepath: str) -> dict:
    """Read minZoom, maxZoom, bounds, version, and schema prefix from a PMTiles v3 header."""
    try:
        st = os.stat(filepath)
        cached = _HEADER_CACHE.get(filepath)
        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            return dict(cached[2])
    except OSError:
        st = None

    info = {
        "minZoom": 0,
        "maxZoom": 14,
        "bounds": None,
        "version": "",
        "pmap_prefix": False,
    }
    try:
        with open(filepath, "rb") as f:
            hdr = f.read(127)
            if len(hdr) < 127 or hdr[:7] != b"PMTiles":
                return info
            meta_off, meta_len = struct.unpack("<QQ", hdr[24:40])
            info["minZoom"] = int(hdr[100])
            info["maxZoom"] = int(hdr[101])
            min_lon, min_lat, max_lon, max_lat = [
                round(v / 1e7, 5) for v in struct.unpack("<iiii", hdr[102:118])
            ]
            if min_lon < max_lon and min_lat < max_lat:
                info["bounds"] = [min_lon, min_lat, max_lon, max_lat]
            if 0 < meta_len < 10 * 1024 * 1024:
                f.seek(meta_off)
                raw = f.read(meta_len)
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                meta = json.loads(raw.decode("utf-8", errors="ignore"))
                info["version"] = str(meta.get("version", ""))
                fields = set()
                for vl in meta.get("vector_layers", []):
                    if isinstance(vl, dict):
                        fields.update((vl.get("fields") or {}).keys())
                info["pmap_prefix"] = "pmap:kind" in fields
    except Exception as exc:
        logger.warning("Failed to inspect PMTiles header %s: %s", filepath, exc)

    if st is not None:
        _HEADER_CACHE[filepath] = (st.st_mtime, st.st_size, dict(info))
    return info


def inspect_vector_mbtiles(filepath: str) -> dict | None:
    """Inspect an MBTiles SQLite archive; returns metadata dict if it contains PBF vector tiles, else None."""
    try:
        st = os.stat(filepath)
        cached = _HEADER_CACHE.get(filepath)
        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            return dict(cached[2]) if cached[2] else None
    except OSError:
        return None

    result: dict = {}
    try:
        conn = sqlite3.connect(f"file:{filepath}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("SELECT name, value FROM metadata")
        meta = {str(k).lower(): str(v) for k, v in cur.fetchall()}
        conn.close()

        fmt = meta.get("format", "").strip().lower()
        if fmt != "pbf":
            _HEADER_CACHE[filepath] = (st.st_mtime, st.st_size, {})
            return None

        min_z = int(meta.get("minzoom", 0) or 0)
        max_z = int(meta.get("maxzoom", 14) or 14)
        bounds = None
        if "bounds" in meta:
            parts = [float(x.strip()) for x in meta["bounds"].split(",")]
            if len(parts) == 4 and parts[0] < parts[2] and parts[1] < parts[3]:
                bounds = parts

        result = {
            "minZoom": min_z,
            "maxZoom": max_z,
            "bounds": bounds,
        }
    except Exception as exc:
        logger.warning("Failed to inspect MBTiles metadata %s: %s", filepath, exc)
        return None

    _HEADER_CACHE[filepath] = (st.st_mtime, st.st_size, dict(result))
    return result


def _has_valid_pmtiles_magic(filepath: str) -> bool:
    try:
        with open(filepath, "rb") as f:
            hdr = f.read(8)
        if len(hdr) == 8 and hdr[:7] == b"PMTiles" and hdr[7] == 3:
            return True
        logger.warning("Skipping incomplete or corrupted PMTiles file (invalid header): %s", filepath)
        return False
    except OSError:
        return False


def discover_pmtiles_files(data_dir: str) -> list[str]:
    if not os.path.isdir(data_dir):
        return []
    all_files = glob.glob(os.path.join(data_dir, "**", "*.pmtiles"), recursive=True)
    return sorted(
        p
        for p in all_files
        if os.path.isfile(p) and not _is_ignored_data_path(p, data_dir) and _has_valid_pmtiles_magic(p)
    )


def select_primary_pmtiles(data_dir: str) -> str:
    """Choose the primary PMTiles archive for TileServer-GL rendering."""
    candidates = discover_pmtiles_files(data_dir)
    if not candidates:
        return ""

    planet_candidates = [c for c in candidates if os.path.basename(c).lower().startswith("planet")]
    if planet_candidates:
        return max(planet_candidates, key=lambda p: os.path.getsize(p))

    return max(candidates, key=lambda p: os.path.getsize(p))


def discover_vector_datasets(data_dir: str) -> list[dict]:
    """Discover all PMTiles and MVT MBTiles datasets under data_dir."""
    if not os.path.isdir(data_dir):
        return []

    datasets: list[dict] = []
    seen_names: set[str] = set()
    seen_ts_keys: set[str] = set()

    def _alloc_ts_key(raw_name: str) -> str:
        base_key = sanitize_tileserver_id(raw_name)
        candidate = base_key
        idx = 2
        while candidate in seen_ts_keys:
            candidate = f"{base_key}_{idx}"
            idx += 1
        seen_ts_keys.add(candidate)
        return candidate

    for pf in discover_pmtiles_files(data_dir):
        base = os.path.splitext(os.path.basename(pf))[0]
        name = base if base not in seen_names else os.path.splitext(os.path.relpath(pf, data_dir))[0].replace(os.sep, "_")
        seen_names.add(name)
        pm_info = inspect_pmtiles(pf)
        entry = {
            "name": name,
            "ts_key": _alloc_ts_key(name),
            "path": os.path.relpath(pf, data_dir),
            "is_mbtiles": False,
            "is_pmtiles": True,
            "is_vector_tiles": True,
            "minZoom": pm_info["minZoom"],
            "maxZoom": pm_info["maxZoom"],
            "pmap_prefix": pm_info["pmap_prefix"],
        }
        if pm_info.get("bounds"):
            entry["bounds"] = pm_info["bounds"]
        datasets.append(entry)

    mbtiles_files = sorted(
        p
        for p in glob.glob(os.path.join(data_dir, "**", "*.mbtiles"), recursive=True)
        if os.path.isfile(p) and not _is_ignored_data_path(p, data_dir)
    )
    for mf in mbtiles_files:
        mb_info = inspect_vector_mbtiles(mf)
        if not mb_info:
            continue
        base = os.path.splitext(os.path.basename(mf))[0]
        name = base if base not in seen_names else os.path.splitext(os.path.relpath(mf, data_dir))[0].replace(os.sep, "_")
        seen_names.add(name)
        entry = {
            "name": name,
            "ts_key": _alloc_ts_key(name),
            "path": os.path.relpath(mf, data_dir),
            "is_mbtiles": True,
            "is_pmtiles": False,
            "is_vector_tiles": True,
            "minZoom": mb_info["minZoom"],
            "maxZoom": mb_info["maxZoom"],
        }
        if mb_info.get("bounds"):
            entry["bounds"] = mb_info["bounds"]
        datasets.append(entry)

    # Sort: planet PMTiles first, then planet MBTiles, then alphabetical by name
    datasets.sort(
        key=lambda d: (
            0 if d["name"].lower().startswith("planet") and d.get("is_pmtiles") else
            1 if d["name"].lower().startswith("planet") else
            2,
            d["name"].lower(),
        )
    )
    return datasets


def adapt_style_schema(style_obj: dict, pmap_prefix: bool) -> dict:
    """Ensure Protomaps style property names match the active PMTiles schema (v3-alpha pmap:* vs v4 unprefixed)."""
    text = json.dumps(style_obj)
    if pmap_prefix:
        return json.loads(text)
    # Modern Protomaps v4+ uses unprefixed kind, kind_detail, min_zoom, population_rank,
    # and stores boundary admin levels in numeric kind_detail instead of pmap:min_admin_level
    text = text.replace('"pmap:min_admin_level"', '"kind_detail"')
    text = text.replace('"pmap:kind"', '"kind"')
    text = text.replace('"pmap:kind_detail"', '"kind_detail"')
    text = text.replace('"pmap:min_zoom"', '"min_zoom"')
    text = text.replace('"pmap:population_rank"', '"population_rank"')
    return json.loads(text)


def _resolve_vendor_dir(tileserver_template_dir: str) -> str:
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(tileserver_template_dir)), "static", "vendor"),
        os.path.join(os.path.dirname(os.path.abspath(tileserver_template_dir)), "app", "static", "vendor"),
        os.path.abspath("static/vendor"),
        os.path.abspath("app/static/vendor"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return ""


def _prepare_runtime_assets(tileserver_template_dir: str, vendor_dir: str, runtime_dir: str) -> tuple[str, str]:
    """Assemble merged fonts and sprites directories in runtime_dir for both Protomaps and OpenMapTiles styles."""
    rt_fonts = os.path.abspath(os.path.join(runtime_dir, "fonts"))
    rt_sprites = os.path.abspath(os.path.join(runtime_dir, "sprites"))
    os.makedirs(rt_fonts, exist_ok=True)
    os.makedirs(rt_sprites, exist_ok=True)

    tpl_fonts = os.path.abspath(os.path.join(tileserver_template_dir, "fonts"))
    if os.path.isdir(tpl_fonts):
        for name in os.listdir(tpl_fonts):
            src = os.path.join(tpl_fonts, name)
            dst = os.path.join(rt_fonts, name)
            if not os.path.lexists(dst):
                try:
                    os.symlink(src, dst)
                except OSError:
                    pass

    tpl_sprites = os.path.abspath(os.path.join(tileserver_template_dir, "sprites"))
    if os.path.isdir(tpl_sprites):
        for name in os.listdir(tpl_sprites):
            src = os.path.join(tpl_sprites, name)
            dst = os.path.join(rt_sprites, name)
            if not os.path.lexists(dst):
                try:
                    os.symlink(src, dst)
                except OSError:
                    pass

    if vendor_dir:
        vendor_glyphs = os.path.abspath(os.path.join(vendor_dir, "font-glyphs", "glyphs"))
        if os.path.isdir(vendor_glyphs):
            for name in os.listdir(vendor_glyphs):
                src = os.path.join(vendor_glyphs, name)
                dst = os.path.join(rt_fonts, name)
                if os.path.isdir(src) and not os.path.lexists(dst):
                    try:
                        os.symlink(src, dst)
                    except OSError:
                        pass

        for meta in OPENMAPTILES_STYLE_DEFS.values():
            sub = meta["vendor_dir"]
            src = os.path.abspath(os.path.join(vendor_dir, sub, "sprites"))
            dst = os.path.join(rt_sprites, sub)
            if os.path.isdir(src) and not os.path.lexists(dst):
                try:
                    os.symlink(src, dst)
                except OSError:
                    pass

    return rt_fonts, rt_sprites


def _adapt_openmaptiles_style(
    style_obj: dict,
    source_spec: dict,
    sprite_ref: str,
    glyphs_ref: str = "{fontstack}/{range}.pbf",
) -> dict:
    adapted = copy.deepcopy(style_obj)
    sources = adapted.get("sources") or {}
    if "natural_earth_shaded_relief" in sources:
        del sources["natural_earth_shaded_relief"]
    if "layers" in adapted and isinstance(adapted["layers"], list):
        adapted["layers"] = [
            lyr for lyr in adapted["layers"] if lyr.get("source") != "natural_earth_shaded_relief"
        ]
    if "openmaptiles" in sources:
        sources["openmaptiles"] = source_spec
    elif "carto" in sources:
        sources["carto"] = source_spec
    else:
        sources["openmaptiles"] = source_spec
    adapted["sources"] = sources
    adapted["glyphs"] = glyphs_ref
    adapted["sprite"] = sprite_ref
    return adapted


def _adapt_protomaps_style_to_openmaptiles(
    style_obj: dict,
    source_spec: dict,
    sprite_ref: str | None = None,
    glyphs_ref: str = "{fontstack}/{range}.pbf",
) -> dict:
    """Adapt any Protomaps basemap style JSON so it renders natively on an OpenMapTiles (.mbtiles) dataset."""
    adapted = copy.deepcopy(style_obj)
    adapted["sources"] = {"protomaps": source_spec}
    adapted["glyphs"] = glyphs_ref
    if sprite_ref is not None:
        adapted["sprite"] = sprite_ref

    earth_color: object = "#e6e6e6"
    for lyr in adapted.get("layers", []):
        if lyr.get("id") == "earth":
            fc = (lyr.get("paint") or {}).get("fill-color")
            if fc is not None:
                earth_color = copy.deepcopy(fc)
            break

    name_expr = ["coalesce", ["get", "name:en"], ["get", "name:latin"], ["get", "name"]]
    new_layers: list[dict] = []

    for lyr in adapted.get("layers", []):
        lid = lyr.get("id", "")
        ltype = lyr.get("type", "")

        # Drop earth polygon (background becomes land; water covers oceans & lakes)
        # and drop Protomaps triplicated _landcover/_natural clones & missing address_label
        if lid in ("earth", "address_label") or lid.endswith("_landcover") or lid.endswith("_natural"):
            continue

        if lid == "background":
            lyr.setdefault("paint", {})["background-color"] = earth_color
            new_layers.append(lyr)
            continue

        # Rewrite pmap:* expressions inside paint/layout JSON before setting layer-specific rules
        raw_txt = json.dumps(lyr)
        if "pmap:" in raw_txt or "population_rank" in raw_txt or "min_zoom" in raw_txt or '"ref"' in raw_txt:
            raw_txt = raw_txt.replace(
                '["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0]',
                '["-", 16, ["coalesce", ["get", "rank"], 10]]',
            )
            raw_txt = raw_txt.replace(
                '["coalesce", ["get", "pmap:min_zoom"], ["get", "min_zoom"], 99]',
                '["coalesce", ["get", "rank"], 10]',
            )
            raw_txt = raw_txt.replace(
                '["length", ["get", "ref"]]',
                '["to-string", ["length", ["to-string", ["get", "ref"]]]]',
            )
            raw_txt = raw_txt.replace('"pmap:kind"', '"class"')
            raw_txt = raw_txt.replace('"pmap:min_zoom"', '"rank"')
            raw_txt = raw_txt.replace('"grassland"', '"grass"')
            raw_txt = raw_txt.replace('"glacier"', '"ice"')
            lyr = json.loads(raw_txt)

        if lid == "landcover":
            lyr["source-layer"] = "landcover"
            lyr["filter"] = ["==", "$type", "Polygon"]
        elif lid == "landuse_park":
            # Emit both OpenMapTiles 'park' layer and natural 'landcover' layer
            park_clone = copy.deepcopy(lyr)
            park_clone["id"] = "landuse_park_area"
            park_clone["source-layer"] = "park"
            park_clone["filter"] = ["==", "$type", "Polygon"]
            fc_str = json.dumps((lyr.get("paint") or {}).get("fill-color", ""))
            m_hex = re.search(r"#[0-9a-fA-F]{6}", fc_str)
            if m_hex:
                park_clone.setdefault("paint", {})["fill-color"] = m_hex.group(0)
            new_layers.append(park_clone)
            lyr["source-layer"] = "landcover"
            lyr["filter"] = ["in", "class", "wood", "grass", "ice", "sand", "wetland"]
        elif lid == "landuse_urban_green":
            lyr["source-layer"] = "landuse"
            lyr["filter"] = ["in", "class", "park", "pitch", "playground", "grass", "cemetery"]
        elif lid == "landuse_hospital":
            lyr["source-layer"] = "landuse"
            lyr["filter"] = ["==", "class", "hospital"]
        elif lid == "landuse_industrial":
            lyr["source-layer"] = "landuse"
            lyr["filter"] = ["in", "class", "industrial", "railway"]
        elif lid == "landuse_school":
            lyr["source-layer"] = "landuse"
            lyr["filter"] = ["in", "class", "school", "university", "college"]
        elif lid == "landuse_beach":
            lyr["source-layer"] = "landcover"
            lyr["filter"] = ["==", "class", "sand"]
        elif lid == "landuse_zoo":
            lyr["source-layer"] = "landuse"
            lyr["filter"] = ["==", "class", "zoo"]
        elif lid == "landuse_aerodrome":
            lyr["source-layer"] = "aeroway"
            lyr["filter"] = ["all", ["==", "$type", "Polygon"], ["==", "class", "aerodrome"]]
        elif lid == "landuse_runway":
            lyr["source-layer"] = "aeroway"
            lyr["filter"] = ["all", ["==", "$type", "Polygon"], ["in", "class", "runway", "taxiway", "apron"]]
        elif lid in ("roads_runway", "roads_taxiway"):
            lyr["source-layer"] = "aeroway"
            cls_val = "taxiway" if "taxiway" in lid else "runway"
            lyr["filter"] = ["all", ["==", "$type", "LineString"], ["==", "class", cls_val]]
        elif lid == "water":
            lyr["source-layer"] = "water"
            lyr["filter"] = ["==", "$type", "Polygon"]
        elif lid == "water_stream":
            lyr["source-layer"] = "waterway"
            lyr["filter"] = ["==", "class", "stream"]
        elif lid == "water_river":
            lyr["source-layer"] = "waterway"
            lyr["filter"] = ["in", "class", "river", "canal"]
        elif lid == "landuse_pedestrian":
            lyr["source-layer"] = "transportation"
            lyr["filter"] = ["all", ["==", "$type", "Polygon"], ["in", "class", "path", "pedestrian"]]
        elif lid == "landuse_pier":
            lyr["source-layer"] = "transportation"
            lyr["filter"] = ["all", ["==", "$type", "Polygon"], ["==", "class", "pier"]]
        elif lid == "roads_pier":
            lyr["source-layer"] = "transportation"
            lyr["filter"] = ["all", ["==", "$type", "LineString"], ["==", "class", "pier"]]
        elif lid == "buildings":
            lyr["source-layer"] = "building"
            lyr["filter"] = ["==", "$type", "Polygon"]
        elif lid == "boundaries_country":
            lyr["source-layer"] = "boundary"
            lyr["filter"] = ["all", ["<=", "admin_level", 2], ["!=", "maritime", 1]]
        elif lid == "boundaries":
            lyr["source-layer"] = "boundary"
            lyr["filter"] = ["all", [">", "admin_level", 2], ["!=", "maritime", 1]]
        elif lid.startswith("roads_") and ltype == "line":
            lyr["source-layer"] = "transportation"
            conds: list = ["all"]
            if "_tunnels_" in lid:
                conds.append(["==", "brunnel", "tunnel"])
            elif "_bridges_" in lid:
                conds.append(["==", "brunnel", "bridge"])
            elif lid != "roads_rail":
                conds.append(["!in", "brunnel", "tunnel", "bridge"])

            if lid == "roads_rail":
                conds.append(["in", "class", "rail", "transit"])
            elif "link" in lid:
                conds.append(["==", "ramp", 1])
            elif "highway" in lid:
                conds.extend([["in", "class", "motorway", "trunk"], ["!=", "ramp", 1]])
            elif "major" in lid:
                conds.extend([["in", "class", "primary", "secondary", "tertiary"], ["!=", "ramp", 1]])
            elif "service" in lid:
                conds.append(["==", "class", "service"])
            elif "minor" in lid:
                conds.append(["==", "class", "minor"])
            else:
                conds.append(["in", "class", "path", "track"])
            lyr["filter"] = conds
        elif ltype == "symbol":
            layout = lyr.setdefault("layout", {})
            if "symbol-sort-key" in layout:
                layout["symbol-sort-key"] = ["coalesce", ["get", "rank"], 10]
            if lid == "water_waterway_label":
                lyr["source-layer"] = "waterway"
                lyr["filter"] = ["in", "class", "river", "stream", "canal"]
                layout["text-field"] = name_expr
            elif lid == "roads_oneway":
                lyr["source-layer"] = "transportation"
                lyr["filter"] = ["==", "oneway", 1]
            elif lid == "roads_labels_minor":
                lyr["source-layer"] = "transportation_name"
                lyr["filter"] = ["in", "class", "minor", "service", "path", "track"]
                layout["text-field"] = name_expr
            elif lid == "roads_labels_major":
                lyr["source-layer"] = "transportation_name"
                lyr["filter"] = ["in", "class", "motorway", "trunk", "primary", "secondary", "tertiary"]
                layout["text-field"] = name_expr
            elif lid == "roads_shields":
                lyr["source-layer"] = "transportation_name"
                lyr["filter"] = [
                    "all",
                    ["in", ["get", "class"], ["literal", ["motorway", "trunk", "primary"]]],
                    ["has", "ref"],
                    [">=", ["length", ["to-string", ["get", "ref"]]], 1],
                    ["<=", ["length", ["to-string", ["get", "ref"]]], 5],
                ]
                layout["text-field"] = ["to-string", ["get", "ref"]]
            elif lid == "water_label_ocean":
                lyr["source-layer"] = "water_name"
                lyr["filter"] = ["in", "class", "ocean", "sea", "bay"]
                layout["text-field"] = name_expr
            elif lid == "water_label_lakes":
                lyr["source-layer"] = "water_name"
                lyr["filter"] = ["==", "class", "lake"]
                layout["text-field"] = name_expr
            elif lid == "earth_label_islands":
                lyr["source-layer"] = "place"
                lyr["filter"] = ["==", "class", "island"]
                layout["text-field"] = name_expr
            elif lid == "pois":
                lyr["source-layer"] = "poi"
                lyr["filter"] = ["all", ["has", "name"], ["<=", "rank", 14]]
                layout["text-field"] = name_expr
                if "icon-image" in layout:
                    del layout["icon-image"]
            elif lid == "places_subplace":
                lyr["source-layer"] = "place"
                lyr["filter"] = ["in", "class", "suburb", "quarter", "neighbourhood"]
                layout["text-field"] = name_expr
            elif lid == "places_locality":
                lyr["source-layer"] = "place"
                lyr["filter"] = ["in", "class", "city", "town", "village", "hamlet"]
                layout["text-field"] = name_expr
            elif lid == "places_region":
                lyr["source-layer"] = "place"
                lyr["filter"] = ["in", "class", "state", "province"]
                layout["text-field"] = name_expr
            elif lid == "places_country":
                lyr["source-layer"] = "place"
                lyr["filter"] = ["==", "class", "country"]
                layout["text-field"] = name_expr

        new_layers.append(lyr)

    adapted["layers"] = new_layers
    return adapted


def _adapt_openmaptiles_style_to_protomaps(
    style_obj: dict,
    pmap_prefix: bool,
    source_spec: dict,
    sprite_ref: str,
    glyphs_ref: str = "{fontstack}/{range}.pbf",
) -> dict:
    """Adapt any OpenMapTiles basemap style JSON so it renders natively on a Protomaps (.pmtiles) dataset."""
    adapted = _adapt_openmaptiles_style(
        style_obj,
        source_spec=source_spec,
        sprite_ref=sprite_ref,
        glyphs_ref=glyphs_ref,
    )
    src_name = next(iter(adapted["sources"].keys()), "openmaptiles")

    k_kind = "pmap:kind" if pmap_prefix else "kind"
    k_detail = "pmap:kind_detail" if pmap_prefix else "kind_detail"
    k_admin = "pmap:min_admin_level" if pmap_prefix else "kind_detail"
    k_pop = "pmap:population_rank" if pmap_prefix else "population_rank"
    k_minz = "pmap:min_zoom" if pmap_prefix else "min_zoom"
    name_expr = ["coalesce", ["get", "name:en"], ["get", "name"]]

    land_color: object = "#f2f3f0"
    water_color: object = "#a0c8f0"
    has_city_large = any("city_large" in (l.get("id") or "").lower() for l in adapted.get("layers", []))

    for lyr in adapted.get("layers", []):
        if lyr.get("type") == "background":
            bgc = (lyr.get("paint") or {}).get("background-color")
            if bgc is not None:
                land_color = copy.deepcopy(bgc)
        elif lyr.get("source-layer") == "water" and lyr.get("type") == "fill" and "intermittent" not in (lyr.get("id") or "").lower():
            wc = (lyr.get("paint") or {}).get("fill-color")
            if wc is not None:
                water_color = copy.deepcopy(wc)

    new_layers: list[dict] = []
    inserted_earth = False
    seen_poi_layer = False

    for lyr in adapted.get("layers", []):
        lid_raw = lyr.get("id", "")
        lid = lid_raw.lower()
        ltype = lyr.get("type", "")
        sl = lyr.get("source-layer")

        if "highway-shield-us-" in lid or sl == "housenumber":
            continue

        if ltype == "background":
            lyr.setdefault("paint", {})["background-color"] = copy.deepcopy(water_color)
            new_layers.append(lyr)
            new_layers.append(
                {
                    "id": "earth",
                    "type": "fill",
                    "source": src_name,
                    "source-layer": "earth",
                    "filter": ["==", "$type", "Polygon"],
                    "paint": {"fill-color": copy.deepcopy(land_color)},
                }
            )
            inserted_earth = True
            continue

        if sl == "water":
            lyr["source-layer"] = "water"
            if "intermittent" in lid:
                lyr["filter"] = ["all", ["==", "$type", "Polygon"], ["has", "intermittent"]]
            else:
                lyr["filter"] = ["==", "$type", "Polygon"]
        elif sl == "waterway":
            lyr["source-layer"] = "water"
            if ltype == "symbol":
                lyr["filter"] = ["all", ["has", "name"], ["in", k_kind, "stream", "river", "canal"]]
                lyr.setdefault("layout", {})["text-field"] = name_expr
            elif "river" in lid or "canal" in lid:
                lyr["filter"] = ["in", k_kind, "river", "canal"]
            else:
                lyr["filter"] = ["in", k_kind, "stream", "river", "canal", "ditch", "drain"]
        elif sl == "water_name":
            lyr["source-layer"] = "water"
            if "ocean" in lid:
                lyr["filter"] = ["all", ["has", "name"], ["in", k_kind, "ocean", "sea", "bay", "strait"]]
            elif "lake" in lid:
                lyr["filter"] = ["all", ["has", "name"], ["in", k_kind, "lake", "water"]]
            else:
                lyr["filter"] = ["has", "name"]
            lyr.setdefault("layout", {})["text-field"] = name_expr
        elif sl == "park":
            lyr["source-layer"] = "landuse"
            lyr["filter"] = ["in", k_kind, "national_park", "park", "nature_reserve", "protected_area", "golf_course"]
        elif sl == "landcover":
            if "ice" in lid or "glacier" in lid:
                lyr["source-layer"] = "landcover"
                lyr["filter"] = ["==", k_kind, "glacier"]
            elif "wood" in lid:
                lyr["source-layer"] = "landuse"
                lyr["filter"] = ["in", k_kind, "forest", "wood"]
            elif "grass" in lid:
                lyr["source-layer"] = "landuse"
                lyr["filter"] = ["in", k_kind, "grass", "grassland", "meadow", "scrub"]
            elif "wetland" in lid:
                lyr["source-layer"] = "landuse"
                lyr["filter"] = ["in", k_kind, "wetland", "marsh"]
            elif "sand" in lid:
                lyr["source-layer"] = "landuse"
                lyr["filter"] = ["in", k_kind, "sand", "beach"]
            else:
                lyr["source-layer"] = "landcover"
                lyr["filter"] = ["==", "$type", "Polygon"]
        elif sl == "landuse":
            lyr["source-layer"] = "landuse"
            if "residential" in lid or "suburb" in lid:
                lyr["filter"] = ["in", k_kind, "residential", "urban_area"]
            elif "cemetery" in lid:
                lyr["filter"] = ["==", k_kind, "cemetery"]
            elif "hospital" in lid:
                lyr["filter"] = ["==", k_kind, "hospital"]
            elif "school" in lid:
                lyr["filter"] = ["in", k_kind, "school", "university", "college"]
            elif "pitch" in lid or "track" in lid or "stadium" in lid:
                lyr["filter"] = ["in", k_kind, "pitch", "playground", "stadium", "recreation_ground"]
            elif "commercial" in lid:
                lyr["filter"] = ["in", k_kind, "commercial", "retail"]
            elif "industrial" in lid:
                lyr["filter"] = ["==", k_kind, "industrial"]
            elif "military" in lid:
                lyr["filter"] = ["in", k_kind, "military", "naval_base"]
            else:
                lyr["filter"] = ["==", "$type", "Polygon"]
        elif sl == "building":
            lyr["source-layer"] = "buildings"
            lyr["filter"] = ["==", "$type", "Polygon"]
            paint = lyr.get("paint") or {}
            if "fill-extrusion-height" in paint:
                paint["fill-extrusion-height"] = ["coalesce", ["get", "height"], 8]
            if "fill-extrusion-base" in paint:
                paint["fill-extrusion-base"] = 0
        elif sl == "aeroway":
            if ltype == "fill":
                lyr["source-layer"] = "landuse"
                lyr["filter"] = ["in", k_kind, "aerodrome", "runway", "taxiway"]
            else:
                lyr["source-layer"] = "roads"
                lyr["filter"] = ["==", k_detail, "taxiway"] if "taxiway" in lid else ["==", k_detail, "runway"]
        elif sl == "boundary":
            lyr["source-layer"] = "boundaries"
            if "country" in lid or "_2" in lid:
                lyr["filter"] = ["<=", k_admin, 2]
            else:
                lyr["filter"] = [">", k_admin, 2]
        elif sl == "transportation":
            if ltype == "fill":
                lyr["source-layer"] = "landuse"
                lyr["filter"] = ["in", k_kind, "pier", "pedestrian"]
            elif ltype == "symbol":
                lyr["source-layer"] = "roads"
                lyr["filter"] = ["==", "oneway", "yes"]
            else:
                lyr["source-layer"] = "roads"
                conds_t: list = ["all"]
                if "tunnel" in lid:
                    conds_t.append(["has", "is_tunnel"])
                elif "bridge" in lid:
                    conds_t.append(["has", "is_bridge"])
                elif not any(w in lid for w in ("rail", "transit", "ferry", "pier")):
                    conds_t.extend([["!has", "is_tunnel"], ["!has", "is_bridge"]])

                if "rail" in lid or "transit" in lid:
                    conds_t.append(["==", k_kind, "rail"])
                elif "ferry" in lid:
                    conds_t.append(["==", k_kind, "ferry"])
                elif "pier" in lid:
                    conds_t.append(["==", k_detail, "pier"])
                elif any(w in lid for w in ("path", "pedestrian", "steps", "track")):
                    conds_t.append(["in", k_kind, "path", "other"])
                elif "link" in lid:
                    conds_t.append(["has", "is_link"])
                elif "motorway" in lid:
                    conds_t.extend([["==", k_kind, "highway"], ["!has", "is_link"]])
                elif any(w in lid for w in ("trunk", "primary", "secondary", "tertiary", "major")):
                    conds_t.extend([["==", k_kind, "major_road"], ["!has", "is_link"]])
                elif "service" in lid:
                    conds_t.extend([["==", k_kind, "minor_road"], ["==", k_detail, "service"]])
                elif "minor" in lid or "street" in lid:
                    conds_t.append(["==", k_kind, "minor_road"])
                else:
                    conds_t.append(["in", k_kind, "minor_road", "major_road", "highway"])
                lyr["filter"] = conds_t
        elif sl == "transportation_name":
            lyr["source-layer"] = "roads"
            layout = lyr.setdefault("layout", {})
            tf_raw = json.dumps(layout.get("text-field", ""))
            if "shield" in lid or ("motorway" in lid and "ref" in tf_raw):
                if "tertiary" in lid:
                    road_conds = [["==", ["get", k_kind], "major_road"], ["==", ["get", k_detail], "tertiary"]]
                elif "secondary" in lid:
                    road_conds = [["==", ["get", k_kind], "major_road"], ["==", ["get", k_detail], "secondary"]]
                elif "primary" in lid:
                    road_conds = [["==", ["get", k_kind], "major_road"], ["==", ["get", k_detail], "primary"]]
                elif "trunk" in lid:
                    road_conds = [["==", ["get", k_kind], "major_road"], ["==", ["get", k_detail], "trunk"]]
                elif "highway shield" in lid:
                    road_conds = [["==", ["get", k_kind], "highway"]]
                else:
                    road_conds = [["in", ["get", k_kind], ["literal", ["highway", "major_road"]]]]
                lyr["filter"] = [
                    "all",
                    *road_conds,
                    ["has", "ref"],
                    [">=", ["length", ["to-string", ["get", "ref"]]], 1],
                    ["<=", ["length", ["to-string", ["get", "ref"]]], 6],
                ]
                layout["text-field"] = ["to-string", ["get", "ref"]]
                if "icon-image" in layout:
                    img_s = str(layout["icon-image"])
                    if "ref_length" in img_s:
                        prefix = "default_" if "default_" in img_s else "road_"
                        layout["icon-image"] = ["concat", prefix, ["to-string", ["length", ["to-string", ["get", "ref"]]]]]
            elif "ferry" in lid:
                lyr["filter"] = ["all", ["has", "name"], ["==", k_kind, "ferry"]]
                layout["text-field"] = name_expr
            elif "major" in lid:
                lyr["filter"] = ["all", ["has", "name"], ["in", k_kind, "highway", "major_road"]]
                layout["text-field"] = name_expr
            elif "path" in lid:
                lyr["filter"] = ["all", ["has", "name"], ["in", k_kind, "path", "other"]]
                layout["text-field"] = name_expr
            else:
                lyr["filter"] = ["all", ["has", "name"], ["in", k_kind, "minor_road", "major_road"]]
                layout["text-field"] = name_expr
        elif sl == "place":
            lyr["source-layer"] = "places"
            layout = lyr.setdefault("layout", {})
            layout["text-field"] = name_expr
            if "continent" in lid or "country_other" in lid or "country_3" in lid:
                lyr["filter"] = ["==", ["get", k_kind], "__none__"]
            elif "country_major" in lid or "country_1" in lid:
                lyr["filter"] = ["all", ["==", ["get", k_kind], "country"], [">=", ["coalesce", ["get", k_pop], 10], 8]]
            elif "country_minor" in lid or "country_2" in lid:
                lyr["filter"] = ["all", ["==", ["get", k_kind], "country"], ["<", ["coalesce", ["get", k_pop], 5], 8]]
            elif "country" in lid:
                lyr["filter"] = ["==", ["get", k_kind], "country"]
            elif "state" in lid:
                lyr["filter"] = ["==", ["get", k_kind], "region"]
            elif "capital" in lid:
                lyr["filter"] = ["all", ["==", ["get", k_kind], "locality"], ["has", "capital"]]
            elif "city_large" in lid:
                lyr["filter"] = ["all", ["==", ["get", k_kind], "locality"], [">=", ["coalesce", ["get", k_pop], 0], 11]]
            elif "city" in lid:
                if has_city_large:
                    lyr["filter"] = [
                        "all",
                        ["==", ["get", k_kind], "locality"],
                        [">=", ["coalesce", ["get", k_pop], 0], 8],
                        ["<", ["coalesce", ["get", k_pop], 0], 11],
                    ]
                else:
                    lyr["filter"] = ["all", ["==", ["get", k_kind], "locality"], [">=", ["coalesce", ["get", k_pop], 0], 8]]
            elif "town" in lid:
                lyr["filter"] = [
                    "all",
                    ["==", ["get", k_kind], "locality"],
                    [">=", ["coalesce", ["get", k_pop], 0], 5],
                    ["<", ["coalesce", ["get", k_pop], 0], 8],
                ]
            elif "village" in lid:
                lyr["filter"] = ["all", ["==", ["get", k_kind], "locality"], ["<", ["coalesce", ["get", k_pop], 0], 5]]
            else:
                lyr["filter"] = ["in", ["get", k_kind], ["literal", ["neighbourhood", "macrohood", "suburb"]]]
        elif sl == "aerodrome_label":
            lyr["source-layer"] = "pois"
            lyr["filter"] = ["all", ["has", "name"], ["in", k_kind, "aerodrome", "airport"]]
            layout = lyr.setdefault("layout", {})
            layout["text-field"] = name_expr
            if "icon-image" in layout and "{" in str(layout["icon-image"]):
                del layout["icon-image"]
        elif sl == "mountain_peak":
            if "volcano" in lid:
                continue
            lyr["source-layer"] = "pois"
            lyr["filter"] = ["all", ["has", "name"], ["==", k_kind, "peak"]]
            layout = lyr.setdefault("layout", {})
            layout["text-field"] = name_expr
            if "icon-image" in layout and "{" in str(layout["icon-image"]):
                del layout["icon-image"]
        elif sl == "poi":
            if seen_poi_layer:
                continue
            seen_poi_layer = True
            lyr["source-layer"] = "pois"
            lyr["filter"] = ["all", ["has", "name"], [">=", ["zoom"], ["coalesce", ["get", k_minz], 14]]]
            layout = lyr.setdefault("layout", {})
            layout["text-field"] = name_expr
            if "icon-image" in layout:
                del layout["icon-image"]

        new_layers.append(lyr)

    if not inserted_earth:
        new_layers.insert(
            0,
            {
                "id": "earth",
                "type": "fill",
                "source": src_name,
                "source-layer": "earth",
                "filter": ["==", "$type", "Polygon"],
                "paint": {"fill-color": copy.deepcopy(land_color)},
            },
        )
    adapted["layers"] = new_layers
    return adapted


def _load_style_templates(tileserver_template_dir: str, vendor_dir: str) -> tuple[dict[str, dict], dict[str, dict]]:
    protomaps_templates: dict[str, dict] = {}
    src_styles_dir = os.path.join(tileserver_template_dir, "styles")
    for style_file in sorted(glob.glob(os.path.join(src_styles_dir, "*.json"))):
        style_id = os.path.splitext(os.path.basename(style_file))[0]
        with open(style_file, "r") as f:
            protomaps_templates[style_id] = json.load(f)

    omt_templates: dict[str, dict] = {}
    if vendor_dir:
        for style_id, meta in OPENMAPTILES_STYLE_DEFS.items():
            omt_path = os.path.join(vendor_dir, meta["vendor_dir"], "style.json")
            if os.path.isfile(omt_path):
                with open(omt_path, "r") as f:
                    omt_templates[style_id] = json.load(f)

    return protomaps_templates, omt_templates


def build_browser_style_json(
    data_dir: str,
    tileserver_template_dir: str,
    dataset_name: str,
    style_id: str,
    base_origin: str,
) -> dict | None:
    """Build a 100% self-contained offline MapLibre GL JS style JSON for any dataset + style combination."""
    datasets = discover_vector_datasets(data_dir)
    if not datasets:
        return None

    ds = next((d for d in datasets if d["name"] == dataset_name or d.get("ts_key") == dataset_name), None)
    if ds is None:
        ds = datasets[0]

    vendor_dir = _resolve_vendor_dir(tileserver_template_dir)
    protomaps_templates, omt_templates = _load_style_templates(tileserver_template_dir, vendor_dir)

    origin = base_origin.rstrip("/")
    max_z = int(ds.get("maxZoom", 14))
    if ds.get("is_pmtiles"):
        encoded_rel = "/".join(urllib.parse.quote(part) for part in ds["path"].split("/"))
        source_spec = {
            "type": "vector",
            "url": f"pmtiles://{origin}/data/{encoded_rel}",
            "maxzoom": max_z,
        }
    else:
        source_spec = {
            "type": "vector",
            "tiles": [f"{origin}/mvt/{urllib.parse.quote(ds['name'])}/{{z}}/{{x}}/{{y}}.pbf"],
            "maxzoom": max_z,
        }

    if style_id in omt_templates:
        meta = OPENMAPTILES_STYLE_DEFS[style_id]
        glyphs_url = f"{origin}/static/vendor/font-glyphs/glyphs/{{fontstack}}/{{range}}.pbf"
        sprite_url = f"{origin}/static/vendor/{meta['browser_sprite']}"
        if ds.get("is_mbtiles"):
            return _adapt_openmaptiles_style(
                omt_templates[style_id],
                source_spec=source_spec,
                sprite_ref=sprite_url,
                glyphs_ref=glyphs_url,
            )
        return _adapt_openmaptiles_style_to_protomaps(
            omt_templates[style_id],
            pmap_prefix=bool(ds.get("pmap_prefix", False)),
            source_spec=source_spec,
            sprite_ref=sprite_url,
            glyphs_ref=glyphs_url,
        )

    st_key = style_id if style_id in protomaps_templates else "light"
    if st_key not in protomaps_templates:
        return None

    valid_pm_sprites = {"light", "dark", "grayscale", "black", "white"}
    sp_name = st_key if st_key in valid_pm_sprites else "light"
    glyphs_url = f"{origin}/static/vendor/protomaps-assets/fonts/{{fontstack}}/{{range}}.pbf"
    sprite_url = f"{origin}/static/vendor/protomaps-assets/sprites/v4/{sp_name}"

    if ds.get("is_pmtiles"):
        base_obj = copy.deepcopy(protomaps_templates[st_key])
        base_obj["sources"] = {"protomaps": source_spec}
        base_obj["glyphs"] = glyphs_url
        base_obj["sprite"] = sprite_url
        return adapt_style_schema(base_obj, pmap_prefix=bool(ds.get("pmap_prefix", False)))

    return _adapt_protomaps_style_to_openmaptiles(
        protomaps_templates[st_key],
        source_spec=source_spec,
        sprite_ref=sprite_url,
        glyphs_ref=glyphs_url,
    )


def sync_tileserver_runtime(
    data_dir: str = "/app/data",
    tileserver_template_dir: str = "/app/tileserver",
    runtime_dir: str = "/tmp/tileserver",
    reload_process: bool = False,
) -> str:
    """Generate runtime TileServer-GL config and schema-adapted styles for all datasets. Returns config path."""
    if not os.path.isdir(tileserver_template_dir):
        return ""

    os.makedirs(data_dir, exist_ok=True)
    migrate_data_directory(data_dir)
    styles_out_dir = os.path.join(runtime_dir, "styles")
    os.makedirs(styles_out_dir, exist_ok=True)

    vendor_dir = _resolve_vendor_dir(tileserver_template_dir)
    fonts_dir, sprites_dir = _prepare_runtime_assets(tileserver_template_dir, vendor_dir, runtime_dir)

    datasets = discover_vector_datasets(data_dir)
    primary_pmtiles = select_primary_pmtiles(data_dir)
    protomaps_templates, omt_templates = _load_style_templates(tileserver_template_dir, vendor_dir)

    data_cfg: dict[str, dict[str, str]] = {}
    styles_cfg: dict[str, dict[str, str]] = {}

    if primary_pmtiles and os.path.isfile(primary_pmtiles):
        data_cfg["planet"] = {"pmtiles": os.path.abspath(primary_pmtiles)}

    default_ts_key = datasets[0]["ts_key"] if datasets else ""
    if primary_pmtiles and os.path.isfile(primary_pmtiles):
        rel_primary = os.path.relpath(primary_pmtiles, data_dir)
        matched = next((d for d in datasets if d["path"] == rel_primary), None)
        if matched:
            default_ts_key = matched["ts_key"]

    for ds in datasets:
        ts_key = ds["ts_key"]
        abs_archive = os.path.abspath(os.path.join(data_dir, ds["path"]))
        if ds.get("is_pmtiles"):
            data_cfg[ts_key] = {"pmtiles": abs_archive}
            pmap_prefix = bool(ds.get("pmap_prefix", False))
            pm_src = {"type": "vector", "url": f"pmtiles://{ts_key}"}

            # All 13 Protomaps styles on this PMTiles dataset
            for style_id, tpl in protomaps_templates.items():
                base_obj = copy.deepcopy(tpl)
                base_obj["sources"] = {"protomaps": pm_src}
                adapted = adapt_style_schema(base_obj, pmap_prefix=pmap_prefix)
                ds_style_key = f"{ts_key}__{style_id}"
                ds_fname = f"{ds_style_key}.json"
                with open(os.path.join(styles_out_dir, ds_fname), "w") as f:
                    json.dump(adapted, f)
                styles_cfg[ds_style_key] = {"style": ds_fname}

            # All 4 OpenMapTiles styles adapted onto this PMTiles dataset
            for style_id, tpl in omt_templates.items():
                sprite_ref = OPENMAPTILES_STYLE_DEFS[style_id]["sprite"]
                adapted = _adapt_openmaptiles_style_to_protomaps(
                    tpl,
                    pmap_prefix=pmap_prefix,
                    source_spec=pm_src,
                    sprite_ref=sprite_ref,
                )
                ds_style_key = f"{ts_key}__{style_id}"
                ds_fname = f"{ds_style_key}.json"
                with open(os.path.join(styles_out_dir, ds_fname), "w") as f:
                    json.dump(adapted, f)
                styles_cfg[ds_style_key] = {"style": ds_fname}

        elif ds.get("is_mbtiles"):
            data_cfg[ts_key] = {"mbtiles": abs_archive}
            mb_src = {"type": "vector", "url": f"mbtiles://{ts_key}"}

            # All 13 Protomaps styles adapted onto this OpenMapTiles MBTiles dataset
            for style_id, tpl in protomaps_templates.items():
                adapted = _adapt_protomaps_style_to_openmaptiles(tpl, source_spec=mb_src)
                ds_style_key = f"{ts_key}__{style_id}"
                ds_fname = f"{ds_style_key}.json"
                with open(os.path.join(styles_out_dir, ds_fname), "w") as f:
                    json.dump(adapted, f)
                styles_cfg[ds_style_key] = {"style": ds_fname}

            # All 4 OpenMapTiles styles on this MBTiles dataset
            for style_id, tpl in omt_templates.items():
                sprite_ref = OPENMAPTILES_STYLE_DEFS[style_id]["sprite"]
                adapted = _adapt_openmaptiles_style(tpl, source_spec=mb_src, sprite_ref=sprite_ref)
                ds_style_key = f"{ts_key}__{style_id}"
                ds_fname = f"{ds_style_key}.json"
                with open(os.path.join(styles_out_dir, ds_fname), "w") as f:
                    json.dump(adapted, f)
                styles_cfg[ds_style_key] = {"style": ds_fname}

    # Register bare style aliases for all 17 styles pointing to the default dataset
    if default_ts_key:
        for style_id in list(protomaps_templates.keys()) + list(omt_templates.keys()):
            target_fname = f"{default_ts_key}__{style_id}.json"
            alias_fname = f"{style_id}.json"
            src_file = os.path.join(styles_out_dir, target_fname)
            if os.path.isfile(src_file):
                with open(src_file, "r") as f_in, open(os.path.join(styles_out_dir, alias_fname), "w") as f_out:
                    f_out.write(f_in.read())
                styles_cfg[style_id] = {"style": alias_fname}

    ts_config = {
        "options": {
            "minRendererPoolSizes": [0, 0, 0],
            "maxRendererPoolSizes": [8, 4, 2],
            "paths": {
                "root": "",
                "fonts": fonts_dir,
                "sprites": sprites_dir,
                "styles": os.path.abspath(styles_out_dir),
                "mbtiles": os.path.abspath(data_dir),
                "pmtiles": os.path.abspath(data_dir),
            },
        },
        "styles": styles_cfg if data_cfg else {},
        "data": data_cfg,
    }

    runtime_cfg_path = os.path.join(runtime_dir, "config.json")
    old_content = ""
    if os.path.exists(runtime_cfg_path):
        try:
            with open(runtime_cfg_path, "r") as f:
                old_content = f.read()
        except Exception:
            pass

    new_content = json.dumps(ts_config, indent=2)
    with open(runtime_cfg_path, "w") as f:
        f.write(new_content)

    if reload_process and new_content != old_content:
        reload_tileserver()

    return runtime_cfg_path


def reload_tileserver() -> None:
    """Send SIGHUP to local TileServer-GL node process if running."""
    pid_file = "/tmp/tileserver.pid"
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, signal.SIGHUP)
            logger.info("Sent SIGHUP to TileServer-GL (PID %d)", pid)
            return
        except Exception as exc:
            logger.warning("Could not signal TileServer PID file: %s", exc)
    try:
        subprocess.run(["pkill", "-HUP", "-f", "tileserver-gl|usr/src/app"], check=False)
    except Exception:
        pass


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate runtime TileServer-GL configuration.")
    parser.add_argument("--data-dir", default="/app/data")
    parser.add_argument("--template-dir", default="/app/tileserver")
    parser.add_argument("--runtime-dir", default="/tmp/tileserver")
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    out = sync_tileserver_runtime(
        data_dir=args.data_dir,
        tileserver_template_dir=args.template_dir,
        runtime_dir=args.runtime_dir,
        reload_process=args.reload,
    )
    print(out)
