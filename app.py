"""Milestone 1 stadium airspace and counter-UAS planning dashboard."""

from __future__ import annotations

import math
import re
from html import escape
from pathlib import Path
from typing import Any
from uuid import uuid4
from datetime import datetime, timezone

import geopandas as gpd
import pydeck as pdk
import requests
import streamlit as st
from pyproj import Geod, Transformer
from shapely import transform as transform_geometry
from shapely.geometry import LineString, Point, Polygon, mapping, shape
from shapely.ops import nearest_points, unary_union
from shapely.strtree import STRtree


NM_TO_METERS = 1_852
CORE_RADIUS_METERS = 3 * NM_TO_METERS
BUFFER_RADIUS_METERS = 5 * NM_TO_METERS
TFR_CEILING_METERS = 914.4
BUILDING_QUERY_METERS = 1_800
DATA_DIR = Path(__file__).parent / "data"
RAY_COUNT = 120
OPENSKY_URL = "https://opensky-network.org/api/states/all"
GEOD = Geod(ellps="WGS84")
SENSOR_TYPES: dict[str, dict[str, Any]] = {
    "Ku-Band Tactical Radar": {
        "max_range_m": 3_500,
        "active_color": [0, 229, 255, 45],
        "beacon_color": [255, 255, 255, 255],
    },
    "RF Drone Telemetry Detector": {
        "max_range_m": 5_500,
        "active_color": [16, 185, 129, 35],
        "beacon_color": [255, 255, 255, 255],
    },
}

VENUES: dict[str, dict[str, Any]] = {
    "MetLife Stadium (East Rutherford, NJ)": {
        "latitude": 40.8135,
        "longitude": -74.0745,
        "capacity": 82_500,
        "airspace": "KTEB (Teterboro - 2.2 NM NE), KEWR (Newark - 8.5 NM S), NYC Class B",
        "slug": "metlife",
    },
    "SHI Stadium - Rutgers University (Piscataway, NJ)": {
        "latitude": 40.5137,
        "longitude": -74.4648,
        "capacity": 52_454,
        "airspace": "Raritan River Valley Terrain Drop, KSMQ (Somerset - 9 NM NW)",
        "slug": "shi_stadium",
    },
}

TACTICAL_PRESETS: dict[str, list[dict[str, Any]]] = {
    "metlife": [
        {"id": "metlife-north", "name": "MetLife Upper Rim North", "latitude": 40.8146, "longitude": -74.0745, "height_agl": 42.0, "sensor_type": "Ku-Band Tactical Radar"},
        {"id": "metlife-lot-g", "name": "Lot G Mobile Radar Trailer", "latitude": 40.8112, "longitude": -74.0785, "height_agl": 8.0, "sensor_type": "Ku-Band Tactical Radar"},
        {"id": "metlife-perimeter-mast", "name": "East Rutherford Perimeter Mast", "latitude": 40.8215, "longitude": -74.0812, "height_agl": 18.0, "sensor_type": "RF Drone Telemetry Detector"},
    ],
    "shi_stadium": [
        {"id": "shi-press-box", "name": "SHI Stadium Press Box Roof", "latitude": 40.5138, "longitude": -74.4659, "height_agl": 32.0, "sensor_type": "Ku-Band Tactical Radar"},
        {"id": "shi-scarlet-lot", "name": "Scarlet Lot Mobile C-UAS Mast", "latitude": 40.5115, "longitude": -74.4622, "height_agl": 8.0, "sensor_type": "Ku-Band Tactical Radar"},
        {"id": "shi-busch-perimeter", "name": "Busch Campus Perimeter Sensor", "latitude": 40.5218, "longitude": -74.4615, "height_agl": 16.0, "sensor_type": "RF Drone Telemetry Detector"},
    ],
}


def classify_aircraft(track: dict[str, Any], latitude: float, longitude: float) -> dict[str, Any]:
    altitude_ft = track.get("altitude_ft")
    _, _, distance_m = GEOD.inv(
        longitude,
        latitude,
        float(track["longitude"]),
        float(track["latitude"]),
    )
    distance_nm = distance_m / NM_TO_METERS
    alert = "normal"
    if altitude_ft is not None:
        if distance_nm <= 3.0 and altitude_ft < 3_000:
            alert = "breach"
        elif distance_nm <= 5.0 and altitude_ft < 5_000:
            alert = "advisory"
    return {**track, "distance_nm": distance_nm, "alert": alert}


def sanitize_aircraft_tracks(
    raw_tracks: list[dict[str, Any]] | None,
    latitude: float,
    longitude: float,
) -> list[dict[str, Any]]:
    """Sanitize all live/sample aircraft rows into pure Python dicts with safe defaults."""
    clean_tracks: list[dict[str, Any]] = []
    for raw in raw_tracks or []:
        if not isinstance(raw, dict):
            continue
        raw_lat = raw.get("latitude") if raw.get("latitude") is not None else raw.get("lat")
        raw_lon = raw.get("longitude") if raw.get("longitude") is not None else raw.get("lon")
        if raw_lat is None or raw_lon is None:
            continue
        try:
            lat = float(raw_lat)
            lon = float(raw_lon)
            if math.isnan(lat) or math.isnan(lon) or not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
                continue
        except (TypeError, ValueError):
            continue

        raw_alt = raw.get("altitude_ft")
        try:
            alt_ft = float(raw_alt) if raw_alt is not None and not math.isnan(float(raw_alt)) else 0.0
        except (TypeError, ValueError):
            alt_ft = 0.0

        raw_spd = raw.get("velocity_knots") if raw.get("velocity_knots") is not None else raw.get("speed")
        try:
            spd_kts = float(raw_spd) if raw_spd is not None and not math.isnan(float(raw_spd)) else 0.0
        except (TypeError, ValueError):
            spd_kts = 0.0

        raw_hdg = raw.get("heading") if raw.get("heading") is not None else raw.get("true_track")
        try:
            hdg = float(raw_hdg) if raw_hdg is not None and not math.isnan(float(raw_hdg)) else 0.0
        except (TypeError, ValueError):
            hdg = 0.0

        callsign = str(raw.get("callsign") or raw.get("icao24") or "UNKNOWN").strip() or "UNKNOWN"
        icao24 = str(raw.get("icao24") or "").strip().lower()

        _, _, distance_m = GEOD.inv(longitude, latitude, lon, lat)
        distance_nm = distance_m / NM_TO_METERS

        alert = "normal"
        if distance_nm <= 3.0 and alt_ft < 3_000:
            alert = "breach"
        elif distance_nm <= 5.0 and alt_ft < 5_000:
            alert = "advisory"

        altitude_str = f"{alt_ft:.0f} ft" if alt_ft > 0 else "ALT N/A"
        speed_str = f"{spd_kts:.0f} kts" if spd_kts > 0 else "SPD N/A"
        label = f"{callsign} / {altitude_str}"
        hover_label = f"Aircraft: {callsign} · {altitude_str} · {speed_str} · {distance_nm:.1f} NM"

        clean_tracks.append(
            {
                "icao24": icao24,
                "callsign": callsign,
                "lat": lat,
                "lon": lon,
                "latitude": lat,
                "longitude": lon,
                "altitude_ft": alt_ft,
                "velocity_knots": spd_kts,
                "heading": hdg,
                "distance_nm": distance_nm,
                "alert": alert,
                "label": label,
                "hover_label": hover_label,
                "simulated": bool(raw.get("simulated", False)),
                "sample": bool(raw.get("sample", False)),
            }
        )
    return clean_tracks


def simulated_aircraft(latitude: float, longitude: float) -> list[dict[str, Any]]:
    """Produce clearly marked fallback tracks for a rate-limited demo."""
    to_local, to_wgs84 = local_transformers(latitude, longitude)
    center_x, center_y = to_local.transform(longitude, latitude)
    tracks = []
    for callsign, bearing, distance_nm, altitude_ft, velocity_knots in [
        ("SIM-ADV", 32.0, 4.2, 4_200.0, 210.0),
        ("SIM-BREACH", 190.0, 2.3, 2_600.0, 165.0),
    ]:
        distance_m = distance_nm * NM_TO_METERS
        angle = math.radians(bearing)
        x = center_x + distance_m * math.sin(angle)
        y = center_y + distance_m * math.cos(angle)
        sim_lon, sim_lat = to_wgs84.transform(x, y)
        tracks.append(
            classify_aircraft(
                {
                    "icao24": callsign.lower(),
                    "callsign": callsign,
                    "longitude": sim_lon,
                    "latitude": sim_lat,
                    "altitude_ft": altitude_ft,
                    "velocity_knots": velocity_knots,
                    "simulated": True,
                },
                latitude,
                longitude,
            )
        )
    return tracks


def parse_opensky_states(states: list[list[Any]] | None, latitude: float, longitude: float) -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    for state in states or []:
        if len(state) <= 9 or state[8] is not False or state[5] is None or state[6] is None:
            continue
        try:
            altitude_m = float(state[7]) if state[7] is not None else None
            velocity_mps = float(state[9]) if state[9] is not None else None
            track = {
                "icao24": str(state[0] or "").strip(),
                "callsign": str(state[1] or state[0] or "UNKNOWN").strip() or "UNKNOWN",
                "longitude": float(state[5]),
                "latitude": float(state[6]),
                "altitude_ft": altitude_m * 3.28084 if altitude_m is not None else None,
                "velocity_knots": velocity_mps * 1.94384 if velocity_mps is not None else None,
                "simulated": False,
            }
        except (TypeError, ValueError):
            continue
        tracks.append(classify_aircraft(track, latitude, longitude))
    return tracks


def fetch_live_traffic(latitude: float, longitude: float) -> tuple[list[dict[str, Any]], str, str | None]:
    """Fetch OpenSky states, caching success and returning a non-blocking fallback on errors."""
    params = {
        "lamin": latitude - 0.25,
        "lomin": longitude - 0.25,
        "lamax": latitude + 0.25,
        "lomax": longitude + 0.25,
    }
    cache_key = f"{latitude:.4f}:{longitude:.4f}"
    caches = st.session_state.setdefault("opensky_caches", {})
    cache = caches.get(cache_key)
    try:
        response = requests.get(OPENSKY_URL, params=params, timeout=10)
        if response.status_code == 429:
            raise requests.exceptions.HTTPError("OpenSky returned HTTP 429")
        response.raise_for_status()
        tracks = parse_opensky_states(response.json().get("states"), latitude, longitude)
        fetched_at = datetime.now(timezone.utc).isoformat()
        caches[cache_key] = {"tracks": tracks, "fetched_at": fetched_at}
        return tracks, f"OpenSky updated {fetched_at}", "live"
    except requests.exceptions.Timeout:
        reason = "OpenSky timed out"
    except requests.exceptions.HTTPError as error:
        if getattr(error.response, "status_code", None) == 429 or "429" in str(error):
            reason = "OpenSky Rate-Limited"
        else:
            reason = f"OpenSky HTTP error: {error}"
    except requests.exceptions.RequestException as error:
        reason = f"OpenSky unavailable: {error}"
    except (ValueError, AttributeError) as error:
        reason = f"OpenSky response error: {error}"

    if cache:
        return cache["tracks"], f"{reason} — Showing Cached/Simulated Tracks", "cached"
    return simulated_aircraft(latitude, longitude), f"{reason} — Showing Cached/Simulated Tracks", "simulated"


def sample_approach_traffic(latitude: float, longitude: float) -> list[dict[str, Any]]:
    """Return six clearly identified demo tracks near the KTEB/KEWR approaches."""
    samples = [
        ("NKS624", "a1b2c3", 40.8520, -74.0750, 2_800.0, 185.0),
        ("JBU418", "b2c3d4", 40.8440, -74.0440, 4_600.0, 205.0),
        ("UAL1837", "c3d4e5", 40.8720, -74.1000, 4_200.0, 220.0),
        ("EWR902", "d4e5f6", 40.7510, -74.1400, 2_400.0, 175.0),
        ("NKS518", "e5f607", 40.7270, -74.1900, 6_500.0, 245.0),
        ("N814KT", "f60718", 40.8620, -74.1600, 5_800.0, 155.0),
    ]
    return [
        classify_aircraft(
            {
                "callsign": callsign,
                "icao24": icao24,
                "latitude": track_latitude,
                "longitude": track_longitude,
                "altitude_ft": altitude_ft,
                "velocity_knots": velocity_knots,
                "simulated": True,
                "sample": True,
            },
            latitude,
            longitude,
        )
        for callsign, icao24, track_latitude, track_longitude, altitude_ft, velocity_knots in samples
    ]


