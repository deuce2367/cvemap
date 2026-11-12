import glob
import gzip
import json
import logging
import os
import signal
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


def inspect_pmtiles(filepath: str) -> dict:
    """Read minZoom, maxZoom, bounds, version, and schema prefix from a PMTiles v3 header."""
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
            if meta_len > 0 and meta_len < 10 * 1024 * 1024:
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
    return info


def adapt_style_schema(style_obj: dict, pmap_prefix: bool) -> dict:
    """Ensure Protomaps style property names match the active PMTiles schema (v3-alpha pmap:* vs v4 unprefixed)."""
    text = json.dumps(style_obj)
    if pmap_prefix:
        # Base styles use pmap:kind / pmap:kind_detail / pmap:min_zoom / pmap:population_rank
        return json.loads(text)
    # Modern Protomaps v4+ uses unprefixed kind, kind_detail, min_zoom, population_rank
    text = text.replace('"pmap:kind"', '"kind"')
    text = text.replace('"pmap:kind_detail"', '"kind_detail"')
    text = text.replace('"pmap:min_zoom"', '"min_zoom"')
    text = text.replace('"pmap:population_rank"', '"population_rank"')
    return json.loads(text)


def select_primary_pmtiles(data_dir: str, config_file: str = "") -> str:
    """Choose the primary PMTiles archive for TileServer-GL rendering."""
    candidates = sorted(glob.glob(os.path.join(data_dir, "webgl", "**", "*.pmtiles"), recursive=True))
    if not candidates:
        candidates = sorted(glob.glob(os.path.join(data_dir, "**", "*.pmtiles"), recursive=True))
    if not candidates:
        return ""

    # 1. Respect the first enabled existing .pmtiles layer in webgl_world config if present
    if config_file and os.path.exists(config_file):
        try:
            with open(config_file, "r") as f:
                cfg = json.load(f)
            layers = cfg.get("maps", {}).get("webgl_world", {}).get("layers", [])
            for layer in layers:
                if layer.get("enabled", True) and str(layer.get("path", "")).endswith(".pmtiles"):
                    candidate = os.path.join(data_dir, layer["path"])
                    if os.path.isfile(candidate):
                        return candidate
        except Exception:
            pass

    # 2. Prefer any planet*.pmtiles file
    for c in candidates:
        if os.path.basename(c).startswith("planet"):
            return c

    # 3. Otherwise use the largest .pmtiles file available
    return max(candidates, key=lambda p: os.path.getsize(p))


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
    os.makedirs(os.path.join(runtime_dir, "styles"), exist_ok=True)
    config_file = os.path.join(data_dir, "config.json")
    primary_pmtiles = select_primary_pmtiles(data_dir, config_file)

    pmap_prefix = True
    if primary_pmtiles and os.path.isfile(primary_pmtiles):
        info = inspect_pmtiles(primary_pmtiles)
        pmap_prefix = info["pmap_prefix"]

    # Write schema-matched styles to runtime_dir/styles
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
        # Also expose any other .pmtiles archives by basename
        for p in sorted(glob.glob(os.path.join(data_dir, "webgl", "**", "*.pmtiles"), recursive=True)):
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
