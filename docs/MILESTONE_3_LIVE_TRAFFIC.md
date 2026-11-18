# MILESTONE_3_LIVE_TRAFFIC.md — Live ADS-B Airspace Feed & Rogue UAS Intrusion Simulator

## Objective
Upgrade `app.py` to **Milestone 3** by integrating real-time commercial/general aviation transponder traffic (OpenSky Network API) around the stadium TFR and adding an interactive **Rogue UAS Incursion Simulator** that tests whether an inbound low-altitude drone is detected by active sensors or exploits a building-masked radar blind spot.

---

## 1. Live Airspace Transponder Feed (OpenSky Network REST API)
- Add a **"LIVE AIRSPACE TELEMETRY"** section in the sidebar with a `Fetch / Refresh Live Traffic` button and an `Auto-Display Live Aircraft` checkbox.
- Query the free public OpenSky Network REST endpoint using a tight bounding box (`±0.25°` lat/lon around the active stadium) with `timeout=10`:
  - `https://opensky-network.org/api/states/all?lamin=...&lomin=...&lamax=...&lomax=...`
- Parse active airborne tracks (`on_ground == False`), extracting `icao24`, `callsign`, `longitude`, `latitude`, `baro_altitude` (converted to feet), and `velocity` (converted to knots).
- **Graceful Rate-Limit Handling:** Cache successful responses in `st.session_state` with a UTC timestamp. If OpenSky returns HTTP `429` (rate limit) or times out, display a clean non-blocking status badge (`OpenSky Rate-Limited — Showing Cached/Simulated Tracks`) rather than crashing.
- **Airspace Deconfliction Classification:**
  - Compute each live aircraft's horizontal distance from the stadium center in nautical miles (`NM`).
  - Flag tracks inside `5.0 NM` (and below `5,000 ft`) as **TFR Proximity Advisory (Amber)** and tracks inside `3.0 NM` below `3,000 ft AGL` as **TFR Breach Alert (Crimson)**—critical for modeling approaches into Teterboro (`KTEB`) and Newark (`KEWR`) near MetLife.
- Render live aircraft on the `pydeck` map using `ScatterplotLayer` + `TextLayer` displaying `Callsign + Altitude (ft)`.

---

## 2. Rogue UAS Intrusion & Blind-Spot Penetration Simulator
Add a **"ROGUE UAS THREAT SIMULATOR"** panel in the sidebar to test sensor coverage against low-altitude drone threats:
- **Controls:**
  - Toggle: `Enable Rogue UAS Simulation`
  - Slider: `Approach Bearing (0° – 359°)` (direction from which the drone approaches the stadium)
  - Slider: `Drone Flight Altitude (10m – 120m AGL)`
  - Slider: `Inbound Track Progress (0% = 5.0 NM Perimeter → 100% = Stadium Center)`
- **Spatial Detection & Masking Logic (UTM Meters):**
  - Compute the drone's exact `(x, y)` point along the straight-line inbound vector from `5.0 NM` (`9,260 m`) to `(0, 0)` at the selected `Inbound Track Progress %`.
  - Evaluate the drone's 3D position against the active C-UAS sensor network and building footprints:
    1. **TFR Zone Check:** Is distance to stadium `<= 3.0 NM` (`5,556 m`)?
    2. **Sensor Detection Check:** Is the drone within at least one active sensor's max range **AND** is the straight line-of-sight segment from that sensor to the drone unobstructed by any building taller than `max(sensor_height_agl, drone_altitude_agl)`?
- **Visual Map & Tactical Alert Banner:**
  - Render the planned **Drone Flight Path** (`PathLayer`, dashed/glowing orange) and the **Current Drone Position** (`ScatterplotLayer` + `TextLayer` label).
  - Display a prominent **Tactical Threat Status Banner** directly above the 3D map showing:
    - `OUTSIDE TFR (>3.0 NM)` vs. `ACTIVE TFR VIOLATION (<3.0 NM)`
    - `SENSOR STATUS: DETECTED & TRACKING` (green/cyan) vs. `SENSOR STATUS: OCCLUDED IN BUILDING BLIND SPOT` (crimson alert) — proving operationally why perimeter blind spots matter.

---

## 3. Updated Tactical HUD KPI Bar
Add one live telemetry metric to the top KPI row:
- **LIVE AIRCRAFT (5 NM RING):** Count of active transponder tracks inside the 5.0 NM early-warning buffer (with count of any TFR proximity advisories).

---

## 4. Tooltip & Hover Label Fix
- Fix the `pydeck` hover tooltip so it displays actual numeric values instead of raw `{properties.height_m}` template strings:
  - Pass building data with top-level attributes (or use a `PolygonLayer` for 3D buildings) and set `tooltip={"text": "Structure Height: {height_m} m"}` so hovering over any extruded structure displays its real height in meters.