def cached_building_count(cache_path: Path) -> int | None:
    if not cache_path.exists():
        return 0
    try:
        return len(gpd.read_file(cache_path))
    except Exception:
        return None


def normalize_polygon_features(
    features: list[dict[str, Any]] | None,
    latitude: float,
    longitude: float,
) -> list[dict[str, Any]]:
    """Project, repair, simplify, and explode overlays into valid polygons for deck.gl."""
    if not features:
        return []
    frame = gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
    if frame.empty:
        return []
    frame = frame[~frame.geometry.is_empty].copy()
    frame = frame[frame.geometry.notna()].copy()
    if frame.empty:
        return []
    local_crs = gpd.GeoSeries([Point(longitude, latitude)], crs="EPSG:4326").estimate_utm_crs()
    frame = frame.to_crs(local_crs)
    frame.geometry = frame.geometry.make_valid().simplify(5.0, preserve_topology=True)
    frame = frame.explode(index_parts=False)
    frame = frame[~frame.geometry.is_empty].copy()
    frame = frame[frame.geometry.notna()].copy()
    frame = frame[
        (frame.geometry.geom_type == "Polygon")
        & frame.geometry.is_valid
    ]
    if frame.empty:
        return []
    frame = frame.to_crs("EPSG:4326")
    normalized = []
    for _, row in frame.iterrows():
        properties = row.drop(labels=["geometry"]).to_dict()
        label = str(properties.get("name") or "Sensor coverage")
        normalized.append(
            {
                "polygon": mapping(row.geometry)["coordinates"],
                "hover_label": label,
            }
        )
    return normalized


def selected_click_coordinates(selection_state: Any) -> tuple[float, float] | None:
    if not selection_state:
        return None
    selection = selection_state.get("selection", selection_state)
    if not isinstance(selection, dict):
        return None
    objects = selection.get("objects", {})
    if isinstance(objects, dict):
        candidates = [
            item
            for layer_objects in objects.values()
            for item in (layer_objects if isinstance(layer_objects, list) else [layer_objects])
        ]
    elif isinstance(objects, list):
        candidates = objects
    else:
        candidates = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        click_lat = candidate.get("click_lat")
        click_lon = candidate.get("click_lon")
        if click_lat is None or click_lon is None:
            properties = candidate.get("properties", {})
            click_lat = properties.get("click_lat")
            click_lon = properties.get("click_lon")
        try:
            click_lat, click_lon = float(click_lat), float(click_lon)
        except (TypeError, ValueError):
            continue
        if -90 <= click_lat <= 90 and -180 <= click_lon <= 180:
            return click_lat, click_lon
    return None


@st.cache_data(show_spinner=False)
def make_clickable_ground_grid(
    latitude: float,
    longitude: float,
    radius_m: float = BUILDING_QUERY_METERS,
    cell_size_m: float = 120.0,
) -> list[dict[str, Any]]:
    """Create pickable square ground cells with WGS84 target coordinates."""
    to_local, to_wgs84 = local_transformers(latitude, longitude)
    center_x, center_y = to_local.transform(longitude, latitude)
    cell_count = math.ceil(radius_m / cell_size_m)
    half_cell = cell_size_m / 2
    cells: list[dict[str, Any]] = []
    for row_index in range(-cell_count, cell_count + 1):
        center_y_cell = center_y + row_index * cell_size_m
        for column_index in range(-cell_count, cell_count + 1):
            center_x_cell = center_x + column_index * cell_size_m
            if math.hypot(center_x_cell - center_x, center_y_cell - center_y) > radius_m:
                continue
            cell_local = Polygon(
                [
                    (center_x_cell - half_cell, center_y_cell - half_cell),
                    (center_x_cell + half_cell, center_y_cell - half_cell),
                    (center_x_cell + half_cell, center_y_cell + half_cell),
                    (center_x_cell - half_cell, center_y_cell + half_cell),
                ]
            )
            cell_wgs84 = transform_geometry(cell_local, to_wgs84.transform, interleaved=False)
            click_lon, click_lat = to_wgs84.transform(center_x_cell, center_y_cell)
            cells.append(
                {
                    "polygon": mapping(cell_wgs84)["coordinates"],
                    "click_lat": click_lat,
                    "click_lon": click_lon,
                    "hover_label": "Click to set sensor coordinates",
                }
            )
    return cells


@st.cache_data(show_spinner=False)
def prep_building_spatial_index(
    latitude: float,
    longitude: float,
    building_features: list[dict[str, Any]],
) -> tuple[list[Any], list[float]]:
    """Convert building GeoJSON features to local UTM geometries and height list with caching."""
    if not building_features:
        return [], []
    center = gpd.GeoSeries([Point(longitude, latitude)], crs="EPSG:4326")
    local_crs = center.estimate_utm_crs()
    buildings_wgs84 = gpd.GeoDataFrame.from_features(building_features, crs="EPSG:4326")
    buildings_local = buildings_wgs84.to_crs(local_crs)
    return list(buildings_local.geometry), [float(h) for h in buildings_local["height_m"]]


def simulate_rogue_uas(
    latitude: float,
    longitude: float,
    approach_bearing: int,
    drone_altitude_m: float,
    progress_percent: int,
    sensors: list[dict[str, Any]],
    building_features: list[dict[str, Any]],
) -> dict[str, Any]:
    """Place an inbound drone in local UTM and test sensor range and building LOS."""
    to_local, to_wgs84 = local_transformers(latitude, longitude)
    center_x, center_y = to_local.transform(longitude, latitude)
    angle = math.radians(approach_bearing)
    start_x = center_x + BUFFER_RADIUS_METERS * math.sin(angle)
    start_y = center_y + BUFFER_RADIUS_METERS * math.cos(angle)
    remaining = 1.0 - progress_percent / 100
    drone_x = center_x + (start_x - center_x) * remaining
    drone_y = center_y + (start_y - center_y) * remaining
    drone_lon, drone_lat = to_wgs84.transform(drone_x, drone_y)
    start_lon, start_lat = to_wgs84.transform(start_x, start_y)

    dash_paths = []
    total_distance = BUFFER_RADIUS_METERS * progress_percent / 100
    dash_length = 140.0
    dash_stride = 260.0
    distance = 0.0
    while distance < total_distance:
        dash_end = min(distance + dash_length, total_distance)
        points = []
        for path_distance in (distance, dash_end):
            fraction = path_distance / BUFFER_RADIUS_METERS
            point_x = start_x + (center_x - start_x) * fraction
            point_y = start_y + (center_y - start_y) * fraction
            point_lon, point_lat = to_wgs84.transform(point_x, point_y)
            points.append([point_lon, point_lat])
        dash_paths.append({"path": points})
        distance += dash_stride

    drone_point = Point(drone_x, drone_y)
    sensors_in_range: list[str] = []
    occluded_by: list[str] = []
    detected_by: list[str] = []
    if building_features:
        building_geometries, building_heights = prep_building_spatial_index(
            latitude, longitude, building_features
        )
        tree = STRtree(building_geometries) if building_geometries else None
    else:
        building_geometries = []
        building_heights = []
        tree = None

    for sensor in sensors:
        sensor_type = sensor["sensor_type"]
        sensor_x, sensor_y = to_local.transform(
            float(sensor["longitude"]), float(sensor["latitude"])
        )
        sensor_point = Point(sensor_x, sensor_y)
        max_range = float(SENSOR_TYPES[sensor_type]["max_range_m"])
        if sensor_point.distance(drone_point) > max_range:
            continue
        sensors_in_range.append(sensor["name"])
        sight_line = LineString([(sensor_x, sensor_y), (drone_x, drone_y)])
        blocking_building = None
        if tree is not None:
            required_height = max(float(sensor["height_agl"]), drone_altitude_m)
            for candidate in tree.query(sight_line, predicate="intersects"):
                building_index = int(candidate)
                if float(building_heights[building_index]) <= required_height:
                    continue
                if sight_line.intersects(building_geometries[building_index]):
                    blocking_building = building_index
                    break
        if blocking_building is None:
            detected_by.append(sensor["name"])
        else:
            occluded_by.append(sensor["name"])

    distance_from_stadium_m = drone_point.distance(Point(center_x, center_y))
    inside_tfr = distance_from_stadium_m <= CORE_RADIUS_METERS
    if detected_by:
        sensor_status = f"DETECTED & TRACKING BY: {', '.join(detected_by)}"
        sensor_status_kind = "detected"
    elif occluded_by:
        sensor_status = "OCCLUDED IN BUILDING BLIND SPOT — UNDETECTED"
        sensor_status_kind = "occluded"
    elif not sensors:
        sensor_status = "NO ACTIVE SENSORS"
        sensor_status_kind = "inactive"
    else:
        sensor_status = "NOT DETECTED — OUTSIDE SENSOR RANGE"
        sensor_status_kind = "out_of_range"

    return {
        "latitude": drone_lat,
        "longitude": drone_lon,
        "approach_bearing": int(approach_bearing),
        "altitude_m": float(drone_altitude_m),
        "altitude_ft": float(drone_altitude_m) / 0.3048,
        "distance_nm": distance_from_stadium_m / NM_TO_METERS,
        "hover_label": f"Rogue UAS: {drone_altitude_m:.1f} m AGL / {distance_from_stadium_m / NM_TO_METERS:.2f} NM",
        "inside_tfr": inside_tfr,
        "tfr_status": "TFR VIOLATION (<3.0 NM)" if inside_tfr else "OUTSIDE TFR (>3.0 NM)",
        "sensor_status": sensor_status,
        "sensor_status_kind": sensor_status_kind,
        "detected_by": detected_by,
        "occluded_by": occluded_by,
        "in_range_sensors": sensors_in_range,
        "path": [[start_lon, start_lat], [longitude, latitude]],
        "dash_paths": dash_paths,
    }


def local_transformers(latitude: float, longitude: float) -> tuple[Transformer, Transformer]:
    """Choose the local UTM zone and return WGS84 <-> projected transformers."""
    center = gpd.GeoSeries([Point(longitude, latitude)], crs="EPSG:4326")
    utm_crs = center.estimate_utm_crs()
    return (
        Transformer.from_crs("EPSG:4326", utm_crs, always_xy=True),
        Transformer.from_crs(utm_crs, "EPSG:4326", always_xy=True),
    )


def make_tfr_geometries(latitude: float, longitude: float) -> tuple[Any, Any, Transformer]:
    """Build precise 3 NM and 5 NM buffers in local projected meters."""
    to_local, to_wgs84 = local_transformers(latitude, longitude)
    center_local = transform_geometry(Point(longitude, latitude), to_local.transform, interleaved=False)
    core_local = center_local.buffer(CORE_RADIUS_METERS, quad_segs=128)
    buffer_local = center_local.buffer(BUFFER_RADIUS_METERS, quad_segs=128)
    return (
        transform_geometry(core_local, to_wgs84.transform, interleaved=False),
        transform_geometry(buffer_local, to_wgs84.transform, interleaved=False),
        to_local,
    )


def parse_height_meters(height: Any, levels: Any) -> float:
    """Read OSM height tags, falling back to building levels and then 9 m."""
    if height is not None:
        match = re.search(r"\d+(?:\.\d+)?", str(height).replace(",", "."))
        if match:
            value = float(match.group())
            if re.search(r"\b(ft|feet|foot)\b|['′]", str(height), re.IGNORECASE):
                value *= 0.3048
            if value > 0:
                return value

    if levels is not None:
        match = re.search(r"\d+(?:\.\d+)?", str(levels).replace(",", "."))
        if match:
            value = float(match.group()) * 3.5
            if value > 0:
                return value
    return 9.0


def building_height_meters(height: Any, levels: Any, footprint_area_m2: float) -> float:
    has_height = height is not None and str(height).strip().lower() not in {"", "none", "nan"}
    has_levels = levels is not None and str(levels).strip().lower() not in {"", "none", "nan"}
    if has_height or has_levels:
        return parse_height_meters(height, levels)
    if footprint_area_m2 >= 15_000:
        return 40.0
    if footprint_area_m2 > 3_500:
        return 20.0
    return 10.0


