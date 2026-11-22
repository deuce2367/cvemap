#!/usr/bin/env python3
"""
Generate CVEMAP custom MapLibre style JSON files (under tileserver/styles/
and app/static/vendor/protomaps-themes/):
  1. elementary.json : Bright classroom / National Geographic choropleth with CC0 'Crayon Libre'
  2. matrix.json     : Matrix Reloaded green/jade palette, dark wine-red country borders, red interior borders,
                       and bright silver-blue 'Share Tech Mono' typography
  3. wasteland.json  : 1988 Wasteland nuclear amber/rust/gold palette (with extra gold/yellow in populated areas)
                       and OFL 'Black Ops One' & 'Quantico'
  4. tolkien.json    : Whimsical Middle-earth / Arda choropleth palette with textured waves water,
                       two-tone silver/gray country borders, and OFL 'Charm' calligraphic script typography
  5. paladin.json    : Tactical gunmetal/titanium grays & dark blues with brushed chrome highlights,
                       yellow-gold accents, and OFL 'Orbitron' robotic/futuristic typography
"""

import copy
import json
import os
import numpy as np
from PIL import Image


ELEMENTARY_COUNTRY_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#fbe17b",
    2, "#f9b679",
    3, "#a6df9e",
    4, "#f7a8bc",
    5, "#c9b1eb",
    6, "#fded9e",
    7, "#95dfd2",
    8, "#f79e8e",
    9, "#cbe693",
    "#fbe17b",
]

ELEMENTARY_STATE_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#fde382",
    2, "#f8b37c",
    3, "#a9e0a0",
    4, "#f6a6ba",
    5, "#c8b0ea",
    6, "#98dfd2",
    7, "#f69d8f",
    8, "#cce696",
    9, "#f9ca84",
    "#fde382",
]

MATRIX_COUNTRY_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#111811",  # Matrix Obsidian Green-Black
    2, "#2E3B2A",  # Inside-the-Matrix Dark Olive-Green (#2E3B2A)
    3, "#063B08",  # Deep Terminal Phosphor Green (#003B00)
    4, "#1d331d",  # Dark Digital Forest Green
    5, "#5C7A4A",  # Inside-the-Matrix Muted Forest Green (#5C7A4A)
    6, "#162616",  # Deep Matrix Code Moss Green
    7, "#264a28",  # Rich Matrix Emerald-Dark Green
    8, "#455e37",  # Matrix Sage-Olive Green
    9, "#33472b",  # Tinted Matrix Pine Green
    "#111811",
]

MATRIX_STATE_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#161f16",
    2, "#33422f",
    3, "#0a420c",
    4, "#223b22",
    5, "#61804f",
    6, "#1b2e1b",
    7, "#2b522d",
    8, "#4b663c",
    9, "#394f30",
    "#161f16",
]

WASTELAND_COUNTRY_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#4d2b14",
    2, "#633015",
    3, "#9c6314",  # Warm Wasteland gold-ochre
    4, "#913f16",  # Brighter burnt orange
    5, "#a34516",  # Vibrant wasteland rust-orange
    6, "#b84d16",  # Bright atomic orange
    7, "#592811",
    8, "#bd7a16",  # Atomic amber-gold
    9, "#733414",
    "#4d2b14",
]

WASTELAND_STATE_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#542e15",
    2, "#a16714",  # Warm gold-ochre
    3, "#803b17",
    4, "#964117",
    5, "#a84717",
    6, "#c27e16",  # Atomic amber-gold
    7, "#5e2a12",
    8, "#873815",
    9, "#733414",
    "#542e15",
]

TOLKIEN_COUNTRY_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#9fb183",  # Valinor / Falas Soft Moss-Sage Green
    2, "#8ba968",  # Aman / West Beleriand Meadow Green
    3, "#b4b196",  # Harad / Middle-earth Weathered Parchment-Sand
    4, "#7c9470",  # Taur-im-Duinath / Doriath Deep Woodland Olive
    5, "#a6b89a",  # East Beleriand / Ossiriand Sage-Celadon
    6, "#839c5e",  # Numenor / Greenwood Rich Moss-Olive
    7, "#8494a1",  # Ered Luin / Blue Mountains Highland Slate
    8, "#a8a68a",  # Northern Waste / Rhun Olive-Parchment
    9, "#b3bfa8",  # Lothlann / Ard-Galen Pale Silvery-Sage
    "#9fb183",
]

TOLKIEN_STATE_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#a5b789",
    2, "#91af6e",
    3, "#bab79c",
    4, "#829a76",
    5, "#acbe9f",
    6, "#89a264",
    7, "#8a9aa7",
    8, "#aeac90",
    9, "#b9c5ae",
    "#a5b789",
]

PALADIN_COUNTRY_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#2d3136",  # Dark Gunmetal Gray
    2, "#3b4046",  # Brushed Titanium Gray
    3, "#4a5058",  # Machined Steel Gray
    4, "#25292d",  # Deep Carbon-Slate Gray
    5, "#1f2f42",  # Deep Paladin Navy / Dark Steel-Blue
    6, "#42484f",  # Forged Iron Gray
    7, "#565d65",  # Chrome-Pewter Gray
    8, "#25364a",  # Dark Cobalt-Slate Blue
    9, "#4d545c",  # Medium Alloy Gray
    "#2d3136",
]

PALADIN_STATE_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#31363b",  # Gunmetal Gray
    2, "#40464d",  # Brushed Steel Gray
    3, "#4f565e",  # Machined Steel Gray
    4, "#292d32",  # Carbon-Slate Gray
    5, "#233449",  # Deep Paladin Navy-Slate Blue
    6, "#464c54",  # Forged Iron Gray
    7, "#59616a",  # Chrome-Pewter Gray
    8, "#293b52",  # Dark Steel-Blue
    9, "#515860",  # Medium Alloy Gray
    "#31363b",
]

BLUEPRINT_COUNTRY_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#124378",
    2, "#164d87",
    3, "#1b5796",
    4, "#144880",
    5, "#1f60a3",
    6, "#18528f",
    7, "#2369b0",
    8, "#0f3c6e",
    9, "#1a5594",
    "#124378",
]

BLUEPRINT_STATE_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#14477d",
    2, "#18518c",
    3, "#1d5b9c",
    4, "#164c85",
    5, "#2164a8",
    6, "#1a5694",
    7, "#256db5",
    8, "#114073",
    9, "#1c5999",
    "#14477d",
]

SYNTHWAVE_COUNTRY_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#1f0c3d",  # Deep Outrun Indigo
    2, "#2e1154",  # Neon Violet
    3, "#401469",  # Cyber Purple
    4, "#521675",  # Sunset Magenta-Violet
    5, "#24164d",  # Arcade Twilight Blue-Violet
    6, "#63197d",  # Electric Plum
    7, "#36125c",  # Royal Synth Purple
    8, "#751c82",  # Hot Orchid Purple
    9, "#1a1240",  # Midnight Synth Navy
    "#1f0c3d",
]

SYNTHWAVE_STATE_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#230e42",
    2, "#321359",
    3, "#44166e",
    4, "#57187a",
    5, "#281852",
    6, "#681b82",
    7, "#3a1461",
    8, "#7a1e87",
    9, "#1e1445",
    "#230e42",
]

# Cyber Futuristic City Palette:
# Swatches from reference: #5137b4, #aa4bdd, #ff40aa, #ffcfd5, #72c4ff, #c7ffff
# Background tones: #080644, #240c55, #404bb1
CYBER_COUNTRY_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#1d1666",  # Deep Cyber Midnight Indigo
    2, "#5137b4",  # Swatch 1: Electric Indigo-Violet (#5137b4)
    3, "#2d248a",  # Futuristic Tower Sapphire-Indigo
    4, "#6b3ec7",  # Neon Violet-Indigo
    5, "#404bb1",  # City Skyline Cobalt-Indigo (#404bb1)
    6, "#8545d4",  # Deep Amethyst Purple
    7, "#252d80",  # Starlight Navy-Indigo
    8, "#aa4bdd",  # Swatch 2: Luminous Amethyst (#aa4bdd)
    9, "#362b9e",  # Royal Cyber Indigo
    "#1d1666",
]

CYBER_STATE_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#221a70",
    2, "#573cbd",
    3, "#322994",
    4, "#7244cf",
    5, "#4651ba",
    6, "#8c4bde",
    7, "#2a338a",
    8, "#b052e3",
    9, "#3c30a8",
    "#221a70",
]


