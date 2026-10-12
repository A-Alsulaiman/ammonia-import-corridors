# -*- coding: utf-8 -*-
"""
country_siting_runner_CELL_ADAPTIVE_CONSTRAINED_HEATMAP_OPT_C_v30_ports.py  (v3.4)

Key fixes for the heatmap+ports overlay:
- Use the v29 heatmap runner only (no alternate runner fallback in this wrapper).
- Capture the *new* figure created by the renderer (diff figures before/after).
- Pick the heatmap axes by its title ("Heatmap — <ISO3> — ..."), not the colorbar or a prior plot.
- Plot ports on that axes with GeoPandas after forcing CRS=EPSG:4326, then restore x/y limits.

Other modules used unchanged:
  - optimization.py (CAPEX_SPECS, AVAILABILITY)
  - ammonia_to_port_optimizer.py (ports loader & cost enrichment)
"""

from __future__ import annotations
import importlib.util
import re
import unicodedata
from pathlib import Path
from typing import Dict, Tuple, Optional, List

import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
from shapely.ops import nearest_points

try:
    import pycountry
except Exception:  # pragma: no cover
    pycountry = None

# Economics primitives
from optimization import CAPEX_SPECS, AVAILABILITY  # noqa

# Ammonia/ports utilities (unchanged)
from ammonia_to_port_optimizer import (
    # existing

    load_ports_from_csv, load_ports_from_unlocode,
    attach_ports_and_ammonia_costs, pipeline_cost_sensitivity,
    plot_sites_and_ports, load_ports_online,
    pareto_front_2d, plot_pareto, monte_carlo_delivered_costs, plot_monte_carlo_boxplot
)

# Online constraints (OSM Overpass)
from online_constraints import build_osm_exclusion_union

# ----------------------- CONFIG -----------------------
CONFIG = dict(
    COUNTRY_ISO3 = "SAU",
    COUNTRY_NAME = "Saudi Arabia",
    OUTDIR = "_out_v30",

    YEAR = 2027,
    CAPEX_CASE = "low",

    BASE_CELL_STEP_DEG = 0.25,
    MAX_CELLS_TOTAL = 25000,
    SHORTLIST = 60,
    TOPK = 10,

    PORTS = dict(
        ENABLE = True,
        SOURCE = "AUTO",           # "AUTO" | "WPI" | "UNLOCODE" | "OSM" | "CSV" | "UNLOCODE_CSV" | "WPI_CSV"
        PATH   = None,
        MAX_DISTANCE_KM = 800.0,
        PLOT_ONLY_CONSIDERED = False,
        COAST_MAX_KM = 35.0
    ),

    AMMONIA = dict(
        ELECTRICITY_INTENSITY_MWH_PER_T = 10.0,
        EU_PIPELINE_COEFF = 0.031,
        GLOBAL_PIPELINE_COEFF = 0.0589,
        PIPELINE_SENSITIVITY = (0.8, 1.0, 1.2)
    ),

    LCOE = dict(
        WACC = 0.07,
        LIFE_YEARS_PV = 25,
        LIFE_YEARS_WIND = 25,
        FOM_FRAC_PV = 0.02,
        FOM_FRAC_WIND = 0.03
    ),

    ONLINE_CONSTRAINTS = dict(
        ENABLE=True,
        APPLY_STAGE='after_adaptive',  # apply once we have the refined grid
        APPLY_TO=('combined','last_level'),
        MIN_DEVELOPABLE_FRACTION=0.40,
        SPEC=[
            {'type':'cities','place_ranks':['city','town'],'buffer_m':1500},
            {'type':'urban_areas','buffer_m':300},
            {'type':'protected_areas','buffer_m':0},
            {'type':'osm_natural','tags':['glacier','cliff','scree','wetland'],'buffer_m':50},
        ]
    ),

    VIZ = dict(
        MONTE_CARLO = dict(ENABLE=True, N_SAMPLES=500, YIELD_SIGMA=0.10, PIPE_SIGMA=0.20, TOPK_ON_PLOT=10),
        PARETO = dict(ENABLE=True, X="distance_km", Y="nh3_delivered_usd_per_t"),
        PORTS_HEATMAP = dict(
            ENABLE = True,
            SOURCE_LEVEL = "last",
            MODE = "cells",
            TITLE = "Delivered NH3 $/t to nearest sea export port",
            FILENAME = "heatmap_ports_constraint.png",
            OVERWRITE_BASE_PNG = False,
            # Plot-only correction: WPI/UpdatedPub150 port coordinates can be
            # offshore anchorage/harbor points. Keep those raw coordinates for
            # matching, distance, and shipping, but draw the marker on the
            # nearest point of the same 10 m country border used by the heatmap.
            SNAP_PORTS_TO_SHORE_FOR_PLOT = True,
            PORT_SNAP_MAX_KM = 100.0,
            PORT_SNAP_SAVE_CSV = True
        )
    )
)