def venue_cache_slug(venue_name: str) -> str:
    if venue_name in VENUES:
        return str(VENUES[venue_name]["slug"])
    slug = re.sub(r"[^a-z0-9]+", "_", venue_name.lower()).strip("_")
    return slug or "custom_venue"


def buildings_to_features(buildings: gpd.GeoDataFrame) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for _, row in buildings.iterrows():
        features.append(
            {
                "type": "Feature",
                "geometry": mapping(row.geometry),
                "properties": {"height_m": float(row.height_m)},
            }
        )
    return features


def get_sensor_network(venue_slug: str) -> list[dict[str, Any]]:
    networks = st.session_state.setdefault("sensor_networks", {})
    return networks.setdefault(venue_slug, [])


def clear_sensor_widget_state(venue_slug: str) -> None:
    prefixes = (f"sensor_{venue_slug}_", f"sensor_add_{venue_slug}_")
    for key in list(st.session_state):
        if key.startswith(prefixes):
            del st.session_state[key]


def make_custom_sensor(
    sensors: list[dict[str, Any]],
    latitude: float,
    longitude: float,
    height_agl: float,
    sensor_type: str,
) -> dict[str, Any]:
    existing_numbers = [
        int(match.group(1))
        for sensor in sensors
        if (match := re.fullmatch(r"Custom Sensor #(\d+)", str(sensor.get("name", ""))))
    ]
    next_number = max(existing_numbers, default=0) + 1
    return {
        "id": uuid4().hex,
        "name": f"Custom Sensor #{next_number}",
        "latitude": float(latitude),
        "longitude": float(longitude),
        "height_agl": float(height_agl),
        "sensor_type": sensor_type,
    }


def apply_tactical_map_selection(
    venue_slug: str,
    stadium_latitude: float,
    stadium_longitude: float,
) -> tuple[float, float]:
    targets = st.session_state.setdefault("selected_targets", {})
    st.session_state.setdefault(f"auto_deploy_{venue_slug}", False)
    if st.session_state.get("_active_map_venue") != venue_slug:
        target = targets.setdefault(
            venue_slug,
            {"latitude": float(stadium_latitude), "longitude": float(stadium_longitude)},
        )
        st.session_state["selected_lat"] = float(target["latitude"])
        st.session_state["selected_lon"] = float(target["longitude"])
        st.session_state["_active_map_venue"] = venue_slug
        existing_click = selected_click_coordinates(st.session_state.get("tactical_map"))
        if existing_click:
            st.session_state["_last_map_click"] = (
                venue_slug,
                round(existing_click[0], 5),
                round(existing_click[1], 5),
            )

    click = selected_click_coordinates(st.session_state.get("tactical_map"))
    if click:
        click_lat, click_lon = click
        rounded_signature = (venue_slug, round(click_lat, 5), round(click_lon, 5))
        if rounded_signature != st.session_state.get("_last_map_click"):
            st.session_state["selected_lat"] = click_lat
            st.session_state["selected_lon"] = click_lon
            targets[venue_slug] = {"latitude": click_lat, "longitude": click_lon}
            sensors = st.session_state.setdefault("sensor_networks", {}).setdefault(venue_slug, [])
            if st.session_state.get(f"auto_deploy_{venue_slug}", False):
                existing_coords = {
                    (round(float(s["latitude"]), 5), round(float(s["longitude"]), 5))
                    for s in sensors
                }
                target_coord = (round(click_lat, 5), round(click_lon, 5))
                if target_coord in existing_coords:
                    st.session_state[f"map_click_notice_{venue_slug}"] = (
                        f"Sensor already deployed at ({click_lat:.5f}, {click_lon:.5f})."
                    )
                elif len(sensors) < 5:
                    sensors.append(
                        make_custom_sensor(
                            sensors,
                            click_lat,
                            click_lon,
                            st.session_state.get(f"sensor_add_{venue_slug}_height", 10.0),
                            st.session_state.get(
                                f"sensor_add_{venue_slug}_type", "Ku-Band Tactical Radar"
                            ),
                        )
                    )
                    st.session_state[f"map_click_notice_{venue_slug}"] = "Sensor auto-deployed at map target."
                else:
                    st.session_state[f"map_click_notice_{venue_slug}"] = "Sensor limit reached; map target updated only."
            st.session_state["_last_map_click"] = rounded_signature

    return float(st.session_state["selected_lat"]), float(st.session_state["selected_lon"])


def render_sensor_controls(
    venue_slug: str,
    target_latitude: float,
    target_longitude: float,
) -> list[dict[str, Any]]:
    networks = st.session_state.setdefault("sensor_networks", {})
    sensors = networks.setdefault(venue_slug, [])

    preset_col, clear_col = st.columns(2)
    if preset_col.button("LOAD TACTICAL PRESET", key=f"preset_{venue_slug}", width="stretch"):
        clear_sensor_widget_state(venue_slug)
        networks[venue_slug] = [dict(sensor) for sensor in TACTICAL_PRESETS.get(venue_slug, [])]
        st.rerun()
    if clear_col.button("CLEAR ALL", key=f"clear_{venue_slug}", width="stretch"):
        clear_sensor_widget_state(venue_slug)
        networks[venue_slug] = []
        st.rerun()

    edited_sensors: list[dict[str, Any]] = []
    removed_ids: set[str] = set()
    for index, sensor in enumerate(sensors):
        sensor_id = str(sensor["id"])
        widget_prefix = f"sensor_{venue_slug}_{sensor_id}_"
        with st.expander(f"{sensor['name']} / {sensor['sensor_type']}", expanded=False):
            name = st.text_input("Name", value=sensor["name"], key=f"{widget_prefix}name")
            latitude = st.number_input(
                "Latitude", min_value=-90.0, max_value=90.0,
                value=float(sensor["latitude"]), format="%.6f", key=f"{widget_prefix}lat",
            )
            longitude = st.number_input(
                "Longitude", min_value=-180.0, max_value=180.0,
                value=float(sensor["longitude"]), format="%.6f", key=f"{widget_prefix}lon",
            )
            height_agl = st.number_input(
                "Mounting Height (m AGL)", min_value=0.0, max_value=500.0,
                value=float(sensor["height_agl"]), step=1.0, key=f"{widget_prefix}height",
            )
            sensor_type = st.selectbox(
                "Sensor Type", list(SENSOR_TYPES),
                index=list(SENSOR_TYPES).index(sensor["sensor_type"]), key=f"{widget_prefix}type",
            )
            if st.button("REMOVE SENSOR", key=f"{widget_prefix}remove"):
                removed_ids.add(sensor_id)
            edited_sensors.append(
                {
                    **sensor,
                    "name": name,
                    "latitude": latitude,
                    "longitude": longitude,
                    "height_agl": height_agl,
                    "sensor_type": sensor_type,
                }
            )

    if removed_ids:
        edited_sensors = [sensor for sensor in edited_sensors if sensor["id"] not in removed_ids]

    st.checkbox(
        "Auto-Deploy Sensor on Map Click",
        value=st.session_state.setdefault(f"auto_deploy_{venue_slug}", False),
        key=f"auto_deploy_{venue_slug}",
    )
    if len(edited_sensors) < 5:
        st.markdown("**ADD CUSTOM SENSOR**")
        name_key = f"sensor_add_{venue_slug}_name"
        latitude_key = "selected_lat"
        longitude_key = "selected_lon"
        height_key = f"sensor_add_{venue_slug}_height"
        type_key = f"sensor_add_{venue_slug}_type"
        st.session_state.setdefault(name_key, "")
        st.session_state.setdefault(latitude_key, float(target_latitude))
        st.session_state.setdefault(longitude_key, float(target_longitude))
        st.session_state.setdefault(height_key, 10.0)
        st.session_state.setdefault(type_key, "Ku-Band Tactical Radar")
        st.text_input("Sensor Name", key=name_key)
        st.number_input("Latitude", min_value=-90.0, max_value=90.0, format="%.6f", key=latitude_key)
        st.number_input("Longitude", min_value=-180.0, max_value=180.0, format="%.6f", key=longitude_key)
        st.number_input("Mast Height (m AGL)", min_value=0.0, max_value=500.0, step=1.0, key=height_key)
        st.selectbox("Sensor Type", list(SENSOR_TYPES), key=type_key)
        if st.button("Deploy Sensor", key=f"deploy_sensor_{venue_slug}", width="stretch"):
            sensor_latitude = float(st.session_state[latitude_key])
            sensor_longitude = float(st.session_state[longitude_key])
            target_coord = (round(sensor_latitude, 5), round(sensor_longitude, 5))
            existing_coords = {
                (round(float(s["latitude"]), 5), round(float(s["longitude"]), 5))
                for s in edited_sensors
            }
            if target_coord in existing_coords:
                st.warning(f"Sensor already deployed at ({sensor_latitude:.5f}, {sensor_longitude:.5f}).")
            elif len(edited_sensors) < 5:
                edited_sensors.append(
                    make_custom_sensor(
                        edited_sensors,
                        sensor_latitude,
                        sensor_longitude,
                        st.session_state[height_key],
                        st.session_state[type_key],
                    )
                )
                st.session_state.setdefault("selected_targets", {})[venue_slug] = {
                    "latitude": sensor_latitude,
                    "longitude": sensor_longitude,
                }
            else:
                st.warning("Maximum of 5 active sensors reached.")
    else:
        st.caption("Maximum of 5 active sensors reached.")

    notice = st.session_state.pop(f"map_click_notice_{venue_slug}", None)
    if notice:
        st.caption(notice)

    networks[venue_slug] = edited_sensors[:5]
    return networks[venue_slug]


