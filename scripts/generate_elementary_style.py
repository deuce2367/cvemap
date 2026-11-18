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
    1, "#1b251a",
    2, "#263822",
    3, "#27542b",
    4, "#354a26",
    5, "#4a5c2f",
    6, "#1f3b29",
    7, "#2e523b",
    8, "#616e36",
    9, "#2b4223",
    "#1b251a",
]

MATRIX_STATE_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#223020",
    2, "#294024",
    3, "#2a572e",
    4, "#3a5029",
    5, "#506332",
    6, "#23402d",
    7, "#345941",
    8, "#616e36",
    9, "#304726",
    "#223020",
]

WASTELAND_COUNTRY_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#462814",
    2, "#592b15",
    3, "#8f5b14",  # Warm Wasteland gold-ochre
    4, "#7d3818",
    5, "#8d3c18",
    6, "#9e4218",
    7, "#502411",
    8, "#a66d16",  # Atomic amber-gold
    9, "#632d14",
    "#462814",
]

WASTELAND_STATE_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#4c2a15",
    2, "#945f14",  # Warm gold-ochre
    3, "#703517",
    4, "#823a18",
    5, "#913e18",
    6, "#ab7216",  # Atomic amber-gold
    7, "#552612",
    8, "#753015",
    9, "#632d14",
    "#4c2a15",
]

TOLKIEN_COUNTRY_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#d9b589",  # Warm Tawny Parchment Brown (Eriador / Gondor plains)
    2, "#a6cadb",  # Misty Sky-Blue (Helcaraxe / Ilmarin)
    3, "#f0dc86",  # Lamps of the Valar / Laurelin Golden Glow
    4, "#c2926b",  # Rich Chestnut / Russet Earth Brown
    5, "#9cd184",  # Shire Spring Meadow Green
    6, "#8bb6cf",  # Coastal Fjord Blue (Lindon / Belfalas)
    7, "#e3c388",  # Warm Golden Amber-Sand (Haradwaith)
    8, "#b5835e",  # Deep Cinnamon / Highland Umber Brown
    9, "#b8d8e6",  # Pale Elven Ice-Blue
    "#d9b589",
]

