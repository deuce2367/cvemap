import glob
import gzip
import json
import logging
import os
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

_HEADER_CACHE: dict[str, tuple[float, int, dict]] = {}


def format_style_title(style_id: str) -> str:
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

    for pf in discover_pmtiles_files(data_dir):
        base = os.path.splitext(os.path.basename(pf))[0]
        name = base if base not in seen_names else os.path.splitext(os.path.relpath(pf, data_dir))[0].replace(os.sep, "_")
        seen_names.add(name)
        pm_info = inspect_pmtiles(pf)
        entry = {
            "name": name,
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


def sync_tileserver_runtime(
    data_dir: str = "/app/data",
    tileserver_template_dir: str = "/app/tileserver",
    runtime_dir: str = "/tmp/tileserver",
    reload_process: bool = False,
) -> str:
    """Generate runtime TileServer-GL config and schema-adapted styles. Returns config path."""
    if not os.path.isdir(tileserver_template_dir):
        return ""

    os.makedirs(data_dir, exist_ok=True)
    migrate_data_directory(data_dir)
    os.makedirs(os.path.join(runtime_dir, "styles"), exist_ok=True)
    primary_pmtiles = select_primary_pmtiles(data_dir)

    pmap_prefix = True
    if primary_pmtiles and os.path.isfile(primary_pmtiles):
        info = inspect_pmtiles(primary_pmtiles)
        pmap_prefix = info["pmap_prefix"]

    src_styles_dir = os.path.join(tileserver_template_dir, "styles")
    styles_cfg = {}
    for style_file in sorted(glob.glob(os.path.join(src_styles_dir, "*.json"))):
        fname = os.path.basename(style_file)
        style_id = os.path.splitext(fname)[0]
        with open(style_file, "r") as f:
            style_obj = json.load(f)
        adapted = adapt_style_schema(style_obj, pmap_prefix=pmap_prefix)
        dst_style_path = os.path.join(runtime_dir, "styles", fname)
        with open(dst_style_path, "w") as f:
            json.dump(adapted, f)
        styles_cfg[style_id] = {"style": fname}

    data_cfg = {}
    if primary_pmtiles and os.path.isfile(primary_pmtiles):
        data_cfg["planet"] = {"pmtiles": os.path.abspath(primary_pmtiles)}
        for p in discover_pmtiles_files(data_dir):
            key = os.path.splitext(os.path.basename(p))[0]
            if key != "planet" and key not in data_cfg:
                data_cfg[key] = {"pmtiles": os.path.abspath(p)}

    ts_config = {
        "options": {
            "paths": {
                "root": "",
                "fonts": os.path.abspath(os.path.join(tileserver_template_dir, "fonts")),
                "sprites": os.path.abspath(os.path.join(tileserver_template_dir, "sprites")),
                "styles": os.path.abspath(os.path.join(runtime_dir, "styles")),
                "mbtiles": os.path.abspath(data_dir),
                "pmtiles": os.path.abspath(data_dir),
            }
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