# -------------------- Import v29 runner helper --------------------
def _import_runner() -> object:
    """
    Import the v29 visualization runner that provides:
      - load_country_polygon, build_square_fishnet, adaptive_cells
      - monthly_rank_points, render_country_heatmap
    """
    here = Path(__file__).parent
    p = here / 'country_siting_runner_CELL_ADAPTIVE_CONSTRAINED_HEATMAP_OPT_C_v29_black_gap_overlay_cells.py'
    if not p.exists():
        raise FileNotFoundError(f"Expected v29 runner not found: {p}")
    spec = importlib.util.spec_from_file_location('v_runner', p.as_posix())
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod

# -------------------- LCOE proxy --------------------
def _crf(rate: float, n_years: int) -> float:
    r = float(rate); n = int(n_years)
    return r * (1 + r)**n / ((1 + r)**n - 1)

def _compute_lcoe_for_site(lat: float, lon: float, year: int, capex_case: str, runner) -> Tuple[float, Dict]:
    """Approximate LCOE from PVGIS/NASA monthly; (min_lcoe, extras)."""
    try:
        solar_month = runner.get_solar_monthly_hcf_from_pvgis(lat, lon)  # kWh/kWp-month
        wind_month  = runner.get_wind_monthly_hcf_from_nasa(lat, lon, year=2019)  # MWh/MW-month proxy
    except Exception as e:
        raise RuntimeError(f"Resource fetch failed at {lat:.3f},{lon:.3f}: {e}")

    annual_pv_mwh_per_mw   = float(np.nansum(solar_month))
    annual_wind_mwh_per_mw = float(np.nansum(wind_month))

    cs = CAPEX_SPECS[year][capex_case]['solar'] * 1000.0
    cw = CAPEX_SPECS[year][capex_case]['wind']  * 1000.0

    WACC = float(CONFIG['LCOE']['WACC'])
    life_pv = int(CONFIG['LCOE']['LIFE_YEARS_PV']); life_w = int(CONFIG['LCOE']['LIFE_YEARS_WIND'])
    fom_pv  = float(CONFIG['LCOE']['FOM_FRAC_PV']) * cs
    fom_w   = float(CONFIG['LCOE']['FOM_FRAC_WIND']) * cw
    crf_pv  = _crf(WACC, life_pv); crf_w = _crf(WACC, life_w)

    lcoe_pv   = (cs * crf_pv + fom_pv) / max(1e-6, annual_pv_mwh_per_mw)
    lcoe_wind = (cw * crf_w   + fom_w) / max(1e-6, annual_wind_mwh_per_mw)

    min_lcoe = float(min(lcoe_pv, lcoe_wind))
    extras = dict(
        annual_pv_mwh_per_mw=annual_pv_mwh_per_mw,
        annual_wind_mwh_per_mw=annual_wind_mwh_per_mw,
        lcoe_pv=lcoe_pv, lcoe_wind=lcoe_wind
    )
    return min_lcoe, extras

# -------------------- text/country helpers --------------------
def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.strip().upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _country_name_candidates(country_name: Optional[str], iso3: Optional[str]) -> List[str]:
    candidates: List[str] = []
    for value in [country_name, iso3]:
        if value:
            candidates.append(str(value))

    if pycountry is not None and iso3:
        try:
            rec = pycountry.countries.get(alpha_3=str(iso3).upper())
            if rec is not None:
                for attr in ["name", "official_name", "common_name"]:
                    value = getattr(rec, attr, None)
                    if value:
                        candidates.append(str(value))
        except Exception:
            pass

    if pycountry is not None and country_name:
        try:
            rec = pycountry.countries.lookup(str(country_name))
            for attr in ["alpha_3", "name", "official_name", "common_name"]:
                value = getattr(rec, attr, None)
                if value:
                    candidates.append(str(value))
        except Exception:
            pass

    alias_map = {
        "UNITED STATES OF AMERICA": ["United States", "USA", "US"],
        "UNITED STATES": ["United States of America", "USA", "US"],
        "UNITED KINGDOM": ["UK", "Great Britain"],
        "RUSSIAN FEDERATION": ["Russia"],
        "KOREA REPUBLIC OF": ["South Korea", "Korea, South"],
        "VIET NAM": ["Vietnam"],
        "COTE D IVOIRE": ["Ivory Coast", "Cote d'Ivoire"],
    }

    expanded = list(candidates)
    for candidate in candidates:
        expanded.extend(alias_map.get(_normalize_text(candidate), []))

    out: List[str] = []
    seen = set()
    for candidate in expanded:
        norm = _normalize_text(candidate)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(candidate)
    return out


def _pick_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    norm_map = {
        re.sub(r"[^a-z0-9]+", "", str(c).strip().lower()): c
        for c in df.columns
    }
    for candidate in candidates:
        key = re.sub(r"[^a-z0-9]+", "", candidate.strip().lower())
        if key in norm_map:
            return norm_map[key]
    return None


