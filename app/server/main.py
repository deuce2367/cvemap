import os
import re
import shutil
import subprocess
import sqlite3
import glob
import json
import urllib.request
import urllib.error
from fastapi import FastAPI, Request, HTTPException, Depends, BackgroundTasks
from fastapi.responses import Response, RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets

from server.tileserver_sync import (
    TILESERVER_STYLES,
    inspect_pmtiles,
    sync_tileserver_runtime,
)

app = FastAPI()
security = HTTPBasic()

DATA_DIR = os.path.abspath("data")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
MAPSERVER_CONF = os.path.abspath("mapserver.conf")
TILESERVER_INTERNAL_URL = os.environ.get("TILESERVER_INTERNAL_URL", "http://127.0.0.1:8080").rstrip("/")


def find_mapserv_binary() -> str:
    for candidate in ("/usr/bin/mapserv", "/usr/lib/cgi-bin/mapserv"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("mapserv") or "/usr/bin/mapserv"


def resolve_internal_tile_url(external_url: str, for_gdal: bool = False) -> str:
    url = external_url.strip()
    url = re.sub(r"^https?://\{HOST\}(?::\d+)?", TILESERVER_INTERNAL_URL, url)
    if url.startswith("/tileserver/"):
        url = f"{TILESERVER_INTERNAL_URL}/{url[len('/tileserver/'):]}"
    elif url.startswith("/"):
        url = f"{TILESERVER_INTERNAL_URL}{url}"
    if for_gdal:
        url = url.replace("{z}", "${z}").replace("{x}", "${x}").replace("{y}", "${y}")
    return url


def write_gdal_wms_xml(map_id: str, layer_name: str, external_url: str, max_zoom: int = 18) -> str:
    safe_map = re.sub(r"[^a-zA-Z0-9_-]", "_", map_id)
    safe_layer = re.sub(r"[^a-zA-Z0-9_-]", "_", layer_name)
    xml_path = os.path.join(DATA_DIR, f".gdal_wms_{safe_map}_{safe_layer}.xml")
    server_url = resolve_internal_tile_url(external_url, for_gdal=True)
    tile_level = max(int(max_zoom or 18), 18)
    xml_content = f"""<GDAL_WMS>
  <Service name="TMS">
    <ServerUrl>{server_url}</ServerUrl>
  </Service>
  <DataWindow>
    <UpperLeftX>-20037508.342789244</UpperLeftX>
    <UpperLeftY>20037508.342789244</UpperLeftY>
    <LowerRightX>20037508.342789244</LowerRightX>
    <LowerRightY>-20037508.342789244</LowerRightY>
    <TileLevel>{tile_level}</TileLevel>
    <TileCountX>1</TileCountX>
    <TileCountY>1</TileCountY>
    <YOrigin>top</YOrigin>
  </DataWindow>
  <Projection>EPSG:3857</Projection>
  <BlockSizeX>256</BlockSizeX>
  <BlockSizeY>256</BlockSizeY>
  <BandsCount>4</BandsCount>
  <ZeroBlockHttpCodes>204,404</ZeroBlockHttpCodes>
  <MaxConnections>8</MaxConnections>
  <Timeout>30</Timeout>
</GDAL_WMS>
"""
    with open(xml_path, "w") as xf:
        xf.write(xml_content)
    return xml_path

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, "admin")
    correct_password = secrets.compare_digest(credentials.password, "password")
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

def infer_layer_type(shp_path):
    lower_path = shp_path.lower()
    try:
        import subprocess
        if lower_path.endswith('.geojson'):
            import json
            with open(shp_path, 'r') as gf:
                data = json.load(gf)
            geom_type = data.get('features', [{}])[0].get('geometry', {}).get('type', '')
            if 'Point' in geom_type: return 'POINT'
            if 'Line' in geom_type: return 'LINE'
            return 'POLYGON'
            
        out = subprocess.check_output(["ogrinfo", "-so", "-al", shp_path], text=True)
        if "Point" in out or "MultiPoint" in out:
            return "POINT"
        elif "Line" in out or "MultiLineString" in out:
            return "LINE"
        else:
            return "POLYGON"
    except:
        if 'point' in lower_path or 'places' in lower_path or 'capital' in lower_path:
            return 'POINT'
        if 'road' in lower_path or 'river' in lower_path or 'line' in lower_path:
            return 'LINE'
        return 'POLYGON'

def get_default_color(layer_type, name):
    lower_name = name.lower()
    if 'ocean' in lower_name or 'water' in lower_name or 'lake' in lower_name:
        return {"color": "170 211 223", "outline": "150 150 150"}
    if layer_type == 'POINT':
        return {"color": "255 0 0", "outline": "0 0 0"}
    if layer_type == 'LINE':
        return {"color": "0 0 255", "outline": "-1 -1 -1"}
    return {"color": "242 239 233", "outline": "150 150 150"}

