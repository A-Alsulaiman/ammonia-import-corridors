# -*- coding: utf-8 -*-
"""
ammonia_to_port_optimizer.py  (v3.2: WPI DMS/DMM parser + sea-export filter + plotting tweaks)

What’s in here
--------------
• Robust WPI latitude/longitude parser that correctly decodes **degrees + minutes (+ seconds)**
  with hemisphere letters (e.g., '41 08.5N', '8 39.1W' or '41°08′30″N').
• Online loaders for **export ports** (WPI, UN/LOCODE, OSM) with an OSM coastline proximity
  filter to remove inland river/lake ports.
• Vectorized nearest-port + delivered NH₃ $/t enrichment.
• Plot helpers (no seaborn) that show country outline + ports + sites.

"""

from __future__ import annotations
import io
import json
import math
import os
import re
from dataclasses import dataclass
from typing import Iterable, Tuple, Dict, Optional

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, LineString

try:
    import requests
except Exception as _e:
    requests = None  # user must have 'requests' installed for online loaders

# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance between two points on WGS84 in **kilometers**."""
    R = 6371.0088
    phi1 = math.radians(float(lat1)); phi2 = math.radians(float(lat2))
    dphi = math.radians(float(lat2) - float(lat1)); dl = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
    return 2*R*math.atan2(math.sqrt(a), math.sqrt(1-a))


def _decode_unlocode_coord(coord: str) -> Optional[Tuple[float, float]]:
    """Decode UN/LOCODE coordinate string like '4916N 12307W' into (lat, lon)."""
    if not isinstance(coord, str):
        return None
    s = coord.strip().replace(',', ' ').replace(';', ' ')
    parts = s.split()
    if len(parts) == 1:
        p = parts[0]
        try:
            idx = p.find('N') if 'N' in p else p.find('S')
            if idx == -1:
                return None
            latp, lonp = p[:idx+1], p[idx+1:]
            parts = [latp, lonp]
        except Exception:
            return None
    if len(parts) != 2:
        return None

    def _one(v: str, hemi_pos: str, hemi_neg: str) -> Optional[float]:
        v = v.strip().upper()
        sign = 1.0
        if v.endswith(hemi_neg):
            sign = -1.0
        if v[-1] in (hemi_pos, hemi_neg):
            v = v[:-1]
        else:
            return None
        if len(v) < 4:
            return None
        try:
            if len(v) in (4,5):  # DDMM or DDDMM
                deg = int(v[:-2]); minutes = int(v[-2:])
                return sign * (deg + minutes/60.0)
            elif len(v) in (6,7):  # DDMMSS or DDDMMSS
                deg = int(v[:-4]); minutes = int(v[-4:-2]); sec = int(v[-2:])
                return sign * (deg + minutes/60.0 + sec/3600.0)
            else:
                # decimal degrees (rare)
                return sign * float(v)
        except Exception:
            return None

    lat = _one(parts[0], 'N', 'S')
    lon = _one(parts[1], 'E', 'W')
    if (lat is None) or (lon is None):
        return None
    return (lat, lon)


def _iso2_to_iso3(iso2: str) -> Optional[str]:
    if not isinstance(iso2, str) or len(iso2) < 2:
        return None
    iso2 = iso2.strip().upper()
    # Try pycountry if available (recommended)
    try:
        import pycountry  # type: ignore
        c = pycountry.countries.get(alpha_2=iso2)
        if c is not None:
            return c.alpha_3
    except Exception:
        pass
    # minimal fallbacks for common codes (extend if needed)
    mini = {
        "US":"USA","GB":"GBR","FR":"FRA","DE":"DEU","ES":"ESP","IT":"ITA","NL":"NLD","BE":"BEL","MA":"MAR",
        "PT":"PRT","NO":"NOR","SE":"SWE","FI":"FIN","DK":"DNK","IE":"IRL","CA":"CAN","AU":"AUS","NZ":"NZL",
        "BR":"BRA","AR":"ARG","MX":"MEX","CN":"CHN","JP":"JPN","KR":"KOR","IN":"IND","SA":"SAU","AE":"ARE",
        "EG":"EGY","ZA":"ZAF","NG":"NGA","GH":"GHA","KE":"KEN","RU":"RUS","TR":"TUR","PL":"POL","RO":"ROU",
        "GR":"GRC"
    }
    return mini.get(iso2)


def _to_gdf(df: pd.DataFrame, lat_col: str, lon_col: str) -> gpd.GeoDataFrame:
    g = gpd.GeoDataFrame(df.copy(), geometry=gpd.points_from_xy(df[lon_col], df[lat_col]), crs=4326)
    return g


# --------------------------------------------------------------------------------------
# Local CSV loaders
# --------------------------------------------------------------------------------------

