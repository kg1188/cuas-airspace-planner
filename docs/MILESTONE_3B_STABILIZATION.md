# MILESTONE_3B_STABILIZATION.md — Fix Grey-Screen Layer Toggles, Organize Sidebar & Harden Telemetry

## Objective
Stabilize `app.py` so toggling map layers never causes a grey screen, organize the sidebar into clean collapsible sections, provide an offline backup air-traffic dataset for reliable demos, and make the Rogue UAS Simulator feedback unmistakable. Keep existing working 3D building heights and hover tooltips intact.

---

## 1. Fix Grey-Screen Crash When Toggling Map Layers
- Before building `pdk.Deck(layers=...)`, validate every optional layer:
  - Only append a layer to the `layers` list if its sidebar toggle is `True` **AND** its underlying GeoDataFrame / DataFrame is non-empty (`len(data) > 0`). Never pass empty collections or `None` geometries into `pydeck`.
  - Ensure `map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"` remains locked on every Streamlit rerun so the dark tactical basemap never resets to grey.

## 2. Clean Collapsible Sidebar Architecture
Reorganize the left sidebar controls into three clean `st.expander` sections (keeping `TEST RANGE` venue selector at the top):
1. **`1. TERRAIN & FAA TFR`** (`expanded=True`):
   - `LOAD / REFRESH BUILDINGS` button + cached structure count status
   - Layer visibility checkboxes (`Show 3D Buildings`, `Show FAA TFR Rings`, `Show Sensor Coverage`, `Show Blind-Spot Shadows`, `Show Live Air Traffic`)
2. **`2. C-UAS SENSOR NETWORK`** (`expanded=True`):
   - `LOAD TACTICAL PRESET` and `CLEAR ALL` buttons
   - Active sensor list and `Add Custom Sensor` form
3. **`3. LIVE AIR & ROGUE UAS SIMULATOR`** (`expanded=True`):
   - `Fetch Live OpenSky Traffic` button **plus** a `Load Sample KTEB/KEWR Approach Traffic` backup button (populates 6 realistic commercial/private tracks around Teterboro and Newark so demos never fail if OpenSky rate-limits)
   - Rogue UAS Simulator controls (`Enable Simulation`, `Approach Bearing`, `Drone Altitude AGL`, `Track Progress %`)

## 3. High-Visibility Rogue UAS ("Red Air") Path & Tactical Banner
- When `Enable Rogue UAS Simulation` is active:
  - Render the planned inbound track from `5.0 NM` to stadium center as a high-contrast glowing orange `PathLayer` (`[249, 115, 22, 220]`, width `4px`) and the drone's current position as a bright beacon (`ScatterplotLayer` + `TextLayer` showing altitude and distance to stadium in NM).
  - Display a prominent **Tactical Threat Status Banner** directly above the 3D map showing:
    - **Airspace Status:** `OUTSIDE TFR (>3.0 NM)` (neutral) vs. `TFR VIOLATION (<3.0 NM)` (red)
    - **Sensor Status:** `DETECTED & TRACKING BY: [Sensor Name(s)]` (green) vs. `OCCLUDED IN BUILDING BLIND SPOT — UNDETECTED` (crimson alert)

## 4. Data Attribution Footer
- Update the bottom status strip to include clean open-data attribution:
  - `GEOMETRY: UTM PROJECTION • TERRAIN: © OPENSTREETMAP CONTRIBUTORS • TELEMETRY: OPENSKY NETWORK • ACADEMIC / PLANNING SIMULATION ONLY`