def _load_ports_from_updatedpub150_csv(
    path: str,
    country_iso3: Optional[str] = None,
    country_name: Optional[str] = None,
) -> gpd.GeoDataFrame:
    """
    Load the same UpdatedPub150/WPI CSV used by Shipping_route_model.py.

    The returned GeoDataFrame intentionally exposes 'port_name' as Main Port
    Name, because attach_ports_and_ammonia_costs reads that label when writing
    nearest_port. This keeps the siting output directly compatible with the
    shipping script's Main Port Name matching.
    """
    if not path:
        raise ValueError("PORTS.PATH must point to UpdatedPub150.csv when SOURCE='WPI_CSV'.")

    df = pd.read_csv(path)
    main_col = _pick_col(df, ["Main Port Name", "MainPortName"])
    lat_col = _pick_col(df, ["Latitude", "Lat"])
    lon_col = _pick_col(df, ["Longitude", "Lon", "Long"])
    country_col = _pick_col(df, ["Country Code", "Country", "Country Name"])
    region_col = _pick_col(df, ["Region Name", "Region"])
    alternate_col = _pick_col(df, ["Alternate Port Name", "AlternatePortName"])

    missing = [
        name for name, col in [
            ("Main Port Name", main_col),
            ("Latitude", lat_col),
            ("Longitude", lon_col),
        ]
        if col is None
    ]
    if missing:
        raise ValueError(f"UpdatedPub150 CSV is missing required column(s): {missing}")

    df = df.copy()
    df[lat_col] = pd.to_numeric(df[lat_col], errors="coerce")
    df[lon_col] = pd.to_numeric(df[lon_col], errors="coerce")
    df = df.dropna(subset=[main_col, lat_col, lon_col]).copy()

    # Prefer ports in the current country. If the WPI country naming does not
    # match, fall back to the whole WPI table; the later coastal/country buffer
    # still limits the working set geographically.
    if country_col is not None:
        country_norms = {
            _normalize_text(x)
            for x in _country_name_candidates(country_name, country_iso3)
            if _normalize_text(x)
        }
        if country_norms:
            filtered = df[df[country_col].apply(_normalize_text).isin(country_norms)].copy()
            if not filtered.empty:
                df = filtered
            else:
                print(
                    "[ports] WARNING: no UpdatedPub150 rows matched "
                    f"country={country_name!r}, ISO3={country_iso3!r}; using all WPI rows before coastal filtering."
                )

    out = pd.DataFrame({
        "port_name": df[main_col].astype(str).str.strip(),
        "main_port_name": df[main_col].astype(str).str.strip(),
        "lat": df[lat_col].astype(float),
        "lon": df[lon_col].astype(float),
    })

    if country_col is not None:
        out["country"] = df[country_col].astype(str).str.strip()
        out["country_code"] = df[country_col].astype(str).str.strip()
    if region_col is not None:
        out["region_name"] = df[region_col].astype(str).str.strip()
    if alternate_col is not None:
        out["alternate_port_name"] = df[alternate_col].astype(str).str.strip()
    if "World Port Index Number" in df.columns:
        out["world_port_index_number"] = df["World Port Index Number"]

    gdf = gpd.GeoDataFrame(
        out,
        geometry=gpd.points_from_xy(out["lon"], out["lat"]),
        crs="EPSG:4326",
    )
    print(f"[ports] Loaded {len(gdf)} UpdatedPub150/WPI ports for siting.")
    return gdf


# -------------------- Ports I/O --------------------
def _load_ports(cfg_ports: Dict, country_iso3: str, country_name: Optional[str] = None) -> gpd.GeoDataFrame:
    src = str(cfg_ports.get('SOURCE','AUTO')).upper()
    if src in {"WPI_CSV", "UPDATEDPUB150", "UPDATEDPUB150_CSV"}:
        path = str(cfg_ports.get('PATH',''))
        return _load_ports_from_updatedpub150_csv(
            path,
            country_iso3=country_iso3,
            country_name=country_name or cfg_ports.get("COUNTRY_NAME"),
        )
    if src == 'CSV':
        path = str(cfg_ports.get('PATH',''))
        if not path:
            raise ValueError("PORTS.PATH must be provided when SOURCE='CSV'.")
        return load_ports_from_csv(path)
    elif src == 'UNLOCODE_CSV':
        path = str(cfg_ports.get('PATH',''))
        if not path:
            raise ValueError("PORTS.PATH must be provided when SOURCE='UNLOCODE_CSV'.")
        return load_ports_from_unlocode(path)
    else:
        return load_ports_online(country_iso3, source=src)