def get_shapefile_fields(shp_path):
    if shp_path.endswith('.pmtiles') or shp_path.endswith('.mbtiles'):
        return []
    try:
        import re
        full_path = os.path.join(DATA_DIR, shp_path)
        out = subprocess.check_output(["ogrinfo", "-so", "-al", full_path], text=True)
        fields = []
        for line in out.splitlines():
            m = re.match(r'^([a-zA-Z0-9_]+):', line)
            if m and m.group(1) not in ['INFO', 'Geometry', 'Feature', 'Layer', 'Extent', 'SRS', 'FID', 'OGRFeature', 'Metadata']:
                fields.append(m.group(1))
        return fields
    except:
        return []

def normalize_bg_color(bg) -> str:
    if isinstance(bg, list) and len(bg) == 3:
        return f"{int(bg[0])} {int(bg[1])} {int(bg[2])}"
    if isinstance(bg, str) and bg.strip():
        return bg.strip()
    return "255 255 255"


def scan_data_directories():
    os.makedirs(DATA_DIR, exist_ok=True)
    maps = {}
    settings = {"reference_map": "current"}
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                raw_cfg = json.load(f)
                maps = raw_cfg.get("maps", {})
                if "settings" in raw_cfg:
                    settings = raw_cfg["settings"]
        except:
            pass

    dirs_to_scan = []
    for base in os.listdir(DATA_DIR):
        base_path = os.path.join(DATA_DIR, base)
        if os.path.isdir(base_path) and base in ['legacy', 'webgl']:
            for item in os.listdir(base_path):
                item_path = os.path.join(base_path, item)
                if os.path.isdir(item_path) and not item.startswith('.'):
                    map_id = f"webgl_{item}" if base == "webgl" else item
                    dirs_to_scan.append((map_id, item_path))
                    
    for map_id, item_path in dirs_to_scan:
            if map_id not in maps:
                maps[map_id] = {
                    "title": "WebGL World" if map_id == "webgl_world" else map_id.capitalize(),
                    "bg_color": "255 255 255",
                    "layers": []
                }
            
            # Ensure map has normalized string bg_color
            maps[map_id]["bg_color"] = normalize_bg_color(maps[map_id].get("bg_color"))

            shp_files = glob.glob(os.path.join(item_path, "**/*.shp"), recursive=True)
            geojson_files = glob.glob(os.path.join(item_path, "**/*.geojson"), recursive=True)
            fgb_files = glob.glob(os.path.join(item_path, "**/*.fgb"), recursive=True)
            mbtiles_files = glob.glob(os.path.join(item_path, "**/*.mbtiles"), recursive=True)
            pmtiles_files = glob.glob(os.path.join(item_path, "**/*.pmtiles"), recursive=True)
            
            existing_layers = {l["name"] for l in maps[map_id]["layers"]}
            existing_paths = {l.get("path") for l in maps[map_id]["layers"] if l.get("path")}
            
            is_new_map = len(maps[map_id]["layers"]) == 0
            has_enabled_polygon = any(
                l.get("type") == "POLYGON" and l.get("enabled", True)
                for l in maps[map_id]["layers"]
            )

            for df in sorted(shp_files + geojson_files + fgb_files):
                layer_name = os.path.splitext(os.path.basename(df))[0]
                rel_df = os.path.relpath(df, DATA_DIR)
                if layer_name not in existing_layers and rel_df not in existing_paths:
                    ltype = infer_layer_type(df)
                    colors = get_default_color(ltype, layer_name)
                    default_enabled = True
                    if ltype == "POLYGON" and has_enabled_polygon:
                        default_enabled = False
                    elif ltype == "POLYGON":
                        has_enabled_polygon = True
                    maps[map_id]["layers"].append({
                        "name": layer_name,
                        "path": rel_df,
                        "type": ltype,
                        "color": colors["color"],
                        "outline": colors["outline"],
                        "enabled": default_enabled,
                        "is_mbtiles": False,
                        "size": 6 if ltype == 'POINT' else 1,
                        "style": "solid",
                        "fields": get_shapefile_fields(rel_df),
                        "label_enabled": False,
                        "label_field": "",
                        "label_color": "0 0 0",
                        "label_outline": "255 255 255",
                        "label_size": 10
                    })
                    existing_layers.add(layer_name)
                    existing_paths.add(rel_df)

            if is_new_map and len(maps[map_id]["layers"]) > 1:
                maps[map_id]["layers"].sort(
                    key=lambda l: {"POINT": 0, "LINE": 1, "POLYGON": 2}.get(l.get("type"), 2)
                )
            
            # Keep fields and PMTiles schema info updated for existing layers
            for l in maps[map_id]["layers"]:
                if not l.get("is_mbtiles") and not str(l.get("path", "")).endswith(".pmtiles"):
                    if not l.get("fields"):
                        l["fields"] = get_shapefile_fields(l["path"])
                    if "size" not in l:
                        l["size"] = 6 if l.get("type") == 'POINT' else 1
                    if "style" not in l:
                        l["style"] = "solid"
                elif str(l.get("path", "")).endswith(".pmtiles"):
                    full_pm = os.path.join(DATA_DIR, l["path"])
                    if os.path.isfile(full_pm):
                        pm_info = inspect_pmtiles(full_pm)
                        l["pmap_prefix"] = pm_info["pmap_prefix"]
                        if pm_info.get("bounds"):
                            l["bounds"] = pm_info["bounds"]
                        if "maxZoom" not in l:
                            l["maxZoom"] = pm_info["maxZoom"]
                        if "minZoom" not in l:
                            l["minZoom"] = pm_info["minZoom"]

            for mf in mbtiles_files:
                layer_name = os.path.splitext(os.path.basename(mf))[0]
                rel_mf = os.path.relpath(mf, DATA_DIR)
                if layer_name not in existing_layers and rel_mf not in existing_paths:
                    # extract minzoom/maxzoom and format if possible
                    minz, maxz = 0, 18
                    is_vec = map_id.startswith("webgl")
                    try:
                        conn = sqlite3.connect(mf)
                        c = conn.cursor()
                        c.execute("SELECT value FROM metadata WHERE name='minzoom'")
                        r = c.fetchone()
                        if r: minz = int(r[0])
                        c.execute("SELECT value FROM metadata WHERE name='maxzoom'")
                        r = c.fetchone()
                        if r: maxz = int(r[0])
                        c.execute("SELECT value FROM metadata WHERE name='format'")
                        r = c.fetchone()
                        if r and str(r[0]).lower() == "pbf":
                            is_vec = True
                        conn.close()
                    except:
                        pass
                        
                    maps[map_id]["layers"].append({
                        "name": layer_name,
                        "path": rel_mf,
                        "type": "RASTER",
                        "color": "",
                        "outline": "",
                        "enabled": True,
                        "is_mbtiles": True,
                        "is_vector_tiles": is_vec,
                        "minZoom": minz,
                        "maxZoom": maxz
                    })
                    existing_layers.add(layer_name)
                    existing_paths.add(rel_mf)

            for pf in sorted(pmtiles_files):
                layer_name = os.path.splitext(os.path.basename(pf))[0]
                rel_pf = os.path.relpath(pf, DATA_DIR)
                if layer_name not in existing_layers and rel_pf not in existing_paths:
                    pm_info = inspect_pmtiles(pf)
                    new_pm_layer = {
                        "name": layer_name,
                        "path": rel_pf,
                        "type": "RASTER",
                        "color": "",
                        "outline": "",
                        "enabled": True,
                        "is_mbtiles": False,
                        "is_vector_tiles": True,
                        "minZoom": pm_info["minZoom"],
                        "maxZoom": pm_info["maxZoom"],
                        "pmap_prefix": pm_info["pmap_prefix"],
                        "fields": [],
                        "size": 1,
                        "style": ""
                    }
                    if pm_info.get("bounds"):
                        new_pm_layer["bounds"] = pm_info["bounds"]
                    maps[map_id]["layers"].append(new_pm_layer)
                    existing_layers.add(layer_name)
                    existing_paths.add(rel_pf)

    # Ensure tileserver_raster map exists if any PMTiles archive is present or already configured
    has_any_pmtiles = bool(glob.glob(os.path.join(DATA_DIR, "**", "*.pmtiles"), recursive=True))
    if "tileserver_raster" not in maps and has_any_pmtiles:
        maps["tileserver_raster"] = {
            "title": "WebGL Raster (TileServer GL)",
            "bg_color": "255 255 255",
            "layers": [
                {
                    "name": "bio",
                    "path": "",
                    "type": "RASTER",
                    "enabled": True,
                    "is_mbtiles": True,
                    "minZoom": 0,
                    "maxZoom": 18,
                    "external_url": "/tileserver/styles/bio/{z}/{x}/{y}.png"
                }
            ]
        }

    for m_obj in maps.values():
        m_obj["bg_color"] = normalize_bg_color(m_obj.get("bg_color"))
                    
    with open(CONFIG_FILE, 'w') as f:
        json.dump({"maps": maps, "settings": settings}, f, indent=2)
        
    return maps


