# MILESTONE_2_CUAS_LOS.md — 3D Building Cache Fix + Virtual C-UAS Sensor & Blind-Spot Engine

## Objective
Fix the OpenStreetMap 3D building loader in `app.py`, add local GeoJSON caching so building data persists offline, and implement **Milestone 2: Interactive Counter-UAS (C-UAS) Sensor Placement & Line-of-Sight Blind-Spot Analysis**. Also create a `.gitignore` excluding `.venv/`, `__pycache__/`, and `cache/`.

---

## 1. Fix & Cache the 3D Building Loader (`osmnx` v2.x)
- Replace any deprecated `osmnx` calls with `osmnx >= 2.0` syntax:
  - Set `ox.settings.use_cache = True` and `ox.settings.requests_timeout = 60`.
  - Query `ox.features_from_point(center_point=(lat, lon), tags={"building": True}, dist=1800)`.
  - Filter strictly to `Polygon` and `MultiPolygon` geometries, normalize height in meters (`height` tag parsed safely to float, fallback to `building:levels * 3.5`, default `9.0` meters), and keep only `geometry` and `height_m` columns to stay lightweight.
- **Local Disk Caching (`data/` directory):**
  - Automatically create a `data/` directory if it does not exist.
  - Save each fetched stadium's processed buildings to `data/<venue_slug>_buildings.geojson` (e.g., `data/metlife_buildings.geojson`, `data/shi_stadium_buildings.geojson`).
  - On app load or stadium switch, automatically load from `data/<venue_slug>_buildings.geojson` if the file already exists; only query Overpass when the cache file is missing or the user clicks `LOAD / REFRESH BUILDINGS`.
  - If an error ever occurs during fetch, display the exact Python exception text inside `st.warning()` so issues are transparent.

---

## 2. Virtual C-UAS Sensor Network (Sidebar Controls)
Add a **"C-UAS SENSOR NETWORK"** control panel in the sidebar:
- **Quick-Deploy Tactical Preset Button (`LOAD TACTICAL PRESET`):**
  - Deploys 3 realistic sensors on elevated structures for the active venue:
    - **MetLife Stadium Preset:**
      1. *MetLife Upper Rim North* (`40.8146, -74.0745`, Height: `42m AGL`, Type: `Ku-Band Tactical Radar`)
      2. *American Dream Complex Roof* (`40.8094, -74.0678`, Height: `34m AGL`, Type: `Ku-Band Tactical Radar`)
      3. *East Rutherford Commercial Tower* (`40.8215, -74.0812`, Height: `38m AGL`, Type: `RF Drone Telemetry Detector`)
    - **SHI Stadium (Rutgers) Preset:**
      1. *SHI Stadium Press Box Roof* (`40.5138, -74.4659`, Height: `32m AGL`, Type: `Ku-Band Tactical Radar`)
      2. *Busch Campus High-Rise Roof* (`40.5218, -74.4615`, Height: `28m AGL`, Type: `RF Drone Telemetry Detector`)
      3. *Werblin / Athletic Complex Tower* (`40.5186, -74.4725`, Height: `24m AGL`, Type: `Ku-Band Tactical Radar`)
- **Custom Sensor Management:**
  - Allow adding, editing, or clearing up to 5 sensors with inputs for `Name`, `Latitude`, `Longitude`, `Mounting Height (m AGL)`, and `Sensor Type`:
    - **Ku-Band Tactical Radar:** Max Range `3,500 m`, Active Color Cyan `[0, 229, 255, 70]`
    - **RF Drone Telemetry Detector:** Max Range `5,500 m`, Active Color Emerald `[16, 185, 129, 60]`

---

## 3. Line-of-Sight & Building Shadow (Masking) Engine
- Project all sensor points, building polygons, and the `3.0 NM` TFR polygon into local UTM meters (`pyproj`) before computing geometry, then reproject back to `EPSG:4326` for `pydeck`.
- If sensors are active and building footprints are not yet loaded, automatically run the building loader/cache check first.
- For each sensor, cast `120` radial rays (`3°` increments around `360°`) from `(sensor_x, sensor_y)` out to `max_range_m`:
  - Intersect each ray against building polygons where `building.height_m > sensor.height_agl`.
  - If a taller building intersects the ray, clip the unobstructed ray at the nearest building wall (`hit_dist`), and record the occluded segment from `hit_dist` to `max_range_m` as a **Masked Blind-Spot Wedge**.
  - Build a unified `Active Coverage Polygon` (unobstructed wedges) and a unified `Blind-Spot Shadow Polygon` (occluded wedges behind taller buildings).
- **3D Map Layers (`pydeck`):**
  - Render extruded 3D buildings (`PolygonLayer`, `extruded=True`, `get_elevation="height_m"`).
  - Render **Active Sensor Coverage** in translucent cyan/emerald.
  - Render **Masked Blind Spots (Building Shadows)** in translucent crimson `[239, 68, 68, 95]`.
  - Render **3D Sensor Pylons** (`ColumnLayer`) at each sensor's coordinates rising to its `height_agl` with bright beacon caps.

---

## 4. Updated Tactical HUD KPI Bar
Add two live calculated metrics to the top HUD bar alongside existing cards:
- **3.0 NM TFR COVERAGE (%):** Percentage of the core 3.0 NM stadium TFR area covered by unobstructed sensor line-of-sight (`(Active_Coverage ∩ TFR).area / TFR.area * 100`).
- **MASKED BLIND-SPOT AREA (km²):** Total area inside the 3.0 NM TFR blocked by taller buildings (`(Blind_Spots ∩ TFR).area / 1e6`).