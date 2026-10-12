#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Hashable

import difflib

import numpy as np
import geopandas as gpd
import pandas as pd
import networkx as nx
from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import unary_union
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

try:
    import requests
except ImportError:
    requests = None

# ------------------- constants -------------------

KM_PER_NM = 1.852
EARTH_RADIUS_NM = 3440.065

AMMONIA_LHV_MWH_PER_TON = 5.17

SCHULER_A_EUR_PER_MWH_PER_KM = 0.0006
SCHULER_B_EUR_PER_MWH = 0.2782

SCENARIO_BAU = "bau"
SCENARIO_DECARB = "decarbonised"
DECARB_SCENARIOS = (SCENARIO_BAU, SCENARIO_DECARB)

COST_YEAR_MULTIPLIERS_BAU = {
    2020: 1.00,
    2030: 1.00,
    2040: 0.90,
    2050: 0.80,
}

COST_YEAR_MULTIPLIERS_DECARB = {
    2020: 2.00,
    2030: 1.00,
    2040: 0.90,
    2050: 0.80,
}

EMISSION_ANCHORS_BAU = {
    2020: 10.0,
    2030: 9.0,
    2040: 7.0,
    2050: 5.0,
}

EMISSION_ANCHORS_DECARB = {
    2020: 10.0,
    2030: 8.0,
    2040: 3.0,
    2050: 0.3,
}

EF_RELATIVE_SPREAD = 0.15

DEFAULT_SUEZ_TOLL_EUR = 300_000.0
DEFAULT_PANAMA_TOLL_EUR = 250_000.0

DEFAULT_SUMMARY_YEARS = [2020, 2030, 2040, 2050]
DEFAULT_SUMMARY_DECARB_SCENARIOS = [SCENARIO_BAU, SCENARIO_DECARB]

DEFAULT_CONFIG_FILENAME = "shipping_route_config.json"

# ocean grid defaults
DEFAULT_OCEAN_GRID_STEP_DEG = 1.0
DEFAULT_OCEAN_GRID_LAT_MIN = -75.0
DEFAULT_OCEAN_GRID_LAT_MAX = 75.0
NE_LAND_10M_URL = "https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_land.zip"
OCEAN_CACHE_DIRNAME = ".ocean_cache"

# Increment when the route-map visual style changes. The wrapper can use
# this value through the sidecar metadata file to decide whether an old
# PNG should be regenerated.
ROUTE_PLOT_STYLE_VERSION = "v6_2x2_bottom_centered_legend"


@dataclass
class RouteResult:
    scenario_name: str
    uses_suez: bool
    uses_panama: bool
    distance_nm: float
    path_nodes: List[Hashable]
    cost_eur_per_ton_range: Tuple[float, float, float]
    total_cost_eur_range: Tuple[float, float, float]
    total_cost_with_carbon_eur_range: Optional[Tuple[float, float, float]]
    emission_g_per_tnm_range: Tuple[float, float, float]
    total_co2_tonnes_range: Tuple[float, float, float]


@dataclass
class RoutePath:
    scenario_name: str
    path_nodes: List[Hashable]
    distance_nm: float
    uses_suez: bool
    uses_panama: bool


# ------------------- helpers: scenarios & config -------------------

def canonical_decarb_scenario(name: str) -> str:
    s = str(name).strip().lower()
    if s == "bau":
        return SCENARIO_BAU
    if s in ("decarbonised", "decarbonized"):
        return SCENARIO_DECARB
    raise ValueError(f"Unknown decarbonisation scenario '{name}'")


def load_json_config(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        raise ValueError("Config JSON must contain an object at top level.")
    return cfg


def apply_config_to_args(args: argparse.Namespace, cfg: Dict) -> argparse.Namespace:
    key_map = {
        "lanes": "lanes",
        "ports": "ports",
        "origin": "origin",
        "origin_country": "origin_country",
        "destination": "destination",
        "destination_country": "destination_country",
        "cargo_tonnes": "cargo_tonnes",
        "year": "year",
        "decarbonisation_scenario": "decarbonisation_scenario",
        "suez_toll_eur": "suez_toll_eur",
        "panama_toll_eur": "panama_toll_eur",
        "carbon_price_eur_per_tco2": "carbon_price_eur_per_tco2",
        "mc_iters": "mc_iters",
        "output_prefix": "output_prefix",
        "random_seed": "random_seed",
        "allow_open_ocean_bridge": "allow_open_ocean_bridge",
        "max_bridge_nm": "max_bridge_nm",
        "min_bridge_nm": "min_bridge_nm",
        "plot": "plot",
        "save_plot": "save_plot",
        "plot_output_path": "plot_output_path",
        "plot_dpi": "plot_dpi",
        "ocean_grid_step_deg": "ocean_grid_step_deg",
        "ocean_grid_lat_min": "ocean_grid_lat_min",
        "ocean_grid_lat_max": "ocean_grid_lat_max",
        "land_shapefile_path": "land_shapefile_path",
        "ocean_cache_dir": "ocean_cache_dir",
    }
    for ck, ak in key_map.items():
        if ck in cfg:
            setattr(args, ak, cfg[ck])

    if hasattr(args, "decarbonisation_scenario") and args.decarbonisation_scenario:
        args.decarbonisation_scenario = canonical_decarb_scenario(
            args.decarbonisation_scenario
        )

    return args


def safe_output_name_part(value: object) -> str:
    """Return a filesystem-safe token for generated output filenames."""
    text = "" if value is None else str(value)
    text = text.strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text)
    text = text.strip("._ ")
    return text or "unnamed"


