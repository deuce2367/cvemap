# CVE MapServer

A lightweight, secure, self-contained map server built with Python (FastAPI) and MapServer (CGI).

## Features
* **Low CVE Footprint**: Minimal dependencies (Alpine Linux, MapServer, FastAPI). No large Java or Node.js stacks.
* **Offline Ready**: Bundled with Leaflet.js and sample Natural Earth data. No external network requests are made.
* **WMS and XYZ Support**: Natively serves standard Web Map Service (WMS) and XYZ tiles.
* **Dynamic Layer Auto-Discovery**: Drop any Shapefile (`.shp`) or GeoJSON (`.geojson`) into the `data/` folder, and they are automatically configured and served!
* **Native MBTiles Support**: Drop any `.mbtiles` file into `data/`, and XYZ tiles will be served directly from it with no configuration needed.
* **Admin Dashboard**: Simple dashboard to view available layers and reload the map configuration on the fly.

## Running the MapServer

Build the Docker image:
```bash
docker build -t cvemap:latest .
```

Run the container, optionally mounting your own data directory:
```bash
docker run -d --name cvemap -p 8000:8000 -v $(pwd)/data:/app/data cvemap:latest
```

## Usage

1. **Map Viewer**: Open `http://localhost:8000/` in your browser.
2. **Admin Panel**: Open `http://localhost:8000/admin`.
   - **Login**: `admin` / `password`
3. **WMS Endpoint**: `http://localhost:8000/wms`
4. **XYZ Endpoint**: `http://localhost:8000/xyz/{layer_name}/{z}/{x}/{y}.png`

## Adding New Data

Simply drop new files into the `data/` folder (or your mounted volume).

* **Shapefiles/GeoJSON**: Place `.shp` or `.geojson` files in `data/`. Then go to the Admin Panel and click **Reload Map**. Your new layer will be available via WMS and XYZ using the file's base name.
* **MBTiles**: Place `.mbtiles` files in `data/`. They are instantly available at the XYZ endpoint (`/xyz/filename/{z}/{x}/{y}.png`) without needing a reload.
