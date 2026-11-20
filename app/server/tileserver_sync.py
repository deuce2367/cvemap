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
    "blueprint",
    "dark",
    "dusk_rose",
    "elementary",
    "flat",
    "grayscale",
    "icy_blue",
    "iris_bloom",
    "jurassic",
    "light",
    "matrix",
    "paladin",
    "rainforest",
    "seafoam",
    "sol",
    "synthwave",
    "tolkien",
    "wasteland",
    "western",
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
    if style_id in (
        "blueprint",
        "elementary",
        "icy_blue",
        "jurassic",
        "matrix",
        "paladin",
        "synthwave",
        "tolkien",
        "wasteland",
        "western",
    ):
        return style_id.replace("_", " ").title()
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

        fmt = meta.get("format", "").strip().lower()
        if fmt != "pbf":
            conn.close()
            _HEADER_CACHE[filepath] = (st.st_mtime, st.st_size, {})
            return None

        actual_max_z: int | None = None
        try:
            row = cur.execute("SELECT MAX(zoom_level) FROM tiles").fetchone()
            if row and row[0] is not None:
                actual_max_z = int(row[0])
        except Exception:
            pass
        conn.close()

        min_z = int(meta.get("minzoom", 0) or 0)
        raw_max = meta.get("maxzoom", "").strip()
        if raw_max:
            max_z = int(raw_max)
            if actual_max_z is not None:
                max_z = min(max_z, actual_max_z)
        elif actual_max_z is not None:
            max_z = actual_max_z
        else:
            max_z = 14
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
        st = os.stat(filepath)
        with open(filepath, "rb") as f:
            hdr = f.read(127)
            if len(hdr) < 127 or hdr[:7] != b"PMTiles" or hdr[7] != 3:
                logger.warning("Skipping incomplete or corrupted PMTiles file (invalid header): %s", filepath)
                return False
            tile_data_off, tile_data_len = struct.unpack("<QQ", hdr[56:72])
            expected_end = tile_data_off + tile_data_len
            if expected_end > st.st_size:
                logger.warning(
                    "Skipping truncated PMTiles file (%d < %d bytes): %s",
                    st.st_size,
                    expected_end,
                    filepath,
                )
                return False
            if hasattr(os, "SEEK_HOLE") and expected_end > 0:
                try:
                    first_hole = os.lseek(f.fileno(), 0, os.SEEK_HOLE)
                    if 0 <= first_hole < expected_end:
                        logger.warning(
                            "Skipping incomplete PMTiles file with sparse hole at byte %d (< %d): %s",
                            first_hole,
                            expected_end,
                            filepath,
                        )
                        return False
                except OSError:
                    pass
        return True
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
    extra_sources: dict | None = None,
) -> dict:
    """Adapt any Protomaps basemap style JSON so it renders natively on an OpenMapTiles (.mbtiles) dataset."""
    adapted = copy.deepcopy(style_obj)
    sources_dict = {"protomaps": source_spec}
    if extra_sources:
        sources_dict.update(extra_sources)
    adapted["sources"] = sources_dict
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

        if lyr.get("source") == "choropleth":
            new_layers.append(lyr)
            continue

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
        elif lid == "landuse_populated_glow":
            lyr["source-layer"] = "landuse"
            lyr["filter"] = ["in", "class", "residential", "commercial", "retail", "suburb", "quarter"]
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
        elif lid in ("water", "water_texture", "water_outline"):
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
        elif lid in ("boundaries_country", "boundaries_country_casing", "boundaries_country_outer"):
            lyr["source-layer"] = "boundary"
            lyr["filter"] = ["all", ["<=", "admin_level", 2], ["!=", "maritime", 1]]
        elif lid in ("boundaries", "boundaries_casing"):
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


_OMT_LAND_TO_PMAP: dict[str, tuple[str, ...]] = {
    "ice": ("glacier",),
    "glacier": ("glacier",),
    "ice_shelf": ("glacier",),
    "wood": ("forest", "wood"),
    "forest": ("forest", "wood"),
    "grass": ("grass", "grassland"),
    "grassland": ("grass", "grassland"),
    "meadow": ("meadow", "grassland", "village_green"),
    "reedbed": ("wetland", "marsh"),
    "saltmarsh": ("wetland", "marsh"),
    "village_green": ("village_green", "grass"),
    "golf_course": ("golf_course",),
    "scrub": ("scrub",),
    "heath": ("scrub",),
    "wetland": ("wetland", "marsh"),
    "marsh": ("marsh", "wetland"),
    "swamp": ("wetland",),
    "bog": ("wetland",),
    "mangrove": ("wetland",),
    "sand": ("sand", "beach"),
    "beach": ("beach", "sand"),
    "dune": ("sand",),
    "farmland": ("farmland",),
    "farm": ("farmland",),
    "agriculture": ("farmland",),
    "orchard": ("orchard",),
    "vineyard": ("vineyard",),
    "plant_nursery": ("orchard", "allotments"),
    "allotments": ("allotments",),
    "park": ("park", "nature_reserve"),
    "national_park": ("national_park", "nature_reserve", "protected_area"),
    "garden": ("garden",),
    "recreation_ground": ("recreation_ground", "park"),
    "residential": ("residential", "urban_area"),
    "suburb": ("residential", "urban_area"),
    "suburbs": ("residential", "urban_area"),
    "neighbourhood": ("residential", "urban_area"),
    "cemetery": ("cemetery",),
    "military": ("military", "naval_base"),
    "railway": ("railway",),
    "industrial": ("industrial",),
    "commercial": ("commercial",),
    "retail": ("retail",),
    "school": ("school",),
    "university": ("university",),
    "college": ("college",),
    "kindergarten": ("kindergarten", "school"),
    "hospital": ("hospital",),
    "stadium": ("stadium",),
    "playground": ("playground",),
    "pitch": ("pitch",),
    "track": ("track", "pitch"),
    "zoo": ("zoo",),
    "theme_park": ("theme_park", "zoo"),
    "dam": ("dam",),
    "quarry": ("quarry",),
}