def build_elementary_style(light_tpl: dict) -> dict:
    style = copy.deepcopy(light_tpl)
    style["name"] = "Elementary"
    style["glyphs"] = "{fontstack}/{range}.pbf"
    style["sprite"] = "v4/light"
    style["sources"] = {
        "protomaps": {"type": "vector", "url": "mbtiles://planet"},
        "choropleth": {"type": "vector", "url": "pmtiles://elementary_choropleth", "maxzoom": 7},
    }

    new_layers: list[dict] = []
    for lyr in style.get("layers", []):
        lid = lyr.get("id", "")
        ltype = lyr.get("type", "")
        paint = lyr.setdefault("paint", {})
        layout = lyr.get("layout")

        if lid == "background":
            paint["background-color"] = "#7ec5ea"
            new_layers.append(lyr)
            continue

        if lid == "earth":
            paint["fill-color"] = "#faf2dc"
            new_layers.append(lyr)
            new_layers.append(
                {
                    "id": "choropleth_countries",
                    "type": "fill",
                    "source": "choropleth",
                    "source-layer": "choropleth",
                    "filter": ["==", ["get", "kind"], "country"],
                    "paint": {
                        "fill-color": ELEMENTARY_COUNTRY_COLORS,
                        "fill-opacity": [
                            "interpolate", ["linear"], ["zoom"],
                            0, 0.94, 4, 0.90, 6, 0.74, 9, 0.42, 12, 0.25, 15, 0.18,
                        ],
                    },
                }
            )
            new_layers.append(
                {
                    "id": "choropleth_regions",
                    "type": "fill",
                    "source": "choropleth",
                    "source-layer": "choropleth",
                    "minzoom": 2,
                    "filter": ["==", ["get", "kind"], "region"],
                    "paint": {
                        "fill-color": ELEMENTARY_STATE_COLORS,
                        "fill-opacity": [
                            "interpolate", ["linear"], ["zoom"],
                            2, 0.0, 2.8, 0.82, 5, 0.84, 7.5, 0.62, 10, 0.35, 13, 0.20,
                        ],
                    },
                }
            )
            continue

        if lid == "landcover":
            lyr["minzoom"] = 5
            paint["fill-color"] = [
                "match", ["get", "pmap:kind"],
                "grassland", "rgba(166, 224, 148, 1)",
                "barren", "rgba(252, 230, 172, 1)",
                "urban_area", "rgba(247, 202, 172, 1)",
                "farmland", "rgba(212, 236, 154, 1)",
                "glacier", "rgba(244, 250, 255, 1)",
                "scrub", "rgba(196, 230, 158, 1)",
                "rgba(138, 214, 142, 1)",
            ]
            paint["fill-opacity"] = ["interpolate", ["linear"], ["zoom"], 5, 0.0, 6.2, 0.35, 7.5, 0.0]
            new_layers.append(lyr)
            continue

        if lid.startswith("landuse_park"):
            paint["fill-color"] = [
                "case",
                ["in", ["get", "pmap:kind"], ["literal", ["national_park", "park", "cemetery", "protected_area", "nature_reserve", "forest", "golf_course"]]],
                "#88d47e",
                ["==", ["get", "pmap:kind"], "wood"],
                "#78ca74",
                ["in", ["get", "pmap:kind"], ["literal", ["scrub", "grassland", "grass"]]],
                "#a4dc8c",
                ["==", ["get", "pmap:kind"], "glacier"],
                "#f2f8fc",
                ["==", ["get", "pmap:kind"], "sand"],
                "#fde07d",
                ["in", ["get", "pmap:kind"], ["literal", ["military", "naval_base", "airfield"]]],
                "#d8cfc2",
                "#faf2dc",
            ]
            paint["fill-opacity"] = ["interpolate", ["linear"], ["zoom"], 6, 0.0, 9, 0.55, 12, 0.82]
            new_layers.append(lyr)
            continue

        if lid.startswith("landuse_urban_green"):
            paint["fill-color"] = "#82d178"
            paint["fill-opacity"] = 0.78
        elif lid.startswith("landuse_hospital"):
            paint["fill-color"] = "#f9b3be"
        elif lid.startswith("landuse_industrial"):
            paint["fill-color"] = "#dfd2eb"
        elif lid.startswith("landuse_school"):
            paint["fill-color"] = "#ffd970"
        elif lid.startswith("landuse_beach"):
            paint["fill-color"] = "#fde07d"
        elif lid.startswith("landuse_zoo"):
            paint["fill-color"] = "#fbc888"
        elif lid.startswith("landuse_aerodrome"):
            paint["fill-color"] = "#d8dfea"
        elif lid.startswith("landuse_runway") or lid in ("roads_runway", "roads_taxiway"):
            if ltype == "fill":
                paint["fill-color"] = "#ebe5d8"
            else:
                paint["line-color"] = "#ebe5d8"
        elif lid == "water":
            paint["fill-color"] = "#7ec5ea"
            new_layers.append(lyr)
            new_layers.append(
                {
                    "id": "water_outline",
                    "type": "line",
                    "source": "protomaps",
                    "source-layer": "water",
                    "filter": ["==", "$type", "Polygon"],
                    "paint": {
                        "line-color": "#4594c4",
                        "line-width": ["interpolate", ["linear"], ["zoom"], 0, 0.6, 4, 1.0, 8, 1.4, 14, 1.8],
                        "line-opacity": 0.85,
                    },
                }
            )
            continue
        elif lid in ("water_stream", "water_river"):
            paint["line-color"] = "#56aee0"
        elif lid.startswith("landuse_pedestrian"):
            paint["fill-color"] = "#f2e5c9"
        elif lid.startswith("landuse_pier") or lid == "roads_pier":
            if ltype == "fill":
                paint["fill-color"] = "#ebdcc0"
            else:
                paint["line-color"] = "#ebdcc0"
        elif lid == "buildings":
            paint["fill-color"] = "#e8c7a3"
            paint["fill-opacity"] = 0.82
            paint["fill-outline-color"] = "#c49c72"
        elif "tunnel" in lid and ltype == "line":
            paint["line-color"] = "#c9b497" if "casing" in lid else "#ede2ce"
        elif lid.startswith("roads_") and ltype == "line":
            if lid == "roads_rail":
                paint["line-color"] = "#735d52"
            elif "highway" in lid and "casing" in lid:
                paint["line-color"] = "#9e331e"
            elif "highway" in lid:
                paint["line-color"] = "#f06449"
            elif "major" in lid and "casing" in lid:
                paint["line-color"] = "#ab7319"
            elif "major" in lid:
                paint["line-color"] = "#f9c24e"
            elif "link" in lid and "casing" in lid:
                paint["line-color"] = "#b5853b"
            elif "link" in lid:
                paint["line-color"] = "#fce08b"
            elif "casing" in lid:
                paint["line-color"] = "#c4af91"
            elif lid == "roads_minor":
                paint["line-color"] = ["interpolate", ["linear"], ["zoom"], 12, "#f3ead8", 15, "#fffdf7"]
            else:
                paint["line-color"] = "#f7f0e1"
        elif lid == "boundaries_country":
            casing_lyr = copy.deepcopy(lyr)
            casing_lyr["id"] = "boundaries_country_casing"
            casing_lyr["paint"] = {
                "line-color": "#e0533c",
                "line-opacity": 0.34,
                "line-width": ["interpolate", ["linear"], ["zoom"], 0, 2.0, 2, 3.2, 5, 5.5, 8, 8.5, 12, 11.0],
            }
            new_layers.append(casing_lyr)
            paint["line-color"] = "#591c15"
            paint["line-width"] = ["interpolate", ["linear"], ["zoom"], 0, 0.8, 2, 1.2, 5, 1.8, 8, 2.5, 12, 3.2]
            new_layers.append(lyr)
            continue
        elif lid == "boundaries":
            casing_lyr = copy.deepcopy(lyr)
            casing_lyr["id"] = "boundaries_casing"
            casing_lyr["paint"] = {
                "line-color": "#9c5638",
                "line-opacity": 0.22,
                "line-width": ["interpolate", ["linear"], ["zoom"], 3, 1.6, 6, 3.2, 10, 5.0],
            }
            new_layers.append(casing_lyr)
            paint["line-color"] = "#5c3828"
            paint["line-width"] = ["interpolate", ["linear"], ["zoom"], 3, 0.7, 6, 1.2, 10, 1.8]
            new_layers.append(lyr)
            continue

        if ltype == "symbol" and isinstance(layout, dict):
            _apply_custom_fonts(
                lyr=lyr,
                lid=lid,
                bold_font="Crayon Libre Bold",
                reg_font="Crayon Libre Regular",
                italic_font="Crayon Libre Italic",
                country_color="#2b1912",
                country_halo="#fffdf7",
                region_color="#592e1e",
                region_halo="#fffaf0",
                city_color="#241914",
                city_halo="#ffffff",
                subplace_color="#4a352b",
                subplace_halo="#fffdf7",
                water_color="#164e78",
                water_halo="#d8f0ff",
                road_color="#4a372d",
                road_halo="#fffdf7",
                shield_color="#2b1912",
                poi_halo="#ffffff",
            )

        new_layers.append(lyr)

    style["layers"] = new_layers
    return style


def _make_tolkien_wave_tile(size: int = 64) -> Image.Image:
    """Create a subtle, fine-grained seamless blue-on-blue Middle-earth wave texture tile (size x size RGBA)."""
    y, x = np.mgrid[0:size, 0:size].astype(np.float64)
    u = 2.0 * np.pi * x / size
    v = 2.0 * np.pi * y / size

    # Finer, smaller-scale periodic horizontal wave undulations (12 wave rows per 64px tile)
    undulation = (
        0.45 * np.sin(4.0 * u + 0.5 * np.sin(2.0 * v))
        + 0.25 * np.cos(6.0 * u - 2.0 * v)
        + 0.15 * np.sin(2.0 * u + 4.0 * v)
    )
    phase = 12.0 * v + 1.25 * undulation
    wave = np.sin(phase)
    # Fine woodcut pen-etching harmonic
    etch = np.sin(24.0 * v + 1.8 * undulation + 0.5 * np.cos(4.0 * u))

    # Base Belegaer / Great Sea cerulean-steel blue (#3b70a6 -> 59, 112, 166)
    r = np.full((size, size), 59.0)
    g = np.full((size, size), 112.0)
    b = np.full((size, size), 166.0)

    # Whisper-subtle blue-on-blue wave troughs (very closely spaced tones)
    trough = np.clip(-wave, 0.0, 1.0) ** 1.35
    r -= trough * 2.8
    g -= trough * 3.2
    b -= trough * 3.8

    # Whisper-subtle blue-on-blue wave crests
    crest = np.clip(wave, 0.0, 1.0) ** 1.55
    r += crest * 2.4
    g += crest * 2.8
    b += crest * 3.4

    # Gentle horizontal pen lines along wave ridges
    ridge = (etch > 0.78).astype(np.float64) * 0.25
    r -= ridge * 2.2
    g -= ridge * 2.6
    b -= ridge * 3.0

    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    rgba[..., 0] = np.clip(r, 0, 255).astype(np.uint8)
    rgba[..., 1] = np.clip(g, 0, 255).astype(np.uint8)
    rgba[..., 2] = np.clip(b, 0, 255).astype(np.uint8)
    rgba[..., 3] = 255
    return Image.fromarray(rgba, mode="RGBA")


def _make_western_water_tile(size: int = 64) -> Image.Image:
    """Create a rich Cobalt Blue water texture tile."""
    y, x = np.mgrid[0:size, 0:size].astype(np.float64)
    u = 2.0 * np.pi * x / size
    v = 2.0 * np.pi * y / size

    undulation = 0.40 * np.sin(4.0 * u + 0.5 * np.sin(2.0 * v)) + 0.20 * np.cos(6.0 * u - 2.0 * v)
    wave = np.sin(12.0 * v + 1.2 * undulation)

    # Rich Cobalt Blue base (#0649c4 -> 6, 73, 196)
    r = np.full((size, size), 6.0)
    g = np.full((size, size), 73.0)
    b = np.full((size, size), 196.0)

    r += wave * 4.0
    g += wave * 7.0
    b += wave * 12.0

    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    rgba[..., 0] = np.clip(r, 0, 255).astype(np.uint8)
    rgba[..., 1] = np.clip(g, 0, 255).astype(np.uint8)
    rgba[..., 2] = np.clip(b, 0, 255).astype(np.uint8)
    rgba[..., 3] = 255
    return Image.fromarray(rgba, mode="RGBA")


