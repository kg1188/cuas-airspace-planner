# MILESTONE_2C_HOTFIX.md — Fix Opaque Polygon Overlay, Dark Basemap & Stadium Heights

## Objective
Fix three visual and geometry issues in `app.py` so the dark map, 3D extruded buildings, transparent sensor cones, and building-masked blind spots render cleanly without opaque white overlays or polygon triangulation stripes.

---

## 1. Force Free Dark Tactical Basemap
- Set `map_style="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"` inside `pdk.Deck(...)` so the map always renders in high-contrast dark tactical mode without requiring a Mapbox API key.

## 2. Switch Overlays to `GeoJsonLayer` with True Transparency (Fixes White Oval & Stripes)
- Replace `PolygonLayer` for sensor coverage, blind-spot shadows, and TFR rings with `pdk.Layer("GeoJsonLayer", ...)` using GeoPandas `.to_json()` (`json.loads(gdf.to_json())`) so `MultiPolygon` shapes and polygons with interior holes triangulate cleanly without striped artifacts.
- Enforce strict low-opacity RGBA colors (`0–255` scale) so the map and buildings remain clearly visible underneath:
  - **Ku-Band Radar Coverage:** `get_fill_color=[0, 229, 255, 38]`, `get_line_color=[0, 229, 255, 180]`, `line_width_min_pixels=2`
  - **RF Telemetry Coverage:** `get_fill_color=[16, 185, 129, 28]`, `get_line_color=[16, 185, 129, 160]`, `line_width_min_pixels=2`
  - **Masked Blind-Spot Shadows:** `get_fill_color=[239, 68, 68, 110]`, `get_line_color=[239, 68, 68, 200]`, `line_width_min_pixels=1`
  - **FAA TFR Perimeter Rings (3 NM & 5 NM):** `filled=False`, `stroked=True`, `get_line_color=[245, 158, 11, 220]`, `line_width_min_pixels=3`

## 3. Realistic Landmark Height Enrichment (Fixes `0.00 km²` Blind Spots)
- OpenStreetMap frequently omits `height` tags on stadiums and mega-complexes. When normalizing building heights in the building loader, apply a footprint-area fallback if `height` and `building:levels` are missing:
  - Footprints with area `> 15,000 m²` (e.g., MetLife Stadium, American Dream, SHI Stadium): set `height_m = 40.0`
  - Footprints with area `> 3,500 m²` (commercial complexes, campus halls): set `height_m = 20.0`
  - All other untagged structures: set `height_m = 10.0`
- Lower the mobile trailer preset sensor heights to **`8.0m AGL`** (standard mast-mounted mobile C-UAS trailer) so the `40m` stadium bowl and surrounding complexes cast clear, realistic red radar shadows.
- Re-save the enriched GeoJSON cache or apply this height logic on load so existing cached files in `data/` immediately get updated heights.

## 4. Render 3D Buildings & Sensor Towers Above Ground Overlays
- Ensure `3D Buildings` (`GeoJsonLayer` or `PolygonLayer` with `extruded=True`, `get_elevation="height_m"`, `get_fill_color=[226, 232, 240, 230]`, `get_line_color=[15, 23, 42, 255]`) and `3D Sensor Pylons` (`ColumnLayer`, `radius=25`, `get_elevation="height_agl"`, `get_fill_color=[255, 255, 255, 255]`) are appended **after** the coverage and shadow layers so they stand out sharply in 3D.
- Wire the sidebar **"MAP LAYERS"** checkboxes (`Show 3D Buildings`, `Show Sensor Coverage`, `Show Blind-Spot Shadows`, `Show FAA TFR Rings`) directly to the `layers` list so toggling any checkbox immediately shows/hides that layer.