def analyze_sensor_coverage(
    latitude: float,
    longitude: float,
    core_geometry_wgs84: Any,
    building_features: list[dict[str, Any]],
    sensors: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], float, float]:
    """Cast 120 local-UTM rays per sensor and return WGS84 coverage/shadow layers."""
    if not sensors:
        return [], [], [], 0.0, 0.0

    center = gpd.GeoSeries([Point(longitude, latitude)], crs="EPSG:4326")
    local_crs = center.estimate_utm_crs()
    to_local = Transformer.from_crs("EPSG:4326", local_crs, always_xy=True)
    to_wgs84 = Transformer.from_crs(local_crs, "EPSG:4326", always_xy=True)
    core_local = transform_geometry(core_geometry_wgs84, to_local.transform, interleaved=False)

    if building_features:
        building_geometries, building_heights = prep_building_spatial_index(
            latitude, longitude, building_features
        )
        tree = STRtree(building_geometries) if building_geometries else None
    else:
        building_geometries = []
        building_heights = []
        tree = None

    active_features: list[dict[str, Any]] = []
    blind_features: list[dict[str, Any]] = []
    sensor_columns: list[dict[str, Any]] = []
    active_geometries: list[Any] = []
    blind_geometries: list[Any] = []

    for sensor in sensors:
        sensor_type = sensor["sensor_type"]
        sensor_style = SENSOR_TYPES[sensor_type]
        max_range = float(sensor_style["max_range_m"])
        mount_height = float(sensor["height_agl"])
        x, y = to_local.transform(float(sensor["longitude"]), float(sensor["latitude"]))
        origin = Point(x, y)
        ray_distances: list[float] = []
        ray_endpoints: list[tuple[float, float]] = []
        ray_hits: list[tuple[float, float]] = []

        for ray_index in range(RAY_COUNT):
            angle = math.radians(ray_index * 360 / RAY_COUNT)
            dx, dy = math.cos(angle), math.sin(angle)
            endpoint = (x + max_range * dx, y + max_range * dy)
            ray = LineString([(x, y), endpoint])
            hit_distance = max_range
            if tree is not None:
                for candidate in tree.query(ray, predicate="intersects"):
                    building_index = int(candidate)
                    if float(building_heights[building_index]) <= mount_height:
                        continue
                    intersection = ray.intersection(building_geometries[building_index])
                    if intersection.is_empty:
                        continue
                    _, nearest_hit = nearest_points(origin, intersection)
                    distance = origin.distance(nearest_hit)
                    if distance < hit_distance:
                        hit_distance = distance
            ray_distances.append(hit_distance)
            ray_endpoints.append(endpoint)
            ray_hits.append((x + hit_distance * dx, y + hit_distance * dy))

        active_sectors: list[Polygon] = []
        blind_sectors: list[Polygon] = []
        for ray_index in range(RAY_COUNT):
            next_index = (ray_index + 1) % RAY_COUNT
            active_sector = Polygon([(x, y), ray_hits[ray_index], ray_hits[next_index]])
            if not active_sector.is_empty and active_sector.area > 1e-6:
                active_sectors.append(active_sector)
            if ray_distances[ray_index] < max_range or ray_distances[next_index] < max_range:
                blind_sector = Polygon(
                    [
                        ray_hits[ray_index],
                        ray_endpoints[ray_index],
                        ray_endpoints[next_index],
                        ray_hits[next_index],
                    ]
                )
                if not blind_sector.is_empty and blind_sector.area > 1e-6:
                    blind_sectors.append(blind_sector)

        active_local = unary_union(active_sectors) if active_sectors else Polygon()
        blind_local = unary_union(blind_sectors) if blind_sectors else Polygon()
        if not active_local.is_empty:
            active_geometries.append(active_local)
            active_wgs84 = transform_geometry(active_local, to_wgs84.transform, interleaved=False)
            active_features.append(
                {
                    "type": "Feature",
                    "geometry": mapping(active_wgs84),
                    "properties": {"name": sensor["name"], "color": sensor_style["active_color"]},
                }
            )
        if not blind_local.is_empty:
            blind_geometries.append(blind_local)
            blind_wgs84 = transform_geometry(blind_local, to_wgs84.transform, interleaved=False)
            blind_features.append(
                {
                    "type": "Feature",
                    "geometry": mapping(blind_wgs84),
                    "properties": {"name": sensor["name"], "color": [239, 68, 68, 95]},
                }
            )
        sensor_columns.append(
            {
                "name": sensor["name"],
                "sensor_type": sensor_type,
                "latitude": float(sensor["latitude"]),
                "longitude": float(sensor["longitude"]),
                "height_agl": mount_height,
                "radius_m": 28,
                "color": sensor_style["beacon_color"],
                    "hover_label": f"{sensor['name']} / {mount_height:.1f} m AGL",
            }
        )

    active_union = unary_union(active_geometries) if active_geometries else Polygon()
    blind_union = unary_union(blind_geometries) if blind_geometries else Polygon()
    tfr_area = core_local.area
    covered_area = active_union.intersection(core_local).area if not active_union.is_empty else 0.0
    masked_area = blind_union.intersection(core_local).area if not blind_union.is_empty else 0.0
    coverage_percent = min(100.0, covered_area / tfr_area * 100) if tfr_area else 0.0
    return active_features, blind_features, sensor_columns, coverage_percent, masked_area / 1_000_000


def load_buildings(
    latitude: float,
    longitude: float,
    venue_slug: str,
    force_refresh: bool = False,
    allow_fetch: bool = False,
) -> tuple[list[dict[str, Any]], str | None]:
    """Load cached OSM buildings or query Overpass and persist a GeoJSON cache."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = DATA_DIR / f"{venue_slug}_buildings.geojson"

    if cache_path.exists() and not force_refresh:
        try:
            cached = gpd.read_file(cache_path)
            if not cached.empty:
                cached = cached[cached.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
                local_crs = gpd.GeoSeries(
                    [Point(longitude, latitude)], crs="EPSG:4326"
                ).estimate_utm_crs()
                cached_local = cached.to_crs(local_crs)
                heights = []
                for (_, row), geometry in zip(cached.iterrows(), cached_local.geometry):
                    height_m = float(row["height_m"])
                    if math.isclose(height_m, 9.0):
                        height_m = building_height_meters(None, None, geometry.area)
                    heights.append(height_m)
                cached["height_m"] = heights
                cache_path.write_text(
                    cached[["geometry", "height_m"]].to_json(drop_id=True),
                    encoding="utf-8",
                )
            return buildings_to_features(cached), None
        except Exception as error:
            return [], f"{type(error).__name__}: {error}"

    if not allow_fetch and not force_refresh:
        return [], None

    import osmnx as ox

    try:
        ox.settings.use_cache = True
        ox.settings.requests_timeout = 60
        buildings = ox.features_from_point(
            center_point=(latitude, longitude),
            tags={"building": True},
            dist=BUILDING_QUERY_METERS,
        )
        if buildings.empty:
            buildings = gpd.GeoDataFrame(
                {"height_m": [], "geometry": gpd.GeoSeries([], crs="EPSG:4326")},
                geometry="geometry",
                crs="EPSG:4326",
            )
        else:
            buildings = buildings[
                buildings.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
            ].copy()
            local_crs = gpd.GeoSeries(
                [Point(longitude, latitude)], crs="EPSG:4326"
            ).estimate_utm_crs()
            projected_buildings = buildings.to_crs(local_crs)
            heights = [
                building_height_meters(
                    row.get("height"),
                    row.get("building:levels"),
                    float(projected_buildings.geometry.iloc[index].area),
                )
                for index, (_, row) in enumerate(buildings.iterrows())
            ]
            buildings = gpd.GeoDataFrame(
                {"height_m": heights, "geometry": buildings.geometry},
                geometry="geometry",
                crs=buildings.crs,
            ).to_crs("EPSG:4326")

        buildings = buildings[["geometry", "height_m"]]
        cache_path.write_text(buildings.to_json(drop_id=True), encoding="utf-8")
        return buildings_to_features(buildings), None
    except Exception as error:
        return [], f"{type(error).__name__}: {error}"


def geojson_feature(geometry: Any, properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": mapping(geometry), "properties": properties}


@st.cache_data(show_spinner=False)
def buildings_to_polygons(building_features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    polygons: list[dict[str, Any]] = []
    for feature in building_features:
        geometry = feature["geometry"]
        if not geometry or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
            continue
        geometry_type = geometry["type"]
        coordinates = geometry["coordinates"]
        polygon_coordinates = [coordinates] if geometry_type == "Polygon" else coordinates
        centroid = shape(geometry).centroid
        for polygon in polygon_coordinates:
            polygons.append(
                {
                    "polygon": polygon,
                    "height_m": float(feature["properties"]["height_m"]),
                    "click_lat": centroid.y,
                    "click_lon": centroid.x,
                    "hover_label": f"Structure Height: {float(feature['properties']['height_m']):.1f} m",
                    "color": [251, 191, 36, 235]
                    if float(feature["properties"]["height_m"]) > 20
                    else [203, 213, 225, 220],
                }
            )
    return polygons


def make_deck(
    latitude: float,
    longitude: float,
    core_geometry: Any,
    buffer_geometry: Any,
    building_features: list[dict[str, Any]],
    active_features: list[dict[str, Any]] | None = None,
    blind_features: list[dict[str, Any]] | None = None,
    sensor_columns: list[dict[str, Any]] | None = None,
    show_buildings: bool = True,
    show_sensor_coverage: bool = True,
    show_blind_spots: bool = True,
    show_tfr_rings: bool = True,
    aircraft: list[dict[str, Any]] | None = None,
    drone_simulation: dict[str, Any] | None = None,
    clickable_ground_grid: list[dict[str, Any]] | None = None,
    target_pin: dict[str, float] | None = None,
) -> pdk.Deck:
    layers: list[pdk.Layer] = []
    to_local, to_wgs84 = local_transformers(latitude, longitude)
    center_x, center_y = to_local.transform(longitude, latitude)
    half_size = 15_000.0
    ground_square_local = Polygon(
        [
            (center_x - half_size, center_y - half_size),
            (center_x + half_size, center_y - half_size),
            (center_x + half_size, center_y + half_size),
            (center_x - half_size, center_y + half_size),
        ]
    )
    ground_square_wgs84 = transform_geometry(
        ground_square_local, to_wgs84.transform, interleaved=False
    )
    ground_square_coords = [list(c[:2]) for c in ground_square_wgs84.exterior.coords]
    layers.append(
        pdk.Layer(
            "PolygonLayer",
            id="tactical-ground-canvas",
            data=[{"polygon": ground_square_coords, "hover_label": "Tactical Ground Canvas"}],
            get_polygon="polygon",
            get_fill_color=[13, 17, 23, 255],
            stroked=False,
            pickable=False,
        )
    )
    normalized_active_features = normalize_polygon_features(active_features, latitude, longitude)
    normalized_blind_features = normalize_polygon_features(blind_features, latitude, longitude)
    if clickable_ground_grid:
        layers.append(
            pdk.Layer(
                "PolygonLayer",
                id="clickable-ground-grid",
                data=clickable_ground_grid,
                get_polygon="polygon",
                get_elevation=0,
                elevation_scale=1,
                extruded=False,
                get_fill_color=[0, 0, 0, 0],
                get_line_color=[0, 0, 0, 0],
                filled=True,
                stroked=False,
                pickable=True,
                auto_highlight=False,
            )
        )
    if show_sensor_coverage and normalized_active_features:
        layers.append(
            pdk.Layer(
                "PolygonLayer",
                id="active-sensor-coverage",
                data=normalized_active_features,
                get_polygon="polygon",
                get_fill_color=[16, 185, 129, 55],
                get_line_color=[16, 185, 129, 120],
                line_width_min_pixels=1,
                filled=True,
                stroked=False,
                extruded=False,
                pickable=False,
            )
        )
    if show_blind_spots and normalized_blind_features:
        layers.append(
            pdk.Layer(
                "PolygonLayer",
                id="blind-spot-shadows",
                data=normalized_blind_features,
                get_polygon="polygon",
                get_fill_color=[239, 68, 68, 85],
                get_line_color=[239, 68, 68, 150],
                line_width_min_pixels=1,
                filled=True,
                stroked=False,
                extruded=False,
                pickable=False,
            )
        )
    if (
        show_tfr_rings
        and core_geometry is not None
        and not core_geometry.is_empty
        and buffer_geometry is not None
        and not buffer_geometry.is_empty
    ):
        core_coords = [list(c[:2]) for c in core_geometry.exterior.coords]
        buffer_coords = [list(c[:2]) for c in buffer_geometry.exterior.coords]
        layers.extend(
            [
                pdk.Layer(
                    "PathLayer",
                    id="core-tfr-ring",
                    data=[
                        {
                            "path": core_coords,
                            "hover_label": "FAA 3.0 NM Core TFR",
                        }
                    ],
                    get_path="path",
                    get_color=[239, 68, 68, 220],
                    width_min_pixels=3,
                    pickable=False,
                ),
                pdk.Layer(
                    "PathLayer",
                    id="early-warning-ring",
                    data=[
                        {
                            "path": buffer_coords,
                            "hover_label": "5.0 NM Early Warning Perimeter",
                        }
                    ],
                    get_path="path",
                    get_color=[245, 158, 11, 220],
                    width_min_pixels=3,
                    pickable=False,
                ),
            ]
        )
    building_polygons = buildings_to_polygons(building_features) if show_buildings and building_features else []
    if building_polygons:
        layers.append(
            pdk.Layer(
                "PolygonLayer",
                id="3d-buildings",
                data=building_polygons,
                get_polygon="polygon",
                get_fill_color="color",
                get_line_color=[31, 41, 55, 255],
                get_elevation="height_m",
                elevation_scale=1,
                extruded=True,
                wireframe=True,
                filled=True,
                stroked=True,
                line_width_min_pixels=1,
                pickable=True,
                auto_highlight=True,
            )
        )
    if sensor_columns:
        layers.extend(
            [
            pdk.Layer(
                "ColumnLayer",
                id="sensor-pylons",
                data=sensor_columns or [],
                get_position=["longitude", "latitude"],
                get_elevation="height_agl",
                elevation_scale=1,
                get_radius=22,
                radius_units="meters",
                get_fill_color=[255, 255, 255, 255],
                get_line_color=[0, 229, 255, 255],
                line_width_min_pixels=2,
                extruded=True,
                pickable=True,
                auto_highlight=True,
            ),
            pdk.Layer(
                "ScatterplotLayer",
                id="sensor-beacons",
                data=sensor_columns or [],
                get_position=["longitude", "latitude", "height_agl"],
                get_radius=48,
                radius_units="meters",
                get_fill_color=[255, 255, 255, 255],
                get_line_color=[0, 229, 255, 255],
                line_width_min_pixels=2,
                stroked=True,
                filled=True,
                pickable=True,
            ),
            ]
        )
    if aircraft:
        aircraft_data = [
            {
                **track,
                "hover_label": track.get(
                    "hover_label",
                    track.get(
                        "label",
                        f"{track.get('callsign', 'Aircraft')}"
                        + (f" / {track['altitude_ft']:.0f} ft" if track.get("altitude_ft") is not None else ""),
                    ),
                ),
            }
            for track in aircraft
        ]
        layers.extend(
            [
                pdk.Layer(
                    "ScatterplotLayer",
                    id="live-aircraft",
                    data=aircraft_data,
                    get_position=["longitude", "latitude"],
                    get_radius=100,
                    radius_units="meters",
                    get_fill_color="color",
                    get_line_color=[255, 255, 255, 240],
                    line_width_min_pixels=2,
                    stroked=True,
                    filled=True,
                    pickable=True,
                ),
                pdk.Layer(
                    "TextLayer",
                    id="live-aircraft-labels",
                    data=aircraft_data,
                    get_position=["longitude", "latitude"],
                    get_text="label",
                    get_color="color",
                    get_size=14,
                    size_units="pixels",
                    get_alignment_baseline="bottom",
                    get_pixel_offset=[0, -12],
                    billboard=True,
                    pickable=False,
                ),
            ]
        )
    if drone_simulation and drone_simulation.get("path"):
        layers.extend(
            [
                pdk.Layer(
                    "PathLayer",
                    id="rogue-uas-path-glow",
                    data=[{"path": drone_simulation["path"], "hover_label": "Rogue UAS Flight Path"}],
                    get_path="path",
                    get_color=[255, 122, 36, 65],
                    get_width=34,
                    width_units="meters",
                    width_min_pixels=8,
                    pickable=False,
                ),
                pdk.Layer(
                    "PathLayer",
                    id="rogue-uas-path",
                    data=[{"path": drone_simulation["path"], "hover_label": "Rogue UAS Flight Path"}],
                    get_path="path",
                    get_color=[249, 115, 22, 220],
                    get_width=4,
                    width_units="pixels",
                    pickable=False,
                ),
            ]
        )
        if drone_simulation.get("dash_paths"):
            dash_data = [
                {**dash, "hover_label": "Rogue UAS Flight Path"}
                for dash in drone_simulation["dash_paths"]
            ]
            layers.append(
                pdk.Layer(
                    "PathLayer",
                    id="rogue-uas-path-dashes",
                    data=dash_data,
                    get_path="path",
                    get_color=[255, 143, 56, 240],
                    get_width=12,
                    width_units="meters",
                    width_min_pixels=3,
                    pickable=False,
                )
            )
        layers.extend(
            [
                pdk.Layer(
                    "ScatterplotLayer",
                    id="rogue-uas-position",
                    data=[
                        {
                            **drone_simulation,
                            "hover_label": drone_simulation.get(
                                "hover_label", "Rogue UAS position"
                            ),
                        }
                    ],
                    get_position=["longitude", "latitude", "altitude_m"],
                    get_radius=85,
                    radius_units="meters",
                    get_fill_color=[255, 79, 45, 245],
                    get_line_color=[255, 255, 255, 255],
                    line_width_min_pixels=2,
                    stroked=True,
                    filled=True,
                    pickable=True,
                ),
                pdk.Layer(
                    "TextLayer",
                    id="rogue-uas-label",
                    data=[
                        {
                            **drone_simulation,
                            "label": f"ROGUE UAS / {drone_simulation['altitude_m']:.0f} m AGL / {drone_simulation['distance_nm']:.2f} NM",
                            "hover_label": drone_simulation.get(
                                "hover_label", "Rogue UAS position"
                            ),
                        }
                    ],
                    get_position=["longitude", "latitude", "altitude_m"],
                    get_text="label",
                    get_color=[255, 186, 128, 255],
                    get_size=15,
                    size_units="pixels",
                    get_pixel_offset=[0, -14],
                    billboard=True,
                    pickable=False,
                ),
            ]
        )
    if target_pin:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                id="target-preview-pin",
                data=[
                    {
                        **target_pin,
                        "hover_label": target_pin.get("hover_label", "Sensor target"),
                    }
                ],
                get_position=["longitude", "latitude"],
                get_radius=180,
                radius_units="meters",
                get_fill_color=[250, 204, 21, 240],
                get_line_color=[255, 255, 255, 255],
                line_width_min_pixels=3,
                stroked=True,
                filled=True,
                pickable=False,
            )
        )
    return pdk.Deck(
        layers=layers,
        initial_view_state=pdk.ViewState(
            latitude=latitude,
            longitude=longitude,
            zoom=14.2,
            pitch=45,
            bearing=-25,
        ),
        map_provider="carto",
        map_style="dark",
        tooltip={"text": "{hover_label}"},
    )


render_tactical_map = make_deck


def apply_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
        :root { --ink: #e8eee9; --muted: #91a39a; --line: #283a34; --mint: #55c5a5; --amber: #f2ae4c; }
        html, body, [class*="css"] { font-family: 'IBM Plex Mono', monospace; }
        .stApp { background: radial-gradient(ellipse at 75% 0%, #1b302c 0%, #101916 42%, #0b100f 100%); color: var(--ink); }
        [data-testid="stSidebar"] { background: #101715; border-right: 1px solid var(--line); }
        [data-testid="stSidebar"] > div { padding-top: 1.4rem; }
        .block-container { max-width: 1600px; padding-top: 1.6rem; padding-bottom: 2rem; }
        h1, h2, h3 { font-family: 'Barlow Condensed', sans-serif !important; letter-spacing: 0 !important; color: var(--ink); }
        h1 { font-size: 2.5rem !important; line-height: 1.05 !important; }
        .eyebrow { color: var(--mint); font-size: .72rem; font-weight: 600; }
        .venue-sub { color: var(--muted); font-size: .79rem; margin-top: -.5rem; }
        .metric-strip { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); margin: 1rem 0 1.2rem; }
        .metric { padding: .8rem 1rem .85rem 0; border-right: 1px solid var(--line); }
        .metric:not(:first-child) { padding-left: 1rem; }
        .metric:last-child { border-right: 0; }
        .metric-label { color: var(--muted); font-size: .66rem; text-transform: uppercase; }
        .metric-value { color: var(--ink); font-family: 'Barlow Condensed', sans-serif; font-size: 1.38rem; font-weight: 600; margin-top: .18rem; }
        .status-line { color: var(--muted); font-size: .72rem; padding-top: .55rem; }
        .status-dot { color: var(--mint); }
        .feed-badge { border: 1px solid var(--line); border-radius: 4px; padding: .45rem .55rem; margin-top: .35rem; font-size: .66rem; line-height: 1.45; color: var(--muted); }
        .feed-live { border-color: #266d5e; color: #77e2c3; background: #10251f; }
        .feed-cached { border-color: #75602f; color: #f3c76e; background: #262115; }
        .feed-simulated { border-color: #73402d; color: #ffad79; background: #2a1c17; }
        .feed-sample { border-color: #33606b; color: #88d9e8; background: #15252a; }
        .threat-banner { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .75rem 1.5rem; border: 1px solid var(--line); border-left: 4px solid var(--amber); padding: .8rem 1rem; margin: .25rem 0 1rem; background: #171d1a; }
        .threat-banner.violation { border-left-color: #ef4444; background: #281817; }
        .threat-banner.detected { border-top-color: #286d5f; }
        .threat-banner.occluded { border-color: #a93636; background: #2d1718; }
        .threat-label { color: var(--muted); font-size: .65rem; text-transform: uppercase; }
        .threat-value { color: var(--ink); font-family: 'Barlow Condensed', sans-serif; font-size: 1.05rem; font-weight: 600; }
        [data-testid="stMetric"] { background: transparent; }
        [data-testid="stAlert"] { background: #29251b; }
        @media (max-width: 1000px) { .metric-strip { grid-template-columns: repeat(3, minmax(0, 1fr)); } .metric:nth-child(3) { border-right: 0; } .metric:nth-child(n+4) { border-top: 1px solid var(--line); } .metric:nth-child(4) { padding-left: 0; } }
        @media (max-width: 600px) { .metric-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); } .metric:nth-child(2), .metric:nth-child(4) { border-right: 0; } .metric:nth-child(n+3) { border-top: 1px solid var(--line); } .metric:nth-child(3), .metric:nth-child(5) { padding-left: 0; } }
        </style>
        """,
        unsafe_allow_html=True,
    )