def ensure_parent_dir(file_path: str) -> None:
    """Create the parent directory for a generated output file if needed."""
    parent = os.path.dirname(os.path.abspath(file_path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def default_route_plot_path(output_prefix: str, lanes_path: str, origin_name: str, dest_name: str) -> str:
    """Build the default PNG path next to the shipping CSV/XLSX outputs."""
    lanes_dir = os.path.dirname(os.path.abspath(lanes_path))
    base_prefix = output_prefix
    if not os.path.isabs(base_prefix):
        base_prefix = os.path.join(lanes_dir, base_prefix)
    origin_token = safe_output_name_part(origin_name)
    dest_token = safe_output_name_part(dest_name)
    return f"{base_prefix}_{origin_token}_{dest_token}_routes.png"


def route_plot_metadata_path(output_path: str) -> str:
    """Return the sidecar metadata path for a saved route-map PNG."""
    root, _ = os.path.splitext(os.path.abspath(output_path))
    return f"{root}_plot_metadata.json"


def _route_signature(route: RouteResult) -> Tuple[Tuple[Hashable, ...], float, bool, bool]:
    """Stable signature used to avoid plotting identical canal-scenario paths twice."""
    return (
        tuple(route.path_nodes),
        round(float(route.distance_nm), 6),
        bool(route.uses_suez),
        bool(route.uses_panama),
    )


def _unique_routes_for_plot(routes: List[RouteResult]) -> List[Tuple[RouteResult, List[str]]]:
    """Collapse canal scenarios that generated the exact same plotted route."""
    unique: List[Tuple[RouteResult, List[str]]] = []
    seen: Dict[Tuple[Tuple[Hashable, ...], float, bool, bool], int] = {}

    for route in routes:
        signature = _route_signature(route)
        if signature in seen:
            unique[seen[signature]][1].append(route.scenario_name)
        else:
            seen[signature] = len(unique)
            unique.append((route, [route.scenario_name]))

    # Present the visual and legend in distance order so that the green line is
    # naturally the first route and the black line is naturally the last route.
    unique.sort(key=lambda item: float(item[0].distance_nm))
    return unique


def _title_case_presentable(text: object) -> str:
    """Make labels readable: no underscores and title case except small words."""
    raw = "" if text is None else str(text)
    raw = raw.replace("_", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return ""

    small_words = {"and", "or", "to", "of", "in", "on", "for", "from", "by", "via", "the", "a", "an", "at"}

    def _format_segment(segment: str) -> str:
        words = segment.split()
        formatted = []
        for idx, word in enumerate(words):
            lower = word.lower()
            if idx > 0 and lower in small_words:
                formatted.append(lower)
            elif word.isupper() and len(word) <= 4:
                formatted.append(word)
            else:
                # Handle simple hyphenated names without forcing small words to uppercase.
                hyphen_parts = []
                for part_idx, part in enumerate(word.split("-")):
                    part_lower = part.lower()
                    if idx > 0 and part_idx == 0 and part_lower in small_words:
                        hyphen_parts.append(part_lower)
                    else:
                        hyphen_parts.append(part_lower[:1].upper() + part_lower[1:])
                formatted.append("-".join(hyphen_parts))
        return " ".join(formatted)

    # Keep slash-separated port names readable, e.g. Jabal Az Zannah/Ruways.
    slash_parts = [_format_segment(part.strip()) for part in raw.split("/")]
    return "/".join(part for part in slash_parts if part)


def _scenario_key_for_label(name: object) -> str:
    """Normalize route scenario names for display-label rules."""
    text = "" if name is None else str(name)
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def _combined_route_label(names: List[str]) -> str:
    """Return the display label for one unique plotted route.

    Duplicate scenario paths are collapsed before plotting. For the common
    two-route comparison, the route where canals are available is shown as
    "Access Canals" and the long diversionary route is shown as
    "Avoid Suez and Panama Canals". This keeps the legend readable and
    avoids labels such as "All Canals / Avoid Panama".
    """
    keys = {_scenario_key_for_label(name) for name in names if str(name).strip()}

    # When all_canals and avoid_panama collapse to the same path, Panama was
    # irrelevant to the route but Suez access was still available. Label this
    # as the general canal-access route.
    if "all_canals" in keys:
        return "Access Canals"

    # When avoid_suez and avoid_suez_and_panama collapse to the same path, the
    # plotted route is the long no-canal diversion used for comparison.
    if "avoid_suez_and_panama" in keys or ({"avoid_suez", "avoid_panama"} <= keys):
        return "Avoid Suez and Panama Canals"

    if "avoid_suez" in keys:
        return "Avoid Suez Canal"

    if "avoid_panama" in keys:
        return "Avoid Panama Canal"

    labels = [_title_case_presentable(name) for name in names if str(name).strip()]
    if not labels:
        return "Route"
    if len(labels) == 1:
        return labels[0]
    return " / ".join(labels)


def _route_distance_extreme_indices(
    unique_routes: List[Tuple[RouteResult, List[str]]]
) -> Tuple[Optional[int], Optional[int]]:
    """Return the shortest and longest route indexes by nautical-mile distance."""
    if not unique_routes:
        return None, None

    distances: List[float] = []
    for route, _names in unique_routes:
        try:
            distance = float(route.distance_nm)
        except Exception:
            distance = float("inf")
        distances.append(distance)

    shortest_idx = min(range(len(unique_routes)), key=lambda i: distances[i])
    longest_idx = max(range(len(unique_routes)), key=lambda i: distances[i])
    return shortest_idx, longest_idx


def _route_colors_for_plot(unique_routes: List[Tuple[RouteResult, List[str]]]) -> Dict[int, str]:
    """Assign green to the shortest route and black to the longest route."""
    n = len(unique_routes)
    if n <= 0:
        return {}

    shortest_idx, longest_idx = _route_distance_extreme_indices(unique_routes)
    colors: Dict[int, str] = {}

    middle_palette = [
        "#1f77b4",  # blue
        "#d62728",  # red
        "#9467bd",  # purple
        "#ff7f0e",  # orange
        "#17becf",  # cyan
        "#8c564b",  # brown
    ]

    for idx in range(n):
        colors[idx] = middle_palette[idx % len(middle_palette)]

    if shortest_idx is not None:
        colors[shortest_idx] = "#2ca02c"  # green = shortest route
    if longest_idx is not None and longest_idx != shortest_idx:
        colors[longest_idx] = "#000000"  # black = longest route

    return colors



# ------------------- geometry & graph helpers -------------------

def haversine_distance_nm(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    lon1_r, lat1_r = math.radians(lon1), math.radians(lat1)
    lon2_r, lat2_r = math.radians(lon2), math.radians(lat2)
    dlon = lon2_r - lon1_r
    dlat = lat2_r - lat1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return EARTH_RADIUS_NM * c


def is_in_suez(lon: float, lat: float) -> bool:
    return (31.0 <= lon <= 33.5) and (29.0 <= lat <= 32.0)


def is_in_panama(lon: float, lat: float) -> bool:
    return (-80.5 <= lon <= -79.0) and (8.0 <= lat <= 10.0)


def build_graph_from_lanes(
    lanes_gdf: gpd.GeoDataFrame,
) -> Tuple[nx.Graph, Dict[Hashable, Tuple[float, float]], Dict[str, int]]:
    G = nx.Graph()
    node_coords: Dict[Hashable, Tuple[float, float]] = {}
    coord_to_node: Dict[Tuple[float, float], Hashable] = {}
    canal_edge_counts = {"suez": 0, "panama": 0}
    node_id_counter = 0

    for _, row in lanes_gdf.iterrows():
        geom = row.geometry
        lane_type = row.get("Type", None)

        if geom is None or geom.is_empty:
            continue

        if isinstance(geom, LineString):
            lines = [geom]
        elif isinstance(geom, MultiLineString):
            lines = list(geom.geoms)
        else:
            continue

        for line in lines:
            coords = list(line.coords)
            if len(coords) < 2:
                continue

            for (lon1, lat1), (lon2, lat2) in zip(coords[:-1], coords[1:]):
                key1 = (round(lon1, 5), round(lat1, 5))
                key2 = (round(lon2, 5), round(lat2, 5))

                u = coord_to_node.get(key1)
                if u is None:
                    u = node_id_counter
                    node_id_counter += 1
                    coord_to_node[key1] = u
                    node_coords[u] = (lon1, lat1)
                    G.add_node(u, lon=lon1, lat=lat1, kind="lane")

                v = coord_to_node.get(key2)
                if v is None:
                    v = node_id_counter
                    node_id_counter += 1
                    coord_to_node[key2] = v
                    node_coords[v] = (lon2, lat2)
                    G.add_node(v, lon=lon2, lat=lat2, kind="lane")

                distance_nm = haversine_distance_nm(lon1, lat1, lon2, lat2)
                mid_lon = (lon1 + lon2) / 2.0
                mid_lat = (lat1 + lat2) / 2.0
                canal = None
                if is_in_suez(mid_lon, mid_lat):
                    canal = "suez"
                    canal_edge_counts["suez"] += 1
                elif is_in_panama(mid_lon, mid_lat):
                    canal = "panama"
                    canal_edge_counts["panama"] += 1

                G.add_edge(
                    u,
                    v,
                    distance_nm=distance_nm,
                    lane_type=lane_type,
                    canal=canal,
                    kind="lane",
                )

    return G, node_coords, canal_edge_counts


# ------------------- ports & matching -------------------

def load_ports(wpi_csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(wpi_csv_path)
    if "Main Port Name" not in df.columns:
        raise ValueError("Expected 'Main Port Name' column in WPI CSV.")
    if "Latitude" not in df.columns or "Longitude" not in df.columns:
        raise ValueError("Expected 'Latitude' and 'Longitude' columns in WPI CSV.")
    return df


def find_port(ports_df: pd.DataFrame, name: str, country_code: Optional[str] = None) -> pd.Series:
    df = ports_df
    if country_code is not None and "Country Code" in df.columns:
        df = df[df["Country Code"] == country_code]

    if df.empty:
        raise ValueError("No ports found after applying country filter.")

    names = df["Main Port Name"].astype(str)
    target = name.strip().upper()

    mask_exact = names.str.upper() == target
    if mask_exact.any():
        return df.loc[mask_exact].iloc[0]

    mask_sub = names.str.upper().str.contains(target)
    if mask_sub.any():
        return df.loc[mask_sub].iloc[0]

    candidate_names = names.unique().tolist()
    candidate_upper = [c.upper() for c in candidate_names]
    match_list = difflib.get_close_matches(target, candidate_upper, n=1)
    if not match_list:
        raise ValueError(f"Could not match port name '{name}' in WPI.")
    matched_upper = match_list[0]
    mask_fuzzy = names.str.upper() == matched_upper
    if not mask_fuzzy.any():
        raise ValueError(f"Fuzzy match for '{name}' failed unexpectedly.")
    return df.loc[mask_fuzzy].iloc[0]


def find_nearest_node(
    node_coords: Dict[Hashable, Tuple[float, float]],
    lon: float,
    lat: float,
) -> Tuple[Hashable, float]:
    best_node = None
    best_dist = float("inf")
    for node_id, (nlon, nlat) in node_coords.items():
        d = haversine_distance_nm(lon, lat, nlon, nlat)
        if d < best_dist:
            best_dist = d
            best_node = node_id
    if best_node is None:
        raise RuntimeError("No nodes found in the graph.")
    return best_node, best_dist


# ------------------- cost & emissions -------------------

def interpolate_by_year(anchor: Dict[int, float], year: int) -> float:
    years = sorted(anchor.keys())
    if year <= years[0]:
        return anchor[years[0]]
    if year >= years[-1]:
        return anchor[years[-1]]
    for y0, y1 in zip(years[:-1], years[1:]):
        if y0 <= year <= y1:
            v0 = anchor[y0]
            v1 = anchor[y1]
            ratio = (year - y0) / float(y1 - y0)
            return v0 + ratio * (v1 - v0)
    return anchor[years[-1]]


def cost_multiplier_for_year(year: int, decarb_scenario: str) -> float:
    scenario = canonical_decarb_scenario(decarb_scenario)
    anchors = COST_YEAR_MULTIPLIERS_DECARB if scenario == SCENARIO_DECARB else COST_YEAR_MULTIPLIERS_BAU
    return interpolate_by_year(anchors, year)


def base_ammonia_cost_2030_per_ton_nm(distance_nm: float) -> float:
    if distance_nm <= 0:
        raise ValueError("distance_nm must be positive.")
    distance_km = distance_nm * KM_PER_NM
    cost_2030_eur_per_mwh = SCHULER_A_EUR_PER_MWH_PER_KM * distance_km + SCHULER_B_EUR_PER_MWH
    cost_2030_eur_per_ton_total = cost_2030_eur_per_mwh * AMMONIA_LHV_MWH_PER_TON
    return cost_2030_eur_per_ton_total / distance_nm


def ammonia_cost_per_ton_nm_range(
    distance_nm: float,
    year: int,
    decarb_scenario: str,
) -> Tuple[float, float, float]:
    base_2030 = base_ammonia_cost_2030_per_ton_nm(distance_nm)
    year_mult = cost_multiplier_for_year(year, decarb_scenario)
    mid = base_2030 * year_mult
    low = 0.7 * mid
    high = 1.3 * mid
    return low, mid, high


def emission_factor_mid_g_per_tnm(year: int, decarb_scenario: str) -> float:
    scenario = canonical_decarb_scenario(decarb_scenario)
    anchors = EMISSION_ANCHORS_DECARB if scenario == SCENARIO_DECARB else EMISSION_ANCHORS_BAU
    return interpolate_by_year(anchors, year)


def emission_factor_range_g_per_tnm(
    year: int,
    decarb_scenario: str,
) -> Tuple[float, float, float]:
    mid = emission_factor_mid_g_per_tnm(year, decarb_scenario)
    low = 0.7 * mid
    high = 1.3 * mid
    return low, mid, high


def compute_route_economics(
    scenario_name: str,
    distance_nm: float,
    uses_suez: bool,
    uses_panama: bool,
    cargo_tonnes: float,
    year: int,
    decarb_scenario: str,
    suez_toll_eur: float,
    panama_toll_eur: float,
    carbon_price_eur_per_tco2: Optional[float],
    path_nodes: List[Hashable],
) -> RouteResult:
    c_low, c_mid, c_high = ammonia_cost_per_ton_nm_range(
        distance_nm, year, decarb_scenario
    )
    per_ton_low = c_low * distance_nm
    per_ton_mid = c_mid * distance_nm
    per_ton_high = c_high * distance_nm

    fuel_cost_low = per_ton_low * cargo_tonnes
    fuel_cost_mid = per_ton_mid * cargo_tonnes
    fuel_cost_high = per_ton_high * cargo_tonnes

    toll_total_eur = 0.0
    if uses_suez:
        toll_total_eur += suez_toll_eur
    if uses_panama:
        toll_total_eur += panama_toll_eur

    total_cost_low = fuel_cost_low + toll_total_eur
    total_cost_mid = fuel_cost_mid + toll_total_eur
    total_cost_high = fuel_cost_high + toll_total_eur

    ef_low, ef_mid, ef_high = emission_factor_range_g_per_tnm(year, decarb_scenario)

    co2_low = ef_low / 1e6 * distance_nm * cargo_tonnes
    co2_mid = ef_mid / 1e6 * distance_nm * cargo_tonnes
    co2_high = ef_high / 1e6 * distance_nm * cargo_tonnes

    carbon_cost_range = None
    if carbon_price_eur_per_tco2 is not None and carbon_price_eur_per_tco2 > 0:
        cc_low = co2_low * carbon_price_eur_per_tco2
        cc_mid = co2_mid * carbon_price_eur_per_tco2
        cc_high = co2_high * carbon_price_eur_per_tco2
        total_with_carbon_low = total_cost_low + cc_low
        total_with_carbon_mid = total_cost_mid + cc_mid
        total_with_carbon_high = total_cost_high + cc_high
        carbon_cost_range = (
            total_with_carbon_low,
            total_with_carbon_mid,
            total_with_carbon_high,
        )

    return RouteResult(
        scenario_name=scenario_name,
        uses_suez=uses_suez,
        uses_panama=uses_panama,
        distance_nm=distance_nm,
        path_nodes=path_nodes,
        cost_eur_per_ton_range=(per_ton_low, per_ton_mid, per_ton_high),
        total_cost_eur_range=(total_cost_low, total_cost_mid, total_cost_high),
        total_cost_with_carbon_eur_range=carbon_cost_range,
        emission_g_per_tnm_range=(ef_low, ef_mid, ef_high),
        total_co2_tonnes_range=(co2_low, co2_mid, co2_high),
    )


# ---------------- Monte Carlo ----------------

def run_monte_carlo_for_routes(
    routes: List[RouteResult],
    node_coords: Dict[Hashable, Tuple[float, float]],
    cargo_tonnes: float,
    year: int,
    decarb_scenario: str,
    suez_toll_eur: float,
    panama_toll_eur: float,
    carbon_price_eur_per_tco2: Optional[float],
    mc_iters: int,
    output_prefix: str,
    random_seed: Optional[int] = None,
) -> None:
    if mc_iters <= 0:
        return

    rng = np.random.default_rng(random_seed)
    records: List[Dict[str, object]] = []

    for route in routes:
        distance_nm = route.distance_nm
        base_cost_2030_per_tnm = base_ammonia_cost_2030_per_ton_nm(distance_nm)
        ef_mid = emission_factor_mid_g_per_tnm(year, decarb_scenario)

        # Pre-compute route geometry as WKT
        try:
            points = [
                Point(node_coords[n][0], node_coords[n][1])
                for n in route.path_nodes
            ]
            line = LineString(points)
            route_wkt = line.wkt
        except Exception:
            route_wkt = ""

        for i in range(mc_iters):
            scen = canonical_decarb_scenario(decarb_scenario)
            if scen == SCENARIO_DECARB:
                fuel_price = rng.triangular(500.0, 900.0, 1500.0)
                fuel_price_ref = 900.0
            else:
                fuel_price = rng.triangular(250.0, 500.0, 800.0)
                fuel_price_ref = 500.0

            charter_rate = rng.triangular(15_000.0, 25_000.0, 50_000.0)
            charter_rate_ref = 25_000.0

            speed = rng.triangular(12.0, 14.0, 15.5)
            speed_ref = 14.0

            load_factor = rng.triangular(0.45, 0.75, 0.90)
            load_factor_ref = 0.75

            share_fuel = 0.5
            share_hire = 0.3
            share_other = 0.2

            fuel_cost_factor = (fuel_price / fuel_price_ref) * (speed / speed_ref) ** 3
            hire_cost_factor = (charter_rate / charter_rate_ref) * (speed_ref / speed)
            other_cost_factor = load_factor_ref / load_factor

            year_mult = cost_multiplier_for_year(year, decarb_scenario)
            structural_mid_cost = base_cost_2030_per_tnm * year_mult
            cost_factor = (
                share_fuel * fuel_cost_factor
                + share_hire * hire_cost_factor
                + share_other * other_cost_factor
            )

            cost_per_tnm = structural_mid_cost * cost_factor
            voyage_cost_per_ton = cost_per_tnm * distance_nm
            variable_cost_eur = voyage_cost_per_ton * cargo_tonnes

            base_tolls = 0.0
            if route.uses_suez:
                base_tolls += suez_toll_eur
            if route.uses_panama:
                base_tolls += panama_toll_eur

            toll_factor = rng.triangular(0.9, 1.0, 1.15) if base_tolls > 0 else 1.0
            tolls = base_tolls * toll_factor

            total_cost = variable_cost_eur + tolls

            ef_uncert_factor = rng.triangular(
                1.0 - EF_RELATIVE_SPREAD,
                1.0,
                1.0 + EF_RELATIVE_SPREAD,
            )
            ef_g_per_tnm = ef_mid * ef_uncert_factor
            total_co2_tonnes = ef_g_per_tnm / 1e6 * distance_nm * cargo_tonnes

            carbon_cost = None
            total_cost_with_carbon = None
            if carbon_price_eur_per_tco2 is not None and carbon_price_eur_per_tco2 > 0:
                carbon_cost = total_co2_tonnes * carbon_price_eur_per_tco2
                total_cost_with_carbon = total_cost + carbon_cost

            records.append(
                {
                    "route_scenario": route.scenario_name,
                    "decarb_scenario": canonical_decarb_scenario(decarb_scenario),
                    "iteration": i + 1,
                    "distance_nm": distance_nm,
                    "cargo_tonnes": cargo_tonnes,
                    "uses_suez": route.uses_suez,
                    "uses_panama": route.uses_panama,
                    "cost_eur_per_ton": total_cost / cargo_tonnes,
                    "total_cost_eur": total_cost,
                    "emission_factor_g_per_tnm": ef_g_per_tnm,
                    "total_co2_tonnes": total_co2_tonnes,
                    "carbon_price_eur_per_tco2": carbon_price_eur_per_tco2,
                    "carbon_cost_eur": carbon_cost,
                    "total_cost_with_carbon_eur": total_cost_with_carbon,
                    "route_wkt": route_wkt,
                }
            )

    if not records:
        print("Monte Carlo: no routes to simulate.")
        return

    df = pd.DataFrame(records)
    csv_path = f"{output_prefix}_mc_results.csv"
    ensure_parent_dir(csv_path)
    df.to_csv(csv_path, index=False)
    print(f"\nMonte Carlo results written to {csv_path} (rows: {len(df)}).")

    try:
        xlsx_path = f"{output_prefix}_mc_results.xlsx"
        ensure_parent_dir(xlsx_path)
        df.to_excel(xlsx_path, index=False)
        print(f"Monte Carlo results also written to {xlsx_path}.")
    except Exception as exc:
        print("Could not write Excel file:", exc)

    try:
        summary = df.groupby(
            ["route_scenario", "decarb_scenario"]
        ).agg(
            distance_nm=("distance_nm", "first"),
            mean_cost_eur_per_ton=("cost_eur_per_ton", "mean"),
            p05_cost_eur_per_ton=("cost_eur_per_ton", lambda s: s.quantile(0.05)),
            p50_cost_eur_per_ton=("cost_eur_per_ton", "median"),
            p95_cost_eur_per_ton=("cost_eur_per_ton", lambda s: s.quantile(0.95)),
            mean_ef_g_per_tnm=("emission_factor_g_per_tnm", "mean"),
            p05_ef_g_per_tnm=(
                "emission_factor_g_per_tnm",
                lambda s: s.quantile(0.05),
            ),
            p95_ef_g_per_tnm=(
                "emission_factor_g_per_tnm",
                lambda s: s.quantile(0.95),
            ),
        )

        print("\nMonte Carlo summary per tonne:")
        for (route_name, scen), row in summary.iterrows():
            print(f"  Route {route_name} | scenario={scen}")
            print(f"    Distance: {row['distance_nm']:.0f} nm")
            print(
                "    Cost EUR/t: "
                f"mean {row['mean_cost_eur_per_ton']:.1f}, "
                f"p05 {row['p05_cost_eur_per_ton']:.1f}, "
                f"p50 {row['p50_cost_eur_per_ton']:.1f}, "
                f"p95 {row['p95_cost_eur_per_ton']:.1f}"
            )
            print(
                "    EF gCO₂/t·nm: "
                f"mean {row['mean_ef_g_per_tnm']:.1f}, "
                f"p05 {row['p05_ef_g_per_tnm']:.1f}, "
                f"p95 {row['p95_ef_g_per_tnm']:.1f}"
            )
    except Exception as exc:
        print("Could not compute Monte Carlo summary statistics:", exc)


# ---------------- routing + ocean grid ----------------

def compute_path_distance_nm(
    G: nx.Graph,
    path_nodes: List[Hashable],
    node_coords: Dict[Hashable, Tuple[float, float]],
) -> float:
    distance_nm = 0.0
    for u, v in zip(path_nodes[:-1], path_nodes[1:]):
        data = G.get_edge_data(u, v)
        if data is not None and "distance_nm" in data:
            distance_nm += data["distance_nm"]
        else:
            lon1, lat1 = node_coords[u]
            lon2, lat2 = node_coords[v]
            distance_nm += haversine_distance_nm(lon1, lat1, lon2, lat2)
    return distance_nm


def path_uses_canal(G: nx.Graph, path_nodes: List[Hashable]) -> Tuple[bool, bool]:
    uses_suez = False
    uses_panama = False
    for u, v in zip(path_nodes[:-1], path_nodes[1:]):
        data = G.get_edge_data(u, v)
        if not data:
            continue
        canal = data.get("canal")
        if canal == "suez":
            uses_suez = True
        elif canal == "panama":
            uses_panama = True
    return uses_suez, uses_panama


# ---- land + ocean grid ----

def ensure_ocean_cache_dir(base_path: str) -> str:
    cache_dir = os.path.join(base_path, OCEAN_CACHE_DIRNAME)
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def download_file(url: str, dest_path: str) -> None:
    if requests is None:
        raise RuntimeError("The 'requests' package is required to auto-download Natural Earth data.")
    print(f"Downloading {url} ...")
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)


def load_land_polygons(cache_dir: str, local_path: Optional[str] = None) -> gpd.GeoDataFrame:
    if local_path is not None and os.path.exists(local_path):
        path = local_path
    else:
        zip_path = os.path.join(cache_dir, "ne_10m_land.zip")
        if not os.path.exists(zip_path):
            download_file(NE_LAND_10M_URL, zip_path)
        path = f"zip://{zip_path}"
    print(f"Loading Natural Earth land polygons from {path} ...")
    land_gdf = gpd.read_file(path)
    if land_gdf.crs is None or land_gdf.crs.to_epsg() != 4326:
        land_gdf = land_gdf.to_crs(epsg=4326)
    print(f"Loaded land polygons for land-aware routing (features: {len(land_gdf)})")
    return land_gdf


def build_or_load_ocean_grid(
    land_union,
    cache_dir: str,
    step_deg: float,
    lat_min: float,
    lat_max: float,
) -> Tuple[nx.Graph, Dict[Hashable, Tuple[float, float]]]:
    cache_name = f"ocean_grid_{step_deg:.3f}_lat{int(lat_min)}_{int(lat_max)}.gpickle".replace(".", "p")
    cache_path = os.path.join(cache_dir, cache_name)

    if os.path.exists(cache_path):
        print(f"Loading ocean grid graph from {cache_path} ...")
        G = nx.read_gpickle(cache_path)
        node_coords = {
            n: (d["lon"], d["lat"])
            for n, d in G.nodes(data=True)
        }
        print(
            f"Ocean grid graph has {G.number_of_nodes()} nodes and "
            f"{G.number_of_edges()} edges (loaded from cache)."
        )
        return G, node_coords

    print(f"Building global ocean grid graph at {step_deg:.3f}°; this may take a while but is cached for future runs.")

    G = nx.Graph()
    node_coords: Dict[Hashable, Tuple[float, float]] = {}

    lon_min = -180.0
    lon_max = 180.0

    lats = np.arange(lat_min, lat_max + 1e-9, step_deg)
    lons = np.arange(lon_min, lon_max + 1e-9, step_deg)

    print(
        f"Generating ocean grid nodes at {step_deg}° resolution "
        f"(lat cells: {len(lats)}, lon cells: {len(lons)}, total cells: {len(lats) * len(lons)}) ..."
    )

    # classify water cells
    water_index: Dict[Tuple[int, int], Hashable] = {}
    node_id_counter = 0
    for i, lat in enumerate(lats):
        for j, lon in enumerate(lons):
            # treat Suez and Panama boxes as non-grid (only lanes allowed)
            if is_in_suez(lon, lat) or is_in_panama(lon, lat):
                continue
            pt = Point(lon, lat)
            if land_union.contains(pt):
                continue
            node_id = f"o_{node_id_counter}"
            node_id_counter += 1
            G.add_node(node_id, lon=lon, lat=lat, kind="ocean")
            node_coords[node_id] = (lon, lat)
            water_index[(i, j)] = node_id

    print(f"Ocean grid water nodes: {len(water_index)}  – building adjacency ...")

    # connect neighbours
    for (i, j), nid in water_index.items():
        lon1, lat1 = node_coords[nid]
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if di == 0 and dj == 0:
                    continue
                ni, nj = i + di, j + dj
                neigh_id = water_index.get((ni, nj))
                if neigh_id is None:
                    continue
                if G.has_edge(nid, neigh_id):
                    continue
                lon2, lat2 = node_coords[neigh_id]
                dist_nm = haversine_distance_nm(lon1, lat1, lon2, lat2)
                G.add_edge(
                    nid,
                    neigh_id,
                    distance_nm=dist_nm,
                    lane_type="OceanGrid",
                    canal=None,
                    kind="ocean",
                )

    print(f"Ocean grid edges: {G.number_of_edges()}")

    # store grid metadata on graph
    G.graph["lat_min"] = float(lat_min)
    G.graph["lat_max"] = float(lat_max)
    G.graph["lon_min"] = float(lon_min)
    G.graph["lon_max"] = float(lon_max)
    G.graph["step_deg"] = float(step_deg)

    nx.write_gpickle(G, cache_path)
    print(f"Saved ocean grid graph to {cache_path}")
    return G, node_coords


def connect_lanes_to_ocean_grid(
    G_lanes: nx.Graph,
    lane_coords: Dict[Hashable, Tuple[float, float]],
    G_ocean: nx.Graph,
    ocean_coords: Dict[Hashable, Tuple[float, float]],
    max_search_radius_cells: int = 3,
) -> Tuple[nx.Graph, Dict[Hashable, Tuple[float, float]]]:
    # compose graphs (disjoint node sets allowed)
    G_union = nx.compose(G_lanes, G_ocean)
    node_coords: Dict[Hashable, Tuple[float, float]] = dict(lane_coords)
    node_coords.update(ocean_coords)

    lat_min = G_ocean.graph["lat_min"]
    lat_max = G_ocean.graph["lat_max"]
    lon_min = G_ocean.graph["lon_min"]
    step = G_ocean.graph["step_deg"]

    # build index from (i,j) -> list of node ids
    index: Dict[Tuple[int, int], List[Hashable]] = {}
    for nid, (lon, lat) in ocean_coords.items():
        i = int(round((lat - lat_min) / step))
        j = int(round((lon - lon_min) / step))
        index.setdefault((i, j), []).append(nid)

    added_edges = 0
    for lane_id, (lon, lat) in lane_coords.items():
        if not (lat_min <= lat <= lat_max):
            continue
        i0 = int(round((lat - lat_min) / step))
        j0 = int(round((lon - lon_min) / step))
        best_node = None
        best_dist = float("inf")

        for radius in range(max_search_radius_cells + 1):
            found_any = False
            for di in range(-radius, radius + 1):
                for dj in range(-radius, radius + 1):
                    ii = i0 + di
                    jj = j0 + dj
                    key = (ii, jj)
                    candidates = index.get(key)
                    if not candidates:
                        continue
                    for oid in candidates:
                        lon2, lat2 = ocean_coords[oid]
                        d_nm = haversine_distance_nm(lon, lat, lon2, lat2)
                        if d_nm < best_dist:
                            best_dist = d_nm
                            best_node = oid
                            found_any = True
            if found_any:
                break

        if best_node is None:
            continue

        G_union.add_edge(
            lane_id,
            best_node,
            distance_nm=best_dist,
            lane_type="LaneToGrid",
            canal=None,
            kind="connector",
        )
        added_edges += 1

    print(f"Added {added_edges} lane-to-ocean connector edges.")
    print(
        f"Union graph (lanes + ocean grid) has {G_union.number_of_nodes()} nodes and "
        f"{G_union.number_of_edges()} edges."
    )

    return G_union, node_coords


# ---- path planning ----

def plan_single_route(
    G: nx.Graph,
    source_node: Hashable,
    target_node: Hashable,
    remove_suez: bool,
    remove_panama: bool,
) -> Optional[List[Hashable]]:
    if remove_suez or remove_panama:
        def edge_ok(u, v):
            data = G.edges[u, v]
            canal = data.get("canal")
            if remove_suez and canal == "suez":
                return False
            if remove_panama and canal == "panama":
                return False
            return True

        G_work = nx.subgraph_view(G, filter_edge=edge_ok)
    else:
        G_work = G

    try:
        path_nodes = nx.shortest_path(
            G_work,
            source=source_node,
            target=target_node,
            weight="distance_nm",
        )
        return path_nodes
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def compute_paths_for_canal_scenarios(
    G: nx.Graph,
    node_coords: Dict[Hashable, Tuple[float, float]],
    source_node: Hashable,
    target_node: Hashable,
) -> Dict[str, RoutePath]:
    scenarios = [
        ("all_canals", False, False),
        ("avoid_suez", True, False),
        ("avoid_panama", False, True),
        ("avoid_suez_and_panama", True, True),
    ]
    paths: Dict[str, RoutePath] = {}

    for name, remove_suez, remove_panama in scenarios:
        path_nodes = plan_single_route(
            G,
            source_node,
            target_node,
            remove_suez=remove_suez,
            remove_panama=remove_panama,
        )
        if path_nodes is None:
            continue
        distance_nm = compute_path_distance_nm(G, path_nodes, node_coords)
        uses_suez, uses_panama = path_uses_canal(G, path_nodes)
        paths[name] = RoutePath(
            scenario_name=name,
            path_nodes=path_nodes,
            distance_nm=distance_nm,
            uses_suez=uses_suez,
            uses_panama=uses_panama,
        )

    return paths


def plan_routes_with_canal_scenarios(
    paths_by_scenario: Dict[str, RoutePath],
    cargo_tonnes: float,
    year: int,
    decarb_scenario: str,
    suez_toll_eur: float,
    panama_toll_eur: float,
    carbon_price_eur_per_tco2: Optional[float],
) -> List[RouteResult]:
    results: List[RouteResult] = []
    for name, rp in paths_by_scenario.items():
        result = compute_route_economics(
            scenario_name=name,
            distance_nm=rp.distance_nm,
            uses_suez=rp.uses_suez,
            uses_panama=rp.uses_panama,
            cargo_tonnes=cargo_tonnes,
            year=year,
            decarb_scenario=decarb_scenario,
            suez_toll_eur=suez_toll_eur,
            panama_toll_eur=panama_toll_eur,
            carbon_price_eur_per_tco2=carbon_price_eur_per_tco2,
            path_nodes=rp.path_nodes,
        )
        results.append(result)
    return results


# ---------------- plotting ----------------

def plot_routes(
    lanes_gdf: gpd.GeoDataFrame,
    land_gdf: gpd.GeoDataFrame,
    node_coords: Dict[Hashable, Tuple[float, float]],
    routes: List[RouteResult],
    origin_name: str,
    dest_name: str,
    origin_lon: float,
    origin_lat: float,
    dest_lon: float,
    dest_lat: float,
    output_path: Optional[str] = None,
    show: bool = True,
    dpi: int = 250,
) -> Optional[str]:
    if not routes:
        print("No routes to plot.")
        return None

    # Collapse cases such as all_canals == avoid_panama and avoid_suez ==
    # avoid_suez_and_panama. Only visually distinct paths are drawn.
    unique_routes = _unique_routes_for_plot(routes)
    route_colors = _route_colors_for_plot(unique_routes)
    shortest_route_idx, longest_route_idx = _route_distance_extreme_indices(unique_routes)

    fig, ax = plt.subplots(figsize=(14, 6.7))

    # Land/coastline context.
    try:
        land_gdf.boundary.plot(ax=ax, linewidth=0.5, alpha=0.65, color="#5DA5DA", zorder=1)
    except Exception:
        pass

    # Background shipping-lane network. Keep it subtle so the selected routes
    # and port markers remain visually dominant.
    try:
        lanes_gdf.plot(ax=ax, linewidth=0.25, alpha=0.18, color="0.70", zorder=2)
    except Exception:
        pass

    legend_handles: List[Line2D] = []
    legend_labels: List[str] = []
    plotted_route_labels: List[str] = []

    for idx, (route, represented_names) in enumerate(unique_routes):
        pts = (
            [(origin_lon, origin_lat)]
            + [(node_coords[n][0], node_coords[n][1]) for n in route.path_nodes]
            + [(dest_lon, dest_lat)]
        )
        line = LineString(pts)
        color = route_colors[idx]
        label = _combined_route_label(represented_names)
        plotted_route_labels.append(label)

        gpd.GeoSeries([line], crs="EPSG:4326").plot(
            ax=ax,
            linewidth=1.0,
            alpha=0.95,
            color=color,
            zorder=5 + idx,
        )
        legend_handles.append(Line2D([0], [0], color=color, lw=2.9))
        # Keep route legend entries short. Color communicates shortest/longest:
        # green is the shortest route, black is the longest route.
        legend_labels.append(label)

    # Port markers: colored circles only. Names are placed in the combined legend.
    export_color = "#FFD000"
    import_color = "#00FF22"
    ax.scatter(
        [origin_lon],
        [origin_lat],
        s=55,
        marker="o",
        facecolor=export_color,
        edgecolor="black",
        linewidth=0.8,
        zorder=30,
    )
    ax.scatter(
        [dest_lon],
        [dest_lat],
        s=55,
        marker="o",
        facecolor=import_color,
        edgecolor="black",
        linewidth=0.8,
        zorder=31,
    )

    route_legend_handles = list(legend_handles)
    route_legend_labels = list(legend_labels)
    port_legend_handles = [
        Line2D(
            [0], [0],
            marker="o",
            color="none",
            markerfacecolor=export_color,
            markeredgecolor="black",
            markersize=8,
        ),
        Line2D(
            [0], [0],
            marker="o",
            color="none",
            markerfacecolor=import_color,
            markeredgecolor="black",
            markersize=8,
        ),
    ]
    port_legend_labels = [
        f"Export Port: {_title_case_presentable(origin_name)}",
        f"Import Port: {_title_case_presentable(dest_name)}",
    ]

    # Matplotlib fills multi-column legends column-by-column. By ordering all
    # route entries first and all port entries second, ncol=2 gives a clean
    # 2-column layout:
    #   route 1    export port
    #   route 2    import port
    # If a route set has a different number of unique paths, pad the shorter
    # column with invisible entries so the ports remain stacked together.
    max_legend_rows = max(len(route_legend_handles), len(port_legend_handles), 1)
    blank_handle = Line2D([0], [0], color="none", linewidth=0, alpha=0.0)
    while len(route_legend_handles) < max_legend_rows:
        route_legend_handles.append(blank_handle)
        route_legend_labels.append(" ")
    while len(port_legend_handles) < max_legend_rows:
        port_legend_handles.append(blank_handle)
        port_legend_labels.append(" ")

    combined_legend_handles = route_legend_handles + port_legend_handles
    combined_legend_labels = route_legend_labels + port_legend_labels

    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(
        f"Routes: {_title_case_presentable(origin_name)} → {_title_case_presentable(dest_name)}"
    )

    # One combined 2-column legend, centered below the map. The left column is
    # the selected lane-route set and the right column is the export/import port set.
    ax.legend(
        combined_legend_handles,
        combined_legend_labels,
        title="Routes and Ports",
        loc="upper center",
        bbox_to_anchor=(0.5, -0.12),
        ncol=2,
        frameon=True,
        framealpha=0.94,
        fontsize=9,
        title_fontsize=10,
        handlelength=2.6,
        handletextpad=0.8,
        columnspacing=2.2,
        labelspacing=0.8,
        borderaxespad=0.0,
    )
    fig.subplots_adjust(bottom=0.26)

    saved_path = None
    if output_path:
        saved_path = os.path.abspath(output_path)
        ensure_parent_dir(saved_path)
        fig.savefig(saved_path, dpi=dpi, bbox_inches="tight")
        print(f"Route plot PNG written to {saved_path}")

        metadata = {
            "plot_style_version": ROUTE_PLOT_STYLE_VERSION,
            "origin": origin_name,
            "destination": dest_name,
            "route_count_input": len(routes),
            "route_count_plotted": len(unique_routes),
            "plotted_route_labels": plotted_route_labels,
            "route_colors": {
                plotted_route_labels[idx]: route_colors[idx]
                for idx in range(len(plotted_route_labels))
            },
            "deduplicated_routes": [
                {
                    "plotted_label": _combined_route_label(names),
                    "represented_scenarios": names,
                    "distance_nm": route.distance_nm,
                    "uses_suez": route.uses_suez,
                    "uses_panama": route.uses_panama,
                }
                for route, names in unique_routes
            ],
        }
        try:
            metadata_path = route_plot_metadata_path(saved_path)
            ensure_parent_dir(metadata_path)
            with open(metadata_path, "w", encoding="utf-8") as handle:
                json.dump(metadata, handle, indent=2, ensure_ascii=False)
            print(f"Route plot metadata written to {metadata_path}")
        except Exception as exc:
            print("Could not write route plot metadata:", exc)

    if show:
        plt.show()
    else:
        plt.close(fig)

    return saved_path


# ---------------- CLI ----------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ammonia shipping routes with land-aware global ocean grid."
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help=f"Optional path to JSON config. If omitted, tries '{DEFAULT_CONFIG_FILENAME}'.",
    )
    parser.add_argument(
        "--lanes",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "Trade_version", "Shipping_Lanes_v1.geojson"),
        help="Path to Shipping_Lanes_v1.geojson.",
    )
    parser.add_argument(
        "--ports",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "Trade_version", "UpdatedPub150.csv"),
        help="Path to World Port Index CSV.",
    )
    parser.add_argument(
        "--origin",
        default="San Antonio",
        help="Origin port name.",
    )
    parser.add_argument(
        "--origin-country",
        default="Chile",
        help="Optional origin country filter.",
    )
    parser.add_argument(
        "--destination",
        default="Rotterdam",
        help="Destination port name.",
    )
    parser.add_argument(
        "--destination-country",
        default="Netherlands",
        help="Optional destination country filter.",
    )
    parser.add_argument(
        "--cargo-tonnes",
        type=float,
        default=50_000.0,
        help="Cargo [t].",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=2030,
        help="Analysis year.",
    )
    parser.add_argument(
        "--decarbonisation-scenario",
        choices=list(DECARB_SCENARIOS),
        default=SCENARIO_BAU,
        help="BAU or decarbonised.",
    )
    parser.add_argument(
        "--suez-toll-eur",
        type=float,
        default=DEFAULT_SUEZ_TOLL_EUR,
        help="Suez toll [EUR].",
    )
    parser.add_argument(
        "--panama-toll-eur",
        type=float,
        default=DEFAULT_PANAMA_TOLL_EUR,
        help="Panama toll [EUR].",
    )
    parser.add_argument(
        "--carbon-price-eur-per-tco2",
        type=float,
        default=None,
        help="Optional carbon price [EUR/tCO2].",
    )
    parser.add_argument(
        "--mc-iters",
        type=int,
        default=1000,
        help="Monte Carlo iterations per route.",
    )
    parser.add_argument(
        "--output-prefix",
        default="ammonia_route",
        help="Prefix for CSV/XLSX outputs.",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=42,
        help="Random seed.",
    )
    parser.add_argument(
        "--allow-open-ocean-bridge",
        action="store_true",
        default=True,
        help="(Legacy, ignored) retained for config compatibility.",
    )
    parser.add_argument(
        "--max-bridge-nm",
        type=float,
        default=2500.0,
        help="(Legacy, ignored).",
    )
    parser.add_argument(
        "--min-bridge-nm",
        type=float,
        default=50.0,
        help="(Legacy, ignored).",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        default=True,
        help="Show the route map interactively.",
    )
    parser.add_argument(
        "--no-plot",
        dest="plot",
        action="store_false",
        help="Do not show the route map interactively.",
    )
    parser.add_argument(
        "--save-plot",
        dest="save_plot",
        action="store_true",
        default=True,
        help="Save the route map PNG next to the CSV/XLSX outputs. Enabled by default.",
    )
    parser.add_argument(
        "--no-save-plot",
        dest="save_plot",
        action="store_false",
        help="Do not save the route map PNG.",
    )
    parser.add_argument(
        "--plot-output-path",
        default=None,
        help="Optional explicit PNG path for the saved route map. If omitted, uses output_prefix + origin + destination + '_routes.png'.",
    )
    parser.add_argument(
        "--plot-dpi",
        type=int,
        default=250,
        help="DPI for the saved route map PNG.",
    )
    parser.add_argument(
        "--ocean-grid-step-deg",
        type=float,
        default=DEFAULT_OCEAN_GRID_STEP_DEG,
        help="Ocean grid resolution in degrees (e.g. 0.5). Smaller is slower.",
    )
    parser.add_argument(
        "--ocean-grid-lat-min",
        type=float,
        default=DEFAULT_OCEAN_GRID_LAT_MIN,
        help="Minimum latitude for ocean grid.",
    )
    parser.add_argument(
        "--ocean-grid-lat-max",
        type=float,
        default=DEFAULT_OCEAN_GRID_LAT_MAX,
        help="Maximum latitude for ocean grid.",
    )
    parser.add_argument(
        "--land-shapefile-path",
        type=str,
        default=None,
        help="Optional local path to ne_10m_land shapefile or ZIP. If omitted, auto-downloads.",
    )
    parser.add_argument(
        "--ocean-cache-dir",
        type=str,
        default=None,
        help="Optional directory for caching ocean grid. Default: project_dir/.ocean_cache",
    )

    args = parser.parse_args()

    # ---- config ----
    cfg = None
    config_path = None

    if args.config is not None:
        if os.path.exists(args.config):
            config_path = args.config
        else:
            print(f"Warning: config file '{args.config}' not found; ignoring.")
    else:
        candidate_paths: List[str] = []
        try:
            lanes_dir = os.path.dirname(os.path.abspath(args.lanes))
            candidate_paths.append(os.path.join(lanes_dir, DEFAULT_CONFIG_FILENAME))
        except Exception:
            pass
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            candidate_paths.append(os.path.join(script_dir, DEFAULT_CONFIG_FILENAME))
        except Exception:
            pass
        for cand in candidate_paths:
            if os.path.exists(cand):
                config_path = cand
                print(f"Using config file: {config_path}")
                break

    if config_path is not None:
        try:
            cfg = load_json_config(config_path)
            args = apply_config_to_args(args, cfg)
        except Exception as exc:
            print(f"Error loading config '{config_path}': {exc}")
            cfg = None

    args.decarbonisation_scenario = canonical_decarb_scenario(args.decarbonisation_scenario)

    summary_years = DEFAULT_SUMMARY_YEARS
    summary_decarbon_scenarios = DEFAULT_SUMMARY_DECARB_SCENARIOS
    if cfg is not None:
        if "summary_years" in cfg:
            summary_years = list(cfg["summary_years"])
        if "summary_decarbonisation_scenarios" in cfg:
            summary_decarbon_scenarios = [
                canonical_decarb_scenario(s) for s in cfg["summary_decarbonisation_scenarios"]
            ]
    else:
        summary_decarbon_scenarios = DEFAULT_SUMMARY_DECARB_SCENARIOS

    # ---- load data ----
    print("Loading shipping lanes...")
    lanes_gdf = gpd.read_file(args.lanes)

    print("Loading World Port Index...")
    ports_df = load_ports(args.ports)

    origin_row = find_port(ports_df, args.origin, args.origin_country)
    dest_row = find_port(ports_df, args.destination, args.destination_country)

    origin_lon = float(origin_row["Longitude"])
    origin_lat = float(origin_row["Latitude"])
    dest_lon = float(dest_row["Longitude"])
    dest_lat = float(dest_row["Latitude"])

    origin_name = str(origin_row["Main Port Name"])
    dest_name = str(dest_row["Main Port Name"])

    print(f"Origin resolved to: {origin_name} ({origin_lat:.4f}, {origin_lon:.4f})")
    print(f"Destination resolved to: {dest_name} ({dest_lat:.4f}, {dest_lon:.4f})")

    # ---- lane graph ----
    print("Building graph from shipping lanes...")
    G_lanes, lane_coords, canal_counts = build_graph_from_lanes(lanes_gdf)
    print(
        f"Lane graph has {G_lanes.number_of_nodes()} nodes and {G_lanes.number_of_edges()} edges."
    )
    print(
        f"Suez-tagged lane edges: {canal_counts['suez']}, "
        f"Panama-tagged lane edges: {canal_counts['panama']}"
    )

    origin_node, origin_offset_nm = find_nearest_node(lane_coords, origin_lon, origin_lat)
    dest_node, dest_offset_nm = find_nearest_node(lane_coords, dest_lon, dest_lat)

    print(
        f"Nearest lane node to origin: id={origin_node}, offset ≈ {origin_offset_nm:.1f} nm"
    )
    print(
        f"Nearest lane node to destination: id={dest_node}, offset ≈ {dest_offset_nm:.1f} nm"
    )

    # ---- land polygons & ocean grid ----
    project_dir = os.path.dirname(os.path.abspath(args.lanes))
    cache_dir = args.ocean_cache_dir or ensure_ocean_cache_dir(project_dir)

    land_gdf = load_land_polygons(cache_dir, args.land_shapefile_path)
    land_union_geom = unary_union(land_gdf.geometry)

    G_ocean, ocean_coords = build_or_load_ocean_grid(
        land_union=land_union_geom,
        cache_dir=cache_dir,
        step_deg=args.ocean_grid_step_deg,
        lat_min=args.ocean_grid_lat_min,
        lat_max=args.ocean_grid_lat_max,
    )

    # ---- connect lanes to ocean grid ----
    G_union, node_coords = connect_lanes_to_ocean_grid(
        G_lanes,
        lane_coords,
        G_ocean,
        ocean_coords,
        max_search_radius_cells=3,
    )

    # ---- compute geometric paths for each canal scenario (once) ----
    paths_by_scenario = compute_paths_for_canal_scenarios(
        G_union,
        node_coords,
        origin_node,
        dest_node,
    )

    if not paths_by_scenario:
        print("No route found for any canal scenario.")
        return

    # ---- deterministic economics for main year & scenario ----
    results = plan_routes_with_canal_scenarios(
        paths_by_scenario=paths_by_scenario,
        cargo_tonnes=args.cargo_tonnes,
        year=args.year,
        decarb_scenario=args.decarbonisation_scenario,
        suez_toll_eur=args.suez_toll_eur,
        panama_toll_eur=args.panama_toll_eur,
        carbon_price_eur_per_tco2=args.carbon_price_eur_per_tco2,
    )

    results_sorted = sorted(results, key=lambda r: r.total_cost_eur_range[1])

    print("\n=== Route options (deterministic ranges) ===")
    for r in results_sorted:
        d_nm = r.distance_nm
        d_km = d_nm * KM_PER_NM
        per_t_low, per_t_mid, per_t_high = r.cost_eur_per_ton_range
        total_low, total_mid, total_high = r.total_cost_eur_range
        ef_low, ef_mid, ef_high = r.emission_g_per_tnm_range
        co2_low, co2_mid, co2_high = r.total_co2_tonnes_range

        print(f"\nScenario: {r.scenario_name}")
        print(f"  Uses Suez?   {'yes' if r.uses_suez else 'no'}")
        print(f"  Uses Panama? {'yes' if r.uses_panama else 'no'}")
        print(f"  Distance:    {d_nm:,.0f} nm  (~{d_km:,.0f} km)")

        print("  Cost per tonne [EUR/t]:")
        print(
            f"    low/mid/high: "
            f"{per_t_low:,.1f} / {per_t_mid:,.1f} / {per_t_high:,.1f}"
        )
        print("  Total voyage cost [EUR] (fuel + tolls):")
        print(
            f"    low/mid/high: "
            f"{total_low:,.0f} / {total_mid:,.0f} / {total_high:,.0f}"
        )

        if r.total_cost_with_carbon_eur_range is not None:
            tc_low, tc_mid, tc_high = r.total_cost_with_carbon_eur_range
            print("  Total cost incl. carbon [EUR]:")
            print(
                f"    low/mid/high: "
                f"{tc_low:,.0f} / {tc_mid:,.0f} / {tc_high:,.0f}"
            )

        print(
            f"  Emission factor [gCO₂/t·nm]: "
            f"{ef_low:.2f} / {ef_mid:.2f} / {ef_high:.2f}"
        )
        print(
            f"  Total CO2 [t]: "
            f"{co2_low:,.1f} / {co2_mid:,.1f} / {co2_high:,.1f}"
        )

    # ---- multi-year, multi-scenario summary (BAU + decarb) ----
    print("\nBuilding multi-year, multi-scenario summary (BAU + decarb)...")
    summary_records: List[Dict[str, object]] = []

    for yr in summary_years:
        for scen in summary_decarbon_scenarios:
            sub_results = plan_routes_with_canal_scenarios(
                paths_by_scenario=paths_by_scenario,
                cargo_tonnes=args.cargo_tonnes,
                year=yr,
                decarb_scenario=scen,
                suez_toll_eur=args.suez_toll_eur,
                panama_toll_eur=args.panama_toll_eur,
                carbon_price_eur_per_tco2=args.carbon_price_eur_per_tco2,
            )
            if not sub_results:
                continue
            best = min(sub_results, key=lambda r: r.total_cost_eur_range[1])
            per_t_low, per_t_mid, per_t_high = best.cost_eur_per_ton_range
            total_low, total_mid, total_high = best.total_cost_eur_range
            ef_low, ef_mid, ef_high = best.emission_g_per_tnm_range
            co2_low, co2_mid, co2_high = best.total_co2_tonnes_range

            summary_records.append(
                {
                    "origin": origin_name,
                    "origin_country": args.origin_country,
                    "destination": dest_name,
                    "destination_country": args.destination_country,
                    "year": yr,
                    "decarbonisation_scenario": scen,
                    "route_scenario": best.scenario_name,
                    "distance_nm": best.distance_nm,
                    "distance_km": best.distance_nm * KM_PER_NM,
                    "cost_eur_per_t_low": per_t_low,
                    "cost_eur_per_t_mid": per_t_mid,
                    "cost_eur_per_t_high": per_t_high,
                    "total_cost_eur_low": total_low,
                    "total_cost_eur_mid": total_mid,
                    "total_cost_eur_high": total_high,
                    "ef_g_per_tnm_low": ef_low,
                    "ef_g_per_tnm_mid": ef_mid,
                    "ef_g_per_tnm_high": ef_high,
                    "total_co2_t_low": co2_low,
                    "total_co2_t_mid": co2_mid,
                    "total_co2_t_high": co2_high,
                }
            )

    if summary_records:
        summary_df = pd.DataFrame(summary_records)
        lanes_dir = os.path.dirname(os.path.abspath(args.lanes))
        base_prefix = args.output_prefix
        if not os.path.isabs(base_prefix):
            base_prefix = os.path.join(lanes_dir, base_prefix)
        origin_token = safe_output_name_part(origin_name)
        dest_token = safe_output_name_part(dest_name)
        base_name = f"{base_prefix}_{origin_token}_{dest_token}"
        summary_csv = f"{base_name}_summary.csv"
        summary_xlsx = f"{base_name}_summary.xlsx"

        ensure_parent_dir(summary_csv)
        summary_df.to_csv(summary_csv, index=False)
        print(f"Summary (multi-year, BAU+decarb) written to {summary_csv}")
        try:
            ensure_parent_dir(summary_xlsx)
            summary_df.to_excel(summary_xlsx, index=False)
            print(f"Summary also written to {summary_xlsx}")
        except Exception as exc:
            print("Could not write summary Excel file:", exc)
    else:
        print("No routes found for multi-year summary.")

    # ---- Monte Carlo ----
    if args.mc_iters > 0:
        lanes_dir = os.path.dirname(os.path.abspath(args.lanes))
        base_prefix = args.output_prefix
        if not os.path.isabs(base_prefix):
            base_prefix = os.path.join(lanes_dir, base_prefix)
        origin_token = safe_output_name_part(origin_name)
        dest_token = safe_output_name_part(dest_name)
        prefix = (
            f"{base_prefix}_"
            f"{origin_token}_"
            f"{dest_token}_"
            f"{args.year}_"
            f"{args.decarbonisation_scenario}"
        )
        print("\nRunning Monte Carlo simulation...")
        run_monte_carlo_for_routes(
            routes=results_sorted,
            node_coords=node_coords,
            cargo_tonnes=args.cargo_tonnes,
            year=args.year,
            decarb_scenario=args.decarbonisation_scenario,
            suez_toll_eur=args.suez_toll_eur,
            panama_toll_eur=args.panama_toll_eur,
            carbon_price_eur_per_tco2=args.carbon_price_eur_per_tco2,
            mc_iters=args.mc_iters,
            output_prefix=prefix,
            random_seed=args.random_seed,
        )

    # ---- plotting / PNG export ----
    if args.plot or args.save_plot:
        if args.plot:
            print("\nPlotting canal-scenario routes...")
        elif args.save_plot:
            print("\nSaving canal-scenario route plot...")

        plot_png_path = args.plot_output_path
        if args.save_plot and not plot_png_path:
            plot_png_path = default_route_plot_path(args.output_prefix, args.lanes, origin_name, dest_name)

        plot_routes(
            lanes_gdf=lanes_gdf,
            land_gdf=land_gdf,
            node_coords=node_coords,
            routes=results_sorted,
            origin_name=origin_name,
            dest_name=dest_name,
            origin_lon=origin_lon,
            origin_lat=origin_lat,
            dest_lon=dest_lon,
            dest_lat=dest_lat,
            output_path=plot_png_path if args.save_plot else None,
            show=bool(args.plot),
            dpi=int(args.plot_dpi),
        )

if __name__ == "__main__":
    main()