_NATURAL_PMAP_KINDS = frozenset(
    {"forest", "wood", "grass", "grassland", "meadow", "scrub", "wetland", "marsh", "sand", "beach", "glacier"}
)

_UNSUPPORTED_OMT_ROAD_CLASSES = frozenset({"raceway", "aerialway", "busway", "bridleway"})


def _replace_json_expr(node: object, target: object, replacement: object) -> object:
    """Recursively replace exact JSON sub-expressions (e.g. ['get', 'rank']) inside layout/paint structures."""
    if node == target:
        return copy.deepcopy(replacement)
    if isinstance(node, list):
        return [_replace_json_expr(item, target, replacement) for item in node]
    if isinstance(node, dict):
        return {k: _replace_json_expr(v, target, replacement) for k, v in node.items()}
    return node


def _enforce_minzoom(lyr: dict, floor: float) -> None:
    cur = lyr.get("minzoom")
    if cur is None or cur < floor:
        lyr["minzoom"] = floor


def _parse_omt_filter(flt: object) -> dict:
    """Extract semantic OpenMapTiles filter constraints from legacy or compound MapLibre filter arrays."""
    info: dict = {
        "classes": set(),
        "neg_classes": set(),
        "subclasses": set(),
        "neg_subclasses": set(),
        "brunnel": None,  # 'tunnel' | 'bridge' | 'both' | 'surface' | None
        "ramp": None,  # True | False | None
        "intermittent": None,  # True | False | None
        "disputed": None,  # True | False | None
        "maritime": None,  # True | False | None
        "admin_levels": set(),
        "admin_max": None,
        "admin_min": None,
        "pos_service": False,
        "neg_service": False,
        "pos_capital": False,
        "neg_capital": False,
        "oneway_rev": False,
        "rank_max": None,
        "rank_min": None,
        "no_iso_a2": False,
        "geom_type": None,
    }

    def _walk(node: object, in_any: bool = False, negated: bool = False) -> None:
        if not isinstance(node, list) or not node:
            return
        op = node[0]
        if not isinstance(op, str):
            return
        if op == "all":
            for child in node[1:]:
                _walk(child, in_any=in_any, negated=negated)
            return
        if op == "any":
            for child in node[1:]:
                _walk(child, in_any=True, negated=negated)
            return
        if op in ("none", "!"):
            for child in node[1:]:
                _walk(child, in_any=in_any, negated=not negated)
            return

        if len(node) < 2:
            return
        raw_prop = node[1]
        if isinstance(raw_prop, list) and len(raw_prop) == 2 and raw_prop[0] == "get" and isinstance(raw_prop[1], str):
            prop = raw_prop[1]
        elif isinstance(raw_prop, str):
            prop = raw_prop
        else:
            return
        vals = node[2:]
        if len(vals) == 1 and isinstance(vals[0], list) and len(vals[0]) == 2 and vals[0][0] == "literal" and isinstance(vals[0][1], list):
            vals = vals[0][1]

        is_pos = (op in ("==", "in") and not negated) or (op in ("!=", "!in") and negated)
        is_neg = (op in ("!=", "!in") and not negated) or (op in ("==", "in") and negated)

        if prop == "$type" and is_pos and vals:
            info["geom_type"] = str(vals[0])
        elif prop == "class":
            if is_pos:
                info["classes"].update(str(v) for v in vals)
            elif is_neg and not in_any:
                info["neg_classes"].update(str(v) for v in vals)
        elif prop == "subclass":
            if is_pos:
                info["subclasses"].update(str(v) for v in vals)
            elif is_neg and not in_any:
                info["neg_subclasses"].update(str(v) for v in vals)
        elif prop in ("brunnel", "brunel"):
            vals_s = {str(v) for v in vals}
            if is_pos:
                if "tunnel" in vals_s and "bridge" in vals_s:
                    info["brunnel"] = "both"
                elif "tunnel" in vals_s:
                    info["brunnel"] = "tunnel"
                elif "bridge" in vals_s:
                    info["brunnel"] = "bridge"
            elif (is_neg or (op == "!has" and not negated)) and not in_any:
                if info["brunnel"] is None:
                    info["brunnel"] = "surface"
        elif prop == "ramp":
            truthy = any(str(v) in ("1", "True", "true", "yes") for v in vals)
            falsy = any(str(v) in ("0", "False", "false", "no") for v in vals)
            if (is_pos and truthy) or (op == "has" and not negated):
                info["ramp"] = True
            elif not in_any and ((is_neg and truthy) or (is_pos and falsy) or (op == "!has" and not negated)):
                info["ramp"] = False
        elif prop == "intermittent":
            if any(str(v) == "1" for v in vals):
                if is_pos:
                    info["intermittent"] = True
                elif is_neg and not in_any:
                    info["intermittent"] = False
        elif prop == "disputed":
            if any(str(v) == "1" for v in vals):
                if is_pos:
                    info["disputed"] = True
                elif is_neg and not in_any:
                    info["disputed"] = False
            elif any(str(v) == "0" for v in vals) and is_pos and not in_any:
                info["disputed"] = False
        elif prop == "maritime":
            if any(str(v) == "1" for v in vals):
                if is_pos:
                    info["maritime"] = True
                elif is_neg and not in_any:
                    info["maritime"] = False
            elif any(str(v) == "0" for v in vals) and is_pos and not in_any:
                info["maritime"] = False
        elif prop == "admin_level" and not negated:
            if op in ("==", "in"):
                for v in vals:
                    if isinstance(v, (int, float)):
                        info["admin_levels"].add(int(v))
            elif op == "<=" and vals and isinstance(vals[0], (int, float)):
                info["admin_max"] = int(vals[0])
            elif op in (">", ">=") and vals and isinstance(vals[0], (int, float)):
                info["admin_min"] = int(vals[0]) + (1 if op == ">" else 0)
        elif prop == "service" and not in_any:
            if (op == "has" and not negated) or is_pos:
                info["pos_service"] = True
            elif (op == "!has" and not negated) or is_neg:
                info["neg_service"] = True
        elif prop == "capital" and not in_any:
            if (op == "has" and not negated) or is_pos:
                info["pos_capital"] = True
            elif (op == "!has" and not negated) or is_neg:
                info["neg_capital"] = True
        elif prop == "oneway" and is_pos and vals == [-1]:
            info["oneway_rev"] = True
        elif prop == "rank" and not negated and vals and isinstance(vals[0], (int, float)):
            v_num = int(vals[0])
            if op == "<=":
                info["rank_max"] = v_num
            elif op == "<":
                info["rank_max"] = v_num - 1
            elif op == ">=":
                info["rank_min"] = v_num
            elif op == ">":
                info["rank_min"] = v_num + 1
        elif prop == "iso_a2" and op == "!has" and not negated:
            info["no_iso_a2"] = True

    _walk(flt)
    return info