def load_ports_from_csv(csv_path: str) -> gpd.GeoDataFrame:
    """
    Load a simple CSV of ports with columns:
        port_name, country_iso3, lat, lon
    Returns a GeoDataFrame in EPSG:4326.
    """
    df = pd.read_csv(csv_path)
    required = {'port_name','country_iso3','lat','lon'}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {missing}")
    g = gpd.GeoDataFrame(df.copy(),
                         geometry=gpd.points_from_xy(df['lon'], df['lat']),
                         crs=4326)
    return g[['port_name','country_iso3','lat','lon','geometry']]


def load_ports_from_unlocode(csv_path: str, keep_if_missing_coords: bool=False) -> gpd.GeoDataFrame:
    """
    Load a UN/LOCODE CSV export. Keeps records whose 'Function' contains '1' (sea port).
    Coordinates are parsed from the 'Coordinates' column.
    """
    df = pd.read_csv(csv_path, dtype=str, low_memory=False)
    # Normalize headers (UN sometimes uses different capitalization)
    cols = {c.lower(): c for c in df.columns}
    func_col = cols.get('function') or cols.get('functions') or 'Function'
    coord_col = cols.get('coordinates') or 'Coordinates'
    name_col = cols.get('name') or cols.get('namewoaccent') or 'NameWoAccent'
    country_col = cols.get('country') or cols.get('country code') or 'Country'
    if func_col not in df or coord_col not in df:
        raise ValueError("UN/LOCODE CSV missing expected 'Function' / 'Coordinates' columns.")
    df = df[df[func_col].astype(str).str.contains('1', na=False)].copy()  # Sea port
    lat = []; lon = []
    for v in df[coord_col].astype(str).values:
        dd = _decode_unlocode_coord(v)
        if dd is None:
            lat.append(np.nan); lon.append(np.nan)
        else:
            lat.append(dd[0]); lon.append(dd[1])
    df['lat'] = lat; df['lon'] = lon
    if not keep_if_missing_coords:
        df = df.dropna(subset=['lat','lon'])
    df['port_name'] = df.get(name_col, pd.Series(['port']*len(df))).astype(str)
    # ISO2→ISO3 if present
    if country_col in df.columns:
        iso3 = []
        for v in df[country_col].astype(str).values:
            x = _iso2_to_iso3(v)
            iso3.append(x if x else None)
        df['country_iso3'] = iso3
    else:
        df['country_iso3'] = None
    g = gpd.GeoDataFrame(df[['port_name','country_iso3','lat','lon']].copy(),
                         geometry=gpd.points_from_xy(df['lon'], df['lat']), crs=4326)
    return g


# --------------------------------------------------------------------------------------
# Online loaders
# --------------------------------------------------------------------------------------

def _cache_dir() -> str:
    d = os.path.join(os.path.dirname(__file__), ".ports_cache")
    os.makedirs(d, exist_ok=True)
    return d


def _http_get(url: str, timeout: int = 90, headers: Optional[Dict[str,str]] = None) -> bytes:
    if requests is None:
        raise RuntimeError("The 'requests' package is required for online loaders. Please 'pip install requests'.")
    hdrs = {"User-Agent": "AP-IRA-Ports/1.0 (+ammonia_to_port_optimizer.py)"}
    if headers: hdrs.update(headers)
    r = requests.get(url, timeout=timeout, headers=hdrs)
    r.raise_for_status()
    return r.content


def _download_wpi_csv() -> pd.DataFrame:
    """
    Download NGA World Port Index CSV (UpdatedPub150.csv) into cache and return DataFrame.
    If the URL changes in the future, update the constant below or override via env var WPI_CSV_URL.
    """
    cache = _cache_dir()
    url = os.environ.get("WPI_CSV_URL",
                         "https://msi.nga.mil/api/publications/download?key=16920959%2FSFH00000%2FUpdatedPub150.csv&type=view")
    fp = os.path.join(cache, "UpdatedPub150.csv")
    if not os.path.exists(fp):
        data = _http_get(url, timeout=120)
        with open(fp, "wb") as f:
            f.write(data)
    # Try basic CSV parse
    try:
        df = pd.read_csv(fp)
    except Exception:
        # Some releases contain BOM/encoding quirks; use python engine
        df = pd.read_csv(fp, engine="python")
    return df