TOLKIEN_STATE_COLORS = [
    "match",
    ["get", "color_id"],
    1, "#dec096",  # Warm Buff-Brown
    2, "#adcfe0",  # Soft Fjord Blue
    3, "#f2e08f",  # Sunlit Gold
    4, "#c79973",  # Warm Russet Brown
    5, "#a2d48b",  # Fresh Meadow Green
    6, "#93bdd6",  # Misty Sea-Blue
    7, "#e6c891",  # Golden Ochre-Sand
    8, "#ba8a66",  # Tawny Chestnut Brown
    9, "#bedce8",  # Pale Ice-Blue
    "#dec096",
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

    # Base Arda sea sapphire-cerulean (#457d9e -> 69, 125, 158)
    r = np.full((size, size), 69.0)
    g = np.full((size, size), 125.0)
    b = np.full((size, size), 158.0)

    # Subtle blue-on-blue wave troughs (closely spaced tones)
    trough = np.clip(-wave, 0.0, 1.0) ** 1.35
    r -= trough * 11.0
    g -= trough * 13.0
    b -= trough * 14.0

    # Subtle blue-on-blue wave crests
    crest = np.clip(wave, 0.0, 1.0) ** 1.55
    r += crest * 9.0
    g += crest * 11.0
    b += crest * 11.0

    # Gentle horizontal pen lines along wave ridges
    ridge = (etch > 0.74).astype(np.float64) * 0.35
    r -= ridge * 8.0
    g -= ridge * 10.0
    b -= ridge * 11.0

    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    rgba[..., 0] = np.clip(r, 0, 255).astype(np.uint8)
    rgba[..., 1] = np.clip(g, 0, 255).astype(np.uint8)
    rgba[..., 2] = np.clip(b, 0, 255).astype(np.uint8)
    rgba[..., 3] = 255
    return Image.fromarray(rgba, mode="RGBA")


def update_tolkien_water_sprite(repo_root: str) -> None:
    """Idempotently add the 'tolkien_water_waves' seamless pattern sprite to v4/light sprite sheets."""
    sprite_dirs = [
        os.path.join(repo_root, "tileserver", "sprites", "v4"),
        os.path.join(repo_root, "app", "static", "vendor", "protomaps-assets", "sprites", "v4"),
    ]
    tile_1x = _make_tolkien_wave_tile(64)
    tile_2x = _make_tolkien_wave_tile(128)

    for sdir in sprite_dirs:
        if not os.path.isdir(sdir):
            continue
        for suffix, scale, tile_img, base_y in [
            ("", 1, tile_1x, 114),
            ("@2x", 2, tile_2x, 228),
        ]:
            png_path = os.path.join(sdir, f"light{suffix}.png")
            json_path = os.path.join(sdir, f"light{suffix}.json")
            if not (os.path.isfile(png_path) and os.path.isfile(json_path)):
                continue

            with open(json_path, "r") as f:
                meta = json.load(f)

            tsize = 64 * scale
            im = Image.open(png_path).convert("RGBA")
            new_h = max(im.height, base_y + tsize)
            canvas = Image.new("RGBA", (im.width, new_h), (0, 0, 0, 0))
            canvas.paste(im, (0, 0))
            canvas.paste(tile_img, (0, base_y))
            canvas.save(png_path)

            meta["tolkien_water_waves"] = {
                "x": 0,
                "y": base_y,
                "width": tsize,
                "height": tsize,
                "pixelRatio": scale,
            }
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


def update_mgs_water_sprite(repo_root: str) -> None:
    """Idempotently add the 'mgs_water_texture' seamless pattern sprite to v4/dark sprite sheets."""
    sprite_dirs = [
        os.path.join(repo_root, "tileserver", "sprites", "v4"),
        os.path.join(repo_root, "app", "static", "vendor", "protomaps-assets", "sprites", "v4"),
    ]
    tile_1x = _make_mgs_water_tile(64)
    tile_2x = _make_mgs_water_tile(128)

    for sdir in sprite_dirs:
        if not os.path.isdir(sdir):
            continue
        for suffix, scale, tile_img, base_y in [
            ("", 1, tile_1x, 114),
            ("@2x", 2, tile_2x, 228),
        ]:
            png_path = os.path.join(sdir, f"dark{suffix}.png")
            json_path = os.path.join(sdir, f"dark{suffix}.json")
            if not (os.path.isfile(png_path) and os.path.isfile(json_path)):
                continue

            with open(json_path, "r") as f:
                meta = json.load(f)

            tsize = 64 * scale
            im = Image.open(png_path).convert("RGBA")
            new_h = max(im.height, base_y + tsize)
            canvas = Image.new("RGBA", (im.width, new_h), (0, 0, 0, 0))
            canvas.paste(im, (0, 0))
            canvas.paste(tile_img, (0, base_y))
            canvas.save(png_path)

            meta["mgs_water_texture"] = {
                "x": 0,
                "y": base_y,
                "width": tsize,
                "height": tsize,
                "pixelRatio": scale,
            }
            with open(json_path, "w") as f:
                json.dump(meta, f, separators=(",", ":"))


def build_themed_style(
    light_tpl: dict,
    name: str,
    sprite_name: str,
    country_colors: list,
    state_colors: list,
    palette: dict[str, str],
    bold_font: str,
    reg_font: str,
    italic_font: str,
    urban_fill_color: str | None = None,
    water_pattern: str | None = None,
    strip_uppercase: bool = False,
    font_scale: float = 1.0,
) -> dict:
    style = copy.deepcopy(light_tpl)
    style["name"] = name
    style["glyphs"] = "{fontstack}/{range}.pbf"
    style["sprite"] = sprite_name
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
            paint["background-color"] = palette["water"]
            new_layers.append(lyr)
            continue

        if lid == "earth":
            paint["fill-color"] = palette["earth"]
            new_layers.append(lyr)
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
            lyr["minzoom"] = 5
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
            paint["fill-opacity"] = ["interpolate", ["linear"], ["zoom"], 5, 0.0, 6.2, 0.45, 8.0, 0.0]
            new_layers.append(lyr)
            continue

        if lid.startswith("landuse_park"):
            paint["fill-color"] = [
                "case",
                ["in", ["get", "pmap:kind"], ["literal", ["national_park", "park", "cemetery", "protected_area", "nature_reserve", "forest", "golf_course"]]],
                palette["park"],
                ["==", ["get", "pmap:kind"], "wood"],
                palette["wood"],
                ["in", ["get", "pmap:kind"], ["literal", ["scrub", "grassland", "grass"]]],
                palette["scrub"],
                ["==", ["get", "pmap:kind"], "glacier"],
                palette["glacier"],
                ["==", ["get", "pmap:kind"], "sand"],
                palette["sand"],
                ["in", ["get", "pmap:kind"], ["literal", ["military", "naval_base", "airfield"]]],
                palette["military"],
                palette["earth"],
            ]
            paint["fill-opacity"] = ["interpolate", ["linear"], ["zoom"], 6, 0.0, 9, 0.65, 12, 0.88]
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
                        "paint": {
                            "fill-pattern": water_pattern,
                            "fill-opacity": 0.55,
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
            if "boundary_country_outer" in palette:
                outer_lyr = copy.deepcopy(lyr)
                outer_lyr["id"] = "boundaries_country_outer"
                outer_lyr["paint"] = {
                    "line-color": palette["boundary_country_outer"],
                    "line-opacity": 0.85,
                    "line-width": ["interpolate", ["linear"], ["zoom"], 0, 2.6, 2, 4.2, 5, 6.8, 8, 9.8, 12, 12.5],
                }
                new_layers.append(outer_lyr)
            casing_lyr = copy.deepcopy(lyr)
            casing_lyr["id"] = "boundaries_country_casing"
            casing_lyr["paint"] = {
                "line-color": palette["boundary_country_casing"],
                "line-opacity": palette.get("boundary_country_casing_opacity", 0.55),
                "line-width": (
                    ["interpolate", ["linear"], ["zoom"], 0, 1.6, 2, 2.5, 5, 4.2, 8, 6.0, 12, 7.6]
                    if "boundary_country_outer" in palette
                    else ["interpolate", ["linear"], ["zoom"], 0, 2.2, 2, 3.5, 5, 6.0, 8, 9.0, 12, 11.5]
                ),
            }
            new_layers.append(casing_lyr)
            paint["line-color"] = palette["boundary_country"]
            paint["line-width"] = (
                ["interpolate", ["linear"], ["zoom"], 0, 0.7, 2, 1.0, 5, 1.5, 8, 2.1, 12, 2.6]
                if "boundary_country_outer" in palette
                else ["interpolate", ["linear"], ["zoom"], 0, 0.9, 2, 1.3, 5, 1.9, 8, 2.6, 12, 3.2]
            )
            new_layers.append(lyr)
            continue
        elif lid == "boundaries":
            casing_lyr = copy.deepcopy(lyr)
            casing_lyr["id"] = "boundaries_casing"
            casing_lyr["paint"] = {
                "line-color": palette["boundary_state_casing"],
                "line-opacity": palette.get("boundary_state_casing_opacity", 0.45),
                "line-width": ["interpolate", ["linear"], ["zoom"], 3, 1.8, 6, 3.4, 10, 5.2],
            }
            new_layers.append(casing_lyr)
            paint["line-color"] = palette["boundary_state"]
            paint["line-width"] = ["interpolate", ["linear"], ["zoom"], 3, 0.8, 6, 1.3, 10, 1.9]
            new_layers.append(lyr)
            continue

        if ltype == "symbol" and isinstance(layout, dict):
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
) -> None:
    layout = lyr.setdefault("layout", {})
    paint = lyr.setdefault("paint", {})

    if strip_uppercase and "text-transform" in layout:
        del layout["text-transform"]

    def _s(val: float) -> int:
        return int(round(val * font_scale))

    if lid == "places_country":
        layout["text-font"] = [bold_font]
        layout["text-letter-spacing"] = 0.03 if strip_uppercase else 0.08
        layout["text-size"] = [
            "interpolate", ["linear"], ["zoom"],
            1, ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 10], _s(9), _s(12)],
            3, ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 10], _s(11), _s(16)],
            6, ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 8], _s(13), _s(21)],
            9, ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 7], _s(15), _s(24)],
        ]
        paint["text-color"] = country_color
        paint["text-halo-color"] = country_halo
        paint["text-halo-width"] = 2.2
    elif lid == "places_region":
        lyr["minzoom"] = 3
        layout["text-font"] = [bold_font]
        layout["text-size"] = ["interpolate", ["linear"], ["zoom"], 3, _s(11), 7, _s(16)]
        paint["text-color"] = region_color
        paint["text-halo-color"] = region_halo
        paint["text-halo-width"] = 1.8
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
        paint["text-halo-width"] = 2.0
    elif lid == "places_subplace":
        layout["text-font"] = [reg_font]
        if font_scale != 1.0:
            layout["text-size"] = ["interpolate", ["linear"], ["zoom"], 10, _s(11), 14, _s(15)]
        paint["text-color"] = subplace_color
        paint["text-halo-color"] = subplace_halo
        paint["text-halo-width"] = 1.8
    elif lid in ("water_label_ocean", "water_label_lakes", "water_waterway_label"):
        layout["text-font"] = [italic_font]
        if font_scale != 1.0 and lid == "water_label_ocean":
            layout["text-size"] = ["interpolate", ["linear"], ["zoom"], 2, _s(12), 6, _s(18), 10, _s(22)]
            layout["text-letter-spacing"] = 0.06
        paint["text-color"] = water_color
        paint["text-halo-color"] = water_halo
        paint["text-halo-width"] = 1.8
    elif lid == "earth_label_islands":
        layout["text-font"] = [italic_font]
        paint["text-color"] = subplace_color
        paint["text-halo-color"] = subplace_halo
        paint["text-halo-width"] = 1.6
    elif lid == "roads_shields":
        layout["text-font"] = [bold_font]
        paint["text-color"] = shield_color
    elif lid in ("roads_labels_major", "roads_labels_minor", "address_label"):
        layout["text-font"] = [reg_font]
        if font_scale != 1.0:
            layout["text-size"] = ["interpolate", ["linear"], ["zoom"], 12, _s(11), 16, _s(15)]
        paint["text-color"] = road_color
        paint["text-halo-color"] = road_halo
        paint["text-halo-width"] = 1.8
    elif lid == "pois":
        layout["text-font"] = [reg_font]
        if font_scale != 1.0:
            layout["text-size"] = _s(12)
        paint["text-halo-color"] = poi_halo
        paint["text-halo-width"] = 1.6
    else:
        layout["text-font"] = [reg_font]

    raw_layout = json.dumps(layout)
    if "Noto Sans" in raw_layout:
        raw_layout = raw_layout.replace('"Noto Sans Medium"', f'"{bold_font}"')
        raw_layout = raw_layout.replace('"Noto Sans Italic"', f'"{italic_font}"')
        raw_layout = raw_layout.replace('"Noto Sans Regular"', f'"{reg_font}"')
        lyr["layout"] = json.loads(raw_layout)


