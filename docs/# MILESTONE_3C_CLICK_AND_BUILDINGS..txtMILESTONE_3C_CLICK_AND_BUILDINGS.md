# MILESTONE_3C_CLICK_AND_BUILDINGS.md — Restore 3D Building Extrusion & Add Click-to-Place Sensor Siting

## Objective
Restore full 3D vertical building extrusion in `app.py` (preserving the `40 m` stadium/complex height rules and working hover tooltips) and implement interactive **Click-to-Place Sensor Siting** so the user can click directly on any rooftop or parking lot on the 3D map to drop a target pin, auto-fill sensor coordinates, or immediately deploy a C-UAS sensor.

---

## 1. Restore 3D Vertical Building Extrusion
- In the `3D Buildings` `pydeck` layer (`PolygonLayer` or `GeoJsonLayer`):
  - Explicitly set `extruded=True`, `wireframe=True`, `elevation_scale=1`, and `get_elevation="height_m"` so all structures extrude vertically in 3D space.
  - Ensure large commercial/stadium footprints (`area_m2 >= 15000`, including MetLife Stadium and American Dream) retain their `40.0` meter default height when OSM height tags are missing.
  - Keep `height_m` as a top-level attribute and preserve the working hover tooltip (`Structure Height: {height_m} m`).
  - Maintain Milestone 3B layer safety checks so toggling layers on/off never triggers a grey screen or drops the dark basemap (`https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json`).

---

## 2. Click-to-Place Sensor Coordinates (Rooftops & Ground Grid)
- **Clickable Surface Coverage:**
  - Set `pickable=True` on the `3D Buildings` layer and attach explicit top-level `click_lat` and `click_lon` attributes (the polygon's WGS84 centroid) to every building feature so clicking a rooftop captures its exact coordinates.
  - Generate a lightweight, fine-grained **Clickable Ground Grid** (`PolygonLayer`, ~`35 m × 35 m` cells covering a `1.8 km` radius around the active stadium) rendered at ground level (`elevation=0`, `extruded=False`, nearly invisible fill `[255, 255, 255, 2]`, `pickable=True`, `auto_highlight=True`, `highlight_color=[56, 189, 248, 60]`), with explicit `click_lat` and `click_lon` properties on every cell. This allows clicking any open parking lot, roadway, or perimeter field.
- **Streamlit Selection Handling (`on_select="rerun"`):**
  - Render `st.pydeck_chart(deck, on_select="rerun", selection_mode="single-object", key="tactical_map")`.
  - When a user clicks either the `3D Buildings` layer or the `Clickable Ground Grid` layer, extract `click_lat` and `click_lon` from the selected object and save them into `st.session_state["selected_lat"]` and `st.session_state["selected_lon"]`.

---

## 3. Target Drop Pin & Sidebar Sensor Controls (`2. C-UAS SENSOR NETWORK`)
- **Visual Target Pin on Map:**
  - Whenever `selected_lat` and `selected_lon` are set in `st.session_state`, render a high-visibility **Target Preview Pin** (`ScatterplotLayer` in bright amber/cyan `[250, 204, 21, 240]` with a white outline) at that coordinate so the user visually sees where their click landed.
- **Auto-Populated Sidebar Inputs & Quick-Drop Toggle:**
  - Inside `2. C-UAS SENSOR NETWORK`, bind the `Latitude` and `Longitude` `st.number_input` widgets directly to `st.session_state["selected_lat"]` and `st.session_state["selected_lon"]` (defaulting to the stadium perimeter) so clicking the map immediately updates the coordinate fields without manual typing.
  - Add an **`Auto-Deploy Sensor on Map Click`** checkbox (`value=False` by default):
    - When **unchecked (Standard Mode)**: Clicking the map moves the yellow target pin and updates the Lat/Lon boxes; clicking **`Deploy Sensor`** adds the sensor at that pin.
    - When **checked (Quick-Drop Mode)**: Every new click on the map immediately appends a sensor at `(click_lat, click_lon)` using the currently selected Sensor Type (`Ku-Band Radar` or `RF Telemetry Detector`), Mast Height (`m AGL`), and auto-incremented name (e.g., `Custom Sensor #1`), recalculating 360° line-of-sight coverage and blind spots immediately.