def get_ogr_layer_name(path):
    try:
        import subprocess
        output = subprocess.check_output(['ogrinfo', os.path.join(DATA_DIR, path)]).decode('utf-8')
        for line in output.split('\n'):
            if line.startswith('1: '):
                return line.split('1: ')[1].split(' ')[0].strip()
    except:
        pass
    return os.path.splitext(os.path.basename(path))[0]

def generate_mapfiles():
    maps = scan_data_directories()
    try:
        sync_tileserver_runtime(data_dir=DATA_DIR, reload_process=True)
    except Exception as exc:
        logging.getLogger(__name__).warning("TileServer runtime sync skipped: %s", exc)
    
    map_template = """MAP
  NAME "{map_id}"
  STATUS ON
  SIZE 256 256
  EXTENT -20037508.34 -20037508.34 20037508.34 20037508.34
  UNITS METERS
  SHAPEPATH "."
  IMAGECOLOR {bg_color}
  IMAGETYPE PNG

  OUTPUTFORMAT
    NAME "png"
    DRIVER "AGG/PNG"
    MIMETYPE "image/png"
    IMAGEMODE RGBA
    EXTENSION "png"
    FORMATOPTION "QUANTIZE_FORCE=off"
  END
  
  FONTSET "/app/fonts.txt"
  
  # Map-wide projection (Spherical Mercator for XYZ tiles)
  PROJECTION
    "init=epsg:3857"
  END

  DEBUG 5
  CONFIG "MS_ERRORFILE" "/tmp/ms_error.log"
  WEB
    METADATA
      "wms_title"           "{map_title}"
      "wms_onlineresource"  "http://localhost:8000/wms/{map_id}?"
      "wms_srs"             "EPSG:4326 EPSG:3857"
      "wms_enable_request"  "*"
      "wms_allow_getmap_without_styles" "true"
      "labelcache_map_edge_buffer" "0"
      "tile_map_edge_buffer" "10"
    END
  END

"""
    for map_id, map_data in maps.items():
        if map_id.startswith('webgl'):
            continue
        layers_str = ""
        defined_layer_names = set()
        for layer in reversed(map_data.get("layers", [])):
            if not layer.get("enabled", True):
                continue
            if layer.get("is_vector_tiles", False):
                continue
            if layer.get("external_url"):
                xml_path = write_gdal_wms_xml(
                    map_id,
                    layer["name"],
                    layer["external_url"],
                    max_zoom=layer.get("maxZoom", 18),
                )
                defined_layer_names.add(layer["name"])
                layers_str += f'''
LAYER
    NAME "{layer['name']}"
    TYPE RASTER
    STATUS ON
    DATA "{xml_path}"
    PROCESSING "OVERSAMPLE_RATIO=1.0"
    PROJECTION
        "init=epsg:3857"
    END
END
'''
                continue
            if layer.get("is_mbtiles", False):
                if not layer.get("path"):
                    continue
                defined_layer_names.add(layer["name"])
                layers_str += f'''
LAYER
    NAME "{layer['name']}"
    TYPE RASTER
    STATUS ON
    DATA "{os.path.join(DATA_DIR, layer['path'])}"
    PROCESSING "OVERSAMPLE_RATIO=1.0"
    PROJECTION
        "init=epsg:3857"
    END
END
'''
                continue
                
            layer_name = layer["name"]
            ltype = layer.get("type", "POLYGON")
            label_item = f'    LABELITEM "{layer.get("label_field")}"' if layer.get("label_enabled") and layer.get("label_field") else ""
            
            max_scale_str = ""
            if layer.get("minZoom") is not None and int(layer.get("minZoom")) > 0:
                layer_min_zoom = int(layer.get("minZoom"))
                layer_scale_denom = int(559082264 / (2 ** layer_min_zoom) * 1.5)
                max_scale_str = f"    MAXSCALEDENOM {layer_scale_denom}"

            label_max_scale_str = ""
            if layer.get("label_enabled") and layer.get("label_min_zoom") is not None and int(layer.get("label_min_zoom")) > 0:
                min_zoom = int(layer.get("label_min_zoom"))
                # rough conversion for spherical mercator scale denom
                scale_denom = int(559082264 / (2 ** min_zoom) * 1.5)
                label_max_scale_str = f"    LABELMAXSCALEDENOM {scale_denom}"

            

            is_ogr = layer["path"].lower().endswith(".geojson") or layer["path"].lower().endswith(".fgb")
            layer_data_name = os.path.splitext(os.path.basename(layer["path"]))[0]
            if is_ogr:
                layer_data_name = get_ogr_layer_name(layer["path"])
                # Handle possible quoting/spacing issues in ogrinfo SQL by quoting the layer name
                connection_str = f"    CONNECTIONTYPE OGR\n    CONNECTION \"{layer['path']}\"\n    DATA \"{layer_data_name}\""
            else:
                connection_str = f"    DATA \"{layer['path']}\""
            
            layers_str += f"""
  LAYER
    NAME "{layer_name}"
{connection_str}
    STATUS ON
    TYPE {ltype}
    PROCESSING "LABEL_NO_CLIP=True"
{max_scale_str}
{label_max_scale_str}
{label_item}
    PROJECTION
      "init=epsg:4326"
    END
"""
            if ltype == 'POLYGON' and layer.get('choro_enabled') and layer.get('choro_field'):
                layers_str += f"    CLASSITEM \"{layer.get('choro_field')}\"\n"

            
            
            label_str = ""
            if layer.get("label_enabled") and layer.get("label_field"):
                label_str = f"""
      LABEL
        COLOR {layer.get('label_color', '0 0 0')}
        OUTLINECOLOR {layer.get('label_outline', '255 255 255')}
        TYPE TRUETYPE
        FONT "lato"
        SIZE {layer.get('label_size', 10)}
        POSITION AUTO
        PARTIALS TRUE
        MINDISTANCE 250
        MINFEATURESIZE AUTO
        WRAP " "
        MAXLENGTH 5
      END
"""
            if ltype == 'POLYGON' and layer.get('choro_enabled') and layer.get('choro_field'):
                try:
                    import subprocess
                    import re
                    layer_file_path = os.path.join(DATA_DIR, layer["path"])
                    # use ogrinfo sqlite dialect to get min and max
                    layer_base = layer_data_name
                    choro_field = layer['choro_field']
                    cmd = ['ogrinfo', '-dialect', 'sqlite', '-sql', f'SELECT MIN({choro_field}), MAX({choro_field}) FROM "{layer_base}"', layer_file_path]
                    output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode('utf-8', errors='ignore')
                    
                    min_val = None
                    max_val = None
                    for line in output.split('\n'):
                        if line.strip().startswith(f'MIN({choro_field}) (Real)') or line.strip().startswith(f'MIN({choro_field}) (Integer)'):
                            try: min_val = float(line.split('=')[1].strip())
                            except: pass
                        if line.strip().startswith(f'MAX({choro_field}) (Real)') or line.strip().startswith(f'MAX({choro_field}) (Integer)'):
                            try: max_val = float(line.split('=')[1].strip())
                            except: pass
                            

                    if min_val is not None and max_val is not None:
                        step = (max_val - min_val) / 5.0
                        if step == 0: step = 1
                        cmaps = {
                            "blues": ["239 243 255", "189 215 231", "107 174 214", "49 130 189", "8 81 156"],
                            "reds": ["254 229 217", "252 174 145", "251 106 74", "222 45 38", "165 15 21"],
                            "viridis": ["68 1 84", "59 82 139", "33 145 140", "94 201 98", "253 231 37"],
                            "greens": ["237 248 233", "186 228 179", "116 196 118", "49 163 84", "0 109 44"],
                            "purples": ["242 240 247", "203 201 226", "158 154 200", "117 107 177", "84 39 143"],
                            "oranges": ["254 237 222", "253 190 133", "253 141 60", "230 85 13", "166 54 3"],
                            "magma": ["0 0 4", "81 18 124", "183 55 121", "252 137 97", "252 253 191"],
                            "plasma": ["13 8 135", "126 3 168", "204 71 120", "248 149 64", "240 249 33"],
                            "natgeo": ["228 217 197", "191 186 159", "162 165 130", "137 142 98", "113 120 73"],
                            "set1": ["228 26 28", "55 126 184", "77 175 74", "152 78 163", "255 127 0"],
                            "set2": ["102 194 165", "252 141 98", "141 160 203", "231 138 195", "166 216 84"]
                        }
                        colors = cmaps.get(layer.get("choro_cmap", "blues"), cmaps["blues"])
                        for i in range(5):
                            c_min = min_val + i*step
                            c_max = min_val + (i+1)*step
                            if i == 4:
                                expr = f"([{layer['choro_field']}] >= {c_min})"
                            elif i == 0:
                                expr = f"([{layer['choro_field']}] <= {c_max})"
                            else:
                                expr = f"([{layer['choro_field']}] > {c_min} AND [{layer['choro_field']}] <= {c_max})"
                            
                            layers_str += f"""
    CLASS
      NAME "{layer_name}_{i}"
      EXPRESSION ({expr})
      STYLE
        COLOR {colors[i]}
        OUTLINECOLOR {layer.get('outline', '150 150 150')}
        WIDTH {layer.get('size', 1)}
      END{label_str}
    END
"""
                    else:
                        # Categorical parsing for strings!
                        cmd2 = ['ogrinfo', '-dialect', 'sqlite', '-sql', f'SELECT DISTINCT {choro_field} FROM "{layer_base}" LIMIT 1000', layer_file_path]
                        output2 = subprocess.check_output(cmd2, stderr=subprocess.STDOUT).decode('utf-8', errors='ignore')
                        unique_vals = []
                        for line in output2.split('\n'):
                            if f'{choro_field} (String) =' in line:
                                val = line.split('=', 1)[1].strip()
                                unique_vals.append(val)
                        
                        if unique_vals:
                            cmaps = {
                                "blues": ["239 243 255", "189 215 231", "107 174 214", "49 130 189", "8 81 156"],
                                "reds": ["254 229 217", "252 174 145", "251 106 74", "222 45 38", "165 15 21"],
                                "viridis": ["68 1 84", "59 82 139", "33 145 140", "94 201 98", "253 231 37"],
                                "greens": ["237 248 233", "186 228 179", "116 196 118", "49 163 84", "0 109 44"],
                                "purples": ["242 240 247", "203 201 226", "158 154 200", "117 107 177", "84 39 143"],
                                "oranges": ["254 237 222", "253 190 133", "253 141 60", "230 85 13", "166 54 3"],
                                "magma": ["0 0 4", "81 18 124", "183 55 121", "252 137 97", "252 253 191"],
                                "plasma": ["13 8 135", "126 3 168", "204 71 120", "248 149 64", "240 249 33"],
                                "natgeo": ["228 217 197", "191 186 159", "162 165 130", "137 142 98", "113 120 73"],
                                "set1": ["228 26 28", "55 126 184", "77 175 74", "152 78 163", "255 127 0"],
                                "set2": ["102 194 165", "252 141 98", "141 160 203", "231 138 195", "166 216 84"]
                            }
                            colors = cmaps.get(layer.get("choro_cmap", "blues"), cmaps["blues"])
                            for i, val in enumerate(unique_vals):
                                c = colors[i % len(colors)]
                                val_clean = val.strip("'\"")
                                layers_str += f"""
    CLASS
      NAME "class_{i}"
      EXPRESSION "{val_clean}"
      STYLE
        COLOR {c}
        OUTLINECOLOR {layer.get('outline', '150 150 150')}
        WIDTH {layer.get('size', 1)}
      END{label_str}
    END
"""
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error("Choro failed: %s\n%s", str(e), __import__("traceback").format_exc())

            # Standard class generation if choropleth is not applied
            if "CLASS" not in layers_str.split("LAYER")[-1]:
                layers_str += f"""
    CLASS
      NAME "{layer_name}"
      STYLE
"""
                if ltype == 'POINT':
                    marker = layer.get("marker", "circle")
                    layers_str += f"""        SYMBOL "{marker}"
        SIZE {layer.get("size", 6)}
        COLOR {layer.get("color", "255 0 0")}
        OUTLINECOLOR {layer.get("outline", "0 0 0")}
      END{label_str}
    END
"""
                elif ltype == 'LINE':
                    pattern = ""
                    if layer.get("style") == "dashed":
                        pattern = "PATTERN 5 5 END"
                    elif layer.get("style") == "dotted":
                        pattern = "PATTERN 2 5 END"
                    layers_str += f"""        COLOR {layer.get("color", "0 0 255")}
        WIDTH {layer.get("size", 1)}
        {pattern}
      END{label_str}
    END
"""
                elif ltype == 'RASTER':
                    layers_str += f"""      END{label_str}\n    END\n"""
                else: # POLYGON
                    layers_str += f"""        COLOR {layer.get("color", "200 200 200")}
        OUTLINECOLOR {layer.get("outline", "150 150 150")}
        WIDTH {layer.get("size", 1)}
      END{label_str}
    END
"""
            

            
            layers_str += "  END\n"

        map_path = os.path.join(DATA_DIR, f"{map_id}.map")
        
        symbols = """
  SYMBOL
    NAME "circle"
    TYPE ellipse
    POINTS 1 1 END
    FILLED true
  END
  SYMBOL
    NAME "square"
    TYPE vector
    POINTS 0 0  0 1  1 1  1 0  0 0 END
    FILLED true
  END
  SYMBOL
    NAME "triangle"
    TYPE vector
    POINTS 0 1  0.5 0  1 1  0 1 END
    FILLED true
  END
  SYMBOL
    NAME "cross"
    TYPE vector
    POINTS 0 0.5  1 0.5  -99 -99  0.5 0  0.5 1 END
  END
  SYMBOL
    NAME "star"
    TYPE vector
    POINTS 0.5 0  0.6 0.3  1 0.3  0.7 0.6  0.8 1  0.5 0.8  0.2 1  0.3 0.6  0 0.3  0.4 0.3  0.5 0 END
    FILLED true
  END
"""
        # Auto-generate any font-awesome symbols used in this map
        for layer in map_data.get("layers", []):
            if layer.get("marker", "").startswith("fa-"):
                h = layer["marker"][3:]
                symbols += f"""  SYMBOL
    NAME "fa-{h}"
    TYPE TRUETYPE
    FONT "fontawesome"
    CHARACTER "&#x{h};"
    ANTIALIAS true
  END
"""

        # For tileserver_raster, also expose all TileServer styles as selectable WMS layers
        if map_id == "tileserver_raster":
            for ts_style in TILESERVER_STYLES:
                if ts_style not in defined_layer_names:
                    ts_xml = write_gdal_wms_xml(
                        map_id,
                        ts_style,
                        f"/tileserver/styles/{ts_style}/{{z}}/{{x}}/{{y}}.png",
                        max_zoom=18,
                    )
                    defined_layer_names.add(ts_style)
                    layers_str += f'''
LAYER
    NAME "{ts_style}"
    TYPE RASTER
    STATUS ON
    DATA "{ts_xml}"
    PROCESSING "OVERSAMPLE_RATIO=1.0"
    PROJECTION
        "init=epsg:3857"
    END
END
'''

        with open(map_path, "w") as f:
            f.write(map_template.format(map_id=map_id, map_title=map_data.get("title", map_id), bg_color=normalize_bg_color(map_data.get("bg_color"))) + symbols + layers_str + "END\n")