MATRIX_PALETTE = {
    "water": "#030303",
    "water_outline": "#27542b",
    "river": "#193822",
    "earth": "#1b251a",
    "lc_grass": "#263822",
    "lc_barren": "#3d4726",
    "lc_urban": "#243632",
    "lc_farm": "#2b4225",
    "lc_scrub": "#324523",
    "lc_forest": "#27542b",
    "park": "#27542b",
    "wood": "#2d5e31",
    "scrub": "#46592b",
    "glacier": "#77a382",
    "sand": "#616e36",
    "military": "#5f0a11",
    "hospital": "#5f0a11",
    "industrial": "#202e20",
    "school": "#263822",
    "zoo": "#27542b",
    "aerodrome": "#1f2e22",
    "runway": "#27542b",
    "pedestrian": "#223322",
    "pier": "#263822",
    "buildings": "#263822",
    "buildings_outline": "#616e36",
    "tunnel": "#1b251a",
    "highway": "#ad1d1d",
    "highway_casing": "#5f0a11",
    "major": "#77a382",
    "major_casing": "#1f3827",
    "link": "#616e36",
    "link_casing": "#1b251a",
    "minor_a": "#27542b",
    "minor_b": "#616e36",
    "minor_casing": "#111810",
    "railway": "#77a382",
    # Two-tone country & major borders: dark wine-red with dark charcoal-gray / black
    "boundary_country_outer": "#383e3c",
    "boundary_country_casing": "#820f1b",
    "boundary_country_casing_opacity": 0.95,
    "boundary_country": "#0b0d0c",
    "boundary_state_casing": "#2c3230",
    "boundary_state_casing_opacity": 0.88,
    "boundary_state": "#961420",
    # Bright luminous silver-blue label fills
    "country_label": "#eef7fd",
    "state_label": "#c8e2f0",
    "city_label": "#e2f2fa",
    "subplace_label": "#bdd9ea",
    "water_label": "#96c5de",
    "water_halo": "#030504",
    "road_label": "#c6e0ee",
    "shield_label": "#e2f2fa",
    "poi_text": "#b5d6e8",
    "label_halo": "#030504",
}

