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
    "dark_matter": {
        "title": "Dark Matter (Dark)",
        "vendor_dir": "carto-dark",
        "sprite": "carto-dark/sprite",
    },
    "osm_bright": {
        "title": "OSM Bright (Colorful)",
        "vendor_dir": "carto-color",
        "sprite": "carto-color/sprite",
    },
    "osm_liberty": {
        "title": "OSM Liberty (Classic OSM)",
        "vendor_dir": "osm-liberty",
        "sprite": "osm-liberty/osm-liberty",
    },
    "positron": {
        "title": "Positron (Light)",
        "vendor_dir": "carto-light",
        "sprite": "carto-light/sprite",
    },
}

OPENMAPTILES_STYLES = list(OPENMAPTILES_STYLE_DEFS.keys())

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

        for sub in ("carto-light", "carto-dark", "carto-color", "osm-liberty"):
            src = os.path.abspath(os.path.join(vendor_dir, sub, "sprites"))
            dst = os.path.join(rt_sprites, sub)
            if os.path.isdir(src) and not os.path.lexists(dst):
                try:
                    os.symlink(src, dst)
                except OSError:
                    pass

    return rt_fonts, rt_sprites


def _adapt_openmaptiles_style(style_obj: dict, ts_key: str, sprite_ref: str) -> dict:
    adapted = copy.deepcopy(style_obj)
    sources = adapted.get("sources") or {}
    if "natural_earth_shaded_relief" in sources:
        del sources["natural_earth_shaded_relief"]
    if "layers" in adapted and isinstance(adapted["layers"], list):
        adapted["layers"] = [
            lyr for lyr in adapted["layers"] if lyr.get("source") != "natural_earth_shaded_relief"
        ]
    mbtiles_src = {"type": "vector", "url": f"mbtiles://{ts_key}"}
    if "openmaptiles" in sources:
        sources["openmaptiles"] = mbtiles_src
    elif "carto" in sources:
        sources["carto"] = mbtiles_src
    else:
        sources["openmaptiles"] = mbtiles_src
    adapted["sources"] = sources
    adapted["glyphs"] = "{fontstack}/{range}.pbf"
    adapted["sprite"] = sprite_ref
    return adapted


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

    data_cfg: dict[str, dict[str, str]] = {}
    styles_cfg: dict[str, dict[str, str]] = {}

    if primary_pmtiles and os.path.isfile(primary_pmtiles):
        data_cfg["planet"] = {"pmtiles": os.path.abspath(primary_pmtiles)}

    primary_pm_ts_key = ""
    if primary_pmtiles and os.path.isfile(primary_pmtiles):
        rel_primary = os.path.relpath(primary_pmtiles, data_dir)
        matched = next((d for d in datasets if d["path"] == rel_primary), None)
        if matched:
            primary_pm_ts_key = matched["ts_key"]

    primary_mb_ts_key = ""
    first_mb = next((d for d in datasets if d.get("is_mbtiles")), None)
    if first_mb:
        primary_mb_ts_key = first_mb["ts_key"]

    for ds in datasets:
        ts_key = ds["ts_key"]
        abs_archive = os.path.abspath(os.path.join(data_dir, ds["path"]))
        if ds.get("is_pmtiles"):
            data_cfg[ts_key] = {"pmtiles": abs_archive}
            if not primary_pm_ts_key:
                primary_pm_ts_key = ts_key
            pmap_prefix = bool(ds.get("pmap_prefix", False))
            for style_id, tpl in protomaps_templates.items():
                base_obj = copy.deepcopy(tpl)
                base_obj["sources"] = {"protomaps": {"type": "vector", "url": f"pmtiles://{ts_key}"}}
                adapted = adapt_style_schema(base_obj, pmap_prefix=pmap_prefix)
                ds_style_key = f"{ts_key}__{style_id}"
                ds_fname = f"{ds_style_key}.json"
                with open(os.path.join(styles_out_dir, ds_fname), "w") as f:
                    json.dump(adapted, f)
                styles_cfg[ds_style_key] = {"style": ds_fname}
        elif ds.get("is_mbtiles"):
            data_cfg[ts_key] = {"mbtiles": abs_archive}
            for style_id, tpl in omt_templates.items():
                sprite_ref = OPENMAPTILES_STYLE_DEFS[style_id]["sprite"]
                adapted = _adapt_openmaptiles_style(tpl, ts_key=ts_key, sprite_ref=sprite_ref)
                ds_style_key = f"{ts_key}__{style_id}"
                ds_fname = f"{ds_style_key}.json"
                with open(os.path.join(styles_out_dir, ds_fname), "w") as f:
                    json.dump(adapted, f)
                styles_cfg[ds_style_key] = {"style": ds_fname}

    # Register bare style aliases for the primary PMTiles and primary MBTiles datasets
    if primary_pm_ts_key:
        for style_id in protomaps_templates:
            target_fname = f"{primary_pm_ts_key}__{style_id}.json"
            alias_fname = f"{style_id}.json"
            src_file = os.path.join(styles_out_dir, target_fname)
            if os.path.isfile(src_file):
                with open(src_file, "r") as f_in, open(os.path.join(styles_out_dir, alias_fname), "w") as f_out:
                    f_out.write(f_in.read())
                styles_cfg[style_id] = {"style": alias_fname}

    if primary_mb_ts_key:
        for style_id in omt_templates:
            target_fname = f"{primary_mb_ts_key}__{style_id}.json"
            alias_fname = f"{style_id}.json"
            src_file = os.path.join(styles_out_dir, target_fname)
            if os.path.isfile(src_file) and style_id not in styles_cfg:
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
