#!/usr/bin/env python3
"""
Generate the 'Elementary' custom MapLibre style JSON (tileserver/styles/elementary.json
and app/static/vendor/protomaps-themes/elementary.json) featuring:
  - National Geographic / classroom political choropleth coloring for countries & states/provinces
  - Bright classroom landcover, landuse, water, coastline outlines, and NatGeo border ribbons
  - CC0 1.0 Public Domain 'Crayon Libre' font stacks (Regular, Bold, Italic)
"""

import copy
import json
import os


COUNTRY_CHOROPLETH_COLOR_EXPR = [
    "match",
    ["get", "color_id"],
    1,
    "#fbe17b",  # Sunny Schoolbus Yellow
    2,
    "#f9b679",  # Classroom Tangerine Apricot
    3,
    "#a6df9e",  # Bright Apple Meadow Green
    4,
    "#f7a8bc",  # Carnation Flamingo Pink
    5,
    "#c9b1eb",  # Classroom Lavender Purple
    6,
    "#fded9e",  # Buttercup Lemon Gold
    7,
    "#95dfd2",  # Seafoam Mint Turquoise
    8,
    "#f79e8e",  # Warm Coral Salmon
    9,
    "#cbe693",  # Spring Chartreuse Pear
    "#fbe17b",
]

STATE_CHOROPLETH_COLOR_EXPR = [
    "match",
    ["get", "color_id"],
    1,
    "#fde382",
    2,
    "#f8b37c",
    3,
    "#a9e0a0",
    4,
    "#f6a6ba",
    5,
    "#c8b0ea",
    6,
    "#98dfd2",
    7,
    "#f69d8f",
    8,
    "#cce696",
    9,
    "#f9ca84",
    "#fde382",
]


def build_elementary_style(light_tpl: dict) -> dict:
    style = copy.deepcopy(light_tpl)
    style["name"] = "Elementary"
    style["glyphs"] = "{fontstack}/{range}.pbf"
    style["sprite"] = "v4/light"
    style["sources"] = {
        "protomaps": {
            "type": "vector",
            "url": "mbtiles://planet",
        },
        "choropleth": {
            "type": "vector",
            "url": "pmtiles://elementary_choropleth",
            "maxzoom": 7,
        },
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
            # Insert political choropleth layers right above earth (and below local parks/water/roads)
            new_layers.append(
                {
                    "id": "choropleth_countries",
                    "type": "fill",
                    "source": "choropleth",
                    "source-layer": "choropleth",
                    "filter": ["==", ["get", "kind"], "country"],
                    "paint": {
                        "fill-color": COUNTRY_CHOROPLETH_COLOR_EXPR,
                        "fill-opacity": [
                            "interpolate",
                            ["linear"],
                            ["zoom"],
                            0,
                            0.94,
                            4,
                            0.90,
                            6,
                            0.74,
                            9,
                            0.42,
                            12,
                            0.25,
                            15,
                            0.18,
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
                        "fill-color": STATE_CHOROPLETH_COLOR_EXPR,
                        "fill-opacity": [
                            "interpolate",
                            ["linear"],
                            ["zoom"],
                            2,
                            0.0,
                            2.8,
                            0.82,
                            5,
                            0.84,
                            7.5,
                            0.62,
                            10,
                            0.35,
                            13,
                            0.20,
                        ],
                    },
                }
            )
            continue

        if lid == "landcover":
            lyr["minzoom"] = 5
            paint["fill-color"] = [
                "match",
                ["get", "pmap:kind"],
                "grassland",
                "rgba(166, 224, 148, 1)",
                "barren",
                "rgba(252, 230, 172, 1)",
                "urban_area",
                "rgba(247, 202, 172, 1)",
                "farmland",
                "rgba(212, 236, 154, 1)",
                "glacier",
                "rgba(244, 250, 255, 1)",
                "scrub",
                "rgba(196, 230, 158, 1)",
                "rgba(138, 214, 142, 1)",
            ]
            paint["fill-opacity"] = [
                "interpolate",
                ["linear"],
                ["zoom"],
                5,
                0.0,
                6.2,
                0.35,
                7.5,
                0.0,
            ]
            new_layers.append(lyr)
            continue

        if lid.startswith("landuse_park"):
            paint["fill-color"] = [
                "case",
                [
                    "in",
                    ["get", "pmap:kind"],
                    [
                        "literal",
                        [
                            "national_park",
                            "park",
                            "cemetery",
                            "protected_area",
                            "nature_reserve",
                            "forest",
                            "golf_course",
                        ],
                    ],
                ],
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
            # Add classic classroom / NatGeo crisp blue coastline & lake shore outline
            new_layers.append(
                {
                    "id": "water_outline",
                    "type": "line",
                    "source": "protomaps",
                    "source-layer": "water",
                    "filter": ["==", "$type", "Polygon"],
                    "paint": {
                        "line-color": "#4594c4",
                        "line-width": [
                            "interpolate",
                            ["linear"],
                            ["zoom"],
                            0,
                            0.6,
                            4,
                            1.0,
                            8,
                            1.4,
                            14,
                            1.8,
                        ],
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
            if "casing" in lid:
                paint["line-color"] = "#c9b497"
            else:
                paint["line-color"] = "#ede2ce"
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
                paint["line-color"] = [
                    "interpolate",
                    ["linear"],
                    ["zoom"],
                    12,
                    "#f3ead8",
                    15,
                    "#fffdf7",
                ]
            else:
                paint["line-color"] = "#f7f0e1"
        elif lid == "boundaries_country":
            # Insert classic National Geographic warm coral-red inner border ribbon before crisp boundary line
            casing_lyr = copy.deepcopy(lyr)
            casing_lyr["id"] = "boundaries_country_casing"
            casing_lyr["paint"] = {
                "line-color": "#e0533c",
                "line-opacity": 0.34,
                "line-width": [
                    "interpolate",
                    ["linear"],
                    ["zoom"],
                    0,
                    2.0,
                    2,
                    3.2,
                    5,
                    5.5,
                    8,
                    8.5,
                    12,
                    11.0,
                ],
            }
            new_layers.append(casing_lyr)

            paint["line-color"] = "#591c15"
            paint["line-width"] = [
                "interpolate",
                ["linear"],
                ["zoom"],
                0,
                0.8,
                2,
                1.2,
                5,
                1.8,
                8,
                2.5,
                12,
                3.2,
            ]
            new_layers.append(lyr)
            continue
        elif lid == "boundaries":
            casing_lyr = copy.deepcopy(lyr)
            casing_lyr["id"] = "boundaries_casing"
            casing_lyr["paint"] = {
                "line-color": "#9c5638",
                "line-opacity": 0.22,
                "line-width": [
                    "interpolate",
                    ["linear"],
                    ["zoom"],
                    3,
                    1.6,
                    6,
                    3.2,
                    10,
                    5.0,
                ],
            }
            new_layers.append(casing_lyr)

            paint["line-color"] = "#5c3828"
            paint["line-width"] = [
                "interpolate",
                ["linear"],
                ["zoom"],
                3,
                0.7,
                6,
                1.2,
                10,
                1.8,
            ]
            new_layers.append(lyr)
            continue

        # Update all symbol (label) layers to use Crayon Libre (Regular, Bold, Italic)
        if ltype == "symbol" and isinstance(layout, dict):
            if lid == "places_country":
                layout["text-font"] = ["Crayon Libre Bold"]
                layout["text-letter-spacing"] = 0.08
                layout["text-size"] = [
                    "interpolate",
                    ["linear"],
                    ["zoom"],
                    1,
                    ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 10], 9, 12],
                    3,
                    ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 10], 11, 16],
                    6,
                    ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 8], 13, 21],
                    9,
                    ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 7], 15, 24],
                ]
                paint["text-color"] = "#2b1912"
                paint["text-halo-color"] = "#fffdf7"
                paint["text-halo-width"] = 2.0
            elif lid == "places_region":
                lyr["minzoom"] = 3
                layout["text-font"] = ["Crayon Libre Bold"]
                layout["text-size"] = ["interpolate", ["linear"], ["zoom"], 3, 11, 7, 16]
                paint["text-color"] = "#592e1e"
                paint["text-halo-color"] = "#fffaf0"
                paint["text-halo-width"] = 1.6
            elif lid == "places_locality":
                lyr["minzoom"] = 3
                layout["text-font"] = [
                    "case",
                    ["<=", ["coalesce", ["get", "pmap:min_zoom"], ["get", "min_zoom"], 99], 5],
                    ["literal", ["Crayon Libre Bold"]],
                    ["literal", ["Crayon Libre Regular"]],
                ]
                layout["text-size"] = [
                    "interpolate",
                    ["linear"],
                    ["zoom"],
                    3,
                    ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 13], 8, 10],
                    5,
                    ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 12], 9, 13],
                    7,
                    ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 11], 11, 16],
                    10,
                    ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 9], 12, 19],
                    14,
                    ["case", ["<", ["coalesce", ["get", "pmap:population_rank"], ["get", "population_rank"], 0], 8], 13, 22],
                ]
                paint["text-color"] = "#241914"
                paint["text-halo-color"] = "#ffffff"
                paint["text-halo-width"] = 1.8
            elif lid == "places_subplace":
                layout["text-font"] = ["Crayon Libre Regular"]
                paint["text-color"] = "#4a352b"
                paint["text-halo-color"] = "#fffdf7"
                paint["text-halo-width"] = 1.5
            elif lid in ("water_label_ocean", "water_label_lakes", "water_waterway_label"):
                layout["text-font"] = ["Crayon Libre Italic"]
                paint["text-color"] = "#164e78"
                paint["text-halo-color"] = "#d8f0ff"
                paint["text-halo-width"] = 1.8
            elif lid == "earth_label_islands":
                layout["text-font"] = ["Crayon Libre Italic"]
                paint["text-color"] = "#3d291e"
                paint["text-halo-color"] = "#fffdf7"
                paint["text-halo-width"] = 1.6
            elif lid == "roads_shields":
                layout["text-font"] = ["Crayon Libre Bold"]
                paint["text-color"] = "#2b1912"
            elif lid in ("roads_labels_major", "roads_labels_minor", "address_label"):
                layout["text-font"] = ["Crayon Libre Regular"]
                paint["text-color"] = "#4a372d"
                paint["text-halo-color"] = "#fffdf7"
                paint["text-halo-width"] = 1.6
            elif lid == "pois":
                layout["text-font"] = ["Crayon Libre Regular"]
                paint["text-halo-color"] = "#ffffff"
                paint["text-halo-width"] = 1.5
            else:
                layout["text-font"] = ["Crayon Libre Regular"]

            # Also replace any embedded Noto Sans font literals inside multiline text-field expressions
            raw_layout = json.dumps(layout)
            if "Noto Sans" in raw_layout:
                raw_layout = raw_layout.replace('"Noto Sans Medium"', '"Crayon Libre Bold"')
                raw_layout = raw_layout.replace('"Noto Sans Italic"', '"Crayon Libre Italic"')
                raw_layout = raw_layout.replace('"Noto Sans Regular"', '"Crayon Libre Regular"')
                lyr["layout"] = json.loads(raw_layout)

        new_layers.append(lyr)

    style["layers"] = new_layers
    return style


def main() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    light_path = os.path.join(repo_root, "tileserver", "styles", "light.json")
    with open(light_path, "r") as f:
        light_tpl = json.load(f)

    elementary_style = build_elementary_style(light_tpl)

    out_paths = [
        os.path.join(repo_root, "tileserver", "styles", "elementary.json"),
        os.path.join(repo_root, "app", "static", "vendor", "protomaps-themes", "elementary.json"),
    ]
    for p in out_paths:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            json.dump(elementary_style, f, indent=2)
        print(f"Wrote {p} ({len(elementary_style['layers'])} layers)")


if __name__ == "__main__":
    main()