def _in_legacy(prop: str, values: list[str] | tuple[str, ...]) -> list:
    uniq = list(dict.fromkeys(values))
    if len(uniq) == 1:
        return ["==", prop, uniq[0]]
    return ["in", prop, *uniq]


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
    k_pop = "pmap:population_rank" if pmap_prefix else "population_rank"
    k_minz = "pmap:min_zoom" if pmap_prefix else "min_zoom"
    name_expr = ["coalesce", ["get", "name:en"], ["get", "name"]]
    place_rank_expr = ["-", 16, ["coalesce", ["get", k_pop], 8]]
    poi_rank_expr = ["coalesce", ["get", k_minz], 14]

    land_color: object = "#f2f3f0"
    water_color: object = "#a0c8f0"
    orig_layers = adapted.get("layers", [])
    has_city_large = any("city_large" in (l.get("id") or "").lower() for l in orig_layers)
    has_lowzoom_point_water_label = any(
        l.get("source-layer") == "water_name"
        and "lakeline" not in (l.get("id") or "").lower()
        and (l.get("minzoom") or 0) <= 6
        for l in orig_layers
    )

    for lyr in orig_layers:
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
    seen_generic_poi = False

    for lyr in orig_layers:
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

        finfo = _parse_omt_filter(lyr.get("filter"))
        classes: set[str] = finfo["classes"]
        neg_classes: set[str] = finfo["neg_classes"]
        subclasses: set[str] = finfo["subclasses"]

        if sl == "water":
            lyr["source-layer"] = "water"
            if finfo["intermittent"] is True or "intermittent" in lid:
                lyr["filter"] = ["all", ["==", "$type", "Polygon"], ["has", "intermittent"]]
            else:
                lyr["filter"] = ["==", "$type", "Polygon"]

        elif sl == "waterway":
            if finfo["intermittent"] is True or finfo["brunnel"] in ("tunnel", "bridge"):
                continue
            lyr["source-layer"] = "physical_line" if pmap_prefix else "water"
            if ltype == "symbol":
                _enforce_minzoom(lyr, 10)
                lyr["filter"] = ["all", ["==", "$type", "LineString"], ["has", "name"], ["in", k_kind, "river", "canal", "stream"]]
                lyr.setdefault("layout", {})["text-field"] = name_expr
            elif "river" in classes and "canal" not in classes and "stream" not in classes:
                _enforce_minzoom(lyr, 6)
                lyr["filter"] = ["all", ["==", "$type", "LineString"], ["in", k_kind, "river", "canal"]]
            elif "river" in neg_classes or "stream" in classes or "ditch" in classes or "drain" in classes:
                _enforce_minzoom(lyr, 11)
                lyr["filter"] = ["all", ["==", "$type", "LineString"], ["in", k_kind, "stream", "canal", "ditch", "drain"]]
            else:
                _enforce_minzoom(lyr, 8)
                lyr["filter"] = ["all", ["==", "$type", "LineString"], ["in", k_kind, "river", "canal", "stream"]]

        elif sl == "water_name":
            is_lakeline = "lakeline" in lid
            if is_lakeline and has_lowzoom_point_water_label:
                continue
            lyr["source-layer"] = "physical_point" if pmap_prefix else "water"
            layout = lyr.setdefault("layout", {})
            layout["text-field"] = name_expr
            if layout.get("symbol-placement") == "line":
                del layout["symbol-placement"]
            if "ocean" in classes or "ocean" in lid:
                lyr["filter"] = ["all", ["==", "$type", "Point"], ["has", "name"], ["in", k_kind, "ocean", "sea", "bay", "strait"]]
            elif "ocean" in neg_classes or "lake" in classes or "lake" in lid:
                lyr["filter"] = ["all", ["==", "$type", "Point"], ["has", "name"], ["in", k_kind, "lake", "water", "bay"]]
            else:
                lyr["filter"] = [
                    "all",
                    ["==", ["geometry-type"], "Point"],
                    ["has", "name"],
                    [">=", ["zoom"], ["coalesce", ["get", k_minz], 3]],
                ]

        elif sl == "park":
            if ltype == "symbol":
                lyr["source-layer"] = "pois"
                _enforce_minzoom(lyr, 8)
                lyr["layout"] = _replace_json_expr(lyr.get("layout") or {}, ["get", "rank"], poi_rank_expr)
                lyr["layout"]["text-field"] = name_expr
                lyr["filter"] = [
                    "all",
                    ["has", "name"],
                    ["in", ["get", k_kind], ["literal", ["national_park", "nature_reserve", "protected_area"]]],
                ]
            elif ltype == "line":
                lyr["source-layer"] = "landuse"
                _enforce_minzoom(lyr, 8)
                lyr["filter"] = ["in", k_kind, "national_park", "nature_reserve", "protected_area"]
            else:
                lyr["source-layer"] = "landuse"
                lyr["filter"] = ["in", k_kind, "national_park", "park", "nature_reserve", "protected_area", "golf_course"]

        elif sl in ("landcover", "landuse"):
            paint = lyr.get("paint") or {}
            if "subclass" in json.dumps(paint):
                paint = _replace_json_expr(paint, ["get", "subclass"], ["get", k_kind])
                lyr["paint"] = paint
            if "class" in json.dumps(paint):
                paint = _replace_json_expr(paint, ["get", "class"], ["get", k_kind])
                lyr["paint"] = paint

            fp = paint.get("fill-pattern")
            if isinstance(fp, list) and fp and fp[0] == "match":
                # Restrict pattern match layers strictly to Protomaps kinds that resolve to a real sprite
                pattern_keys: list[str] = []
                idx = 2
                while idx + 1 < len(fp) - 1:
                    kspec = fp[idx]
                    sprite_val = fp[idx + 1]
                    if sprite_val:
                        if isinstance(kspec, list):
                            pattern_keys.extend(str(x) for x in kspec)
                        elif isinstance(kspec, str):
                            pattern_keys.append(kspec)
                    idx += 2
                valid_pkeys = [
                    pk for pk in pattern_keys
                    if pk in {
                        "allotments", "beach", "scrub", "forest", "marsh", "orchard",
                        "vineyard", "wetland", "wood", "military", "cemetery", "quarry",
                    }
                ]
                if not valid_pkeys:
                    continue
                _enforce_minzoom(lyr, 11)
                lyr["source-layer"] = "landuse"
                lyr["filter"] = _in_legacy(k_kind, valid_pkeys)
                if pmap_prefix and (set(valid_pkeys) & _NATURAL_PMAP_KINDS):
                    nat_clone = copy.deepcopy(lyr)
                    nat_clone["id"] = f"{lid_raw}__natural"
                    nat_clone["source-layer"] = "natural"
                    new_layers.append(nat_clone)
                new_layers.append(lyr)
                continue

            # OpenMapTiles 'landuse' only defines 'class' (never 'subclass'), whereas 'landcover' defines both
            tags = classes if sl == "landuse" else (classes | subclasses)
            if not tags:
                # Never fall back to unfiltered Polygon on landcover/landuse
                continue

            mapped_kinds: list[str] = []
            for t in sorted(tags):
                for mk in _OMT_LAND_TO_PMAP.get(t, ()):
                    if mk not in mapped_kinds:
                        mapped_kinds.append(mk)

            if not mapped_kinds:
                continue

            if ltype == "line" and sl == "landuse" and len(mapped_kinds) > 3:
                # Avoid drawing dark outlines around broad city-wide residential neighborhood polygons
                mapped_kinds = [mk for mk in mapped_kinds if mk not in ("residential", "urban_area")]
                if not mapped_kinds:
                    continue
                _enforce_minzoom(lyr, 14)

            if mapped_kinds == ["glacier"]:
                if ltype == "fill" and (lyr.get("minzoom") or 0) <= 7:
                    lc_clone = copy.deepcopy(lyr)
                    lc_clone["id"] = f"{lid_raw}__lowz"
                    lc_clone["source-layer"] = "landcover"
                    lc_clone["maxzoom"] = 8
                    lc_clone["filter"] = ["==", k_kind, "glacier"]
                    new_layers.append(lc_clone)
                lyr["source-layer"] = "natural" if pmap_prefix else "landuse"
                lyr["filter"] = ["==", k_kind, "glacier"]
            else:
                lyr["source-layer"] = "landuse"
                lyr["filter"] = _in_legacy(k_kind, mapped_kinds)
                if pmap_prefix and (set(mapped_kinds) & _NATURAL_PMAP_KINDS):
                    nat_clone = copy.deepcopy(lyr)
                    nat_clone["id"] = f"{lid_raw}__natural"
                    nat_clone["source-layer"] = "natural"
                    nat_kinds = [mk for mk in mapped_kinds if mk in _NATURAL_PMAP_KINDS]
                    nat_clone["filter"] = _in_legacy(k_kind, nat_kinds)
                    new_layers.append(nat_clone)

        elif sl == "building":
            lyr["source-layer"] = "buildings"
            _enforce_minzoom(lyr, 13)
            lyr["filter"] = ["==", "$type", "Polygon"]
            paint = lyr.get("paint") or {}
            if "fill-extrusion-height" in paint:
                paint["fill-extrusion-height"] = ["coalesce", ["get", "height"], 8]
            if "fill-extrusion-base" in paint:
                paint["fill-extrusion-base"] = 0

        elif sl == "aeroway":
            if ltype == "symbol":
                if "gate" in classes or "gate" in lid:
                    continue
                lyr["source-layer"] = "transit" if pmap_prefix else "roads"
                _enforce_minzoom(lyr, 14)
                layout = _replace_json_expr(lyr.get("layout") or {}, ["get", "rank"], poi_rank_expr)
                layout["text-field"] = name_expr
                lyr["layout"] = layout
                lyr["filter"] = ["all", ["has", "name"], ["in", k_detail, "runway", "taxiway"]]
            elif ltype == "fill":
                lyr["source-layer"] = "landuse"
                if classes == {"runway", "taxiway"}:
                    _enforce_minzoom(lyr, 11)
                    lyr["filter"] = ["in", k_kind, "runway", "taxiway"]
                else:
                    _enforce_minzoom(lyr, 11)
                    lyr["filter"] = ["in", k_kind, "aerodrome", "airfield", "runway", "taxiway"]
            else:
                lyr["source-layer"] = "transit" if pmap_prefix else "roads"
                _enforce_minzoom(lyr, 11)
                lyr["filter"] = ["==", k_detail, "taxiway"] if ("taxiway" in classes or "taxiway" in lid) else ["==", k_detail, "runway"]

        elif sl == "boundary":
            if finfo["maritime"] is True:
                continue
            lyr["source-layer"] = "boundaries"
            b_conds: list = ["all"]
            is_country = (
                2 in finfo["admin_levels"]
                or (finfo["admin_max"] is not None and finfo["admin_max"] <= 2)
                or (not finfo["admin_levels"] and finfo["admin_min"] is None and ("country" in lid or "_2" in lid or "disputed" in lid))
            )
            if is_country:
                b_conds.append(["==", k_kind, "country"])
            elif finfo["admin_levels"] and max(finfo["admin_levels"]) <= 4:
                b_conds.append(["==", k_kind, "region"])
            else:
                b_conds.append(["in", k_kind, "region", "county"])

            if finfo["disputed"] is True:
                b_conds.append(["has", "disputed"])
            elif finfo["disputed"] is False:
                b_conds.append(["!has", "disputed"])

            lyr["filter"] = b_conds[1] if len(b_conds) == 2 else b_conds

        elif sl == "transportation":
            if ltype == "fill":
                if "pier" in classes or "pier" in lid:
                    lyr["source-layer"] = "landuse"
                    _enforce_minzoom(lyr, 12)
                    lyr["filter"] = ["all", ["==", "$type", "Polygon"], ["==", k_kind, "pier"]]
                elif "bridge" in classes or "platform" in subclasses:
                    continue
                else:
                    lyr["source-layer"] = "landuse"
                    _enforce_minzoom(lyr, 13)
                    lyr["filter"] = ["all", ["==", "$type", "Polygon"], ["==", k_kind, "pedestrian"]]
            elif ltype == "symbol":
                if finfo["oneway_rev"]:
                    continue
                lyr["source-layer"] = "roads"
                _enforce_minzoom(lyr, 15)
                if "path" in classes:
                    lyr["filter"] = ["all", ["in", "oneway", "yes", 1, "1", True], ["==", k_kind, "path"]]
                else:
                    lyr["filter"] = ["all", ["in", "oneway", "yes", 1, "1", True], ["in", k_kind, "highway", "major_road", "medium_road", "minor_road"]]
            else:
                # Line layers: strip unsupported construction / raceway / aerialway / bridleway classes
                if subclasses == {"bridleway"}:
                    continue
                had_explicit_target = bool(classes or subclasses)
                eff_classes: list[str] = []
                eff_ramp = finfo["ramp"]
                for raw_c in sorted(classes):
                    if raw_c.endswith("_construction") or raw_c in _UNSUPPORTED_OMT_ROAD_CLASSES:
                        continue
                    if raw_c.endswith("_link"):
                        eff_classes.append(raw_c[:-5])
                        eff_ramp = True
                    else:
                        eff_classes.append(raw_c)
                eff_subclasses = {sc for sc in subclasses if sc != "bridleway"}

                if had_explicit_target and not eff_classes and not eff_subclasses:
                    continue

                if not eff_classes and (eff_subclasses & {"subway", "light_rail", "tram"}):
                    eff_classes = ["transit"]
                elif not eff_classes and eff_ramp is True:
                    eff_classes = ["link"]

                if not eff_classes:
                    continue

                is_transit_sl = pmap_prefix and set(eff_classes) <= {"rail", "transit", "ferry"}
                lyr["source-layer"] = "transit" if is_transit_sl else "roads"

                class_branches: list[list] = []
                minz_floors: list[float] = []

                for rc in eff_classes:
                    if rc == "motorway":
                        minz_floors.append(8 if eff_ramp is True else (6 if finfo["brunnel"] in ("tunnel", "bridge") else 4))
                        if eff_ramp is True:
                            class_branches.append(["==", k_detail, "motorway_link"])
                        elif eff_ramp is False:
                            class_branches.append(["==", k_detail, "motorway"])
                        else:
                            class_branches.append(["in", k_detail, "motorway", "motorway_link"])
                    elif rc == "trunk":
                        minz_floors.append(10 if eff_ramp is True else (7 if finfo["brunnel"] in ("tunnel", "bridge") else 5))
                        if eff_ramp is True:
                            class_branches.append(["==", k_detail, "trunk_link"])
                        elif eff_ramp is False:
                            class_branches.append(["==", k_detail, "trunk"])
                        else:
                            class_branches.append(["in", k_detail, "trunk", "trunk_link"])
                    elif rc == "primary":
                        minz_floors.append(10 if eff_ramp is True else (8 if finfo["brunnel"] in ("tunnel", "bridge") else 7))
                        if eff_ramp is True:
                            class_branches.append(["==", k_detail, "primary_link"])
                        elif eff_ramp is False:
                            class_branches.append(
                                ["any", ["==", k_detail, "primary"], ["all", ["==", k_kind, "major_road"], ["!has", k_detail]]]
                            )
                        else:
                            class_branches.append(
                                ["any", ["in", k_detail, "primary", "primary_link"], ["all", ["==", k_kind, "major_road"], ["!has", k_detail]]]
                            )
                    elif rc == "secondary":
                        minz_floors.append(11 if eff_ramp is True else 9)
                        if eff_ramp is True:
                            class_branches.append(["==", k_detail, "secondary_link"])
                        elif eff_ramp is False:
                            class_branches.append(["==", k_detail, "secondary"])
                        else:
                            class_branches.append(["in", k_detail, "secondary", "secondary_link"])
                    elif rc == "tertiary":
                        minz_floors.append(11 if eff_ramp is True else 10)
                        if eff_ramp is True:
                            class_branches.append(["==", k_detail, "tertiary_link"])
                        elif eff_ramp is False:
                            class_branches.append(["==", k_detail, "tertiary"])
                        else:
                            class_branches.append(["in", k_detail, "tertiary", "tertiary_link"])
                    elif rc == "link":
                        minz_floors.append(10)
                        class_branches.append(
                            ["in", k_detail, "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"]
                        )
                    elif rc in ("minor", "street", "street_limited"):
                        minz_floors.append(12)
                        class_branches.append(["in", k_detail, "residential", "unclassified", "living_street", "road"])
                    elif rc == "service":
                        minz_floors.append(13)
                        class_branches.append(["==", k_detail, "service"])
                    elif rc == "track":
                        minz_floors.append(13)
                        class_branches.append(["==", k_detail, "track"])
                    elif rc in ("path", "pedestrian", "steps", "cycleway", "footway"):
                        minz_floors.append(13)
                        if "steps" in eff_subclasses or rc == "steps":
                            class_branches.append(["==", k_detail, "steps"])
                        elif "cycleway" in eff_subclasses or rc == "cycleway":
                            class_branches.append(["==", k_detail, "cycleway"])
                        elif "pedestrian" in eff_subclasses or rc == "pedestrian":
                            class_branches.append(["==", k_detail, "pedestrian"])
                        elif "footway" in eff_subclasses or rc == "footway":
                            class_branches.append(["in", k_detail, "footway", "path", "sidewalk", "corridor"])
                        else:
                            class_branches.append(["all", ["==", k_kind, "path"], ["!=", k_detail, "pier"]])
                    elif rc == "pier":
                        minz_floors.append(12)
                        class_branches.append(["==", k_detail, "pier"])
                    elif rc == "ferry":
                        minz_floors.append(6)
                        class_branches.append(["==", k_kind, "ferry"])
                    elif rc == "rail":
                        minz_floors.append(13 if finfo["pos_service"] else 9)
                        rail_cond: list = ["all", ["==", k_kind, "rail"], ["!in", k_detail, "subway", "light_rail", "tram", "ventilation_shaft", "adjacent"]]
                        if finfo["pos_service"]:
                            rail_cond.append(["in", "service", "siding", "spur", "yard"])
                        elif finfo["neg_service"]:
                            rail_cond.append(["!in", "service", "siding", "spur", "yard"])
                        class_branches.append(rail_cond)
                    elif rc == "transit":
                        minz_floors.append(12)
                        t_kinds = sorted(eff_subclasses & {"subway", "light_rail", "tram"}) or ["subway", "light_rail", "tram"]
                        class_branches.append(_in_legacy(k_detail, t_kinds))

                if not class_branches:
                    continue

                if minz_floors:
                    _enforce_minzoom(lyr, min(minz_floors))

                conds_t: list = ["all"]
                brunnel = finfo["brunnel"]
                if brunnel == "tunnel":
                    conds_t.append(["<", "pmap:level", 0] if pmap_prefix else ["has", "is_tunnel"])
                elif brunnel == "bridge":
                    conds_t.append([">", "pmap:level", 0] if pmap_prefix else ["has", "is_bridge"])
                elif brunnel == "both":
                    if pmap_prefix:
                        conds_t.append(["in", "pmap:level", -1, 1])
                    else:
                        conds_t.append(["any", ["has", "is_tunnel"], ["has", "is_bridge"]])
                elif brunnel == "surface" and not (set(eff_classes) <= {"ferry", "pier"}):
                    if pmap_prefix:
                        conds_t.append(["!in", "pmap:level", -1, 1])
                    else:
                        conds_t.extend([["!has", "is_tunnel"], ["!has", "is_bridge"]])

                if len(class_branches) == 1:
                    cb = class_branches[0]
                    if cb[0] == "all":
                        conds_t.extend(cb[1:])
                    else:
                        conds_t.append(cb)
                else:
                    conds_t.append(["any", *class_branches])

                lyr["filter"] = conds_t[1] if len(conds_t) == 2 else conds_t

        elif sl == "transportation_name":
            layout = lyr.setdefault("layout", {})
            if "rank" in json.dumps(layout):
                layout = _replace_json_expr(layout, ["get", "rank"], poi_rank_expr)
                lyr["layout"] = layout
            tf_raw = json.dumps(layout.get("text-field", ""))
            if "shield" in lid or "ref" in tf_raw:
                lyr["source-layer"] = "roads"
                if classes == {"tertiary"} or "tertiary" in lid:
                    _enforce_minzoom(lyr, 12)
                    road_cond = ["==", ["get", k_detail], "tertiary"]
                elif classes == {"secondary"} or "secondary" in lid:
                    _enforce_minzoom(lyr, 11)
                    road_cond = ["==", ["get", k_detail], "secondary"]
                elif classes == {"primary"} or "primary" in lid:
                    _enforce_minzoom(lyr, 9)
                    road_cond = ["==", ["get", k_detail], "primary"]
                elif classes == {"trunk"} or "trunk" in lid:
                    _enforce_minzoom(lyr, 8)
                    road_cond = ["==", ["get", k_detail], "trunk"]
                elif classes == {"motorway"} or "highway shield" in lid:
                    _enforce_minzoom(lyr, 7)
                    road_cond = ["==", ["get", k_detail], "motorway"]
                else:
                    _enforce_minzoom(lyr, 7)
                    road_cond = ["in", ["get", k_detail], ["literal", ["motorway", "trunk", "primary"]]]
                lyr["filter"] = [
                    "all",
                    road_cond,
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
            elif classes == {"ferry"} or "ferry" in lid:
                lyr["source-layer"] = "transit" if pmap_prefix else "roads"
                _enforce_minzoom(lyr, 12)
                lyr["filter"] = ["all", ["has", "name"], ["==", k_kind, "ferry"]]
                layout["text-field"] = name_expr
            elif classes == {"path"} or "path" in lid:
                lyr["source-layer"] = "roads"
                _enforce_minzoom(lyr, 15)
                lyr["filter"] = ["all", ["has", "name"], ["==", k_kind, "path"]]
                layout["text-field"] = name_expr
            elif classes and classes <= {"motorway", "trunk", "primary", "secondary", "tertiary"}:
                lyr["source-layer"] = "roads"
                _enforce_minzoom(lyr, 11)
                details = sorted(classes)
                lyr["filter"] = ["all", ["has", "name"], _in_legacy(k_detail, details)]
                layout["text-field"] = name_expr
            elif classes and ("minor" in classes or "service" in classes):
                lyr["source-layer"] = "roads"
                _enforce_minzoom(lyr, 13)
                details = []
                for c in sorted(classes):
                    if c == "minor":
                        details.extend(["residential", "unclassified", "living_street"])
                    elif c in ("service", "track", "secondary", "tertiary"):
                        details.append(c)
                lyr["filter"] = ["all", ["has", "name"], _in_legacy(k_detail, details or ["residential", "unclassified"])]
                layout["text-field"] = name_expr
            else:
                lyr["source-layer"] = "roads"
                _enforce_minzoom(lyr, 11)
                expr_conds: list = [
                    "all",
                    ["has", "name"],
                    [
                        "match",
                        ["get", k_kind],
                        ["highway", "major_road", "medium_road"],
                        True,
                        "minor_road",
                        [">=", ["zoom"], 13],
                        "path",
                        [">=", ["zoom"], 15],
                        False,
                    ],
                ]
                if "motorway" in neg_classes:
                    expr_conds.append(["!=", ["get", k_detail], "motorway"])
                lyr["filter"] = expr_conds
                layout["text-field"] = name_expr

        elif sl == "place":
            if "continent" in classes or finfo["no_iso_a2"] or "continent" in lid or "country_other" in lid or "country_3" in lid:
                continue
            lyr["source-layer"] = "places"
            layout = _replace_json_expr(lyr.get("layout") or {}, ["get", "rank"], place_rank_expr)
            layout = _replace_json_expr(layout, " ", "")
            layout["text-field"] = name_expr
            lyr["layout"] = layout
            if "paint" in lyr:
                lyr["paint"] = _replace_json_expr(lyr["paint"], ["get", "rank"], place_rank_expr)

            cap_expr = ["in", ["to-string", ["coalesce", ["get", "capital"], ""]], ["literal", ["yes", "1", "2"]]]

            if "country" in classes or "country" in lid:
                if finfo["rank_max"] == 1 or "country_major" in lid or "country_1" in lid:
                    lyr["filter"] = ["all", ["==", ["get", k_kind], "country"], [">=", ["coalesce", ["get", k_pop], 10], 8]]
                elif (finfo["rank_min"] is not None and finfo["rank_min"] >= 2) or "country_minor" in lid or "country_2" in lid:
                    lyr["filter"] = ["all", ["==", ["get", k_kind], "country"], ["<", ["coalesce", ["get", k_pop], 5], 8]]
                else:
                    lyr["filter"] = ["==", ["get", k_kind], "country"]
            elif "state" in classes or "province" in classes or "state" in lid:
                lyr["filter"] = ["==", ["get", k_kind], "region"]
            elif "city" in classes or "city" in lid or "capital" in lid:
                if finfo["pos_capital"] or "capital" in lid:
                    lyr["filter"] = ["all", ["==", ["get", k_kind], "locality"], ["==", ["get", k_detail], "city"], cap_expr]
                elif finfo["neg_capital"]:
                    lyr["filter"] = ["all", ["==", ["get", k_kind], "locality"], ["==", ["get", k_detail], "city"], ["!", cap_expr]]
                elif "city_large" in lid or (finfo["rank_max"] is not None and has_city_large):
                    lyr["filter"] = [
                        "all",
                        ["==", ["get", k_kind], "locality"],
                        ["==", ["get", k_detail], "city"],
                        ["any", cap_expr, [">=", ["coalesce", ["get", k_pop], 0], 12]],
                    ]
                elif has_city_large:
                    lyr["filter"] = [
                        "all",
                        ["==", ["get", k_kind], "locality"],
                        ["==", ["get", k_detail], "city"],
                        ["!", cap_expr],
                        ["<", ["coalesce", ["get", k_pop], 9], 12],
                    ]
                else:
                    lyr["filter"] = ["all", ["==", ["get", k_kind], "locality"], ["==", ["get", k_detail], "city"]]
            elif "town" in classes or "town" in lid:
                _enforce_minzoom(lyr, 6)
                lyr["filter"] = ["all", ["==", ["get", k_kind], "locality"], ["==", ["get", k_detail], "town"]]
            elif "village" in classes or "village" in lid:
                _enforce_minzoom(lyr, 10)
                lyr["filter"] = ["all", ["==", ["get", k_kind], "locality"], ["==", ["get", k_detail], "village"]]
            elif classes == {"suburb"} or "suburb" in lid:
                _enforce_minzoom(lyr, 11)
                lyr["filter"] = ["any", ["==", ["get", k_kind], "macrohood"], ["==", ["get", k_detail], "suburb"]]
            else:
                _enforce_minzoom(lyr, 12)
                lyr["filter"] = [
                    "any",
                    ["==", ["get", k_kind], "neighbourhood"],
                    ["in", ["get", k_detail], ["literal", ["hamlet", "neighbourhood", "quarter", "isolated_dwelling"]]],
                ]

        elif sl == "aerodrome_label":
            lyr["source-layer"] = "pois"
            _enforce_minzoom(lyr, 10)
            lyr["filter"] = ["all", ["has", "name"], ["in", k_kind, "aerodrome", "airport"]]
            layout = _replace_json_expr(lyr.get("layout") or {}, ["get", "rank"], poi_rank_expr)
            layout["text-field"] = name_expr
            if "icon-image" in layout and "{" in str(layout["icon-image"]):
                del layout["icon-image"]
            lyr["layout"] = layout

        elif sl == "mountain_peak":
            if "volcano" in lid:
                continue
            lyr["source-layer"] = "physical_point" if pmap_prefix else "pois"
            _enforce_minzoom(lyr, 11)
            lyr["filter"] = ["all", ["has", "name"], ["==", k_kind, "peak"]]
            layout = _replace_json_expr(lyr.get("layout") or {}, ["get", "rank"], poi_rank_expr)
            layout["text-field"] = name_expr
            if "icon-image" in layout and "{" in str(layout["icon-image"]):
                del layout["icon-image"]
            lyr["layout"] = layout

        elif sl == "poi":
            if classes == {"park"}:
                lyr["source-layer"] = "pois"
                _enforce_minzoom(lyr, 13)
                layout = _replace_json_expr(lyr.get("layout") or {}, ["get", "rank"], poi_rank_expr)
                layout["text-field"] = name_expr
                if "icon-image" in layout:
                    del layout["icon-image"]
                lyr["layout"] = layout
                lyr["filter"] = ["all", ["has", "name"], ["in", k_kind, "park", "garden"]]
            elif classes == {"railway"} and (lyr.get("layout") or {}).get("icon-image") == "square_train":
                lyr["source-layer"] = "pois"
                _enforce_minzoom(lyr, 13)
                layout = _replace_json_expr(lyr.get("layout") or {}, ["get", "rank"], poi_rank_expr)
                layout["text-field"] = name_expr
                lyr["layout"] = layout
                lyr["filter"] = ["all", ["has", "name"], ["==", k_kind, "station"]]
            else:
                # Prefer a clean text-only or general POI layer without subclass-gated opacity
                if seen_generic_poi:
                    continue
                seen_generic_poi = True
                lyr["source-layer"] = "pois"
                _enforce_minzoom(lyr, 14)
                layout = _replace_json_expr(lyr.get("layout") or {}, ["get", "rank"], poi_rank_expr)
                layout["text-field"] = name_expr
                if "icon-image" in layout:
                    del layout["icon-image"]
                lyr["layout"] = layout
                paint = lyr.get("paint") or {}
                if isinstance(paint.get("text-opacity"), list):
                    paint["text-opacity"] = 1
                if "icon-opacity" in paint:
                    del paint["icon-opacity"]
                lyr["paint"] = paint
                lyr["filter"] = [
                    "all",
                    ["has", "name"],
                    ["!", ["in", ["get", k_kind], ["literal", ["park", "garden", "national_park", "nature_reserve", "protected_area", "station", "aerodrome", "airport", "peak"]]]],
                    [">=", ["zoom"], ["coalesce", ["get", k_minz], 14]],
                ]

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
    tpl_sprite = str(protomaps_templates[st_key].get("sprite", "")).split("/")[-1]
    sp_name = (
        st_key
        if st_key in valid_pm_sprites
        else (tpl_sprite if tpl_sprite in valid_pm_sprites else "light")
    )
    glyphs_url = f"{origin}/static/vendor/protomaps-assets/fonts/{{fontstack}}/{{range}}.pbf"
    sprite_url = f"{origin}/static/vendor/protomaps-assets/sprites/v4/{sp_name}"
    tpl_sources = protomaps_templates[st_key].get("sources") or {}
    extra_browser_sources: dict = {}
    if "choropleth" in tpl_sources:
        extra_browser_sources["choropleth"] = {
            "type": "vector",
            "url": f"pmtiles://{origin}/static/vendor/elementary/choropleth.pmtiles",
            "maxzoom": 7,
        }

    if ds.get("is_pmtiles"):
        base_obj = copy.deepcopy(protomaps_templates[st_key])
        base_sources = {"protomaps": source_spec}
        base_sources.update(extra_browser_sources)
        base_obj["sources"] = base_sources
        base_obj["glyphs"] = glyphs_url
        base_obj["sprite"] = sprite_url
        return adapt_style_schema(base_obj, pmap_prefix=bool(ds.get("pmap_prefix", False)))

    return _adapt_protomaps_style_to_openmaptiles(
        protomaps_templates[st_key],
        source_spec=source_spec,
        sprite_ref=sprite_url,
        glyphs_ref=glyphs_url,
        extra_sources=extra_browser_sources,
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

    choropleth_pmtiles = (
        os.path.join(vendor_dir, "elementary", "choropleth.pmtiles") if vendor_dir else ""
    )
    has_choropleth_pmtiles = bool(choropleth_pmtiles and os.path.isfile(choropleth_pmtiles))
    if has_choropleth_pmtiles:
        data_cfg["elementary_choropleth"] = {"pmtiles": os.path.abspath(choropleth_pmtiles)}
    ts_choropleth_src = {
        "type": "vector",
        "url": "pmtiles://elementary_choropleth",
        "maxzoom": 7,
    }

    default_ts_key = datasets[0]["ts_key"] if datasets else ""
    if primary_pmtiles and os.path.isfile(primary_pmtiles):
        rel_primary = os.path.relpath(primary_pmtiles, data_dir)
        matched = next((d for d in datasets if d["path"] == rel_primary), None)
        if matched:
            default_ts_key = matched["ts_key"]

    for ds in datasets:
        ts_key = ds["ts_key"]
        abs_archive = os.path.abspath(os.path.join(data_dir, ds["path"]))
        ds_max_z = int(ds.get("maxZoom", 14))
        if ds.get("is_pmtiles"):
            data_cfg[ts_key] = {"pmtiles": abs_archive}
            pmap_prefix = bool(ds.get("pmap_prefix", False))
            pm_src = {"type": "vector", "url": f"pmtiles://{ts_key}", "maxzoom": ds_max_z}

            # All Protomaps styles on this PMTiles dataset
            for style_id, tpl in protomaps_templates.items():
                base_obj = copy.deepcopy(tpl)
                sources_dict = {"protomaps": pm_src}
                if has_choropleth_pmtiles and "choropleth" in (tpl.get("sources") or {}):
                    sources_dict["choropleth"] = copy.deepcopy(ts_choropleth_src)
                base_obj["sources"] = sources_dict
                adapted = adapt_style_schema(base_obj, pmap_prefix=pmap_prefix)
                ds_style_key = f"{ts_key}__{style_id}"
                ds_fname = f"{ds_style_key}.json"
                with open(os.path.join(styles_out_dir, ds_fname), "w") as f:
                    json.dump(adapted, f)
                styles_cfg[ds_style_key] = {"style": ds_fname}

            # All OpenMapTiles styles adapted onto this PMTiles dataset
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
            mb_src = {"type": "vector", "url": f"mbtiles://{ts_key}", "maxzoom": ds_max_z}

            # All Protomaps styles adapted onto this OpenMapTiles MBTiles dataset
            for style_id, tpl in protomaps_templates.items():
                extra_s = (
                    {"choropleth": copy.deepcopy(ts_choropleth_src)}
                    if has_choropleth_pmtiles and "choropleth" in (tpl.get("sources") or {})
                    else None
                )
                adapted = _adapt_protomaps_style_to_openmaptiles(
                    tpl,
                    source_spec=mb_src,
                    extra_sources=extra_s,
                )
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