@app.on_event("startup")
def startup_event():
    generate_mapfiles()

os.makedirs(DATA_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")

@app.get("/")
def read_root():
    return RedirectResponse(url="/static/index.html?v=2")

@app.get("/api/maps")
def api_get_maps():
    config = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
    
    maps = scan_data_directories()
    settings = config.get("settings", {"reference_map": "current"})
    return JSONResponse(content={"maps": maps, "settings": settings, "tileserver_styles": TILESERVER_STYLES})

@app.get("/api/metadata/{map_id}/{layer_name}")
def api_get_metadata(map_id: str, layer_name: str, admin: bool = Depends(verify_admin)):
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f).get("maps", {})
    
    map_data = config.get(map_id, {})
    target_layer = next((l for l in map_data.get("layers", []) if l["name"] == layer_name), None)
    
    if not target_layer or target_layer.get("is_mbtiles"):
        return {"error": "Layer not found or is MBTiles"}
        
    full_path = os.path.join(DATA_DIR, target_layer["path"])
    try:
        out = subprocess.check_output(["ogrinfo", "-al", "-limit", "3", "-q", full_path], text=True)
        return {"metadata": out}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/mapfile/{map_id}")
def api_get_mapfile(map_id: str):
    from fastapi.responses import PlainTextResponse
    map_path = os.path.join(DATA_DIR, f"{map_id}.map")
    if os.path.exists(map_path):
        with open(map_path, "r") as f:
            return PlainTextResponse(f.read())
    return PlainTextResponse("Mapfile not generated yet. Please save configuration.", status_code=404)