# -------------------- Ports‑constrained heatmap helpers --------------------
def _compute_ports_heatmap_scores(level_cells_gdf: gpd.GeoDataFrame,
                                  ports_gdf: gpd.GeoDataFrame,
                                  cfg: Dict,
                                  runner) -> pd.DataFrame:
    """For each selected cell centroid, compute delivered NH3 $/t to nearest sea port."""
    if level_cells_gdf is None or level_cells_gdf.empty:
        return pd.DataFrame(columns=["cell_id","score","lat","lon"])
    rows = []
    for r in level_cells_gdf.itertuples(index=False):
        lat = float(getattr(r, "cent_lat")); lon = float(getattr(r, "cent_lon"))
        cid = str(getattr(r, "cell_id"))
        try:
            lcoe, _ = _compute_lcoe_for_site(lat, lon, cfg['YEAR'], cfg['CAPEX_CASE'], runner)
            rows.append({"cell_id": cid, "lat": lat, "lon": lon, "lcoe_usd_per_mwh": lcoe})
        except Exception:
            continue
    lcoe_sites = pd.DataFrame(rows)
    if lcoe_sites.empty:
        return pd.DataFrame(columns=["cell_id","score","lat","lon"])

    enriched = attach_ports_and_ammonia_costs(
        lcoe_sites, ports_gdf, country_iso3=cfg['COUNTRY_ISO3'],
        electricity_intensity_mwh_per_t=float(cfg['AMMONIA']['ELECTRICITY_INTENSITY_MWH_PER_T']),
        country_coeff_overrides=None,
        max_distance_km=float(cfg['PORTS']['MAX_DISTANCE_KM']) if cfg['PORTS'].get('MAX_DISTANCE_KM') else None
    )
    sc = enriched.rename(columns={"nh3_delivered_usd_per_t":"score"})
    if 'cell_id' not in sc.columns:
        key = level_cells_gdf.assign(lat=level_cells_gdf['cent_lat'].round(6),
                                     lon=level_cells_gdf['cent_lon'].round(6))[['cell_id','lat','lon']]
        sc2 = sc.copy(); sc2['lat'] = sc2['lat'].round(6); sc2['lon'] = sc2['lon'].round(6)
        sc = sc2.merge(key, on=['lat','lon'], how='left')
    return sc[['cell_id','score','lat','lon']]

# ---- NEW: capture the figure created by the heatmap renderer ----
def _render_and_capture_figure(render_fn, *args, **kwargs):
    """
    Call the renderer while plt.show() is suppressed, then return the *new* figure object.
    """
    before = set(plt.get_fignums())
    orig_show = plt.show
    try:
        plt.show = (lambda *a, **k: None)
        render_fn(*args, **kwargs)
    finally:
        plt.show = orig_show
    after = set(plt.get_fignums())
    new_ids = list(after - before)
    if not new_ids:
        # Fallback: current figure
        return plt.gcf()
    return plt.figure(new_ids[-1])

def _find_heatmap_axes(fig, country_iso3: str) -> Optional[plt.Axes]:
    """
    Pick the axes whose title starts with "Heatmap — <ISO3>" (robust against colorbars and other plots).
    """
    title_prefix = f"Heatmap — {country_iso3}"
    candidates = []
    for ax in fig.axes:
        t = (ax.get_title() or "").strip()
        xl = (ax.get_xlabel() or "").strip().lower()
        yl = (ax.get_ylabel() or "").strip().lower()
        is_colorbar = getattr(ax, "name", "").lower() == "colorbar"
        if is_colorbar:
            continue
        score = 0
        if t.startswith(title_prefix): score += 10
        if "longitude" in xl: score += 2
        if "latitude" in yl:  score += 2
        bbox = ax.get_position(); area = float(bbox.width * bbox.height)
        candidates.append((score, area, ax))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[-1][2]


