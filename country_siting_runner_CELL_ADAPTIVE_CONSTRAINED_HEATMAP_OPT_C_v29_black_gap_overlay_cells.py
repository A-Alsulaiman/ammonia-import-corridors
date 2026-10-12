# -*- coding: utf-8 -*-
"""
country_siting_runner_CELL_ADAPTIVE_CONSTRAINED_HEATMAP_OPT_C_v11.py
====================================================================

A fully patched, self-contained runner that:
  • Builds an adaptive cell grid inside a country boundary.
  • Applies real-world constraints (vector exclusions, access-to-roads, optional DEM elevation/slope).
  • Scores cells with either a fast proxy (PVGIS+NASA) or your monthly LP cost (optimize_energy_system).
  • Renders a **gap-free, publication-ready heatmap** clipped to country borders.
  • Shows maps in the Spyder kernel AND exports GeoJSON layers + PNGs.
  • Can exit early with ONLY_PLOT_GRID=True to preview grid/heatmap without running the LP.

Place next to your `optimization.py`. The LP stage reuses:
    optimize_energy_system, CAPEX_SPECS, AVAILABILITY  (from optimization.py)

Dependencies:
    pip install geopandas shapely pandas numpy requests tqdm matplotlib
    # optional for DEM filters and polygon heatmap mode
    pip install rasterio
"""

import json
import math
import time
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import requests

try:
    from tqdm import tqdm
    _USE_TQDM = True
except Exception:
    _USE_TQDM = False

# ======= USER CONFIG =======
CONFIG = dict(
    COUNTRY="IRL",            # ISO3 recommended (e.g., 'FRA', 'USA', 'CHE')
    BASE_CELL_STEP_DEG=0.25,   # coarse cell size in degrees (lat/lon) for L0
    DEMAND_MW=500.0,
    YEAR=2027,
    CAPEX_CASE="low",
    SHORTLIST=40,
    FINALISTS=12,
    TOPK=5,
    CLUSTER_ON=True,
    MIN_SEP_KM=150.0,
    NINJA_TOKEN="",           # add when you want hourly finalists
    OUTDIR="siting_outputs",
    MAX_CELLS_TOTAL=25000,    # safety cap for all levels combined

    # Data & diagnostics
    USE_ISO3_MATCH=True,
    METROPOLITAN_ONLY=True,
    PVGIS_RADDATABASE="PVGIS-ERA5",
    PVGIS_TILT=35,
    PVGIS_AZIMUTH=0,          # 0° = due south (PVGIS)
    FALLBACK_SERIESCALC=True, # PVGIS hourly fallback if monthly incomplete
    CACHE_DIR_NAME="cf_cache_cells_constraints_v11",
    DIAG_PRINT_FIRST_FAILS=10,

    # -------- Constraints block --------
    CONSTRAINTS=dict(
        ENABLE=True,

        # Projected CRS used for buffering / distances (None -> auto UTM from country)
        METRIC_CRS=None,  # e.g., "EPSG:3857" or leave None to auto-pick UTM

        # If the developable portion of a cell (after exclusions) is below this,
        # the cell is dropped. 0.4 → at least 40% of cell area must be developable.
        MIN_DEVELOPABLE_FRACTION=0.40,

        # Vector layers to EXCLUDE (union per layer after buffer_m, then difference with cells)
        VECTOR=[
            # {"name":"cities",     "path":r"D:/gis/cities.geojson",      "buffer_m":1500, "exclude":True},
            # {"name":"villages",   "path":r"D:/gis/villages.geojson",    "buffer_m":800,  "exclude":True},
            # {"name":"roads",      "path":r"D:/gis/roads.geojson",       "buffer_m":60,   "exclude":True},
            # {"name":"rail",       "path":r"D:/gis/railways.geojson",    "buffer_m":50,   "exclude":True},
            # {"name":"water",      "path":r"D:/gis/water_polys.geojson", "buffer_m":0,    "exclude":True},
            # {"name":"rivers",     "path":r"D:/gis/rivers.geojson",      "buffer_m":50,   "exclude":True},
            # {"name":"protected",  "path":r"D:/gis/wdpa.gpkg|layer=pa",  "buffer_m":0,    "exclude":True},
            # {"name":"airports",   "path":r"D:/gis/airports.geojson",    "buffer_m":2000, "exclude":True},
        ],

        # Access filter relative to ROADS: require cells to be within MAX distance (and/or not closer than MIN)
        ACCESS=dict(
            ROADS_PATH=None,        # e.g., r"D:/gis/roads.geojson"
            MAX_DIST_TO_ROAD_M=None,# e.g., 20000  (require <= 20 km to any road)
            MIN_DIST_TO_ROAD_M=None # e.g., 100    (avoid being <100 m from road centerline)
        ),

        # DEM / slope constraints (optional; requires rasterio)
        DEM=dict(
            DEM_PATH=None,          # e.g., r"D:/gis/dem_30m.tif"
            USE_ELEVATION=True,
            ELEV_MIN_M=-200,
            ELEV_MAX_M=2500,
            USE_SLOPE=True,
            SLOPE_MAX_DEG=12.0,
            SAMPLE_STRATEGY="centroid",   # "centroid" | "grid5"
            SAMPLE_WINDOW_PIX=5,
        )
    ),

    # -------- Adaptive refinement --------
    ADAPTIVE_ENABLE=True,               # turn on/off refinement
    SCORING_MODE="proxy",               # "proxy" or "lp_cost"
    PREVIEW_ALLOW_PROXIES=True,         # when ONLY_PLOT_GRID=True, allow proxies so refinement still works
    PROXY_WEIGHTS={"wind": 1.0, "solar": 1.0},
    CELL_LEVELS=[
        {"select": {"method": "topk", "k": 80}, "new_step_deg": 0.20, "variance": {"enable": True, "thresh": 0.15}},
        {"select": {"method": "topk", "k": 40}, "new_step_deg": 0.10, "variance": {"enable": True, "thresh": 0.10}},
    ],
    MAX_NEW_CELLS_PER_LEVEL=15000,      # guardrail for each level

    # -------- Heatmap options --------
    HEATMAP=dict(
        ENABLE=True,
        MODE="cells",  # choices: "tri" (smooth triangulated) | "cells" (blocky polygon fill) | "polygon" (rasterize; needs rasterio)
        SOURCE_LEVEL="last",
        N_LEVELS=24,
        CMAP="viridis",
        INVERT=False,
        TITLE="Resource merit (lower is better)",
        DRAW_COUNTRY_OUTLINE=True,
        DRAW_CELL_OUTLINES=True,
        SAVE_PNG=True,
        FILENAME="heatmap_country.png",
        FIGSIZE=(8, 9),

        INTERPOLATION="nearest",
        BORDER_CLIP=dict(
            ENABLE=True,
            EPS_M=0.0,   # small ±meters buffer to hide 1px seams if needed
        ),
        MISSING_CELLS=dict(
            ENABLE=False,
            LINESTYLE="--",
            LINEWIDTH=0.6,
            LINECOLOR="0.5",
            ALPHA=0.9,
            ZORDER=9,
        ),
        HATCH_INACTIVE=dict(
            ENABLE=True,
            FROM_LEVEL="L0_full",   # full base grid
            REF_LEVEL="selected",   # compare vs level used for coloring
            METHOD="difference",    # "difference" | "centroid" | "area"
            AREA_THRESHOLD=0.05,    # for METHOD="area"
            SMOOTH_BUFFER_M=25.0,   # seal micro gaps before difference
            MIN_AREA_M2=1000.0,     # drop tiny crumbs
            HATCH="///",
            EDGE_COLOR="0.5",
            LINEWIDTH=0.2,
            ZORDER=2,
        ),
        # Fill in black the parts of the country polygon that the base grid cannot cover
        NOT_ANALYZED=dict(
            ENABLE=True,
            FROM_LEVEL="L0_full",    # compute coverage from the full base grid
            SMOOTH_BUFFER_M=20.0,    # grow+shrink union(grid) slightly to seal hairlines
            MIN_AREA_M2=20000.0,     # ignore crumbs below this area
            FACE_COLOR="black",
            EDGE_COLOR="black",
            LINEWIDTH=0.0,
            ALPHA=1.0,
            ZORDER=1,
            LEGEND_LABEL="Not analyzed (grid gap)",
        ),


        # Gap-free border alignment for triangulated mode
        TRI_RASTERIZED=True,   # render triangulation to a grid then mask to polygon (fills to border)
        TRI_GRID_W=1200,       # grid width (pixels) for the rasterized tri field
        MASK_DILATE_PIX=1,     # 1–2 pixel dilation of the mask to kill any single-pixel coastal seams

        # Optional: add “ghost” samples near the border so interpolation is well-defined right to the edge
        EXTEND_TO_BOUNDARY=dict(
            ENABLE=True,
            METHOD="idw",        # or "nn" (nearest)
            K=4,
            PERIMETER_SPACING_KM=12.0,
            RIM_OFFSET_KM=0.2    # ~200 m inside border
        ),
    ),

    # -------- Grid/cell visualization --------
    PLOT_GRID=True,           # show grid map (choropleth for last level) in kernel
    DRAW_OUTLINES=True,       # draw polygon outlines for each level
    PLOT_CHOROPLETH=True,     # if scores exist for last level, draw a choropleth for that level
    SAVE_GEOJSON=True,        # export country & cell layers
    ONLY_PLOT_GRID=False,     # True → build/apply constraints, render plots/heatmap, and exit (no monthly/hourly)
    POINT_SIZE=6,             # marker size for centroids (if drawn)
    FIGSIZE=(8, 9),           # inches
    PLOT_CLIP_TO_BORDER=True,
)

# ======= Imports from your optimizer =======
try:
    from optimization import optimize_energy_system, CAPEX_SPECS, AVAILABILITY  # noqa
except Exception:
    optimize_energy_system = None
    CAPEX_SPECS = {}
    AVAILABILITY = 0.9

# ======= Minimal haversine clustering =======
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0088
    import math as _m
    phi1 = _m.radians(lat1); phi2 = _m.radians(lat2)
    dphi = _m.radians(lat2 - lat1); dl = _m.radians(lon2 - lon1)
    a = _m.sin(dphi/2)**2 + _m.cos(phi1)*_m.cos(phi2)*_m.sin(dl/2)**2
    return 2*R*_m.atan2(_m.sqrt(a), _m.sqrt(1-a))

def select_top_k(df, k, score_col, lat_col="lat", lon_col="lon",
                 cluster=True, min_sep_km=150.0):
    if df is None or df.empty:
        return df
    cand = df.sort_values(score_col, ascending=True).reset_index(drop=True)
    if not cluster:
        return cand.head(k)
    selected = []
    for _, row in cand.iterrows():
        if not selected:
            selected.append(row)
            if len(selected) >= k: break
            continue
        ok = True
        for r in selected:
            if haversine_km(row[lat_col], row[lon_col], r[lat_col], r[lon_col]) < min_sep_km:
                ok = False; break
        if ok:
            selected.append(row)
            if len(selected) >= k: break
    if len(selected) < k:
        taken = {r.name for r in selected}
        for _, row in cand.iterrows():
            if row.name in taken: continue
            selected.append(row)
            if len(selected) >= k: break
    return pd.DataFrame(selected).reset_index(drop=True)

# ======= Country grid/cells =======
import geopandas as gpd
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union


def _load_world_admin0_gdf():
    """
    Load Natural Earth Admin-0 boundaries with high-resolution preference.
    Tries geodatasets 10m -> 50m -> 110m, then falls back to direct S3 downloads.
    Returns GeoDataFrame in EPSG:4326.
    """
    from pathlib import Path
    import geopandas as gpd
    DATA_DIR = Path(__file__).parent / ".data"
    DATA_DIR.mkdir(exist_ok=True)

    # 1) geodatasets (preferred)
    try:
        from geodatasets import get_path as gd_get_path
        for key in (
            "naturalearth.cultural.ne_10m_admin_0_countries",
            "naturalearth.cultural.ne_50m_admin_0_countries",
            "naturalearth.cultural.admin_0_countries"  # usually 110m
        ):
            try:
                p = gd_get_path(key)
                gdf = gpd.read_file(p)
                if not gdf.empty:
                    return gdf.to_crs(4326)
            except Exception:
                continue
    except Exception:
        pass

    # 2) Direct S3 fallback (10m -> 50m -> 110m)
    urls = [
        "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries.zip",
        "https://naturalearth.s3.amazonaws.com/50m_cultural/ne_50m_admin_0_countries.zip",
        "https://naturalearth.s3.amazonaws.com/110m_cultural/ne_110m_admin_0_countries.zip",
    ]
    try:
        import requests
    except Exception:
        requests = None

    for url in urls:
        try:
            name = url.split("/")[-1]
            fp = DATA_DIR / name
            if not fp.exists():
                if requests is None:
                    continue
                r = requests.get(url, timeout=90)
                r.raise_for_status()
                fp.write_bytes(r.content)
            gdf = gpd.read_file(fp.as_posix())
            if not gdf.empty:
                return gdf.to_crs(4326)
        except Exception:
            continue

    # As a last resort, try the old 110m geodatasets key if available
    try:
        from geodatasets import get_path as gd_get_path
        p = gd_get_path("naturalearth.cultural.admin_0_countries")
        gdf = gpd.read_file(p)
        return gdf.to_crs(4326)
    except Exception:
        pass

    raise RuntimeError("Could not load Natural Earth admin-0 boundaries (10m/50m/110m).")

