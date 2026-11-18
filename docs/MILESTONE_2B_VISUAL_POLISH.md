# MILESTONE_2B_VISUAL_POLISH.md — 3D Camera Perspective, High-Contrast Buildings & Realistic Radar Masking

## Objective
Refine the `pydeck` 3D visualization, layer visibility controls, and preset sensor heights in `app.py` so 3D buildings, sensor pylons, and building-masked blind spots are immediately clear and realistic on screen.

---

## 1. Default 3D Angled Tactical Camera
- Update the initial `pdk.ViewState` when loading or switching stadiums so the user immediately sees 3D depth instead of a flat 2D top-down view:
  - `zoom`: `13.4` (focused cleanly on the stadium, surrounding structures, and inner TFR)
  - `pitch`: `52` (angled 3D perspective)
  - `bearing`: `-25`

## 2. High-Contrast 3D Layer Ordering & TFR Perimeter Styling
- **Render Order (Bottom to Top):**
  1. Base dark map style
  2. `Active Sensor Coverage` (translucent cyan `[0, 229, 255, 45]` for Ku-Band Radar; translucent emerald `[16, 185, 129, 35]` for RF Detector)
  3. `Masked Blind-Spot Shadows` (high-visibility crimson `[239, 68, 68, 120]`)
  4. `FAA TFR Perimeter Rings` — Render the `3.0 NM` core TFR and `5.0 NM` early-warning ring using `PathLayer` or hollow stroked `PolygonLayer` (`filled=False`, `stroked=True`, `lineWidthMinPixels=3`) instead of dark solid fills so they never obscure buildings or coverage zones underneath.
  5. `3D Extruded Buildings` (`PolygonLayer`, `extruded=True`, `wireframe=True`) — Render buildings on top of ground coverage layers in crisp light-slate `[203, 213, 225, 220]` (and highlight structures taller than `20m` in warm amber `[251, 191, 36, 235]`) with dark line edges so the stadium bowl and surrounding complexes stand out sharply in 3D.
  6. `3D Sensor Pylons` (`ColumnLayer`, `radius=22`, bright white/cyan `[255, 255, 255, 255]`) rising to each sensor's `height_agl`.

## 3. Sidebar Map Layer Toggles
- Add a **"MAP LAYERS"** section in the sidebar with 4 checkboxes (all checked `True` by default) so the user can isolate layers during demos:
  - `Show 3D Buildings`
  - `Show Sensor Coverage`
  - `Show Blind-Spot Shadows`
  - `Show FAA TFR Rings`

## 4. Realistic Perimeter Mast Heights (So Buildings Cast Real Blind Spots)
- Update the **`LOAD TACTICAL PRESET`** heights so low-altitude ground-based C-UAS units experience realistic urban masking from the stadium bowl and mega-structures:
  - **MetLife Stadium Preset:**
    1. *MetLife Upper Rim North* (`40.8146, -74.0745`, Height: `42.0m AGL`, Type: `Ku-Band Tactical Radar`)
    2. *Lot G Mobile Radar Trailer* (`40.8112, -74.0785`, Height: `14.0m AGL`, Type: `Ku-Band Tactical Radar`) — *positioned so MetLife Stadium and American Dream cast visible red radar shadows*
    3. *East Rutherford Perimeter Mast* (`40.8215, -74.0812`, Height: `18.0m AGL`, Type: `RF Drone Telemetry Detector`)
  - **SHI Stadium (Rutgers) Preset:**
    1. *SHI Stadium Press Box Roof* (`40.5138, -74.4659`, Height: `32.0m AGL`, Type: `Ku-Band Tactical Radar`)
    2. *Scarlet Lot Mobile C-UAS Mast* (`40.5115, -74.4622`, Height: `12.0m AGL`, Type: `Ku-Band Tactical Radar`)
    3. *Busch Campus Perimeter Sensor* (`40.5218, -74.4615`, Height: `16.0m AGL`, Type: `RF Drone Telemetry Detector`)