import traceback
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.post("/api/maps")
async def api_save_maps(request: Request, background_tasks: BackgroundTasks, username: str = Depends(verify_admin)):
    data = await request.json()
    
    maps = data.get("maps", data)
    settings = data.get("settings", {"reference_map": "current"})
    
    # Auto-fix empty fields
    for map_id, map_obj in maps.items():
        for layer in map_obj.get("layers", []):
            if layer.get("label_enabled") and not layer.get("label_field"):
                fields = layer.get("fields", [])
                if fields:
                    layer["label_field"] = fields[-1] if "NAME" not in fields else "NAME"
            if layer.get("choro_enabled") and not layer.get("choro_field"):
                fields = layer.get("fields", [])
                if fields:
                    layer["choro_field"] = fields[-1] if "NAME" not in fields else "NAME"

    logger.info("Saving new configuration to %s", CONFIG_FILE)
    with open(CONFIG_FILE, 'w') as f:
        json.dump({"maps": maps, "settings": settings}, f, indent=2)
        
    def background_generate():
        try:
            logger.info("Regenerating mapfiles in background...")
            generate_mapfiles()
            logger.info("Successfully generated mapfiles.")
        except Exception as e:
            logger.error("Error generating mapfiles: %s", str(e))
            logger.error(traceback.format_exc())

    background_tasks.add_task(background_generate)
    return {"status": "ok"}