def load_country_polygon(country: str, use_iso3=True, metropolitan_only=True):
    world = _load_world_admin0_gdf()
    gdf = gpd.GeoDataFrame()
    if use_iso3 and len(country.strip()) == 3:
        for col in ("ISO_A3", "ADM0_A3", "ISO3"):
            if col in world:
                gdf = world[world[col].astype(str).str.upper() == country.strip().upper()]
                if not gdf.empty:
                    break
    if gdf.empty:
        name_cols = [c for c in world.columns if c.upper() in ("NAME", "NAME_ENGLI", "NAME_LONG")]
        if not name_cols:
            raise ValueError("No suitable name column in Natural Earth.")
        col = name_cols[0]
        gdf = world[world[col].astype(str).str.contains(country.strip(), case=False, na=False)]
    if gdf.empty:
        raise ValueError(f"Country '{country}' not found.")
    if metropolitan_only:
        parts = gdf.explode(index_parts=False, ignore_index=True)
        parts_m = parts.to_crs(6933)  # Equal Earth (meters)
        keep_idx = int(parts_m.area.idxmax())
        gdf = parts.loc[[keep_idx]].to_crs(4326)
    return gdf

def _points_in_polygon(poly_gdf, step_deg):
    minx, miny, maxx, maxy = poly_gdf.total_bounds
    xs = np.arange(minx, maxx + 1e-9, step_deg)
    ys = np.arange(miny, maxy + 1e-9, step_deg)
    pts = [Point(x, y) for x in xs for y in ys]
    g = gpd.GeoDataFrame(geometry=pts, crs=4326)
    try:
        geom = poly_gdf.union_all()
    except AttributeError:
        geom = poly_gdf.unary_union
    g = g[g.within(geom)].copy()
    g["lat"] = g.geometry.y; g["lon"] = g.geometry.x
    return g[["lat","lon","geometry"]].reset_index(drop=True)

def _cells_from_points(gdf_points: 'gpd.GeoDataFrame', step_deg: float) -> 'gpd.GeoDataFrame':
    """
    Create square polygons of size step_deg x step_deg centered on each point.
    Accepts columns either ('lat','lon') or ('cent_lat','cent_lon').
    """
    cols = set(gdf_points.columns)
    if "lon" not in cols and "cent_lon" in cols:
        gdf_points = gdf_points.rename(columns={"cent_lon":"lon", "cent_lat":"lat"})
    if "lat" not in gdf_points.columns or "lon" not in gdf_points.columns:
        raise KeyError("Expected columns 'lat' and 'lon' (or 'cent_lat'/'cent_lon').")

    half = step_deg / 2.0
    polys = []
    for lon, lat in zip(gdf_points["lon"].values, gdf_points["lat"].values):
        poly = Polygon([
            (lon - half, lat - half),
            (lon + half, lat - half),
            (lon + half, lat + half),
            (lon - half, lat + half),
            (lon - half, lat - half),
        ])
        polys.append(poly)
    if not polys:
        return gpd.GeoDataFrame(columns=["cell_id","cent_lat","cent_lon","geometry"], crs=4326)
    cells = gpd.GeoDataFrame({"cent_lat": gdf_points["lat"], "cent_lon": gdf_points["lon"]},
                             geometry=polys, crs=4326)
    # Stable cell id (centroid + size)
    cells["cell_id"] = (cells["cent_lat"].round(6).astype(str) + "_" +
                        cells["cent_lon"].round(6).astype(str) + f"_{step_deg}")
    return cells[["cell_id","cent_lat","cent_lon","geometry"]]

def build_square_fishnet(country_gdf, step_deg):
    pts = _points_in_polygon(country_gdf, step_deg)
    return _cells_from_points(pts, step_deg)


def _subdivide_square_cell(cell_row, new_step_deg):
    """
    Robustly subdivide a square parent cell into an N×N grid of children that
    EXACTLY tile the parent, even if new_step_deg does not divide the parent span.
    """
    import numpy as _np
    geom = cell_row.geometry
    minx, miny, maxx, maxy = geom.bounds
    span_x = maxx - minx; span_y = maxy - miny
    parent_span = min(span_x, span_y)
    # Enforce ≥2×2 and snap to a divisor of the parent
    n = max(2, int(_np.ceil(parent_span / float(new_step_deg))))
    actual_step = parent_span / n
    half = actual_step / 2.0
    xs = _np.linspace(minx + half, maxx - half, n)
    ys = _np.linspace(miny + half, maxy - half, n)
    centers = [Point(x, y) for x in xs for y in ys]
    if not centers:
        return gpd.GeoDataFrame(columns=["cell_id","cent_lat","cent_lon","geometry"], crs=4326)
    g_cent = gpd.GeoDataFrame(geometry=centers, crs=4326)
    g_cent["lat"] = g_cent.geometry.y; g_cent["lon"] = g_cent.geometry.x
    subcells = _cells_from_points(g_cent[["lat","lon","geometry"]], actual_step)
    parent_id = getattr(cell_row, "cell_id", None)
    if parent_id is None:
        try:
            parent_id = str(cell_row["cell_id"])
        except Exception:
            parent_id = "unknown"
    subcells["cell_id"] = subcells["cell_id"].astype(str) + "|p:" + str(parent_id)
    return subcells

# ======= Caching & HTTP =======
CACHE = Path(CONFIG.get("CACHE_DIR_NAME","cf_cache"))
CACHE.mkdir(parents=True, exist_ok=True)

def _key(prefix, **kw):
    s = json.dumps(kw, sort_keys=True)
    return CACHE / f"{prefix}_{hashlib.md5(s.encode()).hexdigest()}.json"

def load_or_fetch(prefix, fetch_fn, **kw):
    fp = _key(prefix, **kw)
    if fp.exists():
        try:
            return json.loads(fp.read_text())
        except Exception:
            pass
    data = fetch_fn(**kw)
    fp.write_text(json.dumps(data))
    return data

def http_get(url, params=None, timeout=60, tries=2, backoff=3):
    hdrs = {"User-Agent": "AP-IRA-Model/1.0 (+cells-constraints-heatmap-v11)"}
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=hdrs, timeout=timeout)
            if r.status_code == 429:
                time.sleep(65)
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            time.sleep(backoff * (i+1))
    raise last

# ======= NASA & PVGIS adapters (monthly) =======
def fetch_nasa_daily(lat, lon, start="2019-01-01", end="2019-12-31"):
    url = "https://power.larc.nasa.gov/api/temporal/daily/point"
    params = {"latitude": lat, "longitude": lon, "start": start.replace("-",""), "end": end.replace("-",""),
              "community": "RE", "parameters": "WS10M", "format": "JSON", "time-standard":"UTC"}
    r = http_get(url, params=params, timeout=60, tries=2)
    return r.json()

def fetch_nasa_monthly(lat, lon, start="2019-01", end="2019-12"):
    url = "https://power.larc.nasa.gov/api/temporal/monthly/point"
    params = {"latitude": lat, "longitude": lon, "start": start.replace("-",""), "end": end.replace("-",""),
              "community": "RE", "parameters": "WS10M", "format": "JSON", "time-standard":"UTC"}
    r = http_get(url, params=params, timeout=60, tries=2)
    return r.json()

def _hours_per_month(year=2019):
    return [744, 672, 744, 720, 744, 720, 744, 744, 720, 744, 720, 744]

def wind_cf_proxy_from_mean_speed(u_mean_ms: float) -> float:
    if u_mean_ms is None or np.isnan(u_mean_ms): 
        return np.nan
    u = max(0.0, float(u_mean_ms))
    if u < 3: return 0.0
    if u < 5: return 0.05 + 0.05*(u-3)/(5-3)
    if u < 7: return 0.10 + 0.10*(u-5)/(7-5)
    if u < 9: return 0.20 + 0.12*(u-7)/(9-7)
    if u < 11: return 0.32 + 0.12*(u-9)/(11-9)
    if u < 13: return 0.44 + 0.10*(u-11)/(13-11)
    return min(0.60, 0.54 + 0.06*(u-13)/7)

def get_wind_monthly_hcf_from_nasa(lat: float, lon: float, year: int = 2019):
    latr, lonr = round(lat,3), round(lon,3)
    hpm = _hours_per_month(year)
    try:
        js = load_or_fetch("nasa_daily_ws10", fetch_nasa_daily, lat=latr, lon=lonr,
                           start=f"{year}-01-01", end=f"{year}-12-31")
        ws = js.get("properties", {}).get("parameter", {}).get("WS10M", {})
        monthly_vals = {m: [] for m in range(1,13)}
        for ymd, val in ws.items():
            m = int(str(ymd)[4:6]); monthly_vals[m].append(float(val))
        monthly_mean_ws = [float(np.mean(monthly_vals[m])) if monthly_vals[m] else np.nan for m in range(1,13)]
    except Exception:
        js = load_or_fetch("nasa_monthly_ws10", fetch_nasa_monthly, lat=latr, lon=lonr,
                           start=f"{year}-01", end=f"{year}-12")
        ws = js.get("properties", {}).get("parameter", {}).get("WS10M", {})
        monthly_mean_ws = [float(ws.get(str(100*m + 1), np.nan)) for m in range(1,13)]
    monthly_cf = [wind_cf_proxy_from_mean_speed(v) for v in monthly_mean_ws]
    monthly_cf = [0.0 if (v!=v) else v for v in monthly_cf]
    monthly_hcf = [ monthly_cf[i] * hpm[i] for i in range(12) ]
    return monthly_hcf

def fetch_pvgis_monthly(lat, lon, loss=10, tilt=35, azim=0, raddb="PVGIS-ERA5", version="v5_3"):
    base = f"https://re.jrc.ec.europa.eu/api/{version}/PVcalc"
    params = dict(lat=lat, lon=lon, peakpower=1.0, loss=loss,
                  angle=tilt, aspect=azim, raddatabase=raddb, outputformat="json")
    r = http_get(base, params=params, timeout=60, tries=2)
    return r.json()

def fetch_pvgis_series_hourly(lat, lon, loss=10, tilt=35, azim=0, year=2019, raddb="PVGIS-ERA5", version="v5_3"):
    base = f"https://re.jrc.ec.europa.eu/api/{version}/seriescalc"
    params = dict(
        lat=lat, lon=lon, startyear=year, endyear=year,
        peakpower=1.0, loss=loss, angle=tilt, aspect=azim,
        raddatabase=raddb, pvcalculation=1, components=1, outputformat="json"
    )
    r = http_get(base, params=params, timeout=90, tries=2)
    return r.json()

def get_solar_monthly_hcf_from_pvgis(lat: float, lon: float):
    lat_r = round(lat,3); lon_r = round(lon,3)
    tilt = CONFIG.get("PVGIS_TILT", 35)
    azim = CONFIG.get("PVGIS_AZIMUTH", 0)
    raddb = CONFIG.get("PVGIS_RADDATABASE", "PVGIS-ERA5")
    fallback_series = bool(CONFIG.get("FALLBACK_SERIESCALC", True))
    try:
        js = load_or_fetch("pvgis_monthly",
                           fetch_pvgis_monthly, lat=lat_r, lon=lon_r, tilt=tilt, azim=azim, raddb=raddb)
        months = js.get("outputs", {}).get("month", [])
        em = [float(m["E_m"]) for m in months]
        if len(em) == 12:
            return em
        if not fallback_series:
            raise RuntimeError(f"PVcalc returned {len(em)} months at {lat_r},{lon_r}; fallback disabled.")
    except Exception:
        if not fallback_series:
            raise
    js_h = load_or_fetch("pvgis_series_hourly",
                         fetch_pvgis_series_hourly, lat=lat_r, lon=lon_r, tilt=tilt, azim=azim, raddb=raddb, year=2019)
    data = js_h.get("outputs", {}).get("hourly", []) or js_h.get("data", [])
    if not data:
        raise RuntimeError(f"seriescalc returned no hourly data at {lat_r},{lon_r}")
    P_key = "P" if "P" in data[0] else ("p" if "p" in data[0] else None)
    t_key = "time" if "time" in data[0] else ("Time" if "Time" in data[0] else None)
    if P_key is None or t_key is None:
        raise RuntimeError(f"seriescalc unexpected schema keys={list(data[0].keys())}")
    monthly_kwh_per_kwp = [0.0]*12
    for row in data:
        ts = row[t_key]
        m = int(str(ts)[4:6])
        P_w = float(row[P_key])
        monthly_kwh_per_kwp[m-1] += P_w / 1000.0
    return monthly_kwh_per_kwp

# ======= Constraints helpers =======
def _metric_crs_from_country(country_gdf, fallback="EPSG:3857"):
    try:
        crs = country_gdf.estimate_utm_crs()
        return crs or fallback
    except Exception:
        return fallback

def _parse_vector_path(path_str):
    if path_str is None:
        return None, None
    if "|layer=" in path_str:
        p, layer = path_str.split("|layer=")
        return p, layer
    return path_str, None