def update_tolkien_water_sprite(repo_root: str) -> None:
    """Idempotently add 'tolkien_water_waves' and 'western_water_waves' to v4/light sprites."""
    sprite_dirs = [
        os.path.join(repo_root, "tileserver", "sprites", "v4"),
        os.path.join(repo_root, "app", "static", "vendor", "protomaps-assets", "sprites", "v4"),
    ]
    tiles_1x = [
        ("tolkien_water_waves", 0, _make_tolkien_wave_tile(64)),
        ("western_water_waves", 64, _make_western_water_tile(64)),
    ]
    tiles_2x = [
        ("tolkien_water_waves", 0, _make_tolkien_wave_tile(128)),
        ("western_water_waves", 128, _make_western_water_tile(128)),
    ]

    for sdir in sprite_dirs:
        if not os.path.isdir(sdir):
            continue
        for suffix, scale, tile_list, base_y in [
            ("", 1, tiles_1x, 114),
            ("@2x", 2, tiles_2x, 228),
        ]:
            png_path = os.path.join(sdir, f"light{suffix}.png")
            json_path = os.path.join(sdir, f"light{suffix}.json")
            if not (os.path.isfile(png_path) and os.path.isfile(json_path)):
                continue

            with open(json_path, "r") as f:
                meta = json.load(f)
            meta.pop("nautical_water_lines", None)

            tsize = 64 * scale
            im = Image.open(png_path).convert("RGBA")
            new_h = max(im.height, base_y + tsize)
            canvas = Image.new("RGBA", (im.width, new_h), (0, 0, 0, 0))
            canvas.paste(im, (0, 0))
            for name, x_off, tile_img in tile_list:
                canvas.paste(tile_img, (x_off, base_y))
                meta[name] = {
                    "x": x_off,
                    "y": base_y,
                    "width": tsize,
                    "height": tsize,
                    "pixelRatio": scale,
                }
            canvas.save(png_path)
            with open(json_path, "w") as f:
                json.dump(meta, f, separators=(",", ":"))


def _make_mgs_water_tile(size: int = 64) -> Image.Image:
    """Create a very subtle black-on-black tactical carbon/ripple water texture tile (size x size RGBA)."""
    y, x = np.mgrid[0:size, 0:size].astype(np.float64)
    u = 2.0 * np.pi * x / size
    v = 2.0 * np.pi * y / size

    undulation = (
        0.40 * np.sin(4.0 * u + 0.5 * np.sin(2.0 * v))
        + 0.25 * np.cos(6.0 * u - 2.0 * v)
    )
    wave = np.sin(14.0 * v + 1.1 * undulation)
    scan = np.sin(28.0 * v + 0.6 * np.cos(4.0 * u))

    # Near-black stealth carbon base (#0c0e10 -> 12, 14, 16)
    r = np.full((size, size), 12.0)
    g = np.full((size, size), 14.0)
    b = np.full((size, size), 16.0)

    # Very subtle black-on-black contrast (±4 RGB units)
    r += wave * 3.2 + (scan > 0.65).astype(np.float64) * 2.2
    g += wave * 3.6 + (scan > 0.65).astype(np.float64) * 2.5
    b += wave * 4.0 + (scan > 0.65).astype(np.float64) * 2.8

    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    rgba[..., 0] = np.clip(r, 0, 255).astype(np.uint8)
    rgba[..., 1] = np.clip(g, 0, 255).astype(np.uint8)
    rgba[..., 2] = np.clip(b, 0, 255).astype(np.uint8)
    rgba[..., 3] = 255
    return Image.fromarray(rgba, mode="RGBA")