def _parse_wpi_df(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Robust WPI parser that properly decodes DMS/DMM strings and preserves hemisphere."""
    cols = {c.lower(): c for c in df.columns}
    # required columns (WPI sometimes changes capitalization)
    name_col = cols.get("main port name") or cols.get("main_port_name") or "Main Port Name"
    lat_col  = cols.get("latitude") or "Latitude"
    lon_col  = cols.get("longitude") or "Longitude"
    ctry_col = cols.get("country code") or "Country Code"

    keep = df[[name_col, lat_col, lon_col]].copy()
    keep.columns = ["port_name", "lat_raw", "lon_raw"]

    def _deg_min_sec_to_dd(s: str, hemi_pos: str, hemi_neg: str) -> float:
        """
        Accepts:
          - decimal degrees:  38.7167 or -9.133
          - D M [S] H:        38 43.0 N, 9 08 00 W, 38°43.0'N, 9°08'W
        Returns signed decimal degrees.
        """
        if s is None:
            return np.nan
        if isinstance(s, (int, float, np.floating)):
            return float(s)

        txt = str(s).strip().upper()
        # Extract all numbers (deg, min, sec)
        nums = re.findall(r'[-+]?\d+(?:\.\d+)?', txt)
        if not nums:
            return np.nan

        # Single number → decimal degrees; apply hemisphere if present
        if len(nums) == 1:
            v = float(nums[0])
            if hemi_neg in txt and v > 0:
                v = -v
            if hemi_pos in txt and v < 0:
                v = abs(v)
            return v

        # Treat as deg/min[/sec]
        deg = abs(float(nums[0]))
        minutes = float(nums[1]) if len(nums) >= 2 else 0.0
        seconds = float(nums[2]) if len(nums) >= 3 else 0.0
        v = deg + minutes / 60.0 + seconds / 3600.0

        # Determine sign using hemisphere if present, else any leading '-'
        sign = 1.0
        if hemi_neg in txt:
            sign = -1.0
        elif hemi_pos in txt:
            sign = 1.0
        elif "-" in txt and "+" not in txt:
            sign = -1.0
        return sign * v

    keep["lat"] = keep["lat_raw"].apply(lambda s: _deg_min_sec_to_dd(s, "N", "S"))
    keep["lon"] = keep["lon_raw"].apply(lambda s: _deg_min_sec_to_dd(s, "E", "W"))
    keep = keep.dropna(subset=["lat","lon"])

    # Country ISO3 (best effort)
    if ctry_col in df.columns:
        iso3 = []
        for v in df[ctry_col].astype(str).values[:len(keep)]:
            x = _iso2_to_iso3(v)
            iso3.append(x if x else None)
        keep["country_iso3"] = iso3[:len(keep)]
    else:
        keep["country_iso3"] = None

    g = _to_gdf(keep[["port_name","country_iso3","lat","lon"]], "lat", "lon")
    return g[["port_name","country_iso3","lat","lon","geometry"]]


def _download_unlocode_csv() -> str:
    """
    Scrape the UNECE UN/LOCODE download page for the latest CSV link and download it.
    Returns the local CSV file path.
    """
    cache = _cache_dir()
    page_url = "https://unece.org/trade/cefact/UNLOCODE-Download"
    html = _http_get(page_url, timeout=90).decode("utf-8", errors="ignore")
    # Find the first CSV link on the page
    m = re.search(r'href="([^"]+\.csv)"', html, flags=re.IGNORECASE)
    if not m:
        # Alternate page
        page_url2 = "https://unece.org/trade/cefact/unlocode-code-list-country-and-territory"
        html = _http_get(page_url2, timeout=90).decode("utf-8", errors="ignore")
        m = re.search(r'href="([^"]+\.csv)"', html, flags=re.IGNORECASE)
    if not m:
        raise RuntimeError("Could not find UN/LOCODE CSV link on UNECE pages.")
    csv_url = m.group(1)
    # absolute URL if needed
    if csv_url.startswith("/"):
        root = "https://unece.org"
        csv_url = root + csv_url
    # Download to cache
    name = os.path.basename(csv_url.split("?")[0])
    fp = os.path.join(cache, name or "UNLOCODE_latest.csv")
    if not os.path.exists(fp):
        data = _http_get(csv_url, timeout=120)
        with open(fp, "wb") as f:
            f.write(data)
    return fp


def _download_osm_ports(country_iso3: str) -> gpd.GeoDataFrame:
    """
    Query OSM Overpass API for harbour/port features in a given ISO3 country.
    Filters out leisure marinas. Returns a GeoDataFrame of points.
    """
    if requests is None:
        raise RuntimeError("requests is required for Overpass queries.")
    # Build Overpass query (ISO3)
    iso3 = country_iso3.strip().upper()
    query = f"""
    [out:json][timeout:180];
    area["ISO3166-1:alpha3"="{iso3}"][admin_level=2];
    (
      node["harbour"](area);
      way["harbour"](area);
      relation["harbour"](area);
      node["industrial"="port"](area);
      way["industrial"="port"](area);
      relation["industrial"="port"](area);
      node["seamark:type"="harbour"](area);
      way["seamark:type"="harbour"](area);
      relation["seamark:type"="harbour"](area);
    );
    // Exclude obvious leisure marinas
    (
      ._;
      - nwr["leisure"="marina"];
    );
    out center tags;
    """
    url = "https://overpass-api.de/api/interpreter"
    r = requests.post(url, data={"data": query}, timeout=180, headers={"User-Agent":"AP-IRA-Ports/1.0"})
    r.raise_for_status()
    js = r.json()
    rows = []
    for el in js.get("elements", []):
        tags = el.get("tags", {}) or {}
        name = tags.get("name") or tags.get("seamark:name") or "harbour"
        lat = el.get("lat")
        lon = el.get("lon")
        if lat is None or lon is None:
            ctr = el.get("center")
            if ctr:
                lat = ctr.get("lat"); lon = ctr.get("lon")
        if (lat is None) or (lon is None):
            continue
        rows.append({"port_name": name, "country_iso3": iso3, "lat": float(lat), "lon": float(lon)})
    if not rows:
        return gpd.GeoDataFrame(columns=["port_name","country_iso3","lat","lon","geometry"], crs=4326)
    df = pd.DataFrame(rows)
    return _to_gdf(df, "lat", "lon")[["port_name","country_iso3","lat","lon","geometry"]]


def _download_osm_coastline(country_iso3: str) -> gpd.GeoDataFrame:
    """
    Download coastline (natural=coastline) geometry for the given ISO3 via Overpass.
    Returns line geometries in EPSG:4326. Empty GeoDataFrame if none.
    """
    if requests is None:
        raise RuntimeError("requests is required for Overpass queries.")
    iso3 = country_iso3.strip().upper()
    query = f"""
    [out:json][timeout:180];
    area["ISO3166-1:alpha3"="{iso3}"][admin_level=2];
    (
      way["natural"="coastline"](area);
      relation["natural"="coastline"](area);
    );
    out geom;
    """
    url = "https://overpass-api.de/api/interpreter"
    r = requests.post(url, data={"data": query}, timeout=180, headers={"User-Agent":"AP-IRA-Ports/1.0"})
    r.raise_for_status()
    js = r.json()
    lines = []
    for el in js.get("elements", []):
        geom = el.get("geometry") or []
        pts = [(pt["lon"], pt["lat"]) for pt in geom if "lat" in pt and "lon" in pt]
        if len(pts) >= 2:
            try:
                lines.append(LineString(pts))
            except Exception:
                continue
    if not lines:
        return gpd.GeoDataFrame(columns=["geometry"], crs=4326)
    return gpd.GeoDataFrame(geometry=lines, crs=4326)


def _filter_to_sea_export_ports(ports_gdf: gpd.GeoDataFrame,
                                country_iso3: str,
                                max_distance_km: float = 35.0) -> gpd.GeoDataFrame:
    """
    Keep only ports within `max_distance_km` of the **coastline** (OSM natural=coastline).
    This removes inland river ports and lakes, ensuring sea export focus.
    If coastline cannot be fetched, returns the input unchanged.
    """
    try:
        coast = _download_osm_coastline(country_iso3)
    except Exception:
        return ports_gdf
    if coast is None or coast.empty or ports_gdf is None or ports_gdf.empty:
        return ports_gdf
    try:
        # Work in metric CRS for buffering & distances
        coast_m = coast.to_crs(3857)
        ports_m = ports_gdf.to_crs(3857)
        buf = float(max_distance_km) * 1000.0
        coast_buf = gpd.GeoSeries(coast_m.buffer(buf)).unary_union
        mask = ports_m.geometry.apply(lambda g: g.within(coast_buf))
        kept = ports_gdf.loc[mask.values].copy()
        return kept if not kept.empty else ports_gdf
    except Exception:
        return ports_gdf


def load_ports_online(country_iso3: str, source: str = "AUTO") -> gpd.GeoDataFrame:
    """
    Online port loader with fallback chain **filtered to sea export ports**.

    Parameters
    ----------
    country_iso3 : str
        Target country (ISO3) used for OSM query and (optionally) for filtering WPI/UNLOCODE.
    source : {"AUTO","WPI","UNLOCODE","OSM"}
        Preferred source. "AUTO" tries WPI → UNLOCODE → OSM.

    Returns
    -------
    gpd.GeoDataFrame
        Columns: ['port_name','country_iso3','lat','lon','geometry']
    """
    src = str(source).upper()
    last_err = None

    # 1) WPI
    if src in ("AUTO","WPI"):
        try:
            df = _download_wpi_csv()
            g = _parse_wpi_df(df)
            # Optional: filter to country if WPI has country_iso3 resolved.
            if "country_iso3" in g and g["country_iso3"].notna().any():
                g = g[(g["country_iso3"].fillna("") == country_iso3) | (g["country_iso3"].isna())].copy()
            # Keep only sea-export ports
            g = _filter_to_sea_export_ports(g, country_iso3)
            return g
        except Exception as e:
            last_err = e

    # 2) UN/LOCODE
    if src in ("AUTO","UNLOCODE"):
        try:
            fp = _download_unlocode_csv()
            g_all = load_ports_from_unlocode(fp, keep_if_missing_coords=False)
            # Filter by country if available in CSV (some rows don't have ISO)
            if "country_iso3" in g_all.columns and g_all["country_iso3"].notna().any():
                g = g_all[g_all["country_iso3"] == country_iso3].copy()
            else:
                g = g_all.copy()
            g = _filter_to_sea_export_ports(g, country_iso3)
            return g
        except Exception as e:
            last_err = e

    # 3) OSM (Overpass)
    if src in ("AUTO","OSM"):
        try:
            g = _download_osm_ports(country_iso3)
            g = _filter_to_sea_export_ports(g, country_iso3)
            return g
        except Exception as e:
            last_err = e

    raise RuntimeError(f"Failed to load ports from source={source}. Last error: {last_err}")


# --------------------------------------------------------------------------------------
# Cost coefficients
# --------------------------------------------------------------------------------------

EU_ISO3 = {
    'AUT','BEL','BGR','HRV','CYP','CZE','DNK','EST','FIN','FRA','DEU','GRC','HUN','IRL',
    'ITA','LVA','LTU','LUX','MLT','NLD','POL','PRT','ROU','SVK','SVN','ESP','SWE'
}

# Global default derived from IEA (2019) ammonia transmission discussion.
DEFAULT_PIPELINE_USD_PER_TKM = 0.05  # $/t-NH3/km

# EU midrange from EHB guidance (converted to $/t-NH3/km).
EU_PIPELINE_USD_PER_TKM = 0.04

def pipeline_coeff_for_country(iso3: str,
                               country_coeff_overrides: Optional[Dict[str, float]] = None) -> float:
    """
    Return pipeline transport coefficient in USD per t-NH3 per km for given ISO3.
    Order of precedence: explicit override → EU midrange → default global.
    """
    if country_coeff_overrides and iso3 in country_coeff_overrides:
        return float(country_coeff_overrides[iso3])
    if iso3 in EU_ISO3:
        return EU_PIPELINE_USD_PER_TKM
    return DEFAULT_PIPELINE_USD_PER_TKM


# --------------------------------------------------------------------------------------
# Main enrichment
# --------------------------------------------------------------------------------------

@dataclass
class AmmoniaParams:
    electricity_intensity_mwh_per_t: float = 10.0  # IEA ATR (2021)
    max_distance_km: Optional[float] = None        # drop sites further than this
    topk: int = 10                                  # for plotting/summary only

def _nearest_ports_vectorized(sites_df: pd.DataFrame, ports_gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    s_lat = sites_df['lat'].values.astype(float); s_lon = sites_df['lon'].values.astype(float)
    p_lat = ports_gdf['lat'].values.astype(float); p_lon = ports_gdf['lon'].values.astype(float)
    nS = s_lat.size; nP = p_lat.size
    best_idx = np.full(nS, -1, dtype=int)
    best_dist = np.full(nS, np.inf, dtype=float)
    # Chunk over ports to bound memory usage
    CHUNK = 1024
    for start in range(0, nP, CHUNK):
        end = min(nP, start + CHUNK)
        # Broadcast computation
        la2 = p_lat[start:end][None, :]
        lo2 = p_lon[start:end][None, :]
        la1 = s_lat[:, None]; lo1 = s_lon[:, None]
        # vectorized haversine
        R = 6371.0088
        phi1 = np.radians(la1); phi2 = np.radians(la2)
        dphi = np.radians(la2 - la1); dl = np.radians(lo2 - lo1)
        a = np.sin(dphi/2)**2 + np.cos(phi1)*np.cos(phi2)*np.sin(dl/2)**2
        d = 2*R*np.arctan2(np.sqrt(a), np.sqrt(1-a))
        # best within chunk
        idx_chunk = np.argmin(d, axis=1)
        dist_chunk = d[np.arange(d.shape[0]), idx_chunk]
        # update global best
        update = dist_chunk < best_dist
        best_dist[update] = dist_chunk[update]
        best_idx[update] = start + idx_chunk[update]
    res = sites_df.copy()
    res['nearest_port_idx'] = best_idx
    res['distance_km'] = best_dist
    res['nearest_port'] = ports_gdf['port_name'].iloc[best_idx].values
    # Country may be missing (WPI/OSM); keep for diagnostics only
    res['nearest_port_country'] = ports_gdf.get('country_iso3', pd.Series([None]*len(ports_gdf))).iloc[best_idx].values \
        if hasattr(ports_gdf, 'columns') and 'country_iso3' in ports_gdf.columns else None
    res['port_lat'] = ports_gdf['lat'].iloc[best_idx].values
    res['port_lon'] = ports_gdf['lon'].iloc[best_idx].values
    return res

def attach_ports_and_ammonia_costs(
    sites_df: pd.DataFrame,
    ports_gdf: gpd.GeoDataFrame,
    country_iso3: str,
    electricity_intensity_mwh_per_t: float = 10.0,
    country_coeff_overrides: Optional[Dict[str, float]] = None,
    max_distance_km: Optional[float] = None,
) -> pd.DataFrame:
    """
    Merge **nearest port** and **delivered ammonia cost** onto sites.

    Expected columns in `sites_df`:
        - lat, lon (float)
        - lcoe_usd_per_mwh (float) : per-site electricity cost proxy (any source)
    Returned columns (adds):
        - nearest_port, distance_km, pipeline_coeff_usd_per_tkm
        - nh3_prod_cost_usd_per_t, nh3_transport_usd_per_t, nh3_delivered_usd_per_t
    """
    if 'lcoe_usd_per_mwh' not in sites_df.columns:
        raise KeyError("sites_df must include 'lcoe_usd_per_mwh'. Compute it before calling this function.")
    if ports_gdf is None or ports_gdf.empty:
        raise ValueError("ports_gdf is empty.")
    # nearest port
    res = _nearest_ports_vectorized(sites_df[['lat','lon']].copy().assign(dummy=1).drop(columns=['dummy']), ports_gdf)
    # pipeline coeff
    coeff = pipeline_coeff_for_country(country_iso3, country_coeff_overrides=country_coeff_overrides)
    res['pipeline_coeff_usd_per_tkm'] = coeff
    # ammonia costs
    res = res.join(sites_df[['lcoe_usd_per_mwh']], how='left')
    res['nh3_prod_cost_usd_per_t'] = res['lcoe_usd_per_mwh'].astype(float) * float(electricity_intensity_mwh_per_t)
    res['nh3_transport_usd_per_t'] = res['distance_km'].astype(float) * res['pipeline_coeff_usd_per_tkm'].astype(float)
    res['nh3_delivered_usd_per_t'] = res['nh3_prod_cost_usd_per_t'] + res['nh3_transport_usd_per_t']
    # enforce max-distance feasibility
    if max_distance_km is not None:
        res = res.loc[res['distance_km'] <= float(max_distance_km)].copy()
    # tidy
    return res[['lat','lon','nearest_port','nearest_port_country','distance_km',
                'pipeline_coeff_usd_per_tkm','lcoe_usd_per_mwh',
                'nh3_prod_cost_usd_per_t','nh3_transport_usd_per_t','nh3_delivered_usd_per_t',
                'port_lat','port_lon']]


def pipeline_cost_sensitivity(df: pd.DataFrame, multipliers: Iterable[float]=(0.8,1.0,1.2)) -> pd.DataFrame:
    """±20% sensitivity to pipeline cost coefficient."""
    out = []
    for m in multipliers:
        tmp = df.copy()
        tmp['sensitivity_multiplier'] = float(m)
        tmp['nh3_transport_usd_per_t'] = tmp['distance_km'].astype(float) * (tmp['pipeline_coeff_usd_per_tkm'].astype(float) * float(m))
        tmp['nh3_delivered_usd_per_t'] = tmp['nh3_prod_cost_usd_per_t'] + tmp['nh3_transport_usd_per_t']
        out.append(tmp.assign(pipeline_coeff_usd_per_tkm_adj=tmp['pipeline_coeff_usd_per_tkm'].astype(float)*float(m)))
    return pd.concat(out, ignore_index=True)


# --------------------------------------------------------------------------------------
# Optional MILP (simple allocator by delivered cost)
# --------------------------------------------------------------------------------------

def select_sites_min_cost_to_meet_target(df: pd.DataFrame,
                                         target_t_per_year: float,
                                         max_sites: Optional[int]=None):
    """Minimize annual delivered cost to meet a t/y target; decision variable is allocation per site."""
    try:
        from pulp import LpProblem, LpMinimize, LpVariable, lpSum, PULP_CBC_CMD, LpStatus
    except Exception as e:
        print("[MILP] PuLP not installed. Skipping allocator. Error:", e)
        return pd.DataFrame(), {"status":"missing_pulp"}
    df2 = df.sort_values('nh3_delivered_usd_per_t').copy()
    if max_sites is not None:
        df2 = df2.head(int(max_sites))
    n = len(df2)
    prob = LpProblem("min_cost_ammonia_allocation", LpMinimize)
    x = [LpVariable(f"x_{i}", lowBound=0) for i in range(n)]  # t/y assigned
    cost = lpSum([float(df2['nh3_delivered_usd_per_t'].iloc[i]) * x[i] for i in range(n)])
    prob += cost
    prob += lpSum(x) >= float(target_t_per_year)
    prob.solve(PULP_CBC_CMD(msg=False))
    from pulp import LpStatus
    status = LpStatus[prob.status]
    sol = np.array([xi.value() if xi.value() is not None else 0.0 for xi in x])
    df_sol = df2.copy(); df_sol['tpy_assigned'] = sol
    total_cost = float((df_sol['tpy_assigned'] * df_sol['nh3_delivered_usd_per_t']).sum())
    return df_sol, {"status": status, "annual_tons": float(sol.sum()), "annual_cost_usd": total_cost}


# --------------------------------------------------------------------------------------
# Plot
# --------------------------------------------------------------------------------------

def plot_sites_and_ports(df: pd.DataFrame,
                         ports_gdf: gpd.GeoDataFrame,
                         out_png: Optional[str]=None,
                         topk: int=10,
                         country_gdf: Optional[gpd.GeoDataFrame]=None,
                         title: str = "Sites and **sea export** ports"):
    import matplotlib.pyplot as plt
    g_sites = gpd.GeoDataFrame(df.copy(), geometry=gpd.points_from_xy(df['lon'], df['lat']), crs=4326)
    fig, ax = plt.subplots(figsize=(8,9))
    if country_gdf is not None:
        try:
            country_gdf.boundary.plot(ax=ax, linewidth=1.0, color='black', label='Country')
        except Exception:
            pass
    # Ports (already filtered to sea export in loader)
    ports_gdf.plot(ax=ax, marker='*', markersize=70, alpha=0.85, label='Sea export ports')
    # Sites
    g_sites.plot(ax=ax, markersize=30, alpha=0.6, label='Sites')
    try:
        top = df.nsmallest(topk, 'nh3_delivered_usd_per_t')
        ax.scatter(top['lon'], top['lat'], s=50, facecolors='none', edgecolors='k', linewidths=1.2, label='Top by delivered $/t')
        for _, r in top.iterrows():
            ax.annotate(f"{r['nh3_delivered_usd_per_t']:.0f} $/t", (r['lon'], r['lat']), xytext=(4,4), textcoords='offset points', fontsize=8)
    except Exception:
        pass
    ax.set_title(title)
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, linestyle=':')
    ax.legend(loc='lower left')
    fig.tight_layout()
    if out_png:
        fig.savefig(out_png, dpi=200)
    # Show in kernel
    plt.show()


# --------------------------------------------------------------------------------------
# Monte‑Carlo and Pareto helpers (visualisation-safe; matplotlib only, no seaborn)
# --------------------------------------------------------------------------------------

def pareto_front_2d(df: pd.DataFrame,
                    x_col: str,
                    y_col: str,
                    minimize_x: bool = True,
                    minimize_y: bool = True) -> pd.Series:
    """
    Return a boolean mask of points on the 2D Pareto front.
    For ties, keeps the first occurrence encountered in the sweep.
    """
    if df is None or df.empty:
        return pd.Series([], dtype=bool)
    d = df[[x_col, y_col]].copy()
    # Normalize directions
    d['_x'] = d[x_col] if minimize_x else -d[x_col]
    d['_y'] = d[y_col] if minimize_y else -d[y_col]
    order = d.sort_values('_x', kind='mergesort').index
    best_y = float('inf')
    mask = pd.Series(False, index=df.index)
    for idx in order:
        y = float(d.loc[idx, '_y'])
        if y <= best_y:
            mask.loc[idx] = True
            best_y = y
    return mask


def plot_pareto(df: pd.DataFrame,
                x_col: str,
                y_col: str,
                mask: pd.Series,
                out_png: str = None,
                title: str = "Pareto front",
                xlabel: str = None,
                ylabel: str = None):
    """Scatter all points and overlay the Pareto front (connected)."""
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7,6))
    ax.scatter(df[x_col].values, df[y_col].values, alpha=0.6, label="Candidates")
    if mask.any():
        front = df.loc[mask, [x_col, y_col]].sort_values(x_col)
        ax.plot(front[x_col].values, front[y_col].values, linewidth=2.0, marker='o', label="Pareto")
    ax.set_title(title)
    ax.set_xlabel(xlabel or x_col); ax.set_ylabel(ylabel or y_col)
    ax.grid(True, linestyle=':'); ax.legend(loc='best')
    fig.tight_layout()
    if out_png:
        fig.savefig(out_png, dpi=200)
    plt.show()


def monte_carlo_delivered_costs(base_df: pd.DataFrame,
                                n_samples: int = 500,
                                yield_sigma: float = 0.10,
                                pipeline_sigma: float = 0.20,
                                electricity_intensity_mwh_per_t: float = 10.0,
                                random_seed: int = None,
                                id_col: str = None) -> pd.DataFrame:
    """
    Fast Monte‑Carlo over **renewable yield** and **pipeline tariff**.
    Approximates LCOE scaling linearly with annual MWh yield.
      - yield ~ lognormal(mean=1, sigma=yield_sigma) applied multiplicatively
      - pipeline_coeff ~ lognormal(mean=1, sigma=pipeline_sigma)
    Delivered cost sample per site i:
        nh3 = (lcoe_i / yield_mult) * intensity + distance_km_i * (pipe_coeff_i * pipe_mult)
    Returns a long DataFrame with columns:
        [sample, site_idx, id(optional), yield_mult, pipeline_mult, nh3_delivered_usd_per_t]
    """
    rng = np.random.default_rng(random_seed)
    df = base_df.copy()
    if df is None or df.empty:
        return pd.DataFrame()
    # pull base vectors
    lcoe = df['lcoe_usd_per_mwh'].astype(float).values
    dist = df['distance_km'].astype(float).values
    coeff = df['pipeline_coeff_usd_per_tkm'].astype(float).values
    n_sites = lcoe.size
    # draws
    # convert sigma to lognormal parameters with mean 1
    if yield_sigma <= 0:
        y_mult = np.ones((n_samples, 1), dtype=float)
    else:
        mu_y = -0.5 * (yield_sigma**2)
        y_mult = rng.lognormal(mean=mu_y, sigma=yield_sigma, size=(n_samples, 1))
    if pipeline_sigma <= 0:
        p_mult = np.ones((n_samples, 1), dtype=float)
    else:
        mu_p = -0.5 * (pipeline_sigma**2)
        p_mult = rng.lognormal(mean=mu_p, sigma=pipeline_sigma, size=(n_samples, 1))
    # broadcast across sites: shape (samples, sites)
    lcoe_mc = (lcoe[None, :] / y_mult)  # MWh cost scales inversely with yield
    coeff_mc = coeff[None, :] * p_mult
    nh3_mc = lcoe_mc * float(electricity_intensity_mwh_per_t) + (dist[None, :] * coeff_mc)
    # build long frame
    samples = np.repeat(np.arange(n_samples), n_sites)
    site_idx = np.tile(np.arange(n_sites), n_samples)
    vals = nh3_mc.reshape(-1)
    yvals = np.repeat(y_mult.reshape(-1), n_sites)
    pvals = np.repeat(p_mult.reshape(-1), n_sites)
    out = pd.DataFrame({
        "sample": samples,
        "site_idx": site_idx,
        "nh3_delivered_usd_per_t": vals,
        "yield_mult": yvals,
        "pipeline_mult": pvals,
    })
    if id_col and (id_col in df.columns):
        ids = np.tile(df[id_col].astype(str).values, n_samples)
        out[id_col] = ids
    # attach a human-friendly site label (rank by base cost)
    base_rank = df.reset_index(drop=True).sort_values("nh3_delivered_usd_per_t").reset_index().rename(columns={"index":"site_rank"})
    out = out.merge(base_rank[["site_rank"]].reset_index().rename(columns={"index":"site_idx"}),
                    on="site_idx", how="left")
    return out


def plot_monte_carlo_boxplot(mc_long: pd.DataFrame,
                             value_col: str = "nh3_delivered_usd_per_t",
                             group_col: str = "site_rank",
                             out_png: str = None,
                             title: str = "Monte‑Carlo delivered NH3 $/t (by site rank)"):
    """Boxplot per site rank for the Monte‑Carlo distribution of delivered costs."""
    import matplotlib.pyplot as plt
    if mc_long is None or mc_long.empty:
        return
    groups = sorted(mc_long[group_col].dropna().unique())
    data = [mc_long.loc[mc_long[group_col] == g, value_col].values for g in groups]
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.boxplot(data, labels=[str(int(g)) for g in groups], showfliers=False)
    ax.set_xlabel("Site rank (base-case by delivered $/t)")
    ax.set_ylabel("Delivered NH3 cost [$ / t]")
    ax.set_title(title)
    ax.grid(True, linestyle=':')
    fig.tight_layout()
    if out_png:
        fig.savefig(out_png, dpi=200)
    plt.show()