def _load_vector_layer(path_str, target_crs):
    if not path_str:
        return None
    p, layer = _parse_vector_path(path_str)
    if not Path(p).exists():
        print(f"[constraints] WARN: vector file not found: {p}")
        return None
    try:
        g = gpd.read_file(p, layer=layer) if layer else gpd.read_file(p)
        if g.empty:
            return None
        return g.to_crs(target_crs)
    except Exception as e:
        print(f"[constraints] WARN: could not load {path_str}: {e}")
        return None

def _buffer_and_union(gdf, buffer_m):
    if gdf is None or gdf.empty:
        return None
    try:
        gdf = gdf[~gdf.geometry.is_empty].copy()
        gdf["geom"] = gdf.buffer(float(buffer_m)) if float(buffer_m) > 0 else gdf.geometry
        return unary_union(gdf["geom"].values)
    except Exception as e:
        print(f"[constraints] WARN: buffer/union failed: {e}")
        return None

def build_exclusion_union(country_gdf, cons_cfg, metric_crs):
    if not cons_cfg or not bool(cons_cfg.get("ENABLE", True)):
        return None
    vect = cons_cfg.get("VECTOR", [])
    if not vect:
        return None
    country_proj = country_gdf.to_crs(metric_crs)
    try:
        country_buf = unary_union(country_proj.buffer(10.0))
    except Exception:
        country_buf = unary_union(country_proj.geometry)
    unions = []
    for rule in vect:
        if not bool(rule.get("exclude", True)):
            continue
        path = rule.get("path")
        g = _load_vector_layer(path, metric_crs)
        if g is None:
            continue
        try:
            g = g.clip(country_proj)
        except Exception:
            pass
        u = _buffer_and_union(g, rule.get("buffer_m", 0.0))
        if u and not u.is_empty:
            unions.append(u.intersection(country_buf) if country_buf else u)
    if not unions:
        return None
    try:
        return unary_union(unions)
    except Exception:
        geom = unions[0]
        for u in unions[1:]:
            try:
                geom = geom.union(u)
            except Exception:
                pass
        return geom

def apply_vector_exclusions(cells_gdf, exclusion_union, min_fraction, metric_crs, update_centroids=True):
    if exclusion_union is None or exclusion_union.is_empty:
        return cells_gdf.copy(), {"dropped":0, "kept":len(cells_gdf)}
    cells_m = cells_gdf.to_crs(metric_crs)
    kept_rows = []
    dropped = 0
    for idx, row in cells_m.iterrows():
        poly = row.geometry
        try:
            dev = poly.difference(exclusion_union)
        except Exception:
            dev = poly
        area_all = max(poly.area, 1e-9)
        area_dev = 0.0 if (dev is None or dev.is_empty) else dev.area
        frac = area_dev / area_all
        if frac < float(min_fraction):
            dropped += 1
            continue
        lat = float(row["cent_lat"]); lon = float(row["cent_lon"])
        if update_centroids and dev and (not dev.is_empty):
            try:
                if dev.geom_type == "Polygon":
                    c = dev.centroid
                elif dev.geom_type == "MultiPolygon":
                    largest = max(dev.geoms, key=lambda g: g.area)
                    c = largest.centroid
                else:
                    c = dev.centroid
                gtmp = gpd.GeoSeries([c], crs=metric_crs).to_crs(4326)
                lat = float(gtmp.y.iloc[0]); lon = float(gtmp.x.iloc[0])
            except Exception:
                pass
        kept_rows.append({"cell_id":row["cell_id"], "cent_lat":lat, "cent_lon":lon, "geometry": row.geometry})
    kept = gpd.GeoDataFrame(kept_rows, geometry=[r["geometry"] for r in kept_rows], crs=metric_crs)
    kept = kept.to_crs(4326)
    return kept[["cell_id","cent_lat","cent_lon","geometry"]], {"dropped":dropped, "kept":len(kept_rows)}

def apply_access_distance_filter(cells_gdf, roads_path, metric_crs, min_d=None, max_d=None):
    if roads_path is None or (min_d is None and max_d is None):
        return cells_gdf.copy(), {"dropped":0, "kept": len(cells_gdf)}
    roads = _load_vector_layer(roads_path, metric_crs)
    if roads is None or roads.empty:
        print("[constraints] WARN: roads layer missing/empty; skipping access filter.")
        return cells_gdf.copy(), {"dropped":0, "kept": len(cells_gdf)}
    roads = roads[~roads.geometry.is_empty].copy()
    try:
        roads_u = unary_union(roads.geometry.values)
    except Exception:
        roads_u = unary_union(roads.buffer(0.1).geometry.values)
    c_m = cells_gdf.to_crs(metric_crs).copy()
    c_m["cent_geom"] = c_m.geometry.centroid
    try:
        dists = c_m["cent_geom"].distance(roads_u)
    except Exception:
        dists = []
        for g in c_m["cent_geom"]:
            try:
                dists.append(g.distance(roads_u))
            except Exception:
                dists.append(float("inf"))
        dists = pd.Series(dists, index=c_m.index)
    keep_mask = pd.Series(True, index=c_m.index)
    if min_d is not None:
        keep_mask &= (dists >= float(min_d))
    if max_d is not None:
        keep_mask &= (dists <= float(max_d))
    kept = c_m.loc[keep_mask].drop(columns=["cent_geom"]).to_crs(4326)
    return kept, {"dropped": int((~keep_mask).sum()), "kept": int(keep_mask.sum())}

# ==== DEM filters (optional; requires rasterio) ====
def _try_import_rasterio():
    try:
        import rasterio  # noqa
        return True
    except Exception:
        return False

def _sample_dem_at_points(dataset, xs, ys, dst_crs=None):
    import rasterio
    from rasterio.warp import transform
    if dataset.crs is None:
        coords = list(zip(xs, ys))
    else:
        if dst_crs is None:
            dst_crs = "EPSG:4326"
        if str(dataset.crs).lower() != str(dst_crs).lower():
            xs2, ys2 = transform(dst_crs, dataset.crs, xs.tolist(), ys.tolist())
            coords = list(zip(xs2, ys2))
        else:
            coords = list(zip(xs, ys))
    vals = list(dataset.sample(coords))
    z = [float(v[0]) if v is not None and len(v) > 0 else np.nan for v in vals]
    return np.array(z)

def _window_slope_deg(arr, pixel_size):
    if arr.ndim != 2 or arr.size < 9:
        return np.nan
    try:
        dzdx = ((arr[:,2:] - arr[:, :-2]) / (2*pixel_size))[:,1:-1]
        dzdy = ((arr[2:, :] - arr[:-2, :]) / (2*pixel_size))[1:-1, :]
        slope_rad = np.arctan(np.sqrt(dzdx**2 + dzdy**2))
        return float(np.nanmax(np.degrees(slope_rad)))
    except Exception:
        return np.nan