def _make_blueprint_grid_tile(size: int = 64) -> Image.Image:
    """Create a seamless, subtle engineering cyanotype blueprint grid water texture tile."""
    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    # Deep cyanotype Prussian blue (#0c335e -> 12, 51, 94)
    rgba[..., 0] = 12
    rgba[..., 1] = 51
    rgba[..., 2] = 94
    rgba[..., 3] = 255

    lw = max(1, size // 64)
    step_minor = max(1, size // 4)
    step_major = max(1, size // 2)

    # Minor drafting sub-grid lines (#2364a8 -> 35, 100, 168)
    for off in range(lw):
        rgba[off::step_minor, :, 0] = 35
        rgba[off::step_minor, :, 1] = 100
        rgba[off::step_minor, :, 2] = 168
        rgba[:, off::step_minor, 0] = 35
        rgba[:, off::step_minor, 1] = 100
        rgba[:, off::step_minor, 2] = 168

    # Major drafting grid lines (#3e8ad4 -> 62, 138, 212)
    for off in range(lw):
        rgba[off::step_major, :, 0] = 62
        rgba[off::step_major, :, 1] = 138
        rgba[off::step_major, :, 2] = 212
        rgba[:, off::step_major, 0] = 62
        rgba[:, off::step_major, 1] = 138
        rgba[:, off::step_major, 2] = 212

    return Image.fromarray(rgba, mode="RGBA")


def _make_synthwave_grid_tile(size: int = 64) -> Image.Image:
    """Create a seamless 1980s Outrun neon-violet laser grid water texture tile."""
    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    # Deep midnight arcade violet (#0e0621 -> 14, 6, 33)
    rgba[..., 0] = 14
    rgba[..., 1] = 6
    rgba[..., 2] = 33
    rgba[..., 3] = 255

    step = max(1, size // 4)
    # Subtle glowing magenta-violet synth grid lines (#2a1052 -> 42, 16, 82)
    rgba[::step, :, 0] = 42
    rgba[::step, :, 1] = 16
    rgba[::step, :, 2] = 82
    rgba[:, ::step, 0] = 34
    rgba[:, ::step, 1] = 14
    rgba[:, ::step, 2] = 72
    return Image.fromarray(rgba, mode="RGBA")


def _make_cyber_water_tile(size: int = 64) -> Image.Image:
    """Create a seamless Cyber Futuristic City midnight-indigo neon-reflection water tile."""
    y, x = np.mgrid[0:size, 0:size].astype(np.float64)
    u = 2.0 * np.pi * x / size
    v = 2.0 * np.pi * y / size

    ripple = np.sin(16.0 * v + 0.6 * np.sin(4.0 * u))
    glint = (np.sin(24.0 * v + 1.2 * np.cos(6.0 * u)) > 0.78).astype(np.float64)

    # Deep starry midnight cyber-indigo (#080644 -> 8, 6, 68)
    r = np.full((size, size), 8.0) + ripple * 4.0 + glint * 14.0
    g = np.full((size, size), 6.0) + ripple * 3.0 + glint * 10.0
    b = np.full((size, size), 68.0) + ripple * 10.0 + glint * 24.0

    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    rgba[..., 0] = np.clip(r, 0, 255).astype(np.uint8)
    rgba[..., 1] = np.clip(g, 0, 255).astype(np.uint8)
    rgba[..., 2] = np.clip(b, 0, 255).astype(np.uint8)
    rgba[..., 3] = 255
    return Image.fromarray(rgba, mode="RGBA")


def update_mgs_water_sprite(repo_root: str) -> None:
    """Idempotently add 'mgs_water_texture', 'blueprint_grid', 'synthwave_grid', and 'cyber_water_grid' to v4/dark sprite sheets."""
    sprite_dirs = [
        os.path.join(repo_root, "tileserver", "sprites", "v4"),
        os.path.join(repo_root, "app", "static", "vendor", "protomaps-assets", "sprites", "v4"),
    ]
    tiles_1x = [
        ("mgs_water_texture", 0, _make_mgs_water_tile(64)),
        ("blueprint_grid", 64, _make_blueprint_grid_tile(64)),
        ("synthwave_grid", 128, _make_synthwave_grid_tile(64)),
        ("cyber_water_grid", 192, _make_cyber_water_tile(64)),
    ]
    tiles_2x = [
        ("mgs_water_texture", 0, _make_mgs_water_tile(128)),
        ("blueprint_grid", 128, _make_blueprint_grid_tile(128)),
        ("synthwave_grid", 256, _make_synthwave_grid_tile(128)),
        ("cyber_water_grid", 384, _make_cyber_water_tile(128)),
    ]

    for sdir in sprite_dirs:
        if not os.path.isdir(sdir):
            continue
        for suffix, scale, tile_list, base_y in [
            ("", 1, tiles_1x, 114),
            ("@2x", 2, tiles_2x, 228),
        ]:
            png_path = os.path.join(sdir, f"dark{suffix}.png")
            json_path = os.path.join(sdir, f"dark{suffix}.json")
            if not (os.path.isfile(png_path) and os.path.isfile(json_path)):
                continue

            with open(json_path, "r") as f:
                meta = json.load(f)

            tsize = 64 * scale
            im = Image.open(png_path).convert("RGBA")
            new_w = max(im.width, 256 * scale)
            new_h = max(im.height, base_y + tsize)
            canvas = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))
            canvas.paste(im, (0, 0))
            for name, x_off, tile_img in tile_list:
                canvas.paste(tile_img, (x_off, base_y))
                meta[name] = {
                    "x": x_off,
                    "y": base_y,
                    "width": tsize,
                    "height": tsize,
                    "pixelRatio": scale,
                }
            canvas.save(png_path)
            with open(json_path, "w") as f:
                json.dump(meta, f, separators=(",", ":"))


def build_themed_style(
    light_tpl: dict,
    name: str,
    sprite_name: str,
    country_colors: list | None,
    state_colors: list | None,
    palette: dict[str, str],
    bold_font: str,
    reg_font: str,
    italic_font: str,
    urban_fill_color: str | None = None,
    water_pattern: str | None = None,
    strip_uppercase: bool = False,
    font_scale: float = 1.0,
    use_choropleth: bool = True,
    hide_road_labels: bool = False,
    halo_scale: float = 1.0,
    halo_blur: float = 0.15,
    border_scale: float = 1.0,
) -> dict:
    style = copy.deepcopy(light_tpl)
    style["name"] = name
    style["glyphs"] = "{fontstack}/{range}.pbf"
    style["sprite"] = sprite_name
    style["sources"] = {
        "protomaps": {"type": "vector", "url": "mbtiles://planet"},
    }
    if use_choropleth:
        style["sources"]["choropleth"] = {
            "type": "vector",
            "url": "pmtiles://elementary_choropleth",
            "maxzoom": 7,
        }

    new_layers: list[dict] = []
    for lyr in style.get("layers", []):
        lid = lyr.get("id", "")
        ltype = lyr.get("type", "")
        paint = lyr.setdefault("paint", {})
        layout = lyr.get("layout")

        if lid == "background":
            paint["background-color"] = palette["water"]
            new_layers.append(lyr)
            continue

        if lid == "earth":
            paint["fill-color"] = palette["earth"]
            new_layers.append(lyr)
            if use_choropleth and country_colors is not None:
                new_layers.append(
                    {
                        "id": "choropleth_countries",
                        "type": "fill",
                        "source": "choropleth",
                        "source-layer": "choropleth",
                        "filter": ["==", ["get", "kind"], "country"],
                        "paint": {
                            "fill-color": country_colors,
                            "fill-opacity": [
                                "interpolate", ["linear"], ["zoom"],
                                0, 0.92, 4, 0.88, 6, 0.72, 9, 0.45, 12, 0.28, 15, 0.18,
                            ],
                        },
                    }
                )
            if use_choropleth and state_colors is not None:
                new_layers.append(
                    {
                        "id": "choropleth_regions",
                        "type": "fill",
                        "source": "choropleth",
                        "source-layer": "choropleth",
                        "minzoom": 2,
                        "filter": ["==", ["get", "kind"], "region"],
                        "paint": {
                            "fill-color": state_colors,
                            "fill-opacity": [
                                "interpolate", ["linear"], ["zoom"],
                                2, 0.0, 2.8, 0.80, 5, 0.82, 7.5, 0.60, 10, 0.35, 13, 0.20,
                            ],
                        },
                    }
                )
            if urban_fill_color:
                new_layers.append(
                    {
                        "id": "landuse_populated_glow",
                        "type": "fill",
                        "source": "protomaps",
                        "source-layer": "landuse",
                        "minzoom": 7,
                        "filter": [
                            "in",
                            "pmap:kind",
                            "residential",
                            "urban_area",
                            "commercial",
                            "retail",
                            "quarter",
                            "suburb",
                        ],
                        "paint": {
                            "fill-color": urban_fill_color,
                            "fill-opacity": [
                                "interpolate", ["linear"], ["zoom"],
                                7, 0.25, 10, 0.48, 13, 0.56,
                            ],
                        },
                    }
                )
            continue

        if lid == "landcover":
            lyr["minzoom"] = 5 if use_choropleth else 0
            paint["fill-color"] = [
                "match", ["get", "pmap:kind"],
                "grassland", palette["lc_grass"],
                "barren", palette["lc_barren"],
                "urban_area", palette["lc_urban"],
                "farmland", palette["lc_farm"],
                "glacier", palette["glacier"],
                "scrub", palette["lc_scrub"],
                palette["lc_forest"],
            ]
            paint["fill-opacity"] = (
                ["interpolate", ["linear"], ["zoom"], 5, 0.0, 6.2, 0.45, 8.0, 0.0]
                if use_choropleth
                else (
                    ["interpolate", ["linear"], ["zoom"], 0, 0.94, 5, 0.88, 8, 0.48, 11, 0.22]
                    if name == "Tolkien"
                    else ["interpolate", ["linear"], ["zoom"], 0, 0.96, 6, 0.94, 9, 0.78, 13, 0.55]
                )
            )
            new_layers.append(lyr)
            continue

        if lid.startswith("landuse_park"):
            if not use_choropleth:
                lyr["minzoom"] = min(int(lyr.get("minzoom", 6)), 3)
                lyr["filter"] = [
                    "in",
                    "pmap:kind",
                    "national_park",
                    "park",
                    "cemetery",
                    "protected_area",
                    "nature_reserve",
                    "forest",
                    "golf_course",
                    "wood",
                    "scrub",
                    "heath",
                    "grassland",
                    "grass",
                    "meadow",
                    "wetland",
                    "marsh",
                    "farmland",
                    "orchard",
                    "vineyard",
                    "bare_rock",
                    "scree",
                    "glacier",
                    "sand",
                    "beach",
                    "military",
                    "naval_base",
                    "airfield",
                ]
            paint["fill-color"] = [
                "case",
                ["in", ["get", "pmap:kind"], ["literal", ["national_park", "park", "cemetery", "protected_area", "nature_reserve", "forest", "golf_course"]]],
                palette["park"],
                ["==", ["get", "pmap:kind"], "wood"],
                palette["wood"],
                ["in", ["get", "pmap:kind"], ["literal", ["scrub", "heath"]]],
                palette.get("lc_scrub", palette["scrub"]),
                ["in", ["get", "pmap:kind"], ["literal", ["grassland", "grass", "meadow", "wetland", "marsh"]]],
                palette["scrub"],
                ["in", ["get", "pmap:kind"], ["literal", ["farmland", "orchard", "vineyard"]]],
                palette["lc_farm"],
                ["in", ["get", "pmap:kind"], ["literal", ["bare_rock", "scree"]]],
                palette.get("rock", palette["lc_barren"]),
                ["==", ["get", "pmap:kind"], "glacier"],
                palette["glacier"],
                ["in", ["get", "pmap:kind"], ["literal", ["sand", "beach"]]],
                palette["sand"],
                ["in", ["get", "pmap:kind"], ["literal", ["military", "naval_base", "airfield"]]],
                palette["military"],
                palette["earth"],
            ]
            paint["fill-opacity"] = (
                ["interpolate", ["linear"], ["zoom"], 6, 0.0, 9, 0.65, 12, 0.88]
                if use_choropleth
                else ["interpolate", ["linear"], ["zoom"], 3, 0.60, 7, 0.85, 12, 0.92]
            )
            new_layers.append(lyr)
            continue

        if lid.startswith("landuse_urban_green"):
            paint["fill-color"] = palette["park"]
            paint["fill-opacity"] = 0.80
        elif lid.startswith("landuse_hospital"):
            paint["fill-color"] = palette["hospital"]
        elif lid.startswith("landuse_industrial"):
            paint["fill-color"] = palette["industrial"]
        elif lid.startswith("landuse_school"):
            paint["fill-color"] = palette["school"]
        elif lid.startswith("landuse_beach"):
            paint["fill-color"] = palette["sand"]
        elif lid.startswith("landuse_zoo"):
            paint["fill-color"] = palette["zoo"]
        elif lid.startswith("landuse_aerodrome"):
            paint["fill-color"] = palette["aerodrome"]
        elif lid.startswith("landuse_runway") or lid in ("roads_runway", "roads_taxiway"):
            if ltype == "fill":
                paint["fill-color"] = palette["runway"]
            else:
                paint["line-color"] = palette["runway"]
        elif lid == "water":
            paint["fill-color"] = palette["water"]
            new_layers.append(lyr)
            if water_pattern:
                new_layers.append(
                    {
                        "id": "water_texture",
                        "type": "fill",
                        "source": "protomaps",
                        "source-layer": "water",
                        "filter": ["==", "$type", "Polygon"],
                        "paint": {
                            "fill-pattern": water_pattern,
                            "fill-opacity": (
                                0.30
                                if water_pattern == "tolkien_water_waves"
                                else (0.62 if water_pattern == "blueprint_grid" else 0.55)
                            ),
                        },
                    }
                )
            new_layers.append(
                {
                    "id": "water_outline",
                    "type": "line",
                    "source": "protomaps",
                    "source-layer": "water",
                    "filter": ["==", "$type", "Polygon"],
                    "paint": {
                        "line-color": palette["water_outline"],
                        "line-width": ["interpolate", ["linear"], ["zoom"], 0, 0.7, 4, 1.1, 8, 1.5, 14, 2.0],
                        "line-opacity": 0.90,
                    },
                }
            )
            continue
        elif lid in ("water_stream", "water_river"):
            paint["line-color"] = palette["river"]
        elif lid.startswith("landuse_pedestrian"):
            paint["fill-color"] = palette["pedestrian"]
        elif lid.startswith("landuse_pier") or lid == "roads_pier":
            if ltype == "fill":
                paint["fill-color"] = palette["pier"]
            else:
                paint["line-color"] = palette["pier"]
        elif lid == "buildings":
            paint["fill-color"] = palette["buildings"]
            paint["fill-opacity"] = 0.86
            paint["fill-outline-color"] = palette["buildings_outline"]
        elif "tunnel" in lid and ltype == "line":
            paint["line-color"] = palette["minor_casing"] if "casing" in lid else palette["tunnel"]
        elif lid.startswith("roads_") and ltype == "line":
            if lid == "roads_rail":
                paint["line-color"] = palette["railway"]
            elif "highway" in lid and "casing" in lid:
                paint["line-color"] = palette["highway_casing"]
            elif "highway" in lid:
                paint["line-color"] = palette["highway"]
            elif "major" in lid and "casing" in lid:
                paint["line-color"] = palette["major_casing"]
            elif "major" in lid:
                paint["line-color"] = palette["major"]
            elif "link" in lid and "casing" in lid:
                paint["line-color"] = palette["link_casing"]
            elif "link" in lid:
                paint["line-color"] = palette["link"]
            elif "casing" in lid:
                paint["line-color"] = palette["minor_casing"]
            elif lid == "roads_minor":
                paint["line-color"] = [
                    "interpolate", ["linear"], ["zoom"],
                    12, palette["minor_a"], 15, palette["minor_b"],
                ]
            else:
                paint["line-color"] = palette["minor_a"]
        elif lid == "boundaries_country":
            def _bw(w: float) -> float:
                return round(w * border_scale, 2)

            if "boundary_country_outer" in palette:
                outer_lyr = copy.deepcopy(lyr)
                outer_lyr["id"] = "boundaries_country_outer"
                outer_lyr["paint"] = {
                    "line-color": palette["boundary_country_outer"],
                    "line-opacity": 0.85,
                    "line-width": ["interpolate", ["linear"], ["zoom"], 0, _bw(2.6), 2, _bw(4.2), 5, _bw(6.8), 8, _bw(9.8), 12, _bw(12.5)],
                }
                new_layers.append(outer_lyr)
            casing_lyr = copy.deepcopy(lyr)
            casing_lyr["id"] = "boundaries_country_casing"
            casing_lyr["paint"] = {
                "line-color": palette["boundary_country_casing"],
                "line-opacity": palette.get("boundary_country_casing_opacity", 0.55),
                "line-width": (
                    ["interpolate", ["linear"], ["zoom"], 0, _bw(1.6), 2, _bw(2.5), 5, _bw(4.2), 8, _bw(6.0), 12, _bw(7.6)]
                    if "boundary_country_outer" in palette
                    else ["interpolate", ["linear"], ["zoom"], 0, _bw(2.2), 2, _bw(3.5), 5, _bw(6.0), 8, _bw(9.0), 12, _bw(11.5)]
                ),
            }
            new_layers.append(casing_lyr)
            paint["line-color"] = palette["boundary_country"]
            paint["line-width"] = (
                ["interpolate", ["linear"], ["zoom"], 0, _bw(0.7), 2, _bw(1.0), 5, _bw(1.5), 8, _bw(2.1), 12, _bw(2.6)]
                if "boundary_country_outer" in palette
                else ["interpolate", ["linear"], ["zoom"], 0, _bw(0.9), 2, _bw(1.3), 5, _bw(1.9), 8, _bw(2.6), 12, _bw(3.2)]
            )
            new_layers.append(lyr)
            continue
        elif lid == "boundaries":
            def _sw(w: float) -> float:
                return round(w * border_scale, 2)

            casing_lyr = copy.deepcopy(lyr)
            casing_lyr["id"] = "boundaries_casing"
            casing_lyr["paint"] = {
                "line-color": palette["boundary_state_casing"],
                "line-opacity": palette.get("boundary_state_casing_opacity", 0.45),
                "line-width": ["interpolate", ["linear"], ["zoom"], 3, _sw(1.8), 6, _sw(3.4), 10, _sw(5.2)],
            }
            new_layers.append(casing_lyr)
            paint["line-color"] = palette["boundary_state"]
            paint["line-width"] = ["interpolate", ["linear"], ["zoom"], 3, _sw(0.8), 6, _sw(1.3), 10, _sw(1.9)]
            new_layers.append(lyr)
            continue

        if ltype == "symbol" and isinstance(layout, dict):
            if hide_road_labels and lid in ("roads_labels_major", "roads_labels_minor", "roads_shields", "address_label"):
                layout["visibility"] = "none"
            if lid == "pois":
                paint["text-color"] = palette["poi_text"]
            _apply_custom_fonts(
                lyr=lyr,
                lid=lid,
                bold_font=bold_font,
                reg_font=reg_font,
                italic_font=italic_font,
                country_color=palette["country_label"],
                country_halo=palette["label_halo"],
                region_color=palette["state_label"],
                region_halo=palette["label_halo"],
                city_color=palette["city_label"],
                city_halo=palette["label_halo"],
                subplace_color=palette["subplace_label"],
                subplace_halo=palette["label_halo"],
                water_color=palette["water_label"],
                water_halo=palette["water_halo"],
                road_color=palette["road_label"],
                road_halo=palette["label_halo"],
                shield_color=palette["shield_label"],
                poi_halo=palette["label_halo"],
                strip_uppercase=strip_uppercase,
                font_scale=font_scale,
                halo_scale=halo_scale,
                halo_blur=halo_blur,
            )

        new_layers.append(lyr)

    style["layers"] = new_layers
    return style


def _apply_custom_fonts(
    lyr: dict,
    lid: str,
    bold_font: str,
    reg_font: str,
    italic_font: str,
    country_color: str,
    country_halo: str,
    region_color: str,
    region_halo: str,
    city_color: str,
    city_halo: str,
    subplace_color: str,
    subplace_halo: str,
    water_color: str,
    water_halo: str,
    road_color: str,
    road_halo: str,
    shield_color: str,
    poi_halo: str,
    strip_uppercase: bool = False,
    font_scale: float = 1.0,
    halo_scale: float = 1.0,
    halo_blur: float = 0.15,
) -> None:
    layout = lyr.setdefault("layout", {})
    paint = lyr.setdefault("paint", {})

    if strip_uppercase and "text-transform" in layout:
        del layout["text-transform"]

    paint["text-halo-blur"] = halo_blur

    def _s(val: float) -> int:
        return int(round(val * font_scale))

    def _h(val: float) -> float:
        return round(val * halo_scale, 2)

    if lid == "places_country":
        layout["text-font"] = [bold_font]
        layout["text-letter-spacing"] = 0.0 if strip_uppercase else 0.08
        layout["text-size"] = [
            "interpolate", ["linear"], ["zoom"],
            1, ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 10], _s(9), _s(12)],
            3, ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 10], _s(11), _s(16)],
            6, ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 8], _s(13), _s(21)],
            9, ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 7], _s(15), _s(24)],
        ]
        paint["text-color"] = country_color
        paint["text-halo-color"] = country_halo
        paint["text-halo-width"] = _h(2.2)
    elif lid == "places_region":
        lyr["minzoom"] = 3
        layout["text-font"] = [bold_font]
        layout["text-size"] = ["interpolate", ["linear"], ["zoom"], 3, _s(11), 7, _s(16)]
        paint["text-color"] = region_color
        paint["text-halo-color"] = region_halo
        paint["text-halo-width"] = _h(1.8)
    elif lid == "places_locality":
        lyr["minzoom"] = 3
        layout["text-font"] = [
            "case",
            ["<=", ["coalesce", ["get", "pmap:min_zoom"], ["get", "min_zoom"], 99], 5],
            ["literal", [bold_font]],
            ["literal", [reg_font]],
        ]
        layout["text-size"] = [
            "interpolate", ["linear"], ["zoom"],
            3, ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 13], _s(8), _s(10)],
            5, ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 12], _s(9), _s(13)],
            7, ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 11], _s(11), _s(16)],
            10, ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 9], _s(12), _s(19)],
            14, ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 8], _s(13), _s(22)],
        ]
        paint["text-color"] = city_color
        paint["text-halo-color"] = city_halo
        paint["text-halo-width"] = _h(2.0)
    elif lid == "places_subplace":
        layout["text-font"] = [reg_font]
        if font_scale != 1.0:
            layout["text-size"] = ["interpolate", ["linear"], ["zoom"], 10, _s(11), 14, _s(15)]
        paint["text-color"] = subplace_color
        paint["text-halo-color"] = subplace_halo
        paint["text-halo-width"] = _h(1.8)
    elif lid in ("water_label_ocean", "water_label_lakes", "water_waterway_label"):
        layout["text-font"] = [italic_font]
        if font_scale != 1.0 and lid == "water_label_ocean":
            layout["text-size"] = ["interpolate", ["linear"], ["zoom"], 2, _s(12), 6, _s(18), 10, _s(22)]
            layout["text-letter-spacing"] = 0.0 if strip_uppercase else 0.06
        paint["text-color"] = water_color
        paint["text-halo-color"] = water_halo
        paint["text-halo-width"] = _h(1.8)
    elif lid == "earth_label_islands":
        layout["text-font"] = [italic_font]
        paint["text-color"] = subplace_color
        paint["text-halo-color"] = subplace_halo
        paint["text-halo-width"] = _h(1.6)
    elif lid == "roads_shields":
        layout["text-font"] = [bold_font]
        paint["text-color"] = shield_color
    elif lid in ("roads_labels_major", "roads_labels_minor", "address_label"):
        layout["text-font"] = [reg_font]
        if font_scale != 1.0:
            layout["text-size"] = ["interpolate", ["linear"], ["zoom"], 12, _s(11), 16, _s(15)]
        paint["text-color"] = road_color
        paint["text-halo-color"] = road_halo
        paint["text-halo-width"] = _h(1.8)
    elif lid == "pois":
        layout["text-font"] = [reg_font]
        if font_scale != 1.0:
            layout["text-size"] = _s(12)
        paint["text-halo-color"] = poi_halo
        paint["text-halo-width"] = _h(1.6)
    else:
        layout["text-font"] = [reg_font]

    raw_layout = json.dumps(layout)
    if "Noto Sans" in raw_layout:
        raw_layout = raw_layout.replace('"Noto Sans Medium"', f'"{bold_font}"')
        raw_layout = raw_layout.replace('"Noto Sans Italic"', f'"{italic_font}"')
        raw_layout = raw_layout.replace('"Noto Sans Regular"', f'"{reg_font}"')
        lyr["layout"] = json.loads(raw_layout)