WASTELAND_PALETTE = {
    "water": "#160b05",
    "water_outline": "#b14518",
    "river": "#462814",
    "earth": "#381e0e",
    # Reverted green changes; rich charred umber/sienna terrain + extra gold/yellow in populated areas
    "lc_grass": "#592b15",
    "lc_barren": "#8d3c18",
    "lc_urban": "#b87c16",
    "lc_farm": "#7d4a16",
    "lc_scrub": "#783517",
    "lc_forest": "#4e2613",
    "park": "#592b15",
    "wood": "#462814",
    "scrub": "#7d3818",
    "glacier": "#fea20f",
    "sand": "#c47d16",
    "military": "#8d3c18",
    "hospital": "#b85216",
    "industrial": "#9c6415",
    "school": "#e0a81b",
    "zoo": "#7d3818",
    "aerodrome": "#6e4614",
    "runway": "#8d3c18",
    "pedestrian": "#b07416",
    "pier": "#8f5b14",
    "buildings": "#d49619",
    "buildings_outline": "#ffe001",
    "tunnel": "#381e0e",
    "highway": "#ffe001",
    "highway_casing": "#6c3317",
    "major": "#ef5b18",
    "major_casing": "#462814",
    "link": "#d14f19",
    "link_casing": "#462814",
    "minor_a": "#8d3c18",
    "minor_b": "#c97e18",
    "minor_casing": "#261309",
    "railway": "#fe7d14",
    "boundary_country_casing": "#d14f19",
    "boundary_country": "#ffe001",
    "boundary_state_casing": "#6c3317",
    "boundary_state": "#fc6a17",
    "country_label": "#ffe001",
    "state_label": "#fea20f",
    "city_label": "#ffe45e",
    "subplace_label": "#feb426",
    "water_label": "#fc6a17",
    "water_halo": "#120804",
    "road_label": "#febe3a",
    "shield_label": "#ffe001",
    "poi_text": "#febe3a",
    "label_halo": "#140a05",
}