def apply_dem_filters(cells_gdf, dem_cfg):
    import importlib
    if dem_cfg is None or dem_cfg.get("DEM_PATH") is None:
        return cells_gdf.copy(), {"dropped":0, "kept": len(cells_gdf), "note":"no DEM"}
    try:
        rasterio = importlib.import_module("rasterio")
    except Exception:
        print("[constraints] WARN: rasterio not installed; skipping DEM filters.")
        return cells_gdf.copy(), {"dropped":0, "kept": len(cells_gdf), "note":"no rasterio"}

    dem_path = dem_cfg["DEM_PATH"]
    if not Path(dem_path).exists():
        print(f"[constraints] WARN: DEM file not found: {dem_path}")
        return cells_gdf.copy(), {"dropped":0, "kept": len(cells_gdf), "note":"dem missing"}

    use_elev = bool(dem_cfg.get("USE_ELEVATION", True))
    elev_min = float(dem_cfg.get("ELEV_MIN_M", -1e9))
    elev_max = float(dem_cfg.get("ELEV_MAX_M", +1e9))
    use_slope = bool(dem_cfg.get("USE_SLOPE", True))
    s_max = float(dem_cfg.get("SLOPE_MAX_DEG", 90.0))
    sample_strategy = str(dem_cfg.get("SAMPLE_STRATEGY","centroid")).lower()
    win_pix = int(dem_cfg.get("SAMPLE_WINDOW_PIX", 5))
    if win_pix % 2 == 0:
        win_pix += 1
    win_pix = max(3, win_pix)

    kept_idx = []
    dropped = 0

    with rasterio.open(dem_path) as ds:
        try:
            pixel_size = float(abs(ds.transform.a))
        except Exception:
            pixel_size = 30.0

        g = cells_gdf.copy()
        if sample_strategy == "grid5":
            lats = []; lons = []; keys = []
            for idx, r in g.iterrows():
                latc, lonc = float(r["cent_lat"]), float(r["cent_lon"])
                minx, miny, maxx, maxy = r.geometry.bounds
                dlat = (maxy - miny) / 4.0; dlon = (maxx - minx) / 4.0
                pts = [(latc, lonc), (latc+dlat, lonc), (latc-dlat, lonc),
                       (latc, lonc+dlon), (latc, lonc-dlon)]
                for (la, lo) in pts:
                    lats.append(la); lons.append(lo); keys.append(idx)
        else:
            lats = list(g["cent_lat"].values); lons = list(g["cent_lon"].values); keys = list(g.index.values)

        zs = _sample_dem_at_points(ds, np.array(lons), np.array(lats), dst_crs="EPSG:4326")

        if sample_strategy == "grid5":
            df = pd.DataFrame({"idx": keys, "z": zs})
            z_mean = df.groupby("idx")["z"].mean()
            elev_ok = pd.Series(True, index=g.index)
            if use_elev:
                elev_ok &= (z_mean >= elev_min) & (z_mean <= elev_max)
            slope_ok = pd.Series(True, index=g.index)
            if use_slope:
                slope_vals = []
                for idx, r in g.iterrows():
                    try:
                        from rasterio.warp import transform
                        x, y = transform("EPSG:4326", ds.crs, [float(r["cent_lon"])], [float(r["cent_lat"])])
                        rowcol = ds.index(x[0], y[0])
                        r0 = max(0, rowcol[0] - win_pix//2); r1 = r0 + win_pix
                        c0 = max(0, rowcol[1] - win_pix//2); c1 = c0 + win_pix
                        window = rasterio.windows.Window.from_slices((r0, r1), (c0, c1))
                        arr = ds.read(1, window=window, masked=True).filled(np.nan)
                        slope_vals.append(_window_slope_deg(arr, pixel_size))
                    except Exception:
                        slope_vals.append(np.nan)
                slope_vals = pd.Series(slope_vals, index=g.index)
                slope_ok &= (slope_vals <= s_max) | slope_vals.isna()
            keep_mask = elev_ok & slope_ok

        else:
            z_mean = pd.Series(zs, index=g.index)
            elev_ok = pd.Series(True, index=g.index)
            if use_elev:
                elev_ok &= (z_mean >= elev_min) & (z_mean <= elev_max)
            slope_ok = pd.Series(True, index=g.index)
            if use_slope:
                slope_vals = []
                for idx, r in g.iterrows():
                    try:
                        from rasterio.warp import transform
                        x, y = transform("EPSG:4326", ds.crs, [float(r["cent_lon"])], [float(r["cent_lat"])])
                        rowcol = ds.index(x[0], y[0])
                        r0 = max(0, rowcol[0] - win_pix//2); r1 = r0 + win_pix
                        c0 = max(0, rowcol[1] - win_pix//2); c1 = c0 + win_pix
                        window = rasterio.windows.Window.from_slices((r0, r1), (c0, c1))
                        arr = ds.read(1, window=window, masked=True).filled(np.nan)
                        slope_vals.append(_window_slope_deg(arr, pixel_size))
                    except Exception:
                        slope_vals.append(np.nan)
                slope_vals = pd.Series(slope_vals, index=g.index)
                slope_ok &= (slope_vals <= s_max) | slope_vals.isna()
            keep_mask = elev_ok & slope_ok

        kept = g.loc[keep_mask].copy()
        dropped = int((~keep_mask).sum())

    return kept, {"dropped": dropped, "kept": len(kept)}

# ======= Scoring (cells) =======
def proxy_score_cells(cells_gdf: 'gpd.GeoDataFrame', year=2019, w_wind=1.0, w_solar=1.0):
    rows = []
    it = tqdm(cells_gdf.itertuples(index=False), total=len(cells_gdf), desc="Proxy score cells") if _USE_TQDM else cells_gdf.itertuples(index=False)
    for row in it:
        lat, lon, cid = float(row.cent_lat), float(row.cent_lon), str(row.cell_id)
        try:
            s = get_solar_monthly_hcf_from_pvgis(lat, lon)
            w = get_wind_monthly_hcf_from_nasa(lat, lon, year=year)
            annual = w_wind * float(np.nansum(w)) + w_solar * float(np.nansum(s))
            score = -annual  # LOWER is better (proxy)
            rows.append({"cell_id": cid, "score": score, "lat": lat, "lon": lon})
        except Exception:
            continue
    return pd.DataFrame(rows).sort_values("score").reset_index(drop=True)

EFF = 0.85

def monthly_cost_score_cells(cells_gdf: 'gpd.GeoDataFrame', year: int, capex_case: str, demand_mw: float):
    if optimize_energy_system is None:
        raise RuntimeError("optimization.py not available; cannot run LP cost scoring.")
    rows = []
    it = tqdm(cells_gdf.itertuples(index=False), total=len(cells_gdf), desc="LP-cost score cells") if _USE_TQDM else cells_gdf.itertuples(index=False)
    for row in it:
        lat, lon, cid = float(row.cent_lat), float(row.cent_lon), str(row.cell_id)
        try:
            solar_hcf = get_solar_monthly_hcf_from_pvgis(lat, lon)
            wind_hcf  = get_wind_monthly_hcf_from_nasa(lat, lon, year=2019)
        except Exception:
            continue
        cw = CAPEX_SPECS[year][capex_case]['wind'] * 1000.0
        cb = CAPEX_SPECS[year][capex_case]['battery'] * 1000.0 / 4.0
        cs = CAPEX_SPECS[year][capex_case]['solar'] * 1000.0
        D_month = demand_mw * 365 * 24 / 12 * AVAILABILITY
        try:
            W,B,S,curt,dis,wg,sg,obj,total_gen,status = optimize_energy_system(
                cw, cb, cs, EFF, D_month, 12, wind_hcf, solar_hcf, withSolar=True
            )
            rows.append({"cell_id": cid, "score": obj, "lat": lat, "lon": lon})
        except Exception:
            continue
    if not rows:
        raise RuntimeError("LP-cost scoring produced no rows.")
    return pd.DataFrame(rows).sort_values("score").reset_index(drop=True)

# Variance/gradient test inside a cell (quick proxy-based dispersion)
def cell_needs_refine(cell_row, step_deg, thresh=0.15):
    latc = float(cell_row["cent_lat"]); lonc = float(cell_row["cent_lon"])
    d = step_deg / 4.0
    samples = [(latc, lonc), (latc+d, lonc), (latc-d, lonc), (latc, lonc+d), (latc, lonc-d)]
    scores = []
    for (la, lo) in samples:
        try:
            s = get_solar_monthly_hcf_from_pvgis(la, lo)
            w = get_wind_monthly_hcf_from_nasa(la, lo, year=2019)
            scores.append(float(np.nansum(s) + np.nansum(w)))
        except Exception:
            pass
    if len(scores) < 3:
        return False
    mu = float(np.mean(scores))
    if mu <= 0:
        return False
    return (max(scores) - min(scores))/mu >= float(thresh)

# ======= Adaptive subdivision driver (with constraints) =======
def select_seeds(cells_gdf: 'gpd.GeoDataFrame', scores_df: pd.DataFrame, sel_cfg: dict) -> 'gpd.GeoDataFrame':
    joined = cells_gdf.merge(scores_df[["cell_id","score"]], on="cell_id", how="inner")
    method = str(sel_cfg.get("method","topk")).lower()
    if method == "topk":
        k = int(sel_cfg.get("k", 40))
        base = joined.sort_values("score", ascending=True).head(k)
    elif method in ("pct","percentile"):
        pct = float(sel_cfg.get("pct", 0.2))
        n = max(1, int(len(joined)*pct))
        base = joined.sort_values("score", ascending=True).head(n)
    else:
        base = joined.sort_values("score", ascending=True).head(40)
    return gpd.GeoDataFrame(base, geometry=base.geometry, crs=joined.crs)

def adaptive_cells(country_gdf: 'gpd.GeoDataFrame', base_cells: 'gpd.GeoDataFrame', cfg: dict):
    total_cap = int(cfg.get("MAX_CELLS_TOTAL", 20000))
    if len(base_cells) > total_cap:
        raise RuntimeError(f"Base cells {len(base_cells)} exceed MAX_CELLS_TOTAL={total_cap}. Increase cell size or cap.")

    # Metric CRS and constraint artifacts
    cons_cfg = cfg.get("CONSTRAINTS", {})
    if bool(cons_cfg.get("ENABLE", True)):
        metric_crs = cons_cfg.get("METRIC_CRS") or _metric_crs_from_country(country_gdf)
        excl_union = build_exclusion_union(country_gdf, cons_cfg, metric_crs)
        acc = cons_cfg.get("ACCESS", {})
        roads_path = acc.get("ROADS_PATH")
        min_d = acc.get("MIN_DIST_TO_ROAD_M")
        max_d = acc.get("MAX_DIST_TO_ROAD_M")
        dem_cfg = cons_cfg.get("DEM", {})
    else:
        metric_crs = _metric_crs_from_country(country_gdf)
        excl_union = None
        roads_path = None; min_d = None; max_d = None
        dem_cfg = None

    # Apply constraints to base cells
    kept0, sum0 = apply_vector_exclusions(base_cells, excl_union, cons_cfg.get("MIN_DEVELOPABLE_FRACTION", 0.40), metric_crs, update_centroids=True)
    kept0, sum0b = apply_access_distance_filter(kept0, roads_path, metric_crs, min_d=min_d, max_d=max_d)
    kept0, sum0c = apply_dem_filters(kept0, dem_cfg)

    # Score base
    mode = str(cfg.get("SCORING_MODE","proxy")).lower()
    if mode == "lp_cost":
        scores_L0 = monthly_cost_score_cells(kept0, year=cfg["YEAR"], capex_case=cfg["CAPEX_CASE"], demand_mw=cfg["DEMAND_MW"])
    else:
        ww = float(cfg.get("PROXY_WEIGHTS", {}).get("wind", 1.0))
        ws = float(cfg.get("PROXY_WEIGHTS", {}).get("solar", 1.0))
        scores_L0 = proxy_score_cells(kept0, year=2019, w_wind=ww, w_solar=ws)

    combined = kept0.copy()
    levels = {"L0": kept0.copy()}
    scores_by_level = {"L0": scores_L0.copy()}
    trace = []
    trace.append(f"L0 constraints → kept={len(kept0)} (vec drop={sum0['dropped']}, access drop={sum0b['dropped']}, dem drop={sum0c['dropped']})")
    remaining_cap = max(0, total_cap - len(combined))
    max_new = int(cfg.get("MAX_NEW_CELLS_PER_LEVEL", 15000))

    prev_cells = kept0.copy()
    prev_scores = scores_L0.copy()
    prev_step = None
    if len(kept0) > 0:
        bx = kept0.iloc[0].geometry.bounds
        prev_step = round(abs(bx[2]-bx[0]), 6)

    for i, lvl in enumerate(cfg.get("CELL_LEVELS", []), start=1):
        if remaining_cap <= 0:
            trace.append(f"L{i}: cap reached → stopping.")
            break

        sel = select_seeds(prev_cells, prev_scores, lvl.get("select", {"method":"topk","k":40}))
        if sel.empty:
            trace.append(f"L{i}: no seeds to refine.")
            levels[f"L{i}"] = gpd.GeoDataFrame(columns=combined.columns, crs=combined.crs)
            scores_by_level[f"L{i}"] = pd.DataFrame(columns=["cell_id","score","lat","lon"])
            continue

        # Variance gate
        var_cfg = lvl.get("variance", {"enable": False, "thresh": 0.15})
        if bool(var_cfg.get("enable", False)):
            thresh = float(var_cfg.get("thresh", 0.15))
            to_refine = []
            it2 = tqdm(sel.iterrows(), total=len(sel), desc=f"L{i} variance check (PVGIS+NASA)") if _USE_TQDM else sel.iterrows()
            for _, r in it2:
                try:
                    if cell_needs_refine(r, prev_step if prev_step else float(cfg["BASE_CELL_STEP_DEG"]), thresh=thresh):
                        to_refine.append(r)
                except Exception:
                    to_refine.append(r)
            refine_df = gpd.GeoDataFrame(to_refine, geometry=[t.geometry for t in to_refine], crs=sel.crs)
        else:
            refine_df = sel

        print(f"[L{i}] seeds={len(sel)}, refine={len(refine_df)}")
        if refine_df.empty:
            trace.append(f"L{i}: variance gate removed all seeds.")
            levels[f"L{i}"] = gpd.GeoDataFrame(columns=combined.columns, crs=combined.crs)
            scores_by_level[f"L{i}"] = pd.DataFrame(columns=["cell_id","score","lat","lon"])
            continue

        new_step = float(lvl.get("new_step_deg", max(0.1, (prev_step or float(cfg["BASE_CELL_STEP_DEG"])) / 2.0)))
        subs_all = []
        it = tqdm(refine_df.itertuples(index=False), total=len(refine_df), desc=f"L{i} subdivide") if _USE_TQDM else refine_df.itertuples(index=False)
        for row in it:
            sub = _subdivide_square_cell(row, new_step)
            if not sub.empty:
                subs_all.append(sub)
        if not subs_all:
            trace.append(f"L{i}: no subcells created.")
            levels[f"L{i}"] = gpd.GeoDataFrame(columns=combined.columns, crs=combined.crs)
            scores_by_level[f"L{i}"] = pd.DataFrame(columns=["cell_id","score","lat","lon"])
            continue
        subs = pd.concat(subs_all, ignore_index=True)

        # Re-apply constraints to new subcells
        subs, sA = apply_vector_exclusions(subs, excl_union, cons_cfg.get("MIN_DEVELOPABLE_FRACTION", 0.40), metric_crs, update_centroids=True)
        subs, sB = apply_access_distance_filter(subs, roads_path, metric_crs, min_d=min_d, max_d=max_d)
        subs, sC = apply_dem_filters(subs, dem_cfg)

        if subs is None or subs.empty:
            trace.append(f"L{i}: all subcells dropped by constraints (vec drop={sA['dropped']}, access drop={sB['dropped']}, dem drop={sC['dropped']}).")
            levels[f"L{i}"] = gpd.GeoDataFrame(columns=combined.columns, crs=combined.crs)
            scores_by_level[f"L{i}"] = pd.DataFrame(columns=["cell_id","score","lat","lon"])
            continue

        # Dedup vs overall
        def _dedup(existing_gdf, new_gdf, precision=6):
            if new_gdf is None or new_gdf.empty:
                return new_gdf
            if existing_gdf is None or existing_gdf.empty:
                return new_gdf
            ex = set(zip(existing_gdf["cent_lat"].round(precision), existing_gdf["cent_lon"].round(precision)))
            keep_rows = []
            for _, r in new_gdf.iterrows():
                key = (round(float(r["cent_lat"]), precision), round(float(r["cent_lon"]), precision))
                if key not in ex:
                    keep_rows.append(r)
            if not keep_rows:
                return gpd.GeoDataFrame(columns=new_gdf.columns, crs=new_gdf.crs)
            return gpd.GeoDataFrame(keep_rows, geometry=[k.geometry for k in keep_rows], crs=new_gdf.crs)

        subs = _dedup(combined, subs)
        if subs is None or subs.empty:
            trace.append(f"L{i}: no new cells after dedup.")
            levels[f"L{i}"] = gpd.GeoDataFrame(columns=combined.columns, crs=combined.crs)
            scores_by_level[f"L{i}"] = pd.DataFrame(columns=["cell_id","score","lat","lon"])
            continue

        n_add = min(len(subs), remaining_cap, int(cfg.get("MAX_NEW_CELLS_PER_LEVEL", 15000)))
        subs = subs.head(n_add).copy()

        levels[f"L{i}"] = subs.copy()
        combined = pd.concat([combined, subs], ignore_index=True)
        remaining_cap = max(0, total_cap - len(combined))

        if mode == "lp_cost":
            scores_i = monthly_cost_score_cells(subs, year=CONFIG["YEAR"], capex_case=CONFIG["CAPEX_CASE"], demand_mw=CONFIG["DEMAND_MW"])
        else:
            ww = float(CONFIG.get("PROXY_WEIGHTS", {}).get("wind", 1.0))
            ws = float(CONFIG.get("PROXY_WEIGHTS", {}).get("solar", 1.0))
            scores_i = proxy_score_cells(subs, year=2019, w_wind=ww, w_solar=ws)
        scores_by_level[f"L{i}"] = scores_i.copy()

        prev_cells = subs.copy()
        prev_scores = scores_i.copy()
        # Use actual child width if available
        try:
            _bx = subs.iloc[0].geometry.bounds
            prev_step = round(abs(_bx[2]-_bx[0]), 6)
        except Exception:
            prev_step = new_step
        trace.append(f"L{i}: constraints kept={len(subs)} (vec drop={sA['dropped']}, access drop={sB['dropped']}, dem drop={sC['dropped']}), combined={len(combined)}")

    return combined, levels, scores_by_level, trace

# ======= Monthly ranking on centroids (final selection) =======
def monthly_rank_points(centroids_df: pd.DataFrame, year: int, capex_case: str, demand_mw: float):
    if optimize_energy_system is None:
        raise RuntimeError("optimization.py not available. Cannot run monthly ranking.")
    rows = []
    n_ok = n_solar_fail = n_wind_fail = n_both_fail = n_infeas = 0
    it = tqdm(centroids_df.iterrows(), total=len(centroids_df), desc="Monthly ranking") if _USE_TQDM else centroids_df.iterrows()
    firstN = int(CONFIG.get("DIAG_PRINT_FIRST_FAILS", 10))

    for idx, r in it:
        lat, lon = float(r["lat"]), float(r["lon"])
        solar_hcf = None
        try:
            solar_hcf = get_solar_monthly_hcf_from_pvgis(lat, lon)
        except Exception as e:
            n_solar_fail += 1
            if n_solar_fail <= firstN:
                print(f"[solar-fail] {lat:.3f},{lon:.3f}: {e}")
        wind_hcf = None
        try:
            wind_hcf  = get_wind_monthly_hcf_from_nasa(lat, lon, year=2019)
        except Exception as e:
            n_wind_fail += 1
            if n_wind_fail <= firstN:
                print(f"[wind-fail]  {lat:.3f},{lon:.3f}: {e}")
        if (solar_hcf is None) and (wind_hcf is None):
            n_both_fail += 1
            continue
        if solar_hcf is None: solar_hcf = [0.0]*12
        if wind_hcf  is None: wind_hcf  = [0.0]*12

        cw = CAPEX_SPECS[year][capex_case]['wind'] * 1000.0
        cb = CAPEX_SPECS[year][capex_case]['battery'] * 1000.0 / 4.0
        cs = CAPEX_SPECS[year][capex_case]['solar'] * 1000.0
        D_month = demand_mw * 365 * 24 / 12 * AVAILABILITY
        try:
            W,B,S,curt,dis,wg,sg,obj,total_gen,status = optimize_energy_system(
                cw, cb, cs, EFF, D_month, 12, wind_hcf, solar_hcf, withSolar=True
            )
            rows.append({"lat":lat,"lon":lon,"obj_cost":obj,"W":W,"B":B,"S":S,"status":status})
            n_ok += 1
        except Exception as e:
            n_infeas += 1
            if n_infeas <= firstN:
                print(f"[infeasible] {lat:.3f},{lon:.3f}: {e}")
            continue

    if not rows:
        raise RuntimeError(
            f"No viable candidates after monthly screen. "
            f"(ok={n_ok}, solar_fail={n_solar_fail}, wind_fail={n_wind_fail}, both_fail={n_both_fail}, infeasible={n_infeas})"
        )
    print(f"[monthly] OK={n_ok} | solar_fail={n_solar_fail} | wind_fail={n_wind_fail} | both_fail={n_both_fail} | infeasible={n_infeas}")
    df = pd.DataFrame(rows).sort_values("obj_cost").reset_index(drop=True)
    return df

# ======= Hourly finalists (ninja) =======
def fetch_ninja_hourly(tech, lat, lon, token, **spec):
    base = "https://www.renewables.ninja/api/"
    url = base + ("data/pv" if tech == "solar" else "data/wind")
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Token {token}", "User-Agent": "AP-IRA-Model/1.0"})
    params = dict(lat=lat, lon=lon, format="json"); params.update(spec or {})
    r = sess.get(url, params=params, timeout=120)
    if r.status_code == 429:
        time.sleep(65); r = sess.get(url, params=params, timeout=120)
    r.raise_for_status()
    return r.json()

def parse_ninja_cf_series(js):
    df = pd.DataFrame.from_dict(js.get("data", {}), orient="index")
    return list(df["electricity"].values())

def hourly_refine(finalists_df: pd.DataFrame, ninja_token: str, year_case: int, capex_case: str, demand_mw: float):
    wind_spec = dict(date_from="2019-01-01", date_to="2019-12-31", capacity=1.0, height=120, turbine="Gamesa G128 5000", format="json")
    solar_spec = dict(date_from="2019-01-01", date_to="2019-12-31", dataset="merra2", capacity=1.0, system_loss=0.1, tracking=0, tilt=35, azim=180, format="json")
    rows = []
    it = tqdm(finalists_df.iterrows(), total=len(finalists_df), desc="Hourly refine (ninja)") if _USE_TQDM else finalists_df.iterrows()
    for _, r in it:
        lat, lon = float(r["lat"]), float(r["lon"])
        w_js = fetch_ninja_hourly("wind", lat, lon, token=ninja_token, **wind_spec)
        s_js = fetch_ninja_hourly("solar", lat, lon, token=ninja_token, **solar_spec)
        cf_w = parse_ninja_cf_series(w_js)  # 8760
        cf_s = parse_ninja_cf_series(s_js)

        cw = CAPEX_SPECS[year_case][capex_case]['wind'] * 1000.0
        cb = CAPEX_SPECS[year_case][capex_case]['battery'] * 1000.0 / 4.0
        cs = CAPEX_SPECS[year_case][capex_case]['solar'] * 1000.0

        D_hour = demand_mw
        W, B, S, curtail, discharge, w_gen, s_gen, obj, total_gen, status = optimize_energy_system(
            cw, cb, cs, EFF, D_hour, len(cf_w), cf_w, cf_s, withSolar=True
        )
        rows.append({"lat":lat, "lon":lon, "hourly_obj_cost":obj, "W_h":W, "B_h":B, "S_h":S})
    return pd.DataFrame(rows).sort_values("hourly_obj_cost").reset_index(drop=True)

# ======= Geometry helpers for border-perfect plots =======
def _ensure_valid(geom):
    "Return a valid geometry (make_valid if available, else buffer(0))."
    try:
        from shapely import make_valid
        g2 = make_valid(geom)
        if g2.is_empty:
            try:
                return geom.buffer(0)
            except Exception:
                return geom
        return g2
    except Exception:
        try:
            return geom.buffer(0)
        except Exception:
            return geom

def _clip_to_country_vector(gdf, country_gdf, eps_m=0.0):
    """
    Vector-clip geometries to the country polygon in a metric CRS, return to EPSG:4326.
    eps_m>0 expands & contracts border slightly to hide micro seams.
    """
    if gdf is None or len(gdf) == 0:
        return gdf
    metric = _metric_crs_from_country(country_gdf)
    g_in = gdf.to_crs(metric).copy()
    c_m = country_gdf.to_crs(metric)
    try:
        c_u = unary_union(c_m.buffer(float(eps_m))) if float(eps_m) != 0.0 else unary_union(c_m.geometry.values)
        if float(eps_m) != 0.0:
            c_u = c_u.buffer(-float(eps_m))
    except Exception:
        c_u = unary_union(c_m.geometry.values)
    # Try vector clip; if it fails, do per-row intersections
    try:
        g_in["geometry"] = g_in.geometry.apply(_ensure_valid)
        clipped = gpd.clip(g_in, gpd.GeoDataFrame(geometry=[c_u], crs=metric))
    except Exception:
        rows = []
        for _, r in g_in.iterrows():
            try:
                inter = _ensure_valid(r.geometry).intersection(c_u)
                if inter.is_empty: 
                    continue
                if getattr(inter, "geoms", None) is not None:
                    for piece in inter.geoms:
                        rec = dict(r); rec["geometry"] = piece
                        rows.append(rec)
                else:
                    rec = dict(r); rec["geometry"] = inter
                    rows.append(rec)
            except Exception:
                continue
        if rows:
            clipped = gpd.GeoDataFrame(rows, crs=metric)
        else:
            clipped = gpd.GeoDataFrame(columns=g_in.columns, crs=metric)
    return clipped.to_crs(4326)

# ======= Plotting helpers (grid/choropleth) =======
import matplotlib.pyplot as plt

def plot_cells_layers(country_gdf: 'gpd.GeoDataFrame',
                      levels_dict: dict,
                      scores_by_level: dict = None,
                      out_png: Path = None,
                      draw_outlines: bool = True,
                      plot_choropleth: bool = True,
                      point_size: float = 6,
                      figsize=(8,9),
                      show=True):
    
    fig, ax = plt.subplots(figsize=figsize)
    # --- Black 'not analyzed' (grid gap) overlay for heatmap ---
    try:
        gap_cfg = hm.get('NOT_ANALYZED', {'ENABLE': True})
        if bool(gap_cfg.get('ENABLE', True)):
            base_src = str(gap_cfg.get('FROM_LEVEL', 'L0_full'))
            if base_src == 'L0_full':
                base_step = float(cfg.get('BASE_CELL_STEP_DEG', 0.25))
                g_base = build_square_fishnet(country_gdf, step_deg=base_step)
            else:
                g_base = levels_dict.get(base_src, None)
            if g_base is not None and len(g_base) > 0:
                eps = hm.get('BORDER_CLIP', {}).get('EPS_M', 0.0)
                try:
                    g_base_clip = _clip_to_country_vector(g_base, country_gdf, eps_m=float(eps))
                except Exception:
                    g_base_clip = g_base
                metric = _metric_crs_from_country(country_gdf)
                g0m = g_base_clip.to_crs(metric)
                country_m = unary_union(country_gdf.to_crs(metric).geometry)
                try:
                    u_grid = unary_union(g0m.geometry.values)
                except Exception:
                    u_grid = unary_union(g0m.buffer(0).geometry.values)
                sb = float(gap_cfg.get('SMOOTH_BUFFER_M', 20.0))
                if sb and sb != 0.0:
                    try:
                        u_grid = u_grid.buffer(sb).buffer(-sb)
                    except Exception:
                        pass
                try:
                    gap_geom = _ensure_valid(country_m).difference(_ensure_valid(u_grid))
                except Exception:
                    try:
                        gap_geom = country_m.buffer(0).difference(u_grid)
                    except Exception:
                        gap_geom = None
                parts = []
                thr = float(gap_cfg.get('MIN_AREA_M2', 20000.0))
                if gap_geom and (not gap_geom.is_empty):
                    if getattr(gap_geom, 'geoms', None) is not None:
                        for piece in gap_geom.geoms:
                            if piece.area >= thr:
                                parts.append(piece)
                    elif gap_geom.area >= thr:
                        parts.append(gap_geom)
                if parts:
                    gap_gdf = gpd.GeoDataFrame(geometry=parts, crs=metric).to_crs(4326)
                    gap_fc = str(gap_cfg.get('FACE_COLOR', 'black'))
                    gap_ec = str(gap_cfg.get('EDGE_COLOR', 'black'))
                    gap_lw = float(gap_cfg.get('LINEWIDTH', 0.0))
                    gap_alpha = float(gap_cfg.get('ALPHA', 1.0))
                    gap_z = int(gap_cfg.get('ZORDER', 0))
                    gap_gdf.plot(ax=ax, facecolor=gap_fc, edgecolor=gap_ec, linewidth=gap_lw, alpha=gap_alpha, zorder=gap_z)
                    try:
                        from matplotlib.patches import Patch
                        lab = str(gap_cfg.get('LEGEND_LABEL', 'Not analyzed (grid gap)'))
                        handles = getattr(ax, '_custom_handles', [])
                        handles.append(Patch(facecolor=gap_fc, edgecolor=gap_ec, label=lab))
                        ax._custom_handles = handles
                    except Exception:
                        pass
                    try:
                        area_km2 = float(sum([g.area for g in parts])) / 1e6
                        country_area_km2 = float(getattr(country_m, 'area', 0.0)) / 1e6
                        pct = (100.0 * area_km2 / country_area_km2) if country_area_km2 > 0 else 0.0
                        print(f"[heatmap] Not-analyzed (grid gap) area = {area_km2:,.1f} km² ({pct:.2f}% of country) in {len(parts)} region(s)")

                    except Exception:
                        pass
    except Exception as _e:
        print(f"[heatmap] WARN: black gap overlay failed: {_e}")
    country_gdf.boundary.plot(ax=ax, linewidth=1)
    # --- Black 'not analyzed' (grid gap) overlay for preview ---
    try:
        gap_cfg = hm_cfg.get('NOT_ANALYZED', {'ENABLE': True})
        if bool(gap_cfg.get('ENABLE', True)):
            base_step = float(cfg.get('BASE_CELL_STEP_DEG', 0.25))
            g_base = build_square_fishnet(country_gdf, step_deg=base_step)
            eps = hm_cfg.get('BORDER_CLIP', {}).get('EPS_M', 0.0)
            try:
                g_base_clip = _clip_to_country_vector(g_base, country_gdf, eps_m=float(eps))
            except Exception:
                g_base_clip = g_base
            metric = _metric_crs_from_country(country_gdf)
            g0m = g_base_clip.to_crs(metric)
            country_m = unary_union(country_gdf.to_crs(metric).geometry)
            try:
                u_grid = unary_union(g0m.geometry.values)
            except Exception:
                u_grid = unary_union(g0m.buffer(0).geometry.values)
            sb = float(gap_cfg.get('SMOOTH_BUFFER_M', 20.0))
            if sb and sb != 0.0:
                try:
                    u_grid = u_grid.buffer(sb).buffer(-sb)
                except Exception:
                    pass
            try:
                gap_geom = _ensure_valid(country_m).difference(_ensure_valid(u_grid))
            except Exception:
                try:
                    gap_geom = country_m.buffer(0).difference(u_grid)
                except Exception:
                    gap_geom = None
            parts = []
            thr = float(gap_cfg.get('MIN_AREA_M2', 20000.0))
            if gap_geom and (not gap_geom.is_empty):
                if getattr(gap_geom, 'geoms', None) is not None:
                    for piece in gap_geom.geoms:
                        if piece.area >= thr:
                            parts.append(piece)
                elif gap_geom.area >= thr:
                    parts.append(gap_geom)
            if parts:
                gap_gdf = gpd.GeoDataFrame(geometry=parts, crs=metric).to_crs(4326)
                gap_fc = str(gap_cfg.get('FACE_COLOR', 'black'))
                gap_ec = str(gap_cfg.get('EDGE_COLOR', 'black'))
                gap_lw = float(gap_cfg.get('LINEWIDTH', 0.0))
                gap_alpha = float(gap_cfg.get('ALPHA', 1.0))
                gap_z = int(gap_cfg.get('ZORDER', 0))
                gap_gdf.plot(ax=ax, facecolor=gap_fc, edgecolor=gap_ec, linewidth=gap_lw, alpha=gap_alpha, zorder=gap_z)
                try:
                    from matplotlib.patches import Patch
                    lab = str(gap_cfg.get('LEGEND_LABEL', 'Not analyzed (grid gap)'))
                    handles = getattr(ax, '_custom_handles', [])
                    handles.append(Patch(facecolor=gap_fc, edgecolor=gap_ec, label=lab))
                    ax._custom_handles = handles
                except Exception:
                    pass
                try:
                    area_km2 = float(sum([g.area for g in parts])) / 1e6
                    country_area_km2 = float(getattr(country_m, 'area', 0.0)) / 1e6
                    pct = (100.0 * area_km2 / country_area_km2) if country_area_km2 > 0 else 0.0
                    print(f"[grid] Not-analyzed (grid gap) area = {area_km2:,.1f} km² ({pct:.2f}% of country) in {len(parts)} region(s)")

                except Exception:
                    pass
    except Exception as _e:
        print(f"[grid] WARN: black gap overlay failed: {_e}")


    # --- Black fill for not-analyzed (grid gap) in preview ---
    try:
        hm = CONFIG.get("HEATMAP", {})
        gap_cfg = hm.get("NOT_ANALYZED", {"ENABLE": True})
        if bool(gap_cfg.get("ENABLE", True)):
            base_step = float(CONFIG.get("BASE_CELL_STEP_DEG", 0.25))
            g_base_gap = build_square_fishnet(country_gdf, step_deg=base_step)
            metric = _metric_crs_from_country(country_gdf)
            g0m = _clip_to_country_vector(g_base_gap, country_gdf).to_crs(metric)
            country_m = unary_union(country_gdf.to_crs(metric).geometry)
            try:
                u_grid = unary_union(g0m.geometry.values)
            except Exception:
                u_grid = unary_union(g0m.buffer(0).geometry.values)
            sb = float(gap_cfg.get("SMOOTH_BUFFER_M", 20.0))
            if sb and sb != 0.0:
                try:
                    u_grid = u_grid.buffer(sb).buffer(-sb)
                except Exception:
                    pass
            try:
                gap_geom = _ensure_valid(country_m).difference(_ensure_valid(u_grid))
            except Exception:
                try:
                    gap_geom = country_m.buffer(0).difference(u_grid)
                except Exception:
                    gap_geom = None
            parts = []
            min_area = float(gap_cfg.get("MIN_AREA_M2", 20000.0))
            if gap_geom and (not gap_geom.is_empty):
                if getattr(gap_geom, "geoms", None) is not None:
                    for piece in gap_geom.geoms:
                        if piece.area >= min_area:
                            parts.append(piece)
                elif gap_geom.area >= min_area:
                    parts.append(gap_geom)
            if parts:
                gap_gdf = gpd.GeoDataFrame(geometry=parts, crs=metric).to_crs(4326)
                gap_fc = str(gap_cfg.get("FACE_COLOR", "black"))
                gap_ec = str(gap_cfg.get("EDGE_COLOR", "black"))
                gap_lw = float(gap_cfg.get("LINEWIDTH", 0.0))
                gap_alpha = float(gap_cfg.get("ALPHA", 1.0))
                gap_z = int(gap_cfg.get("ZORDER", 1))
                gap_gdf.plot(ax=ax, facecolor=gap_fc, edgecolor=gap_ec, linewidth=gap_lw, alpha=gap_alpha, zorder=gap_z)
                # Legend handle
                try:
                    from matplotlib.patches import Patch
                    lab = str(gap_cfg.get("LEGEND_LABEL", "Not analyzed (grid gap)"))
                    handles = getattr(ax, "_custom_handles", [])
                    handles.append(Patch(facecolor=gap_fc, edgecolor=gap_ec, label=lab))
                    ax._custom_handles = handles
                except Exception:
                    pass
                # Console summary
                try:
                    area_km2 = float(sum([g.area for g in parts])) / 1e6
                    gap_area_km2 = float(sum([g.area for g in parts])) / 1e6
                    country_area_km2 = float(getattr(country_m, 'area', 0.0)) / 1e6
                    pct = (100.0 * area_km2 / country_area_km2) if country_area_km2 > 0 else 0.0
                    print(f"[grid] Not-analyzed (grid gap) area = {area_km2:,.1f} km² ({pct:.2f}% of country) in {len(parts)} region(s)")
                except Exception:
                    pass
    except Exception as _e:
        print(f"[grid] WARN: black gap overlay failed: {_e}")

    lvl_names = sorted(levels_dict.keys())
    last_lvl = lvl_names[-1] if lvl_names else None
    if plot_choropleth and last_lvl and last_lvl in levels_dict and scores_by_level and last_lvl in scores_by_level:
        g_last = levels_dict[last_lvl].copy()
        s_last = scores_by_level[last_lvl]
        if not g_last.empty and not s_last.empty:
            g_last = g_last.merge(s_last[["cell_id","score"]], on="cell_id", how="left")
            if bool(CONFIG.get("PLOT_CLIP_TO_BORDER", True)):
                try:
                    g_last = _clip_to_country_vector(g_last, country_gdf)
                except Exception:
                    pass
            g_last.plot(ax=ax, column="score", legend=True)
    if draw_outlines:
        for name in lvl_names:
            g = levels_dict[name]
            if g is None or len(g) == 0:
                continue
            if bool(CONFIG.get("PLOT_CLIP_TO_BORDER", True)):
                try:
                    g = _clip_to_country_vector(g, country_gdf)
                except Exception:
                    pass
            g.boundary.plot(ax=ax, linewidth=0.3)
    lats = []; lons = []
    for name in lvl_names:
        g = levels_dict[name]
        if g is None or len(g) == 0:
            continue
        lats.extend(list(g["cent_lat"].values)); lons.extend(list(g["cent_lon"].values))
    if lats and lons and point_size > 0:
        g_pts = gpd.GeoDataFrame(geometry=gpd.points_from_xy(lons, lats), crs=4326)
        g_pts.plot(ax=ax, markersize=point_size)
    
    # Add legend if custom handles were created
    try:
        if hasattr(ax, "_custom_handles") and ax._custom_handles:
            lg = ax.legend(handles=ax._custom_handles, loc="lower left", frameon=True)
            lg.set_zorder(1000)
    except Exception:
        pass

    ax.set_title("Cell-based adaptive grid (with constraints)")
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.set_aspect("equal", adjustable="box"); ax.grid(True, linestyle=":", linewidth=0.5)
    fig.tight_layout()
    if out_png:
        fig.savefig(out_png, dpi=200)
    if show:
        plt.show()
    else:
        plt.close(fig)

def export_cells_geojson(country_gdf: 'gpd.GeoDataFrame',
                         combined_cells: 'gpd.GeoDataFrame',
                         levels_dict: dict,
                         outdir: Path):
    (outdir / "grid_layers").mkdir(exist_ok=True, parents=True)
    country_gdf.to_file(outdir / "grid_layers" / "country.geojson", driver="GeoJSON")
    combined_cells.to_file(outdir / "grid_layers" / "cells_all.geojson", driver="GeoJSON")
    for name, g in levels_dict.items():
        if g is None or len(g) == 0:
            continue
        g.to_file(outdir / "grid_layers" / f"cells_{name}.geojson", driver="GeoJSON")

# ======= Heatmap helpers (gap-free) =======
import matplotlib.tri as mtri
from matplotlib.path import Path as _MplPath
from matplotlib.patches import PathPatch as _PathPatch

def _country_patch(country_gdf):
    geom = unary_union(country_gdf.to_crs(4326).geometry)
    polys = list(geom.geoms) if getattr(geom, "geoms", None) else [geom]
    vertices = []; codes = []
    for poly in polys:
        x, y = poly.exterior.coords.xy
        pts = np.vstack([x, y]).T
        codes.extend([_MplPath.MOVETO] + [_MplPath.LINETO]*(len(pts)-2) + [_MplPath.CLOSEPOLY])
        vertices.extend(pts.tolist())
        for ring in poly.interiors:
            x, y = ring.coords.xy
            pts = np.vstack([x, y]).T
            codes.extend([_MplPath.MOVETO] + [_MplPath.LINETO]*(len(pts)-2) + [_MplPath.CLOSEPOLY])
            vertices.extend(pts.tolist())
    path = _MplPath(vertices, codes)
    return _PathPatch(path, facecolor="none")

def _country_compound_path(country_gdf):
    geom = unary_union(country_gdf.to_crs(4326).geometry)
    polys = list(geom.geoms) if getattr(geom, "geoms", None) else [geom]
    vertices = []; codes = []
    for poly in polys:
        x, y = poly.exterior.coords.xy
        pts = np.vstack([x, y]).T
        codes.extend([_MplPath.MOVETO] + [_MplPath.LINETO]*(len(pts)-2) + [_MplPath.CLOSEPOLY])
        vertices.extend(pts.tolist())
        for ring in poly.interiors:
            x, y = ring.coords.xy
            pts = np.vstack([x, y]).T
            codes.extend([_MplPath.MOVETO] + [_MplPath.LINETO]*(len(pts)-2) + [_MplPath.CLOSEPOLY])
            vertices.extend(pts.tolist())
    return _MplPath(vertices, codes)

def _metric_crs(country_gdf):
    try:
        return country_gdf.estimate_utm_crs() or "EPSG:3857"
    except Exception:
        return "EPSG:3857"

def _densify_polygon_boundary(country_gdf, spacing_km=20.0, rim_offset_km=0.2):
    """Return lon/lat arrays of points sampled along an inward-offset country perimeter."""
    metric = _metric_crs(country_gdf)
    geom_m = unary_union(country_gdf.to_crs(metric).geometry)
    try:
        rim = geom_m.buffer(-1000.0 * float(rim_offset_km))
        if rim.is_empty:
            rim = geom_m
    except Exception:
        rim = geom_m
    polys = list(rim.geoms) if getattr(rim, "geoms", None) else [rim]
    xs_m = []; ys_m = []
    for poly in polys:
        boundary = poly.exterior
        L = max(boundary.length, 1.0)
        n = max(4, int(np.ceil(L / (1000.0 * float(spacing_km)))))
        for i in range(n):
            p = boundary.interpolate((i + 0.5) * L / n)
            xs_m.append(p.x); ys_m.append(p.y)
    gtmp = gpd.GeoSeries(gpd.points_from_xy(xs_m, ys_m), crs=metric).to_crs(4326)
    return gtmp.x.values, gtmp.y.values

def _idw(boundary_x, boundary_y, xs, ys, vs, k=4, power=2.0):
    """Inverse-distance weighting for arrays of boundary points."""
    xs = np.asarray(xs); ys = np.asarray(ys); vs = np.asarray(vs)
    bx = np.asarray(boundary_x); by = np.asarray(boundary_y)
    out = np.empty_like(bx, dtype=float)
    for i in range(bx.size):
        d2 = (xs - bx[i])**2 + (ys - by[i])**2
        idx = np.argsort(d2)[:max(1,int(k))]
        di = np.sqrt(d2[idx])
        if di[0] < 1e-12:
            out[i] = vs[idx[0]]
        else:
            w = 1.0 / np.maximum(di, 1e-9)**power
            out[i] = float(np.sum(w * vs[idx]) / np.sum(w))
    return out

def _augment_samples_to_boundary(country_gdf, lons, lats, vals,
                                 method="idw", k=4,
                                 spacing_km=20.0, rim_offset_km=0.2):
    """Add 'ghost' samples along an inward-offset border to stabilize interpolation at the edge."""
    bx, by = _densify_polygon_boundary(country_gdf, spacing_km=spacing_km, rim_offset_km=rim_offset_km)
    if bx.size == 0:
        return np.asarray(lons), np.asarray(lats), np.asarray(vals)
    if str(method).lower() == "nn":
        xs = np.asarray(lons); ys = np.asarray(lats); vs = np.asarray(vals)
        ghosts = []
        for xg, yg in zip(bx, by):
            d2 = (xs - xg)**2 + (ys - yg)**2
            ghosts.append(vs[int(np.argmin(d2))])
        gv = np.asarray(ghosts)
    else:
        gv = _idw(bx, by, lons, lats, vals, k=int(k), power=2.0)
    lons2 = np.concatenate([np.asarray(lons), bx])
    lats2 = np.concatenate([np.asarray(lats), by])
    vals2 = np.concatenate([np.asarray(vals), gv])
    return lons2, lats2, vals2

def _choose_heatmap_level(levels_dict, scores_by_level, src):
    lvl_names = sorted(levels_dict.keys())
    if not lvl_names:
        return None, None, None
    if str(src).lower() == "last":
        target = lvl_names[-1]
    else:
        target = str(src) if str(src) in levels_dict else lvl_names[-1]
    cells = levels_dict.get(target, None)
    scores = scores_by_level.get(target, pd.DataFrame(columns=["cell_id","score","lat","lon"])) if scores_by_level else None
    return target, cells, scores


def render_country_heatmap(cfg, country_gdf, levels_dict, scores_by_level, outdir: Path):
    if not bool(cfg.get("HEATMAP", {}).get("ENABLE", True)):
        return
    hm = cfg.get("HEATMAP", {})
    mode = str(hm.get("MODE", "cells")).lower()
    src = hm.get("SOURCE_LEVEL", "last")
    n_levels = int(hm.get("N_LEVELS", 24))
    base_cmap = str(hm.get("CMAP", "viridis"))
    invert = bool(hm.get("INVERT", True))
    title = str(hm.get("TITLE", "Resource merit (lower is better)"))
    draw_country = bool(hm.get("DRAW_COUNTRY_OUTLINE", True))
    draw_cell_out = bool(hm.get("DRAW_CELL_OUTLINES", False))
    save_png = bool(hm.get("SAVE_PNG", True))
    fname = str(hm.get("FILENAME", "heatmap_country.png"))
    figsize = tuple(hm.get("FIGSIZE", (8,9)))
    tri_raster = bool(hm.get("TRI_RASTERIZED", True))
    tri_w = int(hm.get("TRI_GRID_W", 1200))
    mask_dilate = int(hm.get("MASK_DILATE_PIX", 1))
    interp_kind = str(hm.get("INTERPOLATION", "nearest"))

    # Border clip settings
    _border_clip = hm.get("BORDER_CLIP", {"ENABLE": True, "EPS_M": 0.0})
    _clip_enable = bool(_border_clip.get("ENABLE", True))
    _clip_eps    = float(_border_clip.get("EPS_M", 0.0))

    # EXTEND_TO_BOUNDARY (for tri)
    _ext_raw = hm.get("EXTEND_TO_BOUNDARY", {"ENABLE": False})
    ext_cfg = _ext_raw if isinstance(_ext_raw, dict) else {"ENABLE": bool(_ext_raw)}

    # Select level to color
    def _choose_level(levels_dict, scores_by_level, src):
        lvl_names = sorted(levels_dict.keys())
        if not lvl_names: return None, None, None
        target = lvl_names[-1] if str(src).lower() == "last" else (src if src in levels_dict else lvl_names[-1])
        cells = levels_dict.get(target, None)
        scores = scores_by_level.get(target, pd.DataFrame(columns=["cell_id","score","lat","lon"])) if scores_by_level else None
        return target, cells, scores

    level_name, cells, scores = _choose_level(levels_dict, scores_by_level, src)
    if cells is None or len(cells) == 0:
        print("[heatmap] No cells for selected level; skipping.")
        return
    if scores is None or scores.empty or "score" not in scores:
        ww = float(cfg.get("PROXY_WEIGHTS", {}).get("wind", 1.0))
        ws = float(cfg.get("PROXY_WEIGHTS", {}).get("solar", 1.0))
        scores = proxy_score_cells(cells, year=2019, w_wind=ww, w_solar=ws)

    ds = cells.merge(scores[["cell_id","score"]], on="cell_id", how="left").dropna(subset=["score"]).copy()
    if ds.empty:
        print("[heatmap] No scores available; skipping.")
        return

    import matplotlib.pyplot as plt
    
    fig, ax = plt.subplots(figsize=figsize)
    # --- Black fill for country parts not covered by base grid (L0) ---
    try:
        gap_cfg = hm.get("NOT_ANALYZED", {"ENABLE": True})
        if bool(gap_cfg.get("ENABLE", True)):
            base_level_for_gap = str(gap_cfg.get("FROM_LEVEL", "L0_full"))
            if base_level_for_gap == "L0_full":
                g_base_gap = build_square_fishnet(country_gdf, step_deg=float(cfg.get("BASE_CELL_STEP_DEG", 0.25)))
            else:
                g_base_gap = levels_dict.get(base_level_for_gap, None)
            if g_base_gap is not None and len(g_base_gap) > 0:
                metric = _metric_crs_from_country(country_gdf)
                # clip squares to country to ensure no bleed outside
                try:
                    g_base_clip = _clip_to_country_vector(g_base_gap, country_gdf, eps_m=float(hm.get("BORDER_CLIP", {}).get("EPS_M", 0.0))) \
                        if bool(hm.get("BORDER_CLIP", {}).get("ENABLE", True)) else g_base_gap
                except Exception:
                    g_base_clip = g_base_gap
                g0m = g_base_clip.to_crs(metric)
                country_m = unary_union(country_gdf.to_crs(metric).geometry)
                try:
                    u_grid = unary_union(g0m.geometry.values)
                except Exception:
                    u_grid = unary_union(g0m.buffer(0).geometry.values)
                sb = float(gap_cfg.get("SMOOTH_BUFFER_M", 20.0))
                if sb and sb != 0.0:
                    try:
                        u_grid = u_grid.buffer(sb).buffer(-sb)
                    except Exception:
                        pass
                try:
                    gap_geom = _ensure_valid(country_m).difference(_ensure_valid(u_grid))
                except Exception:
                    try:
                        gap_geom = country_m.buffer(0).difference(u_grid)
                    except Exception:
                        gap_geom = None
                parts = []
                min_area = float(gap_cfg.get("MIN_AREA_M2", 20000.0))
                if gap_geom and (not gap_geom.is_empty):
                    if getattr(gap_geom, "geoms", None) is not None:
                        for piece in gap_geom.geoms:
                            if piece.area >= min_area:
                                parts.append(piece)
                    elif gap_geom.area >= min_area:
                        parts.append(gap_geom)
                if parts:
                    gap_gdf = gpd.GeoDataFrame(geometry=parts, crs=metric).to_crs(4326)
                    gap_fc = str(gap_cfg.get("FACE_COLOR", "black"))
                    gap_ec = str(gap_cfg.get("EDGE_COLOR", "black"))
                    gap_lw = float(gap_cfg.get("LINEWIDTH", 0.0))
                    gap_alpha = float(gap_cfg.get("ALPHA", 1.0))
                    gap_z = int(gap_cfg.get("ZORDER", 1))
                    gap_gdf.plot(ax=ax, facecolor=gap_fc, edgecolor=gap_ec, linewidth=gap_lw, alpha=gap_alpha, zorder=gap_z)
                    # Legend handle
                    try:
                        from matplotlib.patches import Patch
                        lab = str(gap_cfg.get("LEGEND_LABEL", "Not analyzed (grid gap)"))
                        handles = getattr(ax, "_custom_handles", [])
                        handles.append(Patch(facecolor=gap_fc, edgecolor=gap_ec, label=lab))
                        ax._custom_handles = handles
                    except Exception:
                        pass
                    # Console summary
                    try:
                        area_km2 = float(sum([g.area for g in parts])) / 1e6
                        gap_area_km2 = float(sum([g.area for g in parts])) / 1e6
                        country_area_km2 = float(getattr(country_m, 'area', 0.0)) / 1e6
                        pct = (100.0 * area_km2 / country_area_km2) if country_area_km2 > 0 else 0.0
                        print(f"[heatmap] Not-analyzed (grid gap) area = {area_km2:,.1f} km² ({pct:.2f}% of country) in {len(parts)} region(s)")

                    except Exception:
                        pass
    except Exception as _e:
        print(f"[heatmap] WARN: black gap overlay failed: {_e}")

    if draw_country:

        country_gdf.boundary.plot(ax=ax, linewidth=1.0, color="black")

    cmap = plt.get_cmap(base_cmap)
    if invert and hasattr(cmap, "reversed"):
        cmap = cmap.reversed()

    # Plot by mode
    if mode == "cells":
        g = ds
        if _clip_enable:
            try:
                g = _clip_to_country_vector(g, country_gdf, eps_m=_clip_eps)
            except Exception as _e:
                print(f"[heatmap] WARN: vector clip failed; using raw cells. {_e}")
        g.plot(ax=ax, column="score", cmap=cmap, edgecolor="none", legend=True)
        if draw_cell_out:
            (g if _clip_enable else ds).boundary.plot(ax=ax, linewidth=0.2, color="white", alpha=0.6)
        # also apply a clip path as guard
        patch = _country_patch(country_gdf); patch.set_transform(ax.transData)
        for c in ax.collections:
            try:
                c.set_clip_path(patch)
            except TypeError:
                c.set_clip_path(patch.get_path(), transform=ax.transData)

    elif mode == "polygon":
        if not _try_import_rasterio():
            print("[heatmap] rasterio not installed → falling back to mode='tri'")
            mode = "tri"
        else:
            from rasterio.features import rasterize, geometry_mask
            from rasterio.transform import from_bounds
            minx, miny, maxx, maxy = country_gdf.total_bounds
            W = max(16, tri_w); H = max(16, int(W * (maxy-miny)/max(1e-9,(maxx-minx)) + 0.5))
            transform = from_bounds(minx, miny, maxx, maxy, W, H)
            shapes = [(geom, float(val)) for geom, val in zip(ds.geometry.values, ds["score"].values)]
            arr = rasterize(shapes=shapes, out_shape=(H, W), transform=transform, fill=np.nan, dtype="float32")
            mask = geometry_mask(country_gdf.geometry.values, out_shape=(H, W), transform=transform, invert=True)
            arr = arr[::-1, :]; mask = mask[::-1, :]
            arr[~mask] = np.nan
            im = ax.imshow(arr, extent=[minx, maxx, miny, maxy], origin='lower', cmap=cmap, interpolation=interp_kind)
            ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
            cbar = fig.colorbar(im, ax=ax, shrink=0.85); cbar.set_label(title)

    if mode == "tri":
        lons = ds["cent_lon"].values; lats = ds["cent_lat"].values; vals = ds["score"].values
        if bool(ext_cfg.get("ENABLE", False)):
            method = str(ext_cfg.get("METHOD", "idw")).lower()
            k = int(ext_cfg.get("K", 4))
            spacing_km = float(ext_cfg.get("PERIMETER_SPACING_KM", 20.0))
            rim_offset_km = float(ext_cfg.get("RIM_OFFSET_KM", 0.2))
            lons, lats, vals = _augment_samples_to_boundary(
                country_gdf, lons, lats, vals, method=method, k=k,
                spacing_km=spacing_km, rim_offset_km=rim_offset_km
            )

        if len(lons) < 3 or np.all(np.isclose(lons, lons[0])) or np.all(np.isclose(lats, lats[0])):
            print("[heatmap] Not enough distinct points for triangulation → falling back to 'cells'.")
            ds.plot(ax=ax, column="score", cmap=cmap, edgecolor="none", legend=True)
        else:
            tri = mtri.Triangulation(lons, lats)
            if tri_raster:
                minx, miny, maxx, maxy = country_gdf.total_bounds
                W = max(64, tri_w)
                H = max(64, int(W * (maxy-miny)/max(1e-9,(maxx-minx)) + 0.5))
                xs = np.linspace(minx, maxx, W); ys = np.linspace(miny, maxy, H)
                XX, YY = np.meshgrid(xs, ys)
                interp = mtri.LinearTriInterpolator(tri, vals)
                Z = interp(XX, YY).filled(np.nan)
                used_rasterio = False
                try:
                    if _try_import_rasterio():
                        from rasterio.features import geometry_mask
                        from rasterio.transform import from_bounds
                        transform = from_bounds(minx, miny, maxx, maxy, W, H)
                        mask = geometry_mask(country_gdf.geometry.values, out_shape=(H, W), transform=transform, invert=True)
                        mask = mask[::-1, :]
                        used_rasterio = True
                    else:
                        raise RuntimeError
                except Exception:
                    path = _country_compound_path(country_gdf)
                    pts = np.column_stack([XX.ravel(), YY.ravel()])
                    rad = 0.5*max((maxx-minx)/W, (maxy-miny)/H)
                    inside = path.contains_points(pts, radius=rad)
                    mask = inside.reshape(H, W)
                if mask_dilate > 0:
                    for _ in range(int(mask_dilate)):
                        mask = mask | np.roll(mask, 1, axis=0) | np.roll(mask, -1, axis=0) | np.roll(mask, 1, axis=1) | np.roll(mask, -1, axis=1)
                Z[~mask] = np.nan
                im = ax.imshow(Z, extent=[minx, maxx, miny, maxy], origin='lower', cmap=cmap, interpolation=interp_kind)
                ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
                cbar = fig.colorbar(im, ax=ax, shrink=0.85); cbar.set_label(title)
            else:
                cont = ax.tricontourf(tri, vals, levels=n_levels, cmap=cmap)
                patch = _country_patch(country_gdf); patch.set_transform(ax.transData)
                for coll in getattr(cont, "collections", []):
                    try:
                        coll.set_clip_path(patch)
                    except TypeError:
                        coll.set_clip_path(patch.get_path(), transform=ax.transData)
        if draw_cell_out:
            ds.boundary.plot(ax=ax, linewidth=0.2, color="white", alpha=0.6)

    # --- Overlays ---
    # A) Dashed outlines for cells at selected level with no score
    _all_cells_this_level = cells.copy()
    _scored_ids = set(ds["cell_id"].astype(str).values.tolist())
    _no_score = _all_cells_this_level[~_all_cells_this_level["cell_id"].astype(str).isin(_scored_ids)].copy()
    _miss_cfg = hm.get("MISSING_CELLS", {})
    _miss_enable = bool(_miss_cfg.get("ENABLE", False)) and (len(_no_score) > 0)
    if _miss_enable:
        try:
            g_no = _clip_to_country_vector(_no_score, country_gdf, eps_m=_clip_eps) if _clip_enable else _no_score
            g_no.boundary.plot(ax=ax,
                               linewidth=float(_miss_cfg.get("LINEWIDTH", 0.6)),
                               linestyle=str(_miss_cfg.get("LINESTYLE", "--")),
                               color=str(_miss_cfg.get("LINECOLOR", "0.5")),
                               alpha=float(_miss_cfg.get("ALPHA", 0.9)),
                               zorder=int(_miss_cfg.get("ZORDER", 9)))
        except Exception as _e:
            print(f"[heatmap] WARN: dashed overlay failed: {_e}")

    # B) Hatching (per-square remainder) or inactive squares
    _hatch_cfg = hm.get("HATCH_INACTIVE", {})
    if bool(_hatch_cfg.get("ENABLE", True)):
        try:
            base_level = str(_hatch_cfg.get("FROM_LEVEL", "L0_full"))
            ref_level  = str(_hatch_cfg.get("REF_LEVEL", "selected"))
            method     = str(_hatch_cfg.get("METHOD", "difference")).lower()
            area_thr   = float(_hatch_cfg.get("AREA_THRESHOLD", 0.05))
            smooth_bm  = float(_hatch_cfg.get("SMOOTH_BUFFER_M", 25.0))
            min_area   = float(_hatch_cfg.get("MIN_AREA_M2", 1000.0))

            # Build base grid to hatch
            if base_level == "L0_full":
                g_base = build_square_fishnet(country_gdf, step_deg=float(cfg.get("BASE_CELL_STEP_DEG", 0.25)))
            else:
                g_base = levels_dict.get(base_level, None)

            if g_base is not None and len(g_base) > 0:
                # Colored reference (use currently colored ds when 'selected')
                if ref_level.lower() == "selected":
                    g_col = ds.copy()
                else:
                    _rf = scores_by_level.get(ref_level, None)
                    _cells_rf = levels_dict.get(ref_level, None)
                    if _cells_rf is not None and _rf is not None and not _rf.empty:
                        g_col = _cells_rf.merge(_rf[["cell_id","score"]], on="cell_id", how="left").dropna(subset=["score"]).copy()
                    else:
                        g_col = ds.copy()

                if g_col is not None and len(g_col) > 0:
                    if method == "difference":
                        metric_crs = _metric_crs_from_country(country_gdf)
                        g0m = g_base.to_crs(metric_crs)
                        gcm = g_col.to_crs(metric_crs)
                        country_m = unary_union(country_gdf.to_crs(metric_crs).geometry)
                        try:
                            u_col = unary_union(gcm.geometry.values)
                        except Exception:
                            u_col = unary_union(gcm.buffer(0).geometry.values)
                        try:
                            u_col = _ensure_valid(u_col).intersection(_ensure_valid(country_m))
                        except Exception:
                            pass
                        if smooth_bm and smooth_bm != 0.0:
                            try:
                                u_col = u_col.buffer(float(smooth_bm)).buffer(-float(smooth_bm))
                            except Exception:
                                pass
                        rem_parts = []
                        for _g in g0m.geometry.values:
                            try:
                                sq_in = _ensure_valid(_g).intersection(country_m)
                            except Exception:
                                try:
                                    sq_in = _g.buffer(0).intersection(country_m)
                                except Exception:
                                    sq_in = None
                            if not sq_in or sq_in.is_empty:
                                continue
                            try:
                                rem = sq_in.difference(u_col)
                            except Exception:
                                try:
                                    rem = sq_in.buffer(0).difference(u_col)
                                except Exception:
                                    rem = None
                            if rem and (not rem.is_empty):
                                if getattr(rem, "geoms", None) is not None:
                                    for piece in rem.geoms:
                                        if piece.area >= min_area:
                                            rem_parts.append(piece)
                                elif rem.area >= min_area:
                                    rem_parts.append(rem)
                        if rem_parts:
                            _remainder_gdf = gpd.GeoDataFrame(geometry=rem_parts, crs=metric_crs).to_crs(4326)
                            _remainder_gdf.plot(ax=ax, facecolor="none",
                                                edgecolor=str(_hatch_cfg.get("EDGE_COLOR", "0.5")),
                                                linewidth=float(_hatch_cfg.get("LINEWIDTH", 0.2)),
                                                hatch=str(_hatch_cfg.get("HATCH", "///")),
                                                zorder=int(_hatch_cfg.get("ZORDER", 2)))
                    elif method == "centroid":
                        # Hatch *inactive* squares: those with no colored centroid
                        pts = gpd.GeoDataFrame(geometry=gpd.points_from_xy(g_col["cent_lon"].values,
                                                                           g_col["cent_lat"].values), crs=4326)
                        try:
                            joined = gpd.sjoin(g_base[["geometry"]].reset_index(drop=False).rename(columns={"index":"_i"}),
                                               pts, predicate="contains", how="left")
                        except TypeError:
                            joined = gpd.sjoin(g_base[["geometry"]].reset_index(drop=False).rename(columns={"index":"_i"}),
                                               pts, how="left", op="contains")
                        has_pt = joined.groupby("_i")["index_right"].apply(lambda s: s.notna().any())
                        inactive_idx = has_pt.index[~has_pt.values]
                        _inactive_gdf = g_base.iloc[inactive_idx].copy()
                        if len(_inactive_gdf) > 0:
                            _inactive_gdf = _clip_to_country_vector(_inactive_gdf, country_gdf, eps_m=_clip_eps) if _clip_enable else _inactive_gdf
                            _inactive_gdf.plot(ax=ax, facecolor="none",
                                               edgecolor=str(_hatch_cfg.get("EDGE_COLOR", "0.5")),
                                               linewidth=float(_hatch_cfg.get("LINEWIDTH", 0.2)),
                                               hatch=str(_hatch_cfg.get("HATCH", "///")),
                                               zorder=int(_hatch_cfg.get("ZORDER", 2)))
                    else:
                        # Area fraction fallback
                        metric_crs = _metric_crs_from_country(country_gdf)
                        g0m = g_base.to_crs(metric_crs)
                        gcm = g_col.to_crs(metric_crs)
                        try:
                            u_col = unary_union(gcm.geometry.values)
                        except Exception:
                            u_col = unary_union(gcm.buffer(0).geometry.values)
                        interA = []; areas = [max(a,1e-9) for a in g0m.area.values]
                        for _g in g0m.geometry.values:
                            try:
                                interA.append(_g.intersection(u_col).area)
                            except Exception:
                                try:
                                    interA.append(_g.buffer(0).intersection(u_col).area)
                                except Exception:
                                    interA.append(0.0)
                        import numpy as _np
                        frac = _np.array(interA) / _np.array(areas)
                        _inactive_gdf = g_base.iloc[(frac <= area_thr).nonzero()[0]].copy()
                        if len(_inactive_gdf) > 0:
                            _inactive_gdf = _clip_to_country_vector(_inactive_gdf, country_gdf, eps_m=_clip_eps) if _clip_enable else _inactive_gdf
                            _inactive_gdf.plot(ax=ax, facecolor="none",
                                               edgecolor=str(_hatch_cfg.get("EDGE_COLOR", "0.5")),
                                               linewidth=float(_hatch_cfg.get("LINEWIDTH", 0.2)),
                                               hatch=str(_hatch_cfg.get("HATCH", "///")),
                                               zorder=int(_hatch_cfg.get("ZORDER", 2)))
        except Exception as _e:
            print(f"[heatmap] WARN: hatching failed: {_e}")

    
    # Add legend for custom overlays if any
    try:
        if hasattr(ax, "_custom_handles") and ax._custom_handles:
            lg = ax.legend(handles=ax._custom_handles, loc="lower left", frameon=True)
            lg.set_zorder(1000)
    except Exception:
        pass

    ax.set_title(f"Heatmap — {cfg['COUNTRY']} — level={level_name} mode={mode}")
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.set_aspect("equal", adjustable="box"); ax.grid(False)
    fig.tight_layout()
    out_png = outdir / fname
    if save_png:
        fig.savefig(out_png, dpi=250)
        print(f"[heatmap] Saved → {out_png.resolve()}")
    plt.show()
    return fig 

# ======= Orchestrator =======
def run_from_config(cfg: dict):
    outdir = Path(cfg["OUTDIR"]); outdir.mkdir(parents=True, exist_ok=True)

    # 1) Build coarse cells
    g_country = load_country_polygon(cfg["COUNTRY"],
                                     use_iso3=bool(cfg.get("USE_ISO3_MATCH", True)),
                                     metropolitan_only=bool(cfg.get("METROPOLITAN_ONLY", True)))
    cells_L0 = build_square_fishnet(g_country, step_deg=float(cfg["BASE_CELL_STEP_DEG"]))
    if len(cells_L0) > cfg["MAX_CELLS_TOTAL"]:
        raise RuntimeError(f"Base cells {len(cells_L0)} exceed MAX_CELLS_TOTAL={cfg['MAX_CELLS_TOTAL']}")

    levels = {"L0": cells_L0.copy()}
    scores_by_level = {}
    combined_cells = cells_L0.copy()
    traces = []

    # 2) Adaptive subdivision (with constraints)
    combined_cells, levels, scores_by_level, traces = adaptive_cells(g_country, cells_L0, cfg)

    # 3) Grid/choropleth plot + export layers
    if cfg.get("PLOT_GRID", True):
        png = outdir / f"cells_adaptive_{cfg['COUNTRY']}_CONSTRAINED.png"
        plot_cells_layers(
            g_country, levels, scores_by_level=scores_by_level,
            out_png=png,
            draw_outlines=bool(cfg.get("DRAW_OUTLINES", True)),
            plot_choropleth=bool(cfg.get("PLOT_CHOROPLETH", True)),
            point_size=float(cfg.get("POINT_SIZE", 6)),
            figsize=tuple(cfg.get("FIGSIZE", (8, 9))),
            show=True
        )
        print(f"[grid] Saved preview → {png.resolve()}")

    if cfg.get("SAVE_GEOJSON", True):
        export_cells_geojson(g_country, combined_cells, levels, outdir)
        print(f"[grid] Wrote GeoJSON layers under → {(outdir / 'grid_layers').resolve()}")

    # 4) Render the **heatmap**
    render_country_heatmap(cfg, g_country, levels, scores_by_level, outdir)

    if traces:
        for msg in traces:
            print("[adaptive]", msg)

    # 5) Exit early if just plotting
    if bool(cfg.get("ONLY_PLOT_GRID", False)):
        print("[grid] ONLY_PLOT_GRID=True → exiting after grid preview/exports/heatmap.")
        return

    # 6) Monthly ranking on centroids of combined cells
    if optimize_energy_system is None:
        raise RuntimeError("optimization.py not available. Cannot proceed to monthly ranking.")

    sites_df = combined_cells.rename(columns={"cent_lat":"lat","cent_lon":"lon"})[["lat","lon"]]
    monthly_ranked = monthly_rank_points(sites_df, year=cfg["YEAR"], capex_case=cfg["CAPEX_CASE"], demand_mw=cfg["DEMAND_MW"])
    monthly_ranked.to_csv(outdir / "monthly_ranked.csv", index=False)

    shortlist_df = monthly_ranked.head(cfg["SHORTLIST"]).copy()
    shortlist_df.to_csv(outdir / "shortlist_monthly.csv", index=False)

    # 7) Hourly refine (optional)
    if cfg["NINJA_TOKEN"]:
        finalists_in = shortlist_df.head(cfg["FINALISTS"]).copy()
        hourly_ranked = hourly_refine(finalists_in, ninja_token=cfg["NINJA_TOKEN"],
                                      year_case=cfg["YEAR"], capex_case=cfg["CAPEX_CASE"],
                                      demand_mw=cfg["DEMAND_MW"])
        hourly_ranked.to_csv(outdir / "hourly_ranked.csv", index=False)
        base = hourly_ranked.rename(columns={"hourly_obj_cost":"score"})
        topk_df = select_top_k(base, k=cfg["TOPK"], score_col="score",
                               cluster=bool(cfg["CLUSTER_ON"]), min_sep_km=float(cfg["MIN_SEP_KM"]))
        topk_df = topk_df.rename(columns={"score":"hourly_obj_cost"})
    else:
        base = shortlist_df.rename(columns={"obj_cost":"score"})
        topk_df = select_top_k(base, k=cfg["TOPK"], score_col="score",
                               cluster=bool(cfg["CLUSTER_ON"]), min_sep_km=float(cfg["MIN_SEP_KM"]))
        topk_df = topk_df.rename(columns={"score":"obj_cost"})

    topk_df.to_csv(outdir / "topk.csv", index=False)

    summary = {
        "args": cfg,
        "n_cells_L0": int(len(cells_L0)),
        "n_cells_combined": int(len(combined_cells)),
        "n_shortlist": int(len(shortlist_df)),
        "topk_rows": int(len(topk_df))
    }
    (outdir / "run_summary.json").write_text(json.dumps(summary, indent=2))

    print("\n=== Top-{} sites ===".format(cfg["TOPK"]))
    print(topk_df.head(cfg["TOPK"]))
    print("\nOutputs saved to:", outdir.resolve())

# ---- Entry point ----
if __name__ == "__main__":
    run_from_config(CONFIG)
