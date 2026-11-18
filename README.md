# Stadium Airspace & Counter-UAS Sensor Planner

An interactive planning prototype for stadium-event airspace deconfliction and counter-UAS sensor siting. It combines local-UTM TFR geometry, cached 3D OpenStreetMap buildings, line-of-sight masking, live ADS-B traffic, and a rogue-UAS scenario simulator. The app also generates a self-contained, printable Tactical Site Survey Brief.

## Operational Problem

Stadium event restrictions coexist with nearby controlled airspace, dense buildings, and uneven sensor sightlines. This prototype provides a shared 3D planning view for comparing sensor locations, estimating building-masked coverage, reviewing nearby transponder traffic, and exporting a concise site survey. It is an academic and planning simulation, not operational flight guidance or authorization.

## System Architecture

1. **Coordinate geometry:** stadium-centered buffers and sensor analyses are computed in local UTM meters and displayed in WGS84.
2. **Urban terrain:** OSM building footprints are height-enriched and persisted as venue-specific GeoJSON in `data/` for offline use.
3. **Coverage and masking:** 120 radial rays per sensor estimate active coverage and building-shadow blind spots.
4. **Airspace scenarios:** OpenSky ADS-B states are classified around the venue; the rogue-UAS simulator checks sensor range and building-interrupted line of sight.

## Product Development

Engineering milestones and acceptance requirements are documented in [`docs/`](docs/): initial stadium airspace planning, C-UAS line-of-sight analysis, visual stabilization, click-to-place sensor siting, and automated survey reporting.

## Quickstart

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

The app is also suitable for Streamlit Community Cloud. Keep the prebuilt `data/*.geojson` venue caches in the repository to avoid depending on live Overpass requests at startup.

## Data Attribution

- Building and terrain context: © OpenStreetMap contributors.
- Air-traffic states: OpenSky Network.
- Basemap: CARTO Dark Matter.

Academic / planning simulation only. Verify current FAA notices, charts, and operational procedures independently.