def _metric_crs_for_gdf(gdf: gpd.GeoDataFrame) -> str:
    """Return a local UTM CRS for distance/nearest-point operations."""
    try:
        g = gdf.to_crs(4326)
        centroid = g.unary_union.centroid
        lon = float(centroid.x)
        lat = float(centroid.y)
        zone = int((lon + 180.0) // 6.0) + 1
        zone = max(1, min(60, zone))
        epsg = 32600 + zone if lat >= 0 else 32700 + zone
        return f"EPSG:{epsg}"
    except Exception:
        return "EPSG:3857"


def _ports_snapped_to_country_boundary_for_plot(
    ports_gdf: gpd.GeoDataFrame,
    country_gdf: gpd.GeoDataFrame,
    cfg: Dict,
    outdir: Optional[Path] = None,
) -> gpd.GeoDataFrame:
    """
    Return a plotting-only copy of ports snapped to the country boundary.

    UpdatedPub150/WPI coordinates are often harbor/anchorage reference points,
    so for map display they can appear offshore. This function does not change
    the port table used for nearest-port matching, distance calculation, or the
    shipping model. It only moves the plotted marker to the nearest point on
    the same country geometry that the heatmap is already using.
    """
    viz_cfg = cfg.get("VIZ", {}).get("PORTS_HEATMAP", {})
    if not bool(viz_cfg.get("SNAP_PORTS_TO_SHORE_FOR_PLOT", True)):
        return ports_gdf.to_crs(4326).copy()

    if ports_gdf is None or ports_gdf.empty:
        return ports_gdf

    try:
        metric_crs = _metric_crs_for_gdf(country_gdf)
        country_m = country_gdf.to_crs(metric_crs)
        boundary = country_m.unary_union.boundary
        ports_m = ports_gdf.to_crs(metric_crs).copy()
        ports_wgs = ports_gdf.to_crs(4326).copy()

        max_km_raw = viz_cfg.get("PORT_SNAP_MAX_KM", 100.0)
        max_km = None if max_km_raw in (None, "", False) else float(max_km_raw)

        snapped_geoms = []
        snap_dist_km = []
        snapped_flags = []

        for geom in ports_m.geometry:
            if geom is None or geom.is_empty or boundary is None or boundary.is_empty:
                snapped_geoms.append(geom)
                snap_dist_km.append(np.nan)
                snapped_flags.append(False)
                continue

            nearest_boundary_point = nearest_points(geom, boundary)[1]
            dist_km = float(geom.distance(nearest_boundary_point) / 1000.0)
            do_snap = (max_km is None) or (dist_km <= max_km)

            snapped_geoms.append(nearest_boundary_point if do_snap else geom)
            snap_dist_km.append(dist_km)
            snapped_flags.append(bool(do_snap))

        out = ports_m.copy()
        out["wpi_lat"] = ports_wgs.geometry.y.values
        out["wpi_lon"] = ports_wgs.geometry.x.values
        out["shore_snap_distance_km"] = snap_dist_km
        out["snapped_to_shore_for_plot"] = snapped_flags
        out["geometry"] = snapped_geoms
        out = out.to_crs(4326)
        out["plot_lat"] = out.geometry.y
        out["plot_lon"] = out.geometry.x

        if bool(viz_cfg.get("PORT_SNAP_SAVE_CSV", True)) and outdir is not None:
            audit_cols = [
                c for c in [
                    "port_name", "main_port_name", "country", "country_code",
                    "wpi_lat", "wpi_lon", "plot_lat", "plot_lon",
                    "shore_snap_distance_km", "snapped_to_shore_for_plot",
                ]
                if c in out.columns
            ]
            try:
                out[audit_cols].to_csv(outdir / "ports_heatmap_plot_coordinates.csv", index=False)
            except Exception as exc:
                print(f"[ports-heatmap] WARNING: could not write port plot-coordinate audit CSV: {exc}")

        n_snapped = int(sum(1 for flag in snapped_flags if flag))
        print(f"[ports-heatmap] Plot-only shore snapping: {n_snapped}/{len(out)} port marker(s) moved to country boundary.")
        return out
    except Exception as exc:
        print(f"[ports-heatmap] WARNING: could not snap ports to shoreline for plotting: {exc}")
        return ports_gdf.to_crs(4326).copy()


def render_ports_constrained_heatmap(country_gdf: gpd.GeoDataFrame,
                                     levels: Dict[str, gpd.GeoDataFrame],
                                     scores_by_level: Dict[str, pd.DataFrame],
                                     ports_gdf: gpd.GeoDataFrame,
                                     cfg: Dict,
                                     runner):
    """
    Render v29 heatmap, then overlay **ports** on the SAME axes in WGS84.
    """
    src_level = str(cfg.get('VIZ',{}).get('PORTS_HEATMAP',{}).get('SOURCE_LEVEL','last'))
    lvl_names = sorted(levels.keys())
    if not lvl_names:
        print("[ports-heatmap] No levels to render."); return
    level_name = (lvl_names[-1] if src_level=='last' else (src_level if src_level in levels else lvl_names[-1]))

    ports_scores = _compute_ports_heatmap_scores(levels[level_name], ports_gdf, cfg, runner)
    if ports_scores is None or ports_scores.empty:
        print("[ports-heatmap] No scores produced; skipping heatmap."); return

    cfg_hm = dict(
        COUNTRY = cfg.get('COUNTRY_ISO3', 'UNK'),
        BASE_CELL_STEP_DEG = cfg.get('BASE_CELL_STEP_DEG', 0.25),
        HEATMAP = dict(
            ENABLE=True,
            MODE=str(cfg.get('VIZ',{}).get('PORTS_HEATMAP',{}).get('MODE','cells')),
            SOURCE_LEVEL=level_name,
            TITLE=str(cfg.get('VIZ',{}).get('PORTS_HEATMAP',{}).get('TITLE','Delivered NH3 $/t to nearest sea export port')),
            SAVE_PNG=True,
            FILENAME=str(cfg.get('VIZ',{}).get('PORTS_HEATMAP',{}).get('FILENAME','heatmap_ports_constraint.png')),
            FIGSIZE=(8,9),
            DRAW_COUNTRY_OUTLINE=True,
            DRAW_CELL_OUTLINES=True,
            TRI_RASTERIZED=True,
            TRI_GRID_W=1200,
            MASK_DILATE_PIX=1,
            BORDER_CLIP=dict(ENABLE=True, EPS_M=0.0),
            EXTEND_TO_BOUNDARY=dict(ENABLE=True, METHOD="idw", K=4, PERIMETER_SPACING_KM=12.0, RIM_OFFSET_KM=0.2),
            NOT_ANALYZED=dict(ENABLE=True, LEGEND_LABEL="Not analyzed (grid gap)"),
        )
    )
    outdir = Path(cfg['OUTDIR'])
    fname = cfg_hm['HEATMAP']['FILENAME']
    overwrite_base = bool(cfg.get('VIZ',{}).get('PORTS_HEATMAP',{}).get('OVERWRITE_BASE_PNG', False))

    # --- Render heatmap and capture the exact figure created
    fig = _render_and_capture_figure(runner.render_country_heatmap, cfg_hm, country_gdf, levels, {level_name: ports_scores}, outdir)
    ax = _find_heatmap_axes(fig, cfg.get('COUNTRY_ISO3','UNK')) or (fig.axes[0] if fig.axes else plt.gca())
    xlim, ylim = ax.get_xlim(), ax.get_ylim()

    # --- Overlay ports on the same axes. The data/source coordinates stay
    # unchanged for economics and shipping; only the plotted marker is snapped
    # to the 10 m country boundary when configured.
    g_ports = ports_gdf.to_crs(4326).copy()
    g_ports = g_ports[~g_ports.geometry.is_empty]
    g_ports_plot = _ports_snapped_to_country_boundary_for_plot(g_ports, country_gdf, cfg, outdir=outdir)
    ax.autoscale(False)
    g_ports_plot.plot(ax=ax, marker="*", markersize=80, alpha=0.90, label="Sea export ports", zorder=100)

    # --- Restore limits, legend, and write an overlaid PNG
    ax.set_xlim(xlim); ax.set_ylim(ylim)
    handles, labels = ax.get_legend_handles_labels()
    if "Sea export ports" not in labels:
        ax.legend(loc="lower left")
    over_png = outdir / ("ports_" + fname)
    fig.savefig(over_png.as_posix(), dpi=250)
    if overwrite_base:
        fig.savefig((outdir / fname).as_posix(), dpi=250)

    # Show once (with ports on top)
    plt.show()

# -------------------- Analytics/plots --------------------
def run_monte_carlo_and_plot(enriched: pd.DataFrame, cfg: Dict, outdir: Path):
    viz = cfg.get('VIZ',{}).get('MONTE_CARLO',{})
    if not bool(viz.get('ENABLE', True)): return
    n = int(viz.get('N_SAMPLES', 500)); sig_y = float(viz.get('YIELD_SIGMA', 0.10)); sig_p = float(viz.get('PIPE_SIGMA', 0.20))
    k = int(viz.get('TOPK_ON_PLOT', 10))
    base = enriched.head(k).copy().reset_index(drop=True)
    mc = monte_carlo_delivered_costs(
        base, n_samples=n, yield_sigma=sig_y, pipeline_sigma=sig_p,
        electricity_intensity_mwh_per_t=float(cfg['AMMONIA']['ELECTRICITY_INTENSITY_MWH_PER_T']),
        random_seed=42, id_col='nearest_port' if 'nearest_port' in base.columns else None
    )
    mc.to_csv(outdir / "monte_carlo_delivered_costs.csv", index=False)
    plot_monte_carlo_boxplot(mc, out_png=(outdir / "mc_boxplot_by_site.png").as_posix())

def run_pareto_and_plot(enriched: pd.DataFrame, cfg: Dict, outdir: Path):
    viz = cfg.get('VIZ',{}).get('PARETO',{})
    if not bool(viz.get('ENABLE', True)): return
    x = str(viz.get('X', 'distance_km')); y = str(viz.get('Y','nh3_delivered_usd_per_t'))
    base = enriched.copy()
    mask = pareto_front_2d(base, x, y, minimize_x=True, minimize_y=True)
    base.assign(pareto=mask).to_csv(outdir / "pareto_points.csv", index=False)
    plot_pareto(base, x, y, mask, out_png=(outdir / "pareto_scatter.png").as_posix(),
                title="Pareto front — distance vs delivered NH3 cost", xlabel=x, ylabel=y)

# -------------------- AP_location_dataset-style export --------------------
def _export_ap_location_dataset(enriched: pd.DataFrame, cfg: Dict, outdir: Path) -> None:
    """
    Export an AP_location_dataset-style Excel without touching the original file.

    We create rows with columns:
        Country | City | lat | lon

    using the TOPK best sites (lowest delivered NH3 cost), and write:
      1) A run-specific file in OUTDIR:  AP_location_dataset_<ISO3>_new.xlsx
      2) A global "AP_location_dataset_new.xlsx" in ./power_markets alongside the original.

    The existing AP_location_dataset.xlsx is never read or overwritten.
    """
    if enriched is None or enriched.empty:
        print("[AP-location] No sites to export (enriched is empty); skipping AP_location Excel.")
        return

    # Limit to top-K sites according to cfg['TOPK']
    topk = int(cfg.get("TOPK", len(enriched)))
    df_top = enriched.head(topk).copy()

    # Ensure we have lat/lon
    if "lat" not in df_top.columns or "lon" not in df_top.columns:
        raise KeyError("[AP-location] Expected 'lat' and 'lon' columns in enriched DataFrame")

    # Country name (prefer human-readable name, fall back to ISO3)
    country_name = cfg.get("COUNTRY_NAME") or cfg.get("COUNTRY_ISO3", "UNK")

    # City labels: A, B, C, ..., Z, AA, AB, ...
    def _idx_to_code(idx: int) -> str:
        n = int(idx)
        if n < 0:
            raise ValueError("index must be non-negative")
        letters: List[str] = []
        while True:
            n, rem = divmod(n, 26)
            letters.append(chr(ord("A") + rem))
            if n == 0:
                break
            n -= 1
        return "".join(reversed(letters))

    ap_df = pd.DataFrame(
        {
            "Country": [country_name] * len(df_top),
            "City": [_idx_to_code(i) for i in range(len(df_top))],
            "lat": df_top["lat"].astype(float).round(6),
            "lon": df_top["lon"].astype(float).round(6),
            "nearest_port": df_top["nearest_port"].astype(str) if "nearest_port" in df_top.columns else "",
            "distance_km": (
                df_top["distance_km"].astype(float).round(1)
                if "distance_km" in df_top.columns
                else 0.0
            ),
        }
    )

    iso3 = cfg.get("COUNTRY_ISO3", "UNK")

    # (1) Run-specific Excel in OUTDIR
    local_path = outdir / f"AP_location_dataset_{iso3}_new.xlsx"
    try:
        ap_df.to_excel(local_path, index=False)
        print(f"[AP-location] Wrote {len(ap_df)} rows to {local_path}")
    except Exception as e:
        print(f"[AP-location] WARNING: could not write local AP_location_dataset Excel: {e}")

    # (2) Global AP_location_dataset_new.xlsx in ./power_markets
    try:
        base_dir = Path(__file__).resolve().parent
        pm_dir = base_dir / "power_markets"
        pm_dir.mkdir(parents=True, exist_ok=True)
        global_path = pm_dir / "AP_location_dataset_new.xlsx"
        ap_df.to_excel(global_path, index=False)
        print(f"[AP-location] Wrote {len(ap_df)} rows to {global_path}")
    except Exception as e:
        print(f"[AP-location] WARNING: could not write AP_location_dataset_new.xlsx in power_markets: {e}")

# -------------------- Main driver --------------------
def run_with_ports(cfg: Dict = CONFIG):
    outdir = Path(cfg['OUTDIR']); outdir.mkdir(parents=True, exist_ok=True)

    # Use v29 runner (no alternate runner fallback)
    runner = _import_runner()

    # 1) Build country base grid and constraints (v29 adaptive runner)
    g_country = runner.load_country_polygon(cfg['COUNTRY_ISO3'], use_iso3=True, metropolitan_only=True)
    cells_L0  = runner.build_square_fishnet(g_country, step_deg=float(cfg['BASE_CELL_STEP_DEG']))
    combined_cells, levels, scores_by_level, trace = runner.adaptive_cells(g_country, cells_L0, {
        "COUNTRY": cfg['COUNTRY_ISO3'],
        "BASE_CELL_STEP_DEG": cfg['BASE_CELL_STEP_DEG'],
        "MAX_CELLS_TOTAL": cfg['MAX_CELLS_TOTAL'],
        "CONSTRAINTS": {"ENABLE": False},
        "SCORING_MODE": "proxy",
        "PROXY_WEIGHTS": {"wind": 1.0, "solar": 1.0},
        "CELL_LEVELS": [
            {"select": {"method": "topk", "k": 60}, "new_step_deg": 0.20, "variance": {"enable": False}},
            {"select": {"method": "topk", "k": 40}, "new_step_deg": 0.10, "variance": {"enable": False}},
        ],
        "MAX_NEW_CELLS_PER_LEVEL": 15000,
        "HEATMAP": {"ENABLE": False},
        "PLOT_GRID": True,
        "SAVE_GEOJSON": False,
        "ONLY_PLOT_GRID": False,
        "YEAR": cfg['YEAR'],
        "CAPEX_CASE": cfg['CAPEX_CASE'],
        "DEMAND_MW": 500.0
    })


    # 1b) Apply ONLINE constraints (cities, urban, protected, natural obstacles) at final stage
    ocfg = cfg.get("ONLINE_CONSTRAINTS", {})
    if bool(ocfg.get("ENABLE", False)):
        try:
            metric_crs = runner._metric_crs_from_country(g_country)
        except Exception:
            metric_crs = "EPSG:3857"
        try:
            union = build_osm_exclusion_union(g_country, cfg["COUNTRY_ISO3"], ocfg.get("SPEC", []), metric_crs)
        except Exception as e:
            union = None
            print("[online-constraints] WARN: could not build OSM union:", e)
        if union is not None:
            min_frac = float(ocfg.get("MIN_DEVELOPABLE_FRACTION", 0.40))
            apply_to = tuple(ocfg.get("APPLY_TO", ("combined","last_level")))
            # last level
            if "last_level" in apply_to and levels:
                last_name = sorted(levels.keys())[-1]
                kept_last, s_last = runner.apply_vector_exclusions(levels[last_name], union, min_frac, metric_crs, update_centroids=True)
                levels[last_name] = kept_last
                if last_name in scores_by_level:
                    ids_keep = set(kept_last["cell_id"].astype(str).tolist())
                    scores_by_level[last_name] = scores_by_level[last_name][scores_by_level[last_name]["cell_id"].astype(str).isin(ids_keep)].reset_index(drop=True)
                print(f"[online-constraints] last level '{last_name}': kept={len(kept_last)} (dropped={s_last['dropped']})")
            # combined
            if "combined" in apply_to and (combined_cells is not None):
                combined_cells, s_comb = runner.apply_vector_exclusions(combined_cells, union, min_frac, metric_crs, update_centroids=True)
                print(f"[online-constraints] combined cells: kept={len(combined_cells)} (dropped={s_comb['dropped']})")

    # 2) Monthly ranking on centroids (v29 runner)
    sites_df = combined_cells.rename(columns={"cent_lat":"lat","cent_lon":"lon"})[["lat","lon"]]
    monthly_df = runner.monthly_rank_points(sites_df, year=cfg['YEAR'], capex_case=cfg['CAPEX_CASE'], demand_mw=500.0)
    shortlist = monthly_df.head(int(cfg['SHORTLIST'])).copy()

    # 3) Per‑site LCOE proxy for shortlist
    rows = []
    for la, lo in zip(shortlist['lat'].tolist(), shortlist['lon'].tolist()):
        lcoe, extras = _compute_lcoe_for_site(la, lo, cfg['YEAR'], cfg['CAPEX_CASE'], runner)
        rows.append(dict(lat=la, lon=lo, lcoe_usd_per_mwh=lcoe, **extras))
    lcoe_df = pd.DataFrame(rows)

    # 4) Load **sea export** ports online + filter near coast; sync lat/lon to geometry
    ports_gdf = _load_ports(cfg['PORTS'], country_iso3=cfg['COUNTRY_ISO3'], country_name=cfg.get('COUNTRY_NAME'))
    try:
        ports_gdf_proj = ports_gdf.to_crs(3857)
        country_proj = g_country.to_crs(3857)
        buffered = country_proj.unary_union.buffer(25000.0)  # 25 km coastal band
        ports_gdf_proj = ports_gdf_proj[ports_gdf_proj.within(buffered)]
        ports_gdf = ports_gdf_proj.to_crs(4326).copy()
    except Exception:
        ports_gdf = ports_gdf.to_crs(4326).copy()
    ports_gdf["lat"] = ports_gdf.geometry.y; ports_gdf["lon"] = ports_gdf.geometry.x
    if ports_gdf is None or ports_gdf.empty:
        raise RuntimeError("Online ports loader returned empty set. Try SOURCE='OSM' or check internet access.")

    # 5) Delivered-to-port NH3 $/t and ranking
    enriched = attach_ports_and_ammonia_costs(
        lcoe_df, ports_gdf, country_iso3=cfg['COUNTRY_ISO3'],
        electricity_intensity_mwh_per_t=float(cfg['AMMONIA']['ELECTRICITY_INTENSITY_MWH_PER_T']),
        country_coeff_overrides=None,
        max_distance_km=float(cfg['PORTS']['MAX_DISTANCE_KM']) if cfg['PORTS'].get('MAX_DISTANCE_KM') else None
    ).sort_values('nh3_delivered_usd_per_t').reset_index(drop=True)
    enriched.to_csv(outdir / "ammonia_delivered_costs.csv", index=False)
    # NEW: AP_location_dataset-style Excel export (top-K sites)
    _export_ap_location_dataset(enriched, cfg, outdir)


    # 6) Pipeline sensitivity
    sens = pipeline_cost_sensitivity(enriched, multipliers=tuple(cfg['AMMONIA']['PIPELINE_SENSITIVITY']))
    sens.to_csv(outdir / "pipeline_sensitivity.csv", index=False)

    # 7) Map (sites + sea export ports)
    ports_for_plot = (ports_gdf[ports_gdf['port_name'].isin(set(enriched['nearest_port'].astype(str)))]
                      if bool(cfg['PORTS'].get('PLOT_ONLY_CONSIDERED', False)) else ports_gdf.copy())
    plot_sites_and_ports(enriched, ports_for_plot, out_png=(outdir / "sites_ports_map.png").as_posix(),
                         topk=int(cfg['TOPK']), country_gdf=g_country)

    # 8) Monte‑Carlo and Pareto (optional)
    run_monte_carlo_and_plot(enriched, cfg, outdir)
    run_pareto_and_plot(enriched, cfg, outdir)

    # 9) Ports‑constrained heatmap (with ports overlay on the SAME axes)
    try:
        render_ports_constrained_heatmap(g_country, levels, scores_by_level, ports_gdf, cfg, runner)
    except Exception as e:
        print(f"[ports-heatmap] WARN: {e}")

    print("\n=== Top by delivered-to-port NH3 $/t ===")
    print(enriched.head(int(cfg['TOPK'])))
    print("\nFiles written to:", outdir.resolve())

if __name__ == "__main__":
    run_with_ports(CONFIG)