def largest_masked_compass_sector(
    latitude: float,
    longitude: float,
    blind_features: list[dict[str, Any]],
) -> str:
    if not blind_features:
        return "None"
    center_local = gpd.GeoSeries([Point(longitude, latitude)], crs="EPSG:4326")
    local_crs = center_local.estimate_utm_crs()
    to_local = Transformer.from_crs("EPSG:4326", local_crs, always_xy=True)
    center_x, center_y = to_local.transform(longitude, latitude)
    shadows = [
        transform_geometry(shape(feature["geometry"]), to_local.transform, interleaved=False)
        for feature in blind_features
        if feature.get("geometry")
    ]
    if not shadows:
        return "None"
    shadow_union = unary_union(shadows)
    sectors = [
        ("N", -22.5), ("NE", 22.5), ("E", 67.5), ("SE", 112.5),
        ("S", 157.5), ("SW", 202.5), ("W", 247.5), ("NW", 292.5),
    ]
    sector_areas: dict[str, float] = {}
    for label, start_bearing in sectors:
        arc = []
        for step in range(13):
            bearing = math.radians(start_bearing + step * 45 / 12)
            arc.append(
                (
                    center_x + BUFFER_RADIUS_METERS * math.sin(bearing),
                    center_y + BUFFER_RADIUS_METERS * math.cos(bearing),
                )
            )
        sector = Polygon([(center_x, center_y), *arc, (center_x, center_y)])
        sector_areas[label] = shadow_union.intersection(sector).area
    maximum = max(sector_areas.values(), default=0.0)
    return max(sector_areas, key=sector_areas.get) if maximum > 0 else "None"