TOLKIEN_PALETTE = {
    "water": "#457d9e",
    "water_outline": "#1f445c",
    "river": "#366c8c",
    "earth": "#ede2c8",
    "lc_grass": "#9cd184",
    "lc_barren": "#cfa67e",
    "lc_urban": "#d4ad82",
    "lc_farm": "#ebd488",
    "lc_scrub": "#c49e78",
    "lc_forest": "#68a85b",
    "park": "#78b86a",
    "wood": "#62a355",
    "scrub": "#c49e78",
    "glacier": "#d8ecf5",
    "sand": "#f0de8a",
    "military": "#9eb8c9",
    "hospital": "#d98f82",
    "industrial": "#c7a987",
    "school": "#f0d478",
    "zoo": "#8cc474",
    "aerodrome": "#b8cad6",
    "runway": "#d4c3a8",
    "pedestrian": "#e6d4b5",
    "pier": "#c49e76",
    "buildings": "#c49a74",
    "buildings_outline": "#6e4b2e",
    "tunnel": "#c9b393",
    "highway": "#e6be50",
    "highway_casing": "#5e3d1e",
    "major": "#ebd28d",
    "major_casing": "#75522d",
    "link": "#e6cb83",
    "link_casing": "#7a5732",
    "minor_a": "#ebdcc0",
    "minor_b": "#f7efe0",
    "minor_casing": "#967958",
    "railway": "#543b24",
    # Two-tone color-on-color silver/gray borders (outer pewter-slate -> middle metallic silver -> inner iron-gray core)
    "boundary_country_outer": "#5c646d",
    "boundary_country_casing": "#d0d8e0",
    "boundary_country_casing_opacity": 0.94,
    "boundary_country": "#363b40",
    "boundary_state_casing": "#b4bdc6",
    "boundary_state_casing_opacity": 0.78,
    "boundary_state": "#484f57",
    "country_label": "#26140c",
    "state_label": "#4a2716",
    "city_label": "#24130b",
    "subplace_label": "#422718",
    "water_label": "#0d283b",
    "water_halo": "#f5faff",
    "road_label": "#3d2417",
    "shield_label": "#26140c",
    "poi_text": "#3b2314",
    "label_halo": "#fcfcfa",
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
            urban_fill_color="#c48816",
        ),
        "tolkien": build_themed_style(
            light_tpl=light_tpl,
            name="Tolkien",
            sprite_name="v4/light",
            country_colors=TOLKIEN_COUNTRY_COLORS,
            state_colors=TOLKIEN_STATE_COLORS,
            palette=TOLKIEN_PALETTE,
            bold_font="Charm Bold",
            reg_font="Charm Regular",
            italic_font="Charm Italic",
            water_pattern="tolkien_water_waves",
            strip_uppercase=True,
            font_scale=1.18,
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
