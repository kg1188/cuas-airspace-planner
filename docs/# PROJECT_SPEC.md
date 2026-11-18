# PROJECT_SPEC.md — Stadium Airspace & Counter-UAS (C-UAS) Sensor Planner
## Role & Objective
Act as a Senior Geospatial Software Engineer. Build **Milestone 1** of an interactive, web-based 3D Stadium Airspace & Counter-UAS (C-UAS) Planning Tool in Python. The application must run locally via Streamlit and use free, open-source geospatial libraries with zero paid API keys required.

## Core Tech Stack (Milestone 1)
- **Frontend / UI:** `streamlit`
- **3D Geospatial Visualization:** `pydeck` (Deck.gl)
- **Geospatial Math & Projections:** `geopandas`, `shapely`, `pyproj`
- **3D Building & Footprint Data:** `osmnx` (OpenStreetMap vector building footprints + height/levels tags)

## Milestone 1 Functional Requirements

### 1. Multi-Stadium Preset Selector (Sidebar)
Create a clean tactical dark-mode sidebar with a dropdown allowing the user to switch dynamically between test ranges, plus an option for custom Latitude/Longitude input:
- **Preset 1 (Default): MetLife Stadium (East Rutherford, NJ)**
  - Coordinates: `40.8135, -74.0745`
  - Seating Capacity: `82,500`
  - Nearby Airspace Context: `KTEB (Teterboro - 2.2 NM NE), KEWR (Newark - 8.5 NM S), NYC Class B`
- **Preset 2: SHI Stadium — Rutgers University (Piscataway, NJ)**
  - Coordinates: `40.5137, -74.4648`
  - Seating Capacity: `52,454`
  - Nearby Airspace Context: `Raritan River Valley Terrain Drop, KSMQ (Somerset - 9 NM NW)`

### 2. FAA Sporting Event TFR Geometry Engine
Using `shapely` and `pyproj` (projecting accurately to local UTM meters before converting back to WGS84 `EPSG:4326`), generate the exact federal FAA Sporting Event Temporary Flight Restriction (FDC NOTAM 4/3621) zones centered on the selected stadium:
- **Core TFR No-Fly Dome:** `3.0 Nautical Miles` (5,556 meters) radius, rendered from ground surface (`0 ft AGL`) up to `3,000 ft AGL` (`914.4 meters`). Render as a semi-transparent crimson/amber 3D cylinder or perimeter ring.
- **Early Warning / Detection Buffer Ring:** `5.0 Nautical Miles` (9,260 meters) outer perimeter ring on the ground plane to represent long-range RF/radar early detection.

### 3. 3D Urban Terrain & Building Extrusion Layer
- Query OpenStreetMap via `osmnx` within a `2.5 km` radius of the stadium center to pull real building footprints.
- Estimate building height in meters using the OSM `height` tag if available, or `building:levels * 3.5` meters (defaulting to `8m` if untagged).
- Render these buildings as extruded 3D polygons in `pydeck` so the user can visually inspect surrounding structures (e.g., American Dream complex next to MetLife, or Busch Campus buildings next to SHI Stadium) that cause radar/RF line-of-sight masking.

### 4. Tactical HUD & Metrics Bar
Display top-level KPI cards above the 3D map showing:
- Active Venue & Coordinates
- TFR Protected Area (in square miles / square kilometers)
- TFR Ceiling (`3,000 ft AGL`)
- Total Surrounding Structures Loaded (count of 3D obstacles in the inner security zone)

## Deliverables Expected from AI Agent
1. `requirements.txt` with all necessary Python packages.
2. `app.py` cleanly structured, modularized, and commented so additional milestones (sensor placement, 3D line-of-sight raycasting, live OpenSky ADS-B feeds) can be added cleanly in future sprints.
3. Brief terminal instructions on how to install dependencies and launch `streamlit run app.py`.