MATRIX_PALETTE = {
    # All countries and large terrain areas strictly in shades of Matrix green (zero blue fills):
    # Greens: #111811, #2E3B2A, #063B08, #5C7A4A, #9BAF7A, #008F11, #00FF41
    # Cold blues, grays, and whites preserved for labels (#eef7fd, #d8ecf8, #c9d9e6, #9DB2C7, #C0C9D1)
    # Red & black reversed on borders and road accents (pitch-black #050705 casing + bright crimson #ed263a core) for high legibility
    "water": "#050705",
    "water_outline": "#008F11",
    "river": "#193b1c",
    "earth": "#111811",
    "lc_grass": "#2E3B2A",
    "lc_barren": "#384d31",
    "lc_urban": "#243d25",
    "lc_farm": "#465e38",
    "lc_scrub": "#2a4026",
    "lc_forest": "#063B08",
    "park": "#0a420c",
    "wood": "#233d23",
    "scrub": "#4b663c",
    "glacier": "#9DB2C7",
    "sand": "#5C7A4A",
    "military": "#8c1420",
    "hospital": "#a61726",
    "industrial": "#182418",
    "school": "#2E3B2A",
    "zoo": "#0a420c",
    "aerodrome": "#1f301f",
    "runway": "#5C7A4A",
    "pedestrian": "#2E3B2A",
    "pier": "#263822",
    "buildings": "#1f331f",
    "buildings_outline": "#5C7A4A",
    "tunnel": "#111811",
    "highway": "#00FF41",
    "highway_casing": "#050705",
    "major": "#9BAF7A",
    "major_casing": "#0b120b",
    "link": "#ed263a",
    "link_casing": "#050705",
    "minor_a": "#264a28",
    "minor_b": "#5C7A4A",
    "minor_casing": "#0a0f0a",
    "railway": "#9DB2C7",
    # Reversed Red & Black borders: pitch-black outer/casing (#050705) with bright legible crimson-red inner core (#ed263a / #d61f32)
    "boundary_country_outer": "#050705",
    "boundary_country_casing": "#050705",
    "boundary_country_casing_opacity": 0.96,
    "boundary_country": "#ed263a",
    "boundary_state_casing": "#050705",
    "boundary_state_casing_opacity": 0.92,
    "boundary_state": "#d61f32",
    # Cold blues, grays, and whites for labels
    "country_label": "#eef7fd",
    "state_label": "#c9d9e6",
    "city_label": "#d8ecf8",
    "subplace_label": "#9DB2C7",
    "water_label": "#789ab8",
    "water_halo": "#050705",
    "road_label": "#C0C9D1",
    "shield_label": "#ff7a88",
    "poi_text": "#ff7a88",
    "label_halo": "#050705",
}

