# FINAL_POLISH_AND_DEPLOY.md — Final Map Visual Polish & GitHub Deployment Check

## Current System Status (Completed)
- **Milestone 1–2:** 3D OpenStreetMap building extrusions (with 40 m stadium/complex height floors), UTM Zone 18N metric buffer geometry, 3.0 NM & 5.0 NM FAA TFR rings, and OpenSky live/simulated ADS-B air traffic + Rogue UAS threat detection.
- **Milestone 3:** Click-to-place sensor siting (clickable rooftops + ground grid), Tactical Preset laydown, and 360° line-of-sight raycasting with building blind-spot shadows.
- **Milestone 4:** Two-tab workspace (`3D Tactical Map` and `Site Survey Brief`) with a 7-section self-contained HTML Tactical Site Survey Brief generator and `.html` export.

---

## Remaining Visual Polish Tasks (Single Pass in `app.py`)
1. **Remove Yellow Screen Blanket (Hollow TFR Rings Only):**
   - On the FAA TFR layer (`3.0 NM` red ring and `5.0 NM` amber ring), set `filled=False`, `stroked=True`, `line_width_min_pixels=2`, and `pickable=False` so only thin ring outlines render and the dark street basemap (`map_provider="carto"`, `map_style="dark"`) remains completely unobstructed.
2. **Prevent Grey Map Freeze on Multi-Sensor Presets:**
   - Simplify sensor coverage and blind-spot geometries (`.simplify(5.0, preserve_topology=True)` and `.explode(index_parts=False)`), drop empty geometries, and render them as lightweight `PolygonLayer` layers with translucent fills (`Sensor Coverage`: `[16, 185, 129, 55]`, `Blind-Spot Shadows`: `[239, 68, 68, 85]`) and `pickable=False`.
   - Keep the invisible clickable ground grid at index `0` (bottom of the `layers` list) with `stroked=False`, `filled=True`, `get_fill_color=[0, 0, 0, 0]`, and `auto_highlight=False`.
3. **Fix Building Hover Tooltip Formatting:**
   - Pre-compute a string column `hover_label` on every layer (`f"Structure Height: {height_m:.1f} m"` on `3D Buildings`; `"Click to set sensor coordinates"` on the ground grid) and set `tooltip={"text": "{hover_label}"}` on `pdk.Deck` so raw `{height_m}` text never displays.

---

## Portfolio & GitHub Readiness Check
- Confirm `requirements.txt`, `.gitignore` (keeping `data/*.geojson` tracked for offline reliability), and a clear executive `README.md` exist in the project root.
- Verify `streamlit run app.py` launches cleanly inside `.venv` with zero console or widget errors.