@app.get("/admin", response_class=HTMLResponse)
def admin_page(username: str = Depends(verify_admin)):
    return RedirectResponse(url="/static/admin.html")


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
def tileserver_proxy(path: str, request: Request):
    qs = f"?{request.url.query}" if request.url.query else ""
    return proxy_internal_url(f"{TILESERVER_INTERNAL_URL}/{path}{qs}")


def invoke_mapserver(map_id: str, request: Request, is_xyz: bool = False, x: int = 0, y: int = 0, z: int = 0, layer: str = ""):
    if is_xyz:
        num_tiles = 1 << z
        x = x % num_tiles

    map_path = os.path.join(DATA_DIR, f"{map_id}.map")
    if not os.path.exists(map_path):
        raise HTTPException(status_code=404, detail="Map not found")
        
    query_string = request.url.query
    if is_xyz:
        query_string = f"map={map_path}&mode=tile&tilemode=gmap&tile={x}+{y}+{z}&layers={layer.replace(',', ' ')}"
    else:
        if "map=" not in query_string:
            query_string = f"map={map_path}&{query_string}"
            
    env = os.environ.copy()
    env["REQUEST_METHOD"] = "GET" if is_xyz else request.method
    env["QUERY_STRING"] = query_string
    env["MAPSERVER_CONFIG_FILE"] = MAPSERVER_CONF
    
    try:
        result = subprocess.run([find_mapserv_binary()], env=env, capture_output=True, check=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
    raw_output = result.stdout
    header_part, _, body_part = raw_output.partition(b"\r\n\r\n")
    if not body_part:
        header_part, _, body_part = raw_output.partition(b"\n\n")
        
    if not body_part or (is_xyz and (b"ServiceException" in body_part or b"msDrawMap" in body_part)):
        if is_xyz:
            raise HTTPException(status_code=404, detail=f"Tile rendering failed: {raw_output.decode('utf-8', errors='ignore')}")
        
    headers = {}
    for line in header_part.decode(errors="ignore").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
            
    return Response(content=body_part, media_type=headers.get("Content-Type", "image/png"))

@app.get("/wms/{map_id}")
def wms_endpoint(map_id: str, request: Request):
    return invoke_mapserver(map_id, request)

@app.get("/xyz/{map_id}/{layer}/{z}/{x}/{y}.png")
def xyz_endpoint(map_id: str, layer: str, z: int, x: int, y: int, request: Request):
    config = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f).get("maps", {})
    map_data = config.get(map_id, {})

    if layer == "all":
        enabled_layers = [l for l in map_data.get("layers", []) if l.get("enabled", True)]
        if len(enabled_layers) == 1 and enabled_layers[0].get("external_url"):
            num_tiles = 1 << z
            url = resolve_internal_tile_url(enabled_layers[0]["external_url"]).format(z=z, x=x % num_tiles, y=y)
            return proxy_internal_url(url)
        layer_names = ",".join(reversed([l["name"] for l in enabled_layers])) if enabled_layers else "all"
        return invoke_mapserver(map_id, request, is_xyz=True, x=x, y=y, z=z, layer=layer_names)

    if map_id == "tileserver_raster" and layer in TILESERVER_STYLES:
        num_tiles = 1 << z
        return proxy_internal_url(f"{TILESERVER_INTERNAL_URL}/styles/{layer}/{z}/{x % num_tiles}/{y}.png")

    target_layer = next((l for l in map_data.get("layers", []) if l["name"] == layer), None)

    if target_layer and target_layer.get("external_url"):
        num_tiles = 1 << z
        url = resolve_internal_tile_url(target_layer["external_url"]).format(z=z, x=x % num_tiles, y=y)
        return proxy_internal_url(url)
    
    if target_layer and target_layer.get("is_mbtiles") and target_layer.get("path"):
        mbtiles_path = os.path.join(DATA_DIR, target_layer["path"])
        if os.path.isfile(mbtiles_path):
            tms_y = (1 << z) - 1 - y
            conn = sqlite3.connect(mbtiles_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                (z, x, tms_y)
            )
            row = cursor.fetchone()
            conn.close()
            if row:
                tile_data = row[0]
                media_type = "image/png"
                if tile_data.startswith(b'\xff\xd8\xff'):
                    media_type = "image/jpeg"
                elif tile_data.startswith(b'RIFF'):
                    media_type = "image/webp"
                return Response(content=tile_data, media_type=media_type)
            else:
                raise HTTPException(status_code=404, detail="Tile not found in MBTiles")
                
    return invoke_mapserver(map_id, request, is_xyz=True, x=x, y=y, z=z, layer=layer)

@app.get("/mvt/{map_id}/{layer}/{z}/{x}/{y}.pbf")
def mvt_endpoint(map_id: str, layer: str, z: int, x: int, y: int):
    # Endpoint specifically for serving Vector Tiles (PBF) from an MBTiles database.
    # Planetiler produces MBTiles where tile_data is a gzip-compressed PBF blob.
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f).get("maps", {})
    map_data = config.get(map_id, {})
    target_layer = next((l for l in map_data.get("layers", []) if l["name"] == layer), None)
    
    if target_layer and target_layer.get("is_mbtiles") and target_layer.get("path"):
        mbtiles_path = os.path.join(DATA_DIR, target_layer["path"])
        if os.path.isfile(mbtiles_path):
            tms_y = (1 << z) - 1 - y
            conn = sqlite3.connect(mbtiles_path)
            cursor = conn.cursor()
            cursor.execute("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?", (z, x, tms_y))
            row = cursor.fetchone()
            conn.close()
            if row:
                return Response(
                    content=row[0], 
                    media_type="application/x-protobuf", 
                    headers={"Content-Encoding": "gzip", "Access-Control-Allow-Origin": "*"}
                )
    raise HTTPException(status_code=404, detail="Vector Tile not found")

@app.get("/modern")
def modern_endpoint():
    modern_path = os.path.abspath("static/modern.html")
    with open(modern_path, "r") as f:
        return HTMLResponse(
            content=f.read(),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