WASTELAND_PALETTE = {
    "water": "#160b05",
    "water_outline": "#c95118",
    "river": "#522d14",
    "earth": "#3d200e",
    # Slightly brighter burnt-orange & atomic amber terrain (no drastic changes)
    "lc_grass": "#633015",
    "lc_barren": "#9e4418",
    "lc_urban": "#c98616",
    "lc_farm": "#8a5216",
    "lc_scrub": "#873c17",
    "lc_forest": "#572a13",
    "park": "#633015",
    "wood": "#4d2b14",
    "scrub": "#8f3f18",
    "glacier": "#ffaa14",
    "sand": "#d48616",
    "military": "#9e4418",
    "hospital": "#cc5b16",
    "industrial": "#ab6d15",
    "school": "#ebb01c",
    "zoo": "#8f3f18",
    "aerodrome": "#7a4d14",
    "runway": "#9e4418",
    "pedestrian": "#bd7c16",
    "pier": "#9c6314",
    "buildings": "#de9e1a",
    "buildings_outline": "#ffe001",
    "tunnel": "#3d200e",
    "highway": "#ffe001",
    "highway_casing": "#783817",
    "major": "#ff681a",
    "major_casing": "#4d2b14",
    "link": "#e85a19",
    "link_casing": "#4d2b14",
    "minor_a": "#a34618",
    "minor_b": "#db8818",
    "minor_casing": "#261309",
    "railway": "#ff8519",
    "boundary_country_casing": "#e85a19",
    "boundary_country": "#ffe001",
    "boundary_state_casing": "#783817",
    "boundary_state": "#ff7417",
    "country_label": "#ffe001",
    "state_label": "#ffaa14",
    "city_label": "#ffe45e",
    "subplace_label": "#feb82b",
    "water_label": "#ff7417",
    "water_halo": "#120804",
    "road_label": "#fec240",
    "shield_label": "#ffe001",
    "poi_text": "#fec240",
    "label_halo": "#140a05",
}

TOLKIEN_PALETTE = {
    # Middle-earth / Arda & Beleriand Physical Zone Palette (no choropleths):
    # Belegaer cerulean-steel water (#3b70a6), moss & meadow greens (#8ba968, #a6b88e, #6c8c5a, #557848),
    # Gondolin / Ered Luin stone whites & silver-grays (#e0ded6, #d4d8dc, #a4abb2, #dfe3e6, #f5f7fa),
    # subtle parchment tans (#cbbd9b, #b5a482), heraldic gold accents (#e6bc45, #d4a838, #f2c94c),
    # skinny dark charcoal-ink borders (#111312), and bright white (#ffffff) labels with skinny black outlines
    "water": "#3b70a6",
    "water_outline": "#141816",
    "river": "#4a84bd",
    "earth": "#e4e2da",
    "lc_grass": "#8ba968",
    "lc_barren": "#b8ab8e",
    "rock": "#a6acb2",
    "lc_urban": "#c2c6ca",
    "lc_farm": "#a6b88e",
    "lc_scrub": "#a89674",
    "lc_forest": "#557848",
    "park": "#6c8c5a",
    "wood": "#4a6b3e",
    "scrub": "#8c9c72",
    "glacier": "#f5f7fa",
    "sand": "#cbbd9b",
    "military": "#9aa1a8",
    "hospital": "#d9d4c7",
    "industrial": "#b4bac0",
    "school": "#d6c89e",
    "zoo": "#8ba968",
    "aerodrome": "#bcc2c7",
    "runway": "#e8ebed",
    "pedestrian": "#eceff2",
    "pier": "#c2c6ca",
    "buildings": "#dfe3e6",
    "buildings_outline": "#4a5056",
    "tunnel": "#b8bec4",
    "highway": "#e6bc45",
    "highway_casing": "#1c201e",
    "major": "#d4a838",
    "major_casing": "#242823",
    "link": "#e0c268",
    "link_casing": "#333832",
    "minor_a": "#d8dce0",
    "minor_b": "#f7f9fa",
    "minor_casing": "#5c636a",
    "railway": "#2b3036",
    # Skinny dark charcoal-ink borders
    "boundary_country_casing": "#2b2f2a",
    "boundary_country_casing_opacity": 0.65,
    "boundary_country": "#111312",
    "boundary_state_casing": "#3b4038",
    "boundary_state_casing_opacity": 0.45,
    "boundary_state": "#1e221f",
    # Black ink fill with bright white outline
    "country_label": "#111312",
    "state_label": "#1c201e",
    "city_label": "#111312",
    "subplace_label": "#1c201e",
    "water_label": "#0d1b2e",
    "water_halo": "#ffffff",
    "road_label": "#181c19",
    "shield_label": "#111312",
    "poi_text": "#1c201e",
    "label_halo": "#ffffff",
}

PALADIN_PALETTE = {
    "water": "#0c0e10",
    "water_outline": "#a6b2bd",
    "river": "#22272c",
    "earth": "#292d32",
    "lc_grass": "#33383e",
    "lc_barren": "#243345",
    "lc_urban": "#5c646d",
    "lc_farm": "#28374a",
    "lc_scrub": "#373c42",
    "lc_forest": "#2b3035",
    "park": "#31363c",
    "wood": "#282c31",
    "scrub": "#263547",
    "glacier": "#dce6f0",
    "sand": "#6e5c32",
    # Subtle red only on small zoomed-in area fills (hospital/military) and high-zoom POI labels
    "military": "#4d2326",
    "hospital": "#6e1f24",
    "industrial": "#484f57",
    "school": "#6e5c2c",
    "zoo": "#3d434a",
    "aerodrome": "#505860",
    "runway": "#9aa4ae",
    "pedestrian": "#565e66",
    "pier": "#6c757e",
    "buildings": "#747e88",
    "buildings_outline": "#dce6f0",
    "tunnel": "#202428",
    # Polished chrome highways, muted yellow-gold major roads, brushed steel/chrome minor roads
    "highway": "#dce6f0",
    "highway_casing": "#1c2024",
    "major": "#c9a842",
    "major_casing": "#2e2712",
    "link": "#9aa4ae",
    "link_casing": "#1c2024",
    "minor_a": "#68717a",
    "minor_b": "#b8c2cc",
    "minor_casing": "#181b1e",
    "railway": "#d0dae4",
    # Darker recessed armor-seam country borders with chrome bevel highlight (zero red)
    "boundary_country_outer": "#14171a",
    "boundary_country_casing": "#b8c4d0",
    "boundary_country_casing_opacity": 0.82,
    "boundary_country": "#121518",
    "boundary_state_casing": "#747e88",
    "boundary_state_casing_opacity": 0.55,
    "boundary_state": "#202428",
    # Chrome, Silver, and Yellow-Gold typography (red restricted to zoomed-in POI labels)
    "country_label": "#eef4fa",
    "state_label": "#dfc05a",
    "city_label": "#dce6f0",
    "subplace_label": "#b4c0cc",
    "water_label": "#73808c",
    "water_halo": "#08090b",
    "road_label": "#cfd9e3",
    "shield_label": "#e8c858",
    "poi_text": "#d4484e",
    "label_halo": "#0b0d0f",
}

# Snazzy Maps #7 "Icy Blue": vibrant azure water, bright frost land, crisp glacial terrain contrast,
# lightened simplified roads, road labels hidden, Exo 2 geometric typography
ICY_BLUE_PALETTE = {
    "water": "#5ba6f7",
    "water_outline": "#2e75c9",
    "river": "#4d99ef",
    "earth": "#dceeff",
    "lc_grass": "#b4d7fa",
    "lc_barren": "#f0f7ff",
    "lc_urban": "#c2defa",
    "lc_farm": "#cce4fd",
    "lc_scrub": "#9ec8f5",
    "lc_forest": "#8ec0f4",
    "park": "#8ec0f4",
    "wood": "#82b8f2",
    "scrub": "#a8d0f8",
    "glacier": "#ffffff",
    "sand": "#e6f2ff",
    "military": "#b0d1f5",
    "hospital": "#cbe2fa",
    "industrial": "#bddaf8",
    "school": "#cfe5fb",
    "zoo": "#99c8f6",
    "aerodrome": "#c6dff9",
    "runway": "#f5faff",
    "pedestrian": "#e8f3ff",
    "pier": "#cde3fb",
    "buildings": "#c2defa",
    "buildings_outline": "#8bb9eb",
    "tunnel": "#d4e8fd",
    "highway": "#ffffff",
    "highway_casing": "#7eb3eb",
    "major": "#fafdff",
    "major_casing": "#93c1f2",
    "link": "#f4f9ff",
    "link_casing": "#a4ccf5",
    "minor_a": "#f0f7ff",
    "minor_b": "#ffffff",
    "minor_casing": "#b5d6f7",
    "railway": "#6da6e3",
    "boundary_country_casing": "#d6ebff",
    "boundary_country_casing_opacity": 0.72,
    "boundary_country": "#102a49",
    "boundary_state_casing": "#e2f1ff",
    "boundary_state_casing_opacity": 0.58,
    "boundary_state": "#4a82bd",
    "country_label": "#0b1c30",
    "state_label": "#153252",
    "city_label": "#0b1c30",
    "subplace_label": "#1c3f63",
    "water_label": "#134780",
    "water_halo": "#92c8fc",
    "road_label": "#1c3f63",
    "shield_label": "#0b1c30",
    "poi_text": "#173759",
    "label_halo": "#ffffff",
}