def build_polar_svg(
    latitude: float,
    longitude: float,
    sensors: list[dict[str, Any]],
    primary_sector: str,
    drone_simulation: dict[str, Any] | None,
) -> str:
    """Render a vector polar radar schematic plotting 3.0/5.0 NM rings and sensor sites."""
    cx, cy = 150.0, 150.0
    r_outer = 118.0
    r_3nm = r_outer * (3.0 / 5.0)
    r_1nm = r_outer * (1.0 / 5.0)

    sector_bearings = {
        "N": (-22.5, 22.5),
        "NE": (22.5, 67.5),
        "E": (67.5, 112.5),
        "SE": (112.5, 157.5),
        "S": (157.5, 202.5),
        "SW": (202.5, 247.5),
        "W": (247.5, 292.5),
        "NW": (292.5, 337.5),
    }

    svg_parts = [
        '<svg viewBox="0 0 300 300" width="100%" style="max-width:280px;display:block;margin:0 auto;" xmlns="http://www.w3.org/2000/svg">',
        f'<circle cx="{cx}" cy="{cy}" r="138" fill="#080e1a" stroke="#1e293b" stroke-width="1.5"/>',
        f'<circle cx="{cx}" cy="{cy}" r="{r_outer}" fill="none" stroke="#334155" stroke-width="1" stroke-dasharray="2 3"/>',
        f'<circle cx="{cx}" cy="{cy}" r="{r_1nm}" fill="none" stroke="#1e293b" stroke-width="1" stroke-dasharray="2 2"/>',
        f'<line x1="{cx}" y1="18" x2="{cx}" y2="282" stroke="#1e293b" stroke-width="1"/>',
        f'<line x1="18" y1="{cy}" x2="282" y2="{cy}" stroke="#1e293b" stroke-width="1"/>',
        f'<line x1="66" y1="66" x2="234" y2="234" stroke="#131c2e" stroke-width="1" stroke-dasharray="3 3"/>',
        f'<line x1="66" y1="234" x2="234" y2="66" stroke="#131c2e" stroke-width="1" stroke-dasharray="3 3"/>',
    ]

    if primary_sector in sector_bearings:
        a1, a2 = sector_bearings[primary_sector]
        rad1, rad2 = math.radians(a1), math.radians(a2)
        x1 = cx + r_outer * math.sin(rad1)
        y1 = cy - r_outer * math.cos(rad1)
        x2 = cx + r_outer * math.sin(rad2)
        y2 = cy - r_outer * math.cos(rad2)
        svg_parts.append(
            f'<path d="M {cx} {cy} L {x1:.1f} {y1:.1f} A {r_outer} {r_outer} 0 0 1 {x2:.1f} {y2:.1f} Z" '
            f'fill="rgba(239, 68, 68, 0.22)" stroke="rgba(239, 68, 68, 0.6)" stroke-width="1" stroke-dasharray="3 3"/>'
        )

    svg_parts.extend([
        f'<circle cx="{cx}" cy="{cy}" r="{r_outer}" fill="none" stroke="#f59e0b" stroke-width="1.8" stroke-dasharray="5 3"/>',
        f'<text x="{cx + r_outer - 4}" y="{cy - 5}" fill="#f59e0b" font-size="8" font-family="monospace" font-weight="700" text-anchor="end">5.0 NM</text>',
        f'<circle cx="{cx}" cy="{cy}" r="{r_3nm}" fill="none" stroke="#ef4444" stroke-width="2"/>',
        f'<text x="{cx + r_3nm - 4}" y="{cy - 5}" fill="#ef4444" font-size="8" font-family="monospace" font-weight="700" text-anchor="end">3.0 NM</text>',
        f'<text x="{cx}" y="14" fill="#94a3b8" font-size="10" font-family="sans-serif" font-weight="700" text-anchor="middle">N</text>',
        f'<text x="{cx}" y="296" fill="#64748b" font-size="10" font-family="sans-serif" font-weight="700" text-anchor="middle">S</text>',
        f'<text x="294" y="154" fill="#64748b" font-size="10" font-family="sans-serif" font-weight="700" text-anchor="middle">E</text>',
        f'<text x="6" y="154" fill="#64748b" font-size="10" font-family="sans-serif" font-weight="700" text-anchor="middle">W</text>',
        f'<circle cx="{cx}" cy="{cy}" r="4" fill="#f59e0b" stroke="#ffffff" stroke-width="1.5"/>',
        f'<text x="{cx}" y="{cy + 13}" fill="#cbd5e1" font-size="8" font-family="sans-serif" font-weight="600" text-anchor="middle">VENUE</text>',
    ])

    for idx, s in enumerate(sensors):
        _, az, dist_m = GEOD.inv(longitude, latitude, float(s["longitude"]), float(s["latitude"]))
        dist_nm = dist_m / NM_TO_METERS
        r_s = min(r_outer, (dist_nm / 5.0) * r_outer)
        rad = math.radians(az)
        sx = cx + r_s * math.sin(rad)
        sy = cy - r_s * math.cos(rad)
        s_name = escape(str(s.get("name", f"S{idx+1}")))
        short_name = s_name.replace("Custom Sensor #", "S#").replace("MetLife ", "").replace("SHI ", "")
        svg_parts.extend([
            f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="7" fill="none" stroke="#10b981" stroke-width="1" opacity="0.6"/>',
            f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="4" fill="#10b981" stroke="#ffffff" stroke-width="1.5"/>',
            f'<text x="{sx + 6:.1f}" y="{sy + 3:.1f}" fill="#34d399" font-size="8" font-family="monospace" font-weight="700">{short_name}</text>',
        ])

    if drone_simulation:
        _, d_az, d_dist = GEOD.inv(longitude, latitude, float(drone_simulation["longitude"]), float(drone_simulation["latitude"]))
        r_d = min(r_outer, (d_dist / NM_TO_METERS / 5.0) * r_outer)
        d_rad = math.radians(d_az)
        dx = cx + r_d * math.sin(d_rad)
        dy = cy - r_d * math.cos(d_rad)
        svg_parts.extend([
            f'<circle cx="{dx:.1f}" cy="{dy:.1f}" r="9" fill="none" stroke="#ef4444" stroke-width="1.2" stroke-dasharray="2 2"/>',
            f'<circle cx="{dx:.1f}" cy="{dy:.1f}" r="4.5" fill="#ef4444" stroke="#ffffff" stroke-width="1.5"/>',
            f'<text x="{dx + 7:.1f}" y="{dy + 3:.1f}" fill="#f87171" font-size="8" font-family="monospace" font-weight="700">RED AIR ({drone_simulation["altitude_m"]:.0f}m)</text>',
        ])

    svg_parts.append('</svg>')
    return "\n".join(svg_parts)


def generate_site_survey_html(
    venue_name: str,
    latitude: float,
    longitude: float,
    capacity: int | None,
    airspace: str,
    core_geometry: Any,
    buffer_geometry: Any,
    building_features: list[dict[str, Any]],
    sensors: list[dict[str, Any]],
    active_features: list[dict[str, Any]],
    blind_features: list[dict[str, Any]],
    coverage_percent: float,
    masked_area_km2: float,
    aircraft: list[dict[str, Any]],
    drone_simulation: dict[str, Any] | None,
    cache_status: str,
) -> str:
    center = gpd.GeoSeries([Point(longitude, latitude)], crs="EPSG:4326")
    utm_crs = center.estimate_utm_crs()
    epsg = utm_crs.to_epsg() or 0
    utm_zone_number = epsg % 100
    utm_hemisphere = "N" if 32601 <= epsg <= 32660 else "S"
    utm_zone = f"{utm_zone_number}{utm_hemisphere}" if utm_zone_number else str(utm_crs)
    to_local, _ = local_transformers(latitude, longitude)
    core_local = transform_geometry(core_geometry, to_local.transform, interleaved=False)
    buffer_local = transform_geometry(buffer_geometry, to_local.transform, interleaved=False)
    tfr_area_km2 = core_local.area / 1_000_000
    tfr_area_sqmi = core_local.area / (1_609.344**2)
    coverage_area_km2 = tfr_area_km2 * coverage_percent / 100
    outer_ring = buffer_local.difference(core_local)
    active_local = [
        transform_geometry(shape(feature["geometry"]), to_local.transform, interleaved=False)
        for feature in active_features
        if feature.get("geometry")
    ]
    active_union = unary_union(active_local) if active_local else Polygon()
    outer_covered_km2 = (
        active_union.intersection(outer_ring).area / 1_000_000
        if not active_union.is_empty
        else 0.0
    )
    uncovered_outer_km2 = max(0.0, outer_ring.area / 1_000_000 - outer_covered_km2)
    heights = [float(feature["properties"].get("height_m", 0)) for feature in building_features]
    mean_height = sum(heights) / len(heights) if heights else 0.0
    major_obstacles = sum(height >= 20 for height in heights)
    very_tall_obstacles = sum(height >= 30 for height in heights)
    primary_sector = largest_masked_compass_sector(latitude, longitude, blind_features)
    advisory_count = sum(track.get("alert") in {"advisory", "breach"} for track in aircraft)
    aircraft_inside_ring = sum(float(track.get("distance_nm", float("inf"))) <= 5 for track in aircraft)
    live_time_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    local_time = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    if sensors:
        sensor_rows = "".join(
            "<tr>"
            f"<td>{escape(str(sensor['name']))}</td>"
            f"<td>{escape(str(sensor['sensor_type']))}</td>"
            f"<td>{float(sensor['latitude']):.6f}, {float(sensor['longitude']):.6f}</td>"
            f"<td>{float(sensor['height_agl']):.1f}</td>"
            f"<td>{SENSOR_TYPES[sensor['sensor_type']]['max_range_m'] / 1000:.1f}</td>"
            "</tr>"
            for sensor in sensors
        )
    else:
        sensor_rows = '<tr><td colspan="5" class="warn">No C-UAS sensors are deployed.</td></tr>'

    if drone_simulation:
        drone_outcome = (
            f"DETECTED BY {', '.join(drone_simulation['detected_by'])}"
            if drone_simulation["detected_by"]
            else "UNDETECTED — OCCLUDED IN BUILDING BLIND SPOT"
            if drone_simulation["occluded_by"]
            else drone_simulation["sensor_status"]
        )
        drone_log = (
            f"Bearing {int(drone_simulation['approach_bearing'])}°; "
            f"{float(drone_simulation['altitude_m']):.1f} m AGL; "
            f"{float(drone_simulation['distance_nm']):.2f} NM from stadium; {escape(drone_outcome)}."
        )
    else:
        drone_log = "No simulated incursion track was active at export time."

    recommendations: list[tuple[str, str, str]] = []
    if very_tall_obstacles and any(float(sensor["height_agl"]) < 15 for sensor in sensors):
        recommendations.append(
            (
                "ADVISORY",
                "amber",
                "Low-mounted sensors operate near major vertical obstacles; elevate mast positions or relocate to higher rooftops.",
            )
        )
    undetected_incursion = bool(
        drone_simulation
        and drone_simulation["inside_tfr"]
        and not drone_simulation["detected_by"]
    )
    if undetected_incursion:
        recommendations.append(
            (
                "CRITICAL",
                "red",
                f"Active Red Air incursion penetrated the 3.0 NM TFR undetected in the {primary_sector} sector. Deploy immediate C-UAS gap-filler asset.",
            )
        )
    elif masked_area_km2 > 1.5:
        recommendations.append(
            (
                "ACTION REQUIRED",
                "amber",
                f"Add a gap-filler sensor in the {primary_sector} sector to address building-masked coverage ({masked_area_km2:.2f} km² masked).",
            )
        )
    if coverage_percent > 85 and masked_area_km2 <= 1.5:
        recommendations.append(
            (
                "OPTIMAL",
                "green",
                "Perimeter posture is optimal: TFR coverage exceeds 85% with minimal building masking.",
            )
        )
    if not recommendations:
        recommendations.append(
            (
                "STANDARD",
                "blue",
                "Maintain sensor coverage monitoring and verify current airspace restrictions before each operation.",
            )
        )
    recommendation_html = "".join(
        f'<li class="rec-item"><span class="rec-badge badge-{color}">{escape(sev)}</span><span>{escape(text)}</span></li>'
        for sev, color, text in recommendations
    )
    capacity_html = f"{capacity:,} seats" if capacity is not None else "Not specified"

    if coverage_percent >= 80.0:
        bar_color = "#059669"
        bar_bg = "#dcfce7"
        bar_text = "#065f46"
        bar_status = "OPTIMAL POSTURE"
    elif coverage_percent >= 50.0:
        bar_color = "#d97706"
        bar_bg = "#fef3c7"
        bar_text = "#92400e"
        bar_status = "MODERATE COVERAGE"
    else:
        bar_color = "#dc2626"
        bar_bg = "#fee2e2"
        bar_text = "#991b1b"
        bar_status = "CRITICAL VULNERABILITY"

    polar_svg = build_polar_svg(latitude, longitude, sensors, primary_sector, drone_simulation)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tactical Site Survey Brief — {escape(venue_name)}</title>