# "Clover": Green counterpart to Icy Blue — vibrant shamrock/emerald water, bright mint-clover land,
# crisp monochromatic green terrain zones, simplified white/mint roads, road labels hidden, Exo 2 typography
CLOVER_PALETTE = {
    "water": "#46b862",
    "water_outline": "#217537",
    "river": "#38a653",
    "earth": "#dcf7e2",
    "lc_grass": "#a8e8b7",
    "lc_barren": "#edfbf0",
    "lc_urban": "#b8edc5",
    "lc_farm": "#c5f2d0",
    "lc_scrub": "#8fdb9f",
    "lc_forest": "#78d18b",
    "park": "#78d18b",
    "wood": "#6ac77e",
    "scrub": "#9ae0a9",
    "glacier": "#ffffff",
    "sand": "#e3faea",
    "military": "#a3e3b2",
    "hospital": "#c2f0cd",
    "industrial": "#b2ebc0",
    "school": "#c8f2d2",
    "zoo": "#88d99a",
    "aerodrome": "#bdeec9",
    "runway": "#f2fcf5",
    "pedestrian": "#e3faea",
    "pier": "#c4f0ce",
    "buildings": "#b8edc5",
    "buildings_outline": "#7ac98d",
    "tunnel": "#ccf2d5",
    "highway": "#ffffff",
    "highway_casing": "#6cc280",
    "major": "#f7fff9",
    "major_casing": "#82cf94",
    "link": "#f0fcf3",
    "link_casing": "#96dba6",
    "minor_a": "#ebf9ee",
    "minor_b": "#ffffff",
    "minor_casing": "#aae3b7",
    "railway": "#5cb570",
    "boundary_country_casing": "#cef5d7",
    "boundary_country_casing_opacity": 0.72,
    "boundary_country": "#0d3818",
    "boundary_state_casing": "#dff9e5",
    "boundary_state_casing_opacity": 0.58,
    "boundary_state": "#378f4d",
    "country_label": "#092610",
    "state_label": "#113d1c",
    "city_label": "#092610",
    "subplace_label": "#174f25",
    "water_label": "#0d471c",
    "water_halo": "#80dba0",
    "road_label": "#174f25",
    "shield_label": "#092610",
    "poi_text": "#13421f",
    "label_halo": "#ffffff",
}

BLUEPRINT_PALETTE = {
    "water": "#0c335e",
    "water_outline": "#e6f5ff",
    "river": "#2b78c2",
    "earth": "#144880",
    "lc_grass": "#174f8a",
    "lc_barren": "#1c5a99",
    "lc_urban": "#246bb3",
    "lc_farm": "#195391",
    "lc_scrub": "#1b5694",
    "lc_forest": "#13457a",
    "park": "#164d87",
    "wood": "#124275",
    "scrub": "#1a5591",
    "glacier": "#d9f0ff",
    "sand": "#2b73b8",
    "military": "#1e62a8",
    "hospital": "#2876c4",
    "industrial": "#1f5f9e",
    "school": "#2570ba",
    "zoo": "#1c5a99",
    "aerodrome": "#2265a8",
    "runway": "#9cd6ff",
    "pedestrian": "#2870b8",
    "pier": "#3682cc",
    "buildings": "#2975bf",
    "buildings_outline": "#e6f5ff",
    "tunnel": "#103c6b",
    "highway": "#ffffff",
    "highway_casing": "#0a2a4d",
    "major": "#b8e3ff",
    "major_casing": "#0d3561",
    "link": "#85ccff",
    "link_casing": "#0d3561",
    "minor_a": "#52a8eb",
    "minor_b": "#c4e8ff",
    "minor_casing": "#0e3866",
    "railway": "#d4eeff",
    "boundary_country_outer": "#3898eb",
    "boundary_country_casing": "#f2faff",
    "boundary_country_casing_opacity": 0.94,
    "boundary_country": "#092647",
    "boundary_state_casing": "#75c2ff",
    "boundary_state_casing_opacity": 0.65,
    "boundary_state": "#e6f5ff",
    # Crisp bright white typography across all Blueprint label tiers
    "country_label": "#ffffff",
    "state_label": "#ffffff",
    "city_label": "#ffffff",
    "subplace_label": "#ffffff",
    "water_label": "#f2faff",
    "water_halo": "#061d38",
    "road_label": "#ffffff",
    "shield_label": "#ffffff",
    "poi_text": "#ffffff",
    "label_halo": "#061d38",
}

SYNTHWAVE_PALETTE = {
    "water": "#0e0621",
    "water_outline": "#d91678",
    "river": "#9e1468",
    "earth": "#220d42",
    "lc_grass": "#2c1152",
    "lc_barren": "#3d1466",
    "lc_urban": "#591878",
    "lc_farm": "#33125c",
    "lc_scrub": "#381361",
    "lc_forest": "#240e47",
    "park": "#2b1254",
    "wood": "#200c40",
    "scrub": "#36145e",
    "glacier": "#ff94cc",
    "sand": "#7a1c7d",
    "military": "#6e1458",
    "hospital": "#8c1968",
    "industrial": "#3d1766",
    "school": "#731c78",
    "zoo": "#42156b",
    "aerodrome": "#381863",
    "runway": "#ff4da6",
    "pedestrian": "#4c1a73",
    "pier": "#591c80",
    "buildings": "#461873",
    "buildings_outline": "#ff2e97",
    "tunnel": "#180930",
    "highway": "#ff1f8f",
    "highway_casing": "#3d0524",
    "major": "#ff9e1b",
    "major_casing": "#3b1704",
    "link": "#ff59b0",
    "link_casing": "#2e061e",
    "minor_a": "#8c289e",
    "minor_b": "#ff73bf",
    "minor_casing": "#180830",
    "railway": "#ff4da6",
    # Two-tone hot neon pink/magenta & pitch-black borders (no neon blue on borders)
    "boundary_country_outer": "#ff1f8f",
    "boundary_country_casing": "#0d041c",
    "boundary_country_casing_opacity": 0.95,
    "boundary_country": "#ff85c8",
    "boundary_state_casing": "#ff2491",
    "boundary_state_casing_opacity": 0.72,
    "boundary_state": "#0b0317",
    # Neon blue & glowing gold preserved for fonts and labels
    "country_label": "#ffe652",
    "state_label": "#00f0ff",
    "city_label": "#70f8ff",
    "subplace_label": "#99faff",
    "water_label": "#00e5ff",
    "water_halo": "#090317",
    "road_label": "#66f7ff",
    "shield_label": "#ffe652",
    "poi_text": "#00f0ff",
    "label_halo": "#0c041c",
}

WESTERN_PALETTE = {
    # Vivid Cobalt Blue water + geographic/physical zone colors from Geological Survey reference
    "water": "#0649c4",
    "water_outline": "#00266e",
    "river": "#1258db",
    "earth": "#d6be8e",
    "lc_grass": "#e8d258",
    "lc_barren": "#8f6b44",
    "lc_urban": "#c97a32",
    "lc_farm": "#dec268",
    "lc_scrub": "#c77d5f",
    "lc_forest": "#c98522",
    "park": "#c98522",
    "wood": "#b3721b",
    "scrub": "#e0ca58",
    "glacier": "#f5edd8",
    "sand": "#ebda6c",
    "military": "#b87358",
    "hospital": "#c77a5e",
    "industrial": "#b59568",
    "school": "#d6a449",
    "zoo": "#c49a45",
    "aerodrome": "#baa075",
    "runway": "#d9c7a3",
    "pedestrian": "#d4be94",
    "pier": "#b89b6c",
    "buildings": "#ba9463",
    "buildings_outline": "#593d1e",
    "tunnel": "#baa37a",
    "highway": "#b85d19",
    "highway_casing": "#421e06",
    "major": "#d6942a",
    "major_casing": "#54350c",
    "link": "#e0b44c",
    "link_casing": "#614215",
    "minor_a": "#dfd0ae",
    "minor_b": "#f5ebd4",
    "minor_casing": "#856a44",
    "railway": "#3d2612",
    "boundary_country_outer": "#6e4e2e",
    "boundary_country_casing": "#ebd7a7",
    "boundary_country_casing_opacity": 0.90,
    "boundary_country": "#24160a",
    "boundary_state_casing": "#b59663",
    "boundary_state_casing_opacity": 0.65,
    "boundary_state": "#422b15",
    "country_label": "#1f1107",
    "state_label": "#42220e",
    "city_label": "#1c0f06",
    "subplace_label": "#3d2412",
    "water_label": "#ffffff",
    "water_halo": "#021847",
    "road_label": "#331e0e",
    "shield_label": "#1f1107",
    "poi_text": "#38200e",
    "label_halo": "#ffffff",
}

CYBER_PALETTE = {
    # Cyber Futuristic City Color Palette (https://i.pinimg.com/474x/56/48/92/5648920f5a78ea8b09f812fc8aa5f7e0.jpg):
    # Physical/geographic zone color mapping (no choropleth):
    # Swatches: #5137b4, #aa4bdd, #ff40aa, #ffcfd5, #72c4ff, #c7ffff
    # Midnight Starry Indigo Water & Sky: #080644, #240c55, #404bb1
    "water": "#080644",
    "water_outline": "#72c4ff",
    "river": "#5137b4",
    "earth": "#1d1666",
    "lc_grass": "#3b32a6",
    "lc_barren": "#733ec7",
    "lc_urban": "#b24ce0",
    "lc_farm": "#444eb8",
    "lc_scrub": "#5638b8",
    "lc_forest": "#281f88",
    "park": "#342b99",
    "wood": "#231a7a",
    "scrub": "#5e3ec2",
    "glacier": "#c7ffff",
    "sand": "#8e46d6",
    "military": "#8545d4",
    "hospital": "#ff40aa",
    "industrial": "#404bb1",
    "school": "#6b3ec7",
    "zoo": "#5137b4",
    "aerodrome": "#382d9c",
    "runway": "#72c4ff",
    "pedestrian": "#5137b4",
    "pier": "#404bb1",
    "buildings": "#404bb1",
    "buildings_outline": "#72c4ff",
    "tunnel": "#15104d",
    "highway": "#c7ffff",
    "highway_casing": "#1d1666",
    "major": "#ff40aa",
    "major_casing": "#240c55",
    "link": "#aa4bdd",
    "link_casing": "#1d1666",
    "minor_a": "#5137b4",
    "minor_b": "#72c4ff",
    "minor_casing": "#120d42",
    "railway": "#ffcfd5",
    # Two-tone Holographic Ice-Cyan & Electric Indigo-Amethyst borders
    "boundary_country_outer": "#5137b4",
    "boundary_country_casing": "#080644",
    "boundary_country_casing_opacity": 0.94,
    "boundary_country": "#c7ffff",
    "boundary_state_casing": "#240c55",
    "boundary_state_casing_opacity": 0.80,
    "boundary_state": "#72c4ff",
    # Cyber Neon Blush (#ffcfd5), Ice-Cyan (#c7ffff), and Holographic Sky-Blue (#72c4ff) typography
    "country_label": "#ffcfd5",
    "state_label": "#c7ffff",
    "city_label": "#c7ffff",
    "subplace_label": "#72c4ff",
    "water_label": "#72c4ff",
    "water_halo": "#05042b",
    "road_label": "#ffcfd5",
    "shield_label": "#c7ffff",
    "poi_text": "#ff40aa",
    "label_halo": "#06042e",
}

JURASSIC_PALETTE = {
    # Physical/geographic zone colors (no choropleth): lush rainforest greens, savanna yellows, volcanic highland browns
    "water": "#9cd3f5",
    "water_outline": "#194519",
    "river": "#6ebce8",
    "earth": "#88cf4c",
    "lc_grass": "#54b336",
    "lc_barren": "#b5762a",
    "lc_urban": "#d99b38",
    "lc_farm": "#bee063",
    "lc_scrub": "#f0df78",
    "lc_forest": "#2e8222",
    "park": "#3b962b",
    "wood": "#29731d",
    "scrub": "#59b538",
    "glacier": "#faf2b8",
    "sand": "#f5e884",
    "military": "#9e6b2e",
    "hospital": "#d47a46",
    "industrial": "#b88c44",
    "school": "#e8be54",
    "zoo": "#66bd3e",
    "aerodrome": "#c29e56",
    "runway": "#e6d49c",
    "pedestrian": "#e8dca2",
    "pier": "#b5853d",
    "buildings": "#b5833d",
    "buildings_outline": "#4a2e0e",
    "tunnel": "#c4b276",
    "highway": "#e69c24",
    "highway_casing": "#472806",
    "major": "#f2d261",
    "major_casing": "#594112",
    "link": "#f5e082",
    "link_casing": "#634b18",
    "minor_a": "#ebe3ad",
    "minor_b": "#faf5d4",
    "minor_casing": "#756634",
    "railway": "#4a3012",
    "boundary_country_outer": "#1d4d1a",
    "boundary_country_casing": "#f2dc74",
    "boundary_country_casing_opacity": 0.90,
    "boundary_country": "#261708",
    "boundary_state_casing": "#c9df75",
    "boundary_state_casing_opacity": 0.65,
    "boundary_state": "#264f1c",
    "country_label": "#211206",
    "state_label": "#3b230c",
    "city_label": "#1f1106",
    "subplace_label": "#33200d",
    "water_label": "#103c5c",
    "water_halo": "#dcf2ff",
    "road_label": "#331f0b",
    "shield_label": "#211206",
    "poi_text": "#301d0a",
    "label_halo": "#fcf9eb",
}


def main() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    update_tolkien_water_sprite(repo_root)
    update_mgs_water_sprite(repo_root)

    light_path = os.path.join(repo_root, "tileserver", "styles", "light.json")
    with open(light_path, "r") as f:
        light_tpl = json.load(f)

    styles_to_write = {
        "elementary": build_elementary_style(light_tpl),
        "matrix": build_themed_style(
            light_tpl=light_tpl,
            name="Matrix",
            sprite_name="v4/dark",
            country_colors=MATRIX_COUNTRY_COLORS,
            state_colors=MATRIX_STATE_COLORS,
            palette=MATRIX_PALETTE,
            bold_font="Share Tech Mono Bold",
            reg_font="Share Tech Mono Regular",
            italic_font="Share Tech Mono Italic",
        ),
        "wasteland": build_themed_style(
            light_tpl=light_tpl,
            name="Wasteland",
            sprite_name="v4/dark",
            country_colors=WASTELAND_COUNTRY_COLORS,
            state_colors=WASTELAND_STATE_COLORS,
            palette=WASTELAND_PALETTE,
            bold_font="Black Ops One Regular",
            reg_font="Quantico Regular",
            italic_font="Quantico Italic",
            urban_fill_color="#d49216",
        ),
        "tolkien": build_themed_style(
            light_tpl=light_tpl,
            name="Tolkien",
            sprite_name="v4/light",
            country_colors=None,
            state_colors=None,
            palette=TOLKIEN_PALETTE,
            bold_font="Cinzel Decorative Bold",
            reg_font="IM Fell English Regular",
            italic_font="Fondamento Italic",
            urban_fill_color="#d4d8dc",
            water_pattern="tolkien_water_waves",
            strip_uppercase=True,
            use_choropleth=False,
            font_scale=1.14,
            halo_scale=0.64,
            halo_blur=0.0,
            border_scale=0.34,
        ),
        "paladin": build_themed_style(
            light_tpl=light_tpl,
            name="Paladin",
            sprite_name="v4/dark",
            country_colors=PALADIN_COUNTRY_COLORS,
            state_colors=PALADIN_STATE_COLORS,
            palette=PALADIN_PALETTE,
            bold_font="Orbitron Bold",
            reg_font="Orbitron Regular",
            italic_font="Orbitron Italic",
            urban_fill_color="#68727c",
            water_pattern="mgs_water_texture",
        ),
        "icy_blue": build_themed_style(
            light_tpl=light_tpl,
            name="Icy Blue",
            sprite_name="v4/light",
            country_colors=None,
            state_colors=None,
            palette=ICY_BLUE_PALETTE,
            bold_font="Exo 2 Bold",
            reg_font="Exo 2 Regular",
            italic_font="Exo 2 Italic",
            strip_uppercase=True,
            use_choropleth=False,
            hide_road_labels=True,
        ),
        "clover": build_themed_style(
            light_tpl=light_tpl,
            name="Clover",
            sprite_name="v4/light",
            country_colors=None,
            state_colors=None,
            palette=CLOVER_PALETTE,
            bold_font="Exo 2 Bold",
            reg_font="Exo 2 Regular",
            italic_font="Exo 2 Italic",
            strip_uppercase=True,
            use_choropleth=False,
            hide_road_labels=True,
        ),
        "blueprint": build_themed_style(
            light_tpl=light_tpl,
            name="Blueprint",
            sprite_name="v4/dark",
            country_colors=BLUEPRINT_COUNTRY_COLORS,
            state_colors=BLUEPRINT_STATE_COLORS,
            palette=BLUEPRINT_PALETTE,
            bold_font="Rajdhani Bold",
            reg_font="Architects Daughter Regular",
            italic_font="Architects Daughter Italic",
            urban_fill_color="#246bb3",
            water_pattern="blueprint_grid",
            font_scale=1.12,
        ),
        "synthwave": build_themed_style(
            light_tpl=light_tpl,
            name="Synthwave",
            sprite_name="v4/dark",
            country_colors=SYNTHWAVE_COUNTRY_COLORS,
            state_colors=SYNTHWAVE_STATE_COLORS,
            palette=SYNTHWAVE_PALETTE,
            bold_font="Audiowide Regular",
            reg_font="Righteous Regular",
            italic_font="Righteous Italic",
            urban_fill_color="#6b1982",
            water_pattern="synthwave_grid",
        ),
        "western": build_themed_style(
            light_tpl=light_tpl,
            name="Western",
            sprite_name="v4/light",
            country_colors=None,
            state_colors=None,
            palette=WESTERN_PALETTE,
            bold_font="Montserrat Bold",
            reg_font="Montserrat SemiBold",
            italic_font="Montserrat SemiBold Italic",
            urban_fill_color="#c97a32",
            water_pattern="western_water_waves",
            use_choropleth=False,
            font_scale=1.06,
            halo_scale=0.64,
            halo_blur=0.0,
        ),
        "jurassic": build_themed_style(
            light_tpl=light_tpl,
            name="Jurassic",
            sprite_name="v4/light",
            country_colors=None,
            state_colors=None,
            palette=JURASSIC_PALETTE,
            bold_font="Skranji Bold",
            reg_font="Skranji Regular",
            italic_font="Skranji Italic",
            urban_fill_color="#d99b38",
            strip_uppercase=True,
            use_choropleth=False,
            font_scale=1.08,
        ),
        "cyber": build_themed_style(
            light_tpl=light_tpl,
            name="Cyber",
            sprite_name="v4/dark",
            country_colors=None,
            state_colors=None,
            palette=CYBER_PALETTE,
            bold_font="Tektur Bold",
            reg_font="Tektur Regular",
            italic_font="Tektur Italic",
            urban_fill_color="#b24ce0",
            water_pattern="cyber_water_grid",
            use_choropleth=False,
            font_scale=1.06,
        ),
    }

    for style_id, style_obj in styles_to_write.items():
        out_paths = [
            os.path.join(repo_root, "tileserver", "styles", f"{style_id}.json"),
            os.path.join(repo_root, "app", "static", "vendor", "protomaps-themes", f"{style_id}.json"),
        ]
        for p in out_paths:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                json.dump(style_obj, f, indent=2)
            print(f"Wrote {p} ({len(style_obj['layers'])} layers)")


if __name__ == "__main__":
    main()