<style>
:root{{--page:#f1f5f9;--sheet:#ffffff;--ink:#0f172a;--navy:#0f172a;--amber:#d97706;--red:#dc2626;--green:#059669;--line:#e2e8f0;--muted:#64748b}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--page);color:var(--ink);font:13.5px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}}
.sheet{{max-width:980px;margin:24px auto;padding:0 0 32px 0;background:var(--sheet);box-shadow:0 16px 36px rgba(15,23,42,0.12);border-radius:8px;overflow:hidden}}
.command-banner{{background:#0f172a;color:white;padding:26px 36px;border-bottom:4px solid var(--amber)}}
.banner-top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:10px}}
.class-badge-group{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.class-badge{{background:#dc2626;color:white;font-size:10px;font-weight:800;letter-spacing:0.12em;padding:4px 10px;border-radius:4px;text-transform:uppercase}}
.class-subbadge{{background:#1e293b;color:#94a3b8;font-size:10px;font-weight:700;letter-spacing:0.08em;padding:4px 8px;border-radius:4px}}
.print-btn{{background:#2563eb;color:white;border:none;padding:8px 16px;font-size:11.5px;font-weight:700;border-radius:4px;cursor:pointer;letter-spacing:0.06em;display:inline-flex;align-items:center;gap:6px;box-shadow:0 2px 6px rgba(0,0,0,0.3);transition:background 0.15s}}
.print-btn:hover{{background:#1d4ed8}}
.banner-eyebrow{{color:#38bdf8;font-size:11px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;margin-bottom:4px}}
.banner-title{{font-size:28px;font-weight:800;margin:0 0 10px 0;letter-spacing:0.02em;color:#f8fafc}}
.banner-meta{{display:flex;flex-wrap:wrap;gap:8px 14px;font-size:11.5px;color:#94a3b8}}
.meta-item b{{color:#f1f5f9}}
.meta-sep{{color:#334155}}
.sheet-content{{padding:8px 36px 20px 36px}}
.section{{margin:24px 0 0;break-inside:avoid}}
h2{{font-size:14.5px;font-weight:700;margin:0 0 12px;padding:8px 12px;background:#1e293b;color:white;letter-spacing:0.04em;border-left:4px solid #38bdf8;border-radius:0 4px 4px 0}}
.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}
.metric{{padding:12px 14px;border:1px solid var(--line);background:#f8fafc;border-radius:5px}}
.metric b{{display:block;font-size:19px;font-weight:700;margin-top:3px;color:var(--navy)}}
.metric span{{display:block;font-size:11.5px;color:var(--muted);margin-top:2px}}
.label{{font-size:10.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em}}
.callout{{border-left:4px solid var(--amber);padding:10px 14px;background:#fffbeb;font-size:12.5px;border-radius:0 4px 4px 0;margin-top:10px}}
.badge{{display:inline-block;padding:3px 8px;border-radius:12px;background:#dcfce7;color:var(--green);font-weight:700;font-size:11px}}
.warn{{color:var(--red);font-weight:700}}
table{{width:100%;border-collapse:collapse;font-size:12px;margin-top:4px}}
th,td{{padding:9px 12px;border:1px solid var(--line);text-align:left}}
th{{background:#f1f5f9;font-weight:700;color:#334155;font-size:11px;text-transform:uppercase;letter-spacing:0.04em}}
tbody tr:nth-child(even){{background:#f8fafc}}
.progress-box{{border:1px solid var(--line);background:#f8fafc;padding:14px 16px;border-radius:6px;margin-bottom:14px}}
.progress-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;flex-wrap:wrap;gap:6px}}
.progress-title{{font-size:11px;font-weight:800;color:#334155;text-transform:uppercase;letter-spacing:0.06em}}
.progress-status{{font-size:11px;font-weight:800;padding:3px 8px;border-radius:12px;letter-spacing:0.04em}}
.progress-track{{height:12px;background:#e2e8f0;border-radius:6px;overflow:hidden}}
.progress-fill{{height:100%;border-radius:6px;transition:width 0.3s ease}}
.progress-sub{{display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-top:6px;flex-wrap:wrap}}
.sec5-layout{{display:grid;grid-template-columns:1fr 300px;gap:16px;align-items:start}}
.sec5-grid{{grid-template-columns:1fr;gap:10px}}
.polar-card{{border:1px solid var(--line);background:#0b1120;border-radius:6px;padding:12px;color:white;text-align:center}}
.polar-card-title{{font-size:10.5px;font-weight:800;letter-spacing:0.08em;color:#94a3b8;text-transform:uppercase;margin-bottom:6px}}
.polar-legend{{display:flex;justify-content:center;gap:10px;margin-top:8px;font-size:10px;color:#cbd5e1;flex-wrap:wrap}}
.leg-item{{display:inline-flex;align-items:center;gap:4px}}
.leg-dot{{width:8px;height:8px;border-radius:50%;display:inline-block}}
.leg-red{{background:#ef4444}}
.leg-amber{{background:#f59e0b}}
.leg-green{{background:#10b981}}
.leg-shadow{{background:rgba(239,68,68,0.4);border:1px dashed #ef4444}}
.rec-list{{list-style:none;padding:0;margin:4px 0 0 0}}
.rec-item{{display:flex;align-items:flex-start;gap:10px;padding:9px 12px;border:1px solid var(--line);border-radius:5px;background:#f8fafc;margin-bottom:8px;font-size:12.5px;line-height:1.45}}
.rec-badge{{display:inline-block;padding:2px 7px;font-size:9.5px;font-weight:800;border-radius:4px;letter-spacing:0.06em;text-transform:uppercase;flex-shrink:0;margin-top:2px}}
.badge-red{{background:#fee2e2;color:#991b1b;border:1px solid #f87171}}
.badge-amber{{background:#fef3c7;color:#92400e;border:1px solid #fcd34d}}
.badge-green{{background:#dcfce7;color:#065f46;border:1px solid #86efac}}
.badge-blue{{background:#dbeafe;color:#1e40af;border:1px solid #93c5fd}}
.footer{{margin-top:24px;padding-top:12px;border-top:1px solid var(--line);font-size:11px;color:var(--muted);text-align:center}}
@media print{{
body{{background:#fff}}
.sheet{{max-width:none;margin:0;padding:0;box-shadow:none;border-radius:0}}
.command-banner{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
.print-btn{{display:none !important}}
h2{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
.progress-fill{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
.polar-card{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
.section{{break-inside:avoid}}
}}
@media(max-width:820px){{
.sec5-layout{{grid-template-columns:1fr}}
.grid{{grid-template-columns:1fr}}
.sheet{{margin:0;border-radius:0}}
.sheet-content{{padding:12px 18px}}
.command-banner{{padding:18px}}
}}
</style></head>
<body><main class="sheet">
<header class="command-banner">
  <div class="banner-top">
    <div class="class-badge-group">
      <span class="class-badge">TACTICAL C-UAS ASSESSMENT</span>
      <span class="class-subbadge">EXERCISE // FOR OFFICIAL PLANNING ONLY</span>
    </div>
    <button class="print-btn" onclick="window.print()" title="Print Brief or Save as PDF">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:text-bottom;margin-right:4px;"><path d="M6 9V2h12v7"></path><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect></svg>
      PRINT / SAVE AS PDF
    </button>
  </div>
  <div class="banner-main">
    <div class="banner-eyebrow">AIRSPACE DECONFLICTION &amp; C-UAS PLANNING SYSTEM</div>
    <h1 class="banner-title">TACTICAL SITE SURVEY BRIEF</h1>
    <div class="banner-meta">
      <span class="meta-item"><span class="meta-label">TARGET VENUE:</span> <b>{escape(venue_name)}</b></span>
      <span class="meta-sep">|</span>
      <span class="meta-item"><span class="meta-label">COORDINATES:</span> <b>{latitude:.5f}, {longitude:.5f}</b></span>
      <span class="meta-sep">|</span>
      <span class="meta-item"><span class="meta-label">PROJECTION:</span> <b>UTM {escape(utm_zone)}</b></span>
      <span class="meta-sep">|</span>
      <span class="meta-item"><span class="meta-label">TIMESTAMP:</span> <b>{live_time_utc}</b></span>
    </div>
  </div>
</header>
<div class="sheet-content">
<section class="section"><h2>1. Mission Header &amp; Venue Baseline</h2>
<div class="grid"><div class="metric"><span class="label">Venue center · WGS84</span><b>{latitude:.6f}, {longitude:.6f}</b></div>
<div class="metric"><span class="label">Local projection</span><b>UTM {escape(utm_zone)}</b></div>
<div class="metric"><span class="label">Seating capacity</span><b>{capacity_html}</b></div></div>
<p class="callout">Academic / planning simulation only. This brief is not authorization for flight or counter-UAS operations.</p></section>
<section class="section"><h2>2. Airspace Control Measures &amp; Local Hazards</h2>
<div class="grid"><div class="metric"><span class="label">Core FAA TFR</span><b>3.0 NM · Surface–3,000 ft AGL</b><span>{tfr_area_sqmi:.1f} sq mi / {tfr_area_km2:.1f} km²</span></div>
<div class="metric"><span class="label">Early-warning buffer</span><b>5.0 NM</b></div>
<div class="metric"><span class="label">Nearby airspace</span><b>{escape(airspace)}</b></div></div></section>
<section class="section"><h2>3. Urban Terrain &amp; Obstacle Summary</h2>
<div class="grid"><div class="metric"><span class="label">Structures analyzed · 1.8 km</span><b>{len(building_features):,}</b></div>
<div class="metric"><span class="label">Mean structure height</span><b>{mean_height:.1f} m</b></div>
<div class="metric"><span class="label">Major obstacles · ≥20 m</span><b>{major_obstacles}</b></div></div>
<p>Local GeoJSON cache: <span class="badge">{escape(cache_status)}</span></p></section>
<section class="section"><h2>4. C-UAS Sensor Laydown Roster</h2>
<table><thead><tr><th>Name</th><th>Sensor type</th><th>Coordinates · Lat, Lon</th><th>Mount · m AGL</th><th>Range · km</th></tr></thead>
<tbody>{sensor_rows}</tbody></table></section>
<section class="section"><h2>5. Coverage &amp; Blind-Spot Vulnerability Assessment</h2>
<div class="progress-box">
  <div class="progress-header">
    <span class="progress-title">TFR CORE (3.0 NM) LINE-OF-SIGHT RADAR/RF COVERAGE</span>
    <span class="progress-status" style="background:{bar_bg};color:{bar_text};">{bar_status} · {coverage_percent:.1f}%</span>
  </div>
  <div class="progress-track">
    <div class="progress-fill" style="width:{min(100.0, max(0.0, coverage_percent)):.1f}%;background:{bar_color};"></div>
  </div>
  <div class="progress-sub">
    <span>Unobstructed Coverage: <b>{coverage_area_km2:.2f} km²</b> of <b>{tfr_area_km2:.1f} km²</b></span>
    <span>Building Blind-Spot Masking: <b>{masked_area_km2:.2f} km²</b></span>
  </div>
</div>
<div class="sec5-layout">
  <div class="grid sec5-grid">
    <div class="metric"><span class="label">Unobstructed TFR coverage</span><b>{coverage_percent:.1f}%</b><span>{coverage_area_km2:.2f} km²</span></div>
    <div class="metric"><span class="label">Masked blind-spot area</span><b>{masked_area_km2:.2f} km²</b><span>{masked_area_km2 * 0.386102:.2f} sq mi</span></div>
    <div class="metric"><span class="label">Uncovered 3–5 NM range gap</span><b>{uncovered_outer_km2:.2f} km²</b><span>Largest masked sector: {escape(primary_sector)}</span></div>
  </div>
  <div class="polar-card">
    <div class="polar-card-title">AIRSPACE POLAR SECTOR SCHEMATIC</div>
    {polar_svg}
    <div class="polar-legend">
      <span class="leg-item"><span class="leg-dot leg-red"></span> 3.0 NM Core</span>
      <span class="leg-item"><span class="leg-dot leg-amber"></span> 5.0 NM Buffer</span>
      <span class="leg-item"><span class="leg-dot leg-green"></span> Sensor</span>
      <span class="leg-item"><span class="leg-dot leg-shadow"></span> Masked Sector</span>
    </div>
  </div>
</div>
</section>
<section class="section"><h2>6. Live Airspace &amp; “Red Air” UAS Stress-Test Log</h2>
<p><b>Airborne tracks inside 5.0 NM:</b> {aircraft_inside_ring}; <b>TFR proximity advisories:</b> {advisory_count}</p>
<p>{escape(drone_log)}</p></section>
<section class="section"><h2>7. Automated Decision-Support Recommendations</h2><ul class="rec-list">{recommendation_html}</ul></section>
<div class="footer">Terrain and building data: © OpenStreetMap contributors. Air-traffic data: OpenSky Network. Academic / planning simulation only.</div>
</div>
</main></body></html>"""


def main() -> None:
    st.set_page_config(
        page_title="Stadium Airspace Planner",
        page_icon="🛰️",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    apply_styles()

    with st.sidebar:
        st.markdown('<div class="eyebrow">FIELD SYSTEM / M3</div>', unsafe_allow_html=True)
        st.title("AIRSPACE\nPLANNER")
        st.caption("STADIUM SECURITY / LOCAL PLANNING")
        venue_choice = st.selectbox(
            "TEST RANGE",
            [*VENUES, "Custom Coordinates"],
            index=0,
        )

        if venue_choice == "Custom Coordinates":
            venue_name = st.text_input("VENUE NAME", value="Custom Test Range")
            latitude = st.number_input("LATITUDE", min_value=-90.0, max_value=90.0, value=40.8135, format="%.6f")
            longitude = st.number_input("LONGITUDE", min_value=-180.0, max_value=180.0, value=-74.0745, format="%.6f")
            capacity = None
            airspace = "User-defined coordinates; verify current charted airspace before operations."
        else:
            venue = VENUES[venue_choice]
            venue_name = venue_choice
            latitude = venue["latitude"]
            longitude = venue["longitude"]
            capacity = venue["capacity"]
            airspace = venue["airspace"]

        venue_slug = venue_cache_slug(venue_name)
        cache_path = DATA_DIR / f"{venue_slug}_buildings.geojson"
        traffic_cache_key = f"{latitude:.4f}:{longitude:.4f}"
        selected_latitude, selected_longitude = apply_tactical_map_selection(
            venue_slug,
            latitude,
            longitude,
        )

        with st.expander("1. TERRAIN & FAA TFR", expanded=True):
            st.caption(airspace)
            if capacity is not None:
                st.caption(f"{capacity:,} seats")
            st.caption("3 NM radius / surface to 3,000 ft AGL · 5 NM early warning")
            refresh_clicked = st.button(
                "LOAD / REFRESH BUILDINGS",
                key=f"refresh_buildings_{venue_slug}",
                width="stretch",
            )
            if refresh_clicked:
                st.session_state["osm_force_refresh"] = True
            structure_count = cached_building_count(cache_path)
            if structure_count is None:
                st.caption("Local building cache could not be read.")
            elif structure_count:
                st.caption(f"{structure_count:,} cached structures")
            else:
                st.caption("No local building cache yet.")
            st.markdown("**MAP LAYERS**")
            show_buildings = st.checkbox("Show 3D Buildings", value=True)
            show_tfr_rings = st.checkbox("Show FAA TFR Rings", value=True)
            show_sensor_coverage = st.checkbox("Show Sensor Coverage", value=True)
            show_blind_spots = st.checkbox("Show Blind-Spot Shadows", value=True)
            show_live_air_traffic = st.checkbox("Show Live Air Traffic", value=True)

        with st.expander("2. C-UAS SENSOR NETWORK", expanded=True):
            active_sensors = render_sensor_controls(
                venue_slug,
                selected_latitude,
                selected_longitude,
            )

        with st.expander("3. LIVE AIR & ROGUE UAS SIMULATOR", expanded=True):
            traffic_results = st.session_state.setdefault("live_traffic_results", {})
            fetch_traffic_clicked = st.button("Fetch Live OpenSky Traffic", width="stretch")
            sample_traffic_clicked = st.button(
                "Load Sample KTEB/KEWR Approach Traffic", width="stretch"
            )
            if fetch_traffic_clicked:
                tracks, traffic_status, traffic_source = fetch_live_traffic(latitude, longitude)
                traffic_results[traffic_cache_key] = {
                    "tracks": tracks,
                    "status": traffic_status,
                    "source": traffic_source,
                }
            elif sample_traffic_clicked:
                tracks = sample_approach_traffic(latitude, longitude)
                traffic_results[traffic_cache_key] = {
                    "tracks": tracks,
                    "status": "Six sample KTEB/KEWR approach tracks loaded",
                    "source": "sample",
                }
            traffic_result = traffic_results.get(traffic_cache_key)
            if traffic_result:
                traffic_tracks = traffic_result["tracks"]
                traffic_status = traffic_result["status"]
                traffic_source = traffic_result["source"]
            else:
                cached_feed = st.session_state.get("opensky_caches", {}).get(traffic_cache_key)
                if cached_feed:
                    traffic_tracks = cached_feed["tracks"]
                    traffic_status = f"Cached OpenSky feed from {cached_feed['fetched_at']}"
                    traffic_source = "cached"
                else:
                    traffic_tracks = []
                    traffic_status = "OpenSky not fetched — use Fetch Live OpenSky Traffic"
                    traffic_source = "idle"
            st.markdown(
                f'<div class="feed-badge feed-{escape(traffic_source)}">{escape(traffic_status)}</div>',
                unsafe_allow_html=True,
            )
            simulation_enabled = st.checkbox("Enable Simulation", value=False)
            approach_bearing = st.slider("Approach Bearing", min_value=0, max_value=359, value=0)
            drone_altitude_m = st.slider("Drone Altitude AGL", min_value=10, max_value=120, value=30)
            track_progress = st.slider("Track Progress %", min_value=0, max_value=100, value=0)

        selected_latitude = float(st.session_state.get("selected_lat", selected_latitude))
        selected_longitude = float(st.session_state.get("selected_lon", selected_longitude))

    core_geometry, buffer_geometry, _ = make_tfr_geometries(latitude, longitude)
    should_fetch_missing_cache = not cache_path.exists()
    force_refresh = st.session_state.pop("osm_force_refresh", False)
    if active_sensors or force_refresh:
        with st.spinner("Loading local building cache or querying OpenStreetMap..."):
            building_features, building_error = load_buildings(
                latitude,
                longitude,
                venue_slug,
                force_refresh=force_refresh,
                allow_fetch=True,
            )
    else:
        if should_fetch_missing_cache:
            with st.spinner("Querying OpenStreetMap for this venue's building cache..."):
                building_features, building_error = load_buildings(
                    latitude,
                    longitude,
                    venue_slug,
                    allow_fetch=True,
                )
        else:
            building_features, building_error = load_buildings(latitude, longitude, venue_slug)

    if building_error:
        active_features, blind_features, sensor_columns = [], [], []
        coverage_percent: float | None = None
        masked_area_km2: float | None = None
    else:
        active_features, blind_features, sensor_columns, coverage_percent, masked_area_km2 = analyze_sensor_coverage(
            latitude,
            longitude,
            core_geometry,
            building_features,
            active_sensors,
        )
    drone_simulation = None
    if simulation_enabled:
        drone_simulation = simulate_rogue_uas(
            latitude,
            longitude,
            approach_bearing,
            drone_altitude_m,
            track_progress,
            active_sensors,
            building_features,
        )

    live_aircraft_for_map = []
    for track in traffic_tracks:
        if track["alert"] == "breach":
            color = [239, 68, 68, 245]
        elif track["alert"] == "advisory":
            color = [251, 191, 36, 245]
        else:
            color = [65, 201, 220, 220]
        altitude_label = f"{track['altitude_ft']:.0f} ft" if track["altitude_ft"] is not None else "ALT N/A"
        label_text = f"{track['callsign']} / {altitude_label}"
        live_aircraft_for_map.append(
            {
                **track,
                "color": color,
                "label": label_text,
                "hover_label": label_text,
            }
        )
    live_aircraft_count = sum(track["distance_nm"] <= 5.0 for track in traffic_tracks)
    proximity_advisory_count = sum(
        track["distance_nm"] <= 5.0 and track["alert"] in {"advisory", "breach"}
        for track in traffic_tracks
    )

    st.markdown('<div class="eyebrow">COUNTER-UAS / AIRSPACE OVERVIEW</div>', unsafe_allow_html=True)
    st.title(venue_name)
    st.markdown(
        f'<div class="venue-sub">{latitude:.5f}, {longitude:.5f} &nbsp; / &nbsp; WGS 84 &nbsp; / &nbsp; '
        f'{"CUSTOM RANGE" if capacity is None else f"{capacity:,} SEATS"}</div>',
        unsafe_allow_html=True,
    )

    area_km2 = math.pi * CORE_RADIUS_METERS**2 / 1_000_000
    area_sqmi = math.pi * CORE_RADIUS_METERS**2 / (1_609.344**2)
    coverage_label = f"{coverage_percent:.1f}%" if coverage_percent is not None else "N/A"
    masked_area_label = f"{masked_area_km2:.2f} km²" if masked_area_km2 is not None else "N/A"
    st.markdown(
        f"""
        <div class="metric-strip">
          <div class="metric"><div class="metric-label">TFR area / 3 NM</div><div class="metric-value">{area_sqmi:.1f} sq mi &nbsp;·&nbsp; {area_km2:.1f} km²</div></div>
          <div class="metric"><div class="metric-label">TFR ceiling</div><div class="metric-value">3,000 ft AGL</div></div>
          <div class="metric"><div class="metric-label">Structures / 1.8 km query</div><div class="metric-value">{len(building_features):,}</div></div>
          <div class="metric"><div class="metric-label">Early warning perimeter</div><div class="metric-value">5.0 NM</div></div>
          <div class="metric"><div class="metric-label">3.0 NM TFR coverage</div><div class="metric-value">{coverage_label}</div></div>
          <div class="metric"><div class="metric-label">Masked blind-spot area</div><div class="metric-value">{masked_area_label}</div></div>
                    <div class="metric"><div class="metric-label">Live aircraft / 5 NM</div><div class="metric-value">{live_aircraft_count} / {proximity_advisory_count} advisory</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    cache_status = (
        f"{len(building_features):,} structures loaded from {cache_path.name}"
        if cache_path.exists()
        else "No local GeoJSON cache; live OpenStreetMap query used"
    )
    report_html = generate_site_survey_html(
        venue_name,
        latitude,
        longitude,
        capacity,
        airspace,
        core_geometry,
        buffer_geometry,
        building_features,
        active_sensors,
        active_features,
        blind_features,
        float(coverage_percent or 0.0),
        float(masked_area_km2 or 0.0),
        traffic_tracks,
        drone_simulation,
        cache_status,
    )

    map_tab, report_tab = st.tabs(["3D TACTICAL MAP", "SITE SURVEY BRIEF"])
    with map_tab:
        if building_error:
            st.warning(f"OpenStreetMap building fetch failed: {building_error}")
        elif not building_features and (force_refresh or active_sensors):
            st.info("No OSM building footprints were returned for this location.")

        if drone_simulation:
            tfr_class = "violation" if drone_simulation["inside_tfr"] else ""
            sensor_class = drone_simulation["sensor_status_kind"]
            st.markdown(
                f'<div class="threat-banner {tfr_class} {sensor_class}">'
                f'<div><div class="threat-label">TFR ZONE CHECK / {drone_simulation["distance_nm"]:.2f} NM</div>'
                f'<div class="threat-value">{escape(drone_simulation["tfr_status"])}</div></div>'
                f'<div><div class="threat-label">SENSOR STATUS</div>'
                f'<div class="threat-value">{escape(drone_simulation["sensor_status"])}</div></div>'
                '</div>',
                unsafe_allow_html=True,
            )

        clickable_ground_grid = make_clickable_ground_grid(latitude, longitude)
        deck = make_deck(
            latitude,
            longitude,
            core_geometry,
            buffer_geometry,
            building_features,
            active_features,
            blind_features,
            sensor_columns,
            show_buildings,
            show_sensor_coverage,
            show_blind_spots,
            show_tfr_rings,
            live_aircraft_for_map if show_live_air_traffic else [],
            drone_simulation,
            clickable_ground_grid,
            {"latitude": selected_latitude, "longitude": selected_longitude},
        )
        st.pydeck_chart(
            deck,
            width="stretch",
            height=670,
            on_select="rerun",
            selection_mode="single-object",
            key="tactical_map",
            alt="Interactive 3D stadium buildings and sensor siting map",
        )
        st.markdown(
            '<div class="status-line">GEOMETRY: UTM PROJECTION • TERRAIN: © OPENSTREETMAP CONTRIBUTORS '
            '• TELEMETRY: OPENSKY NETWORK • ACADEMIC / PLANNING SIMULATION ONLY</div>',
            unsafe_allow_html=True,
        )

    with report_tab:
        st.download_button(
            "Download Site Survey Brief (.html)",
            data=report_html,
            file_name=f"{venue_slug}_tactical_site_survey.html",
            mime="text/html",
            width="stretch",
            key="download_site_survey_brief",
        )
        st.components.v1.html(report_html, height=850, scrolling=True)


if __name__ == "__main__":
    main()