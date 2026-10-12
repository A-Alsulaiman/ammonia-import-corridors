# -*- coding: utf-8 -*-
"""
online_constraints.py (v1.3, ASCII-safe, with progress logging)

Improvements versus v1.2:
  ✓ Robust Overpass fallback across multiple mirrors with retries & backoff
  ✓ Optional **tiled** queries (1° boxes intersecting the country) to avoid 504s
  ✓ Uses Shapely 2 `union_all` when available (falls back to unary_union)
  ✓ Higher Overpass query timeout ([timeout:300]) + gzip + User-Agent
  ✓ Cache keys include bbox when tiling; existing cache files still honored
  ✓ **NEW**: logging of cache hits, tile feature counts, per-spec totals and grand totals

Main entry point (unchanged signature):
  - build_osm_exclusion_union(country_gdf, iso3, spec_list, metric_crs)

Specs examples (each item in spec_list):
  {"type":"cities", "place_ranks":["city","town"], "buffer_m":1500, "min_population": None}
  {"type":"urban_areas", "tags":[("landuse","residential"),("landuse","industrial"),("landuse","commercial")], "buffer_m":300}
  {"type":"protected_areas", "buffer_m":0}
  {"type":"osm_natural", "tags":[("natural","glacier"),("natural","cliff"),("natural","scree"),("natural","wetland")], "buffer_m":50}

All outputs are clipped to the country border and projected to `metric_crs`.
"""

from __future__ import annotations
import json
import math
import random
import time
from typing import List, Tuple, Dict, Optional, Iterable
from pathlib import Path

import logging  # NEW

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, LineString, Polygon, box as _box
try:
    # Shapely 2 (preferred)
    from shapely import union_all as _union_all
except Exception:  # Shapely 1.x
    _union_all = None
try:
    from shapely.ops import unary_union as _unary_union
except Exception:
    _unary_union = None

log = logging.getLogger(__name__)  # NEW


def _safe_union(geoms: Iterable):
    """Return a unary union using Shapely 2's union_all when available."""
    geoms = [g for g in geoms if g is not None]
    if not geoms:
        return None
    if _union_all is not None:
        try:
            return _union_all(geoms)
        except Exception:
            pass
    if _unary_union is not None:
        return _unary_union(geoms)
    # last resort: iterative union
    acc = geoms[0]
    for g in geoms[1:]:
        try:
            acc = acc.union(g)
        except Exception:
            pass
    return acc

try:
    import requests
except Exception:
    requests = None  # user must install requests

CACHE_DIR = Path(__file__).parent / ".online_constraints_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ---- Overpass endpoints & HTTP helpers ----
# Respect env var overrides; otherwise try mirrors in random order.
_DEFAULT_OVERPASS_ENDPOINTS = [
    # A few widely used mirrors; order randomized per run to spread load
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass.nchc.org.tw/api/interpreter",
]


def _overpass_endpoints():
    env = (os.environ.get("OVERPASS_URLS") or os.environ.get("OVERPASS_URL") or "").strip()
    if env:
        eps = [u.strip() for u in env.split(",") if u.strip()]
    else:
        eps = list(_DEFAULT_OVERPASS_ENDPOINTS)
    random.shuffle(eps)
    return eps


import os


def _http_post_overpass(query: str, timeout: int = 300, tries_per_endpoint: int = 2) -> Dict:
    """
    POST an Overpass query with mirror fallback, handling 429/504/timeouts.
    Raises last exception if all mirrors fail.
    """
    if requests is None:
        raise RuntimeError("The 'requests' package is required for Overpass queries. Please 'pip install requests'.")
    headers = {
        "User-Agent": "AP-IRA-Model/online-constraints/1.3",
        "Accept-Encoding": "gzip, deflate",
    }
    last_err: Optional[Exception] = None
    endpoints = _overpass_endpoints()
    for base in endpoints:
        for attempt in range(1, int(tries_per_endpoint) + 1):
            try:
                r = requests.post(base, data={"data": query}, timeout=timeout, headers=headers)
                # Handle common throttling conditions
                if r.status_code == 429:
                    # Too Many Requests → wait then retry this same endpoint
                    time.sleep(min(90, 15 * attempt))
                    continue
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_err = e
                # For 504/timeout/connection errors → short backoff then next try/endpoint
                msg = str(e)
                if any(code in msg for code in ("504", "Read timed out", "Timeout")):
                    time.sleep(min(45, 5 * attempt))
                else:
                    time.sleep(2.0 * attempt)
        # next endpoint
    raise last_err if last_err else RuntimeError("Overpass query failed with unknown error.")


def _area_clause_iso3(iso3: str) -> str:
    iso3 = iso3.strip().upper()
    return f'area["ISO3166-1:alpha3"="{iso3}"][admin_level=2];'


def _cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.geojson"


def _elements_to_geoms(js: Dict) -> Tuple[list, list, list]:
    """Return lists of shapely Polygons, LineStrings, Points from Overpass result with 'out geom' or 'out center'."""
    polys = []
    lines = []
    points = []
    for el in js.get("elements", []):
        et = el.get("type")
        if et == "node":
            lat = el.get("lat")
            lon = el.get("lon")
            if lat is not None and lon is not None:
                points.append(Point(float(lon), float(lat)))
            continue
        # prefer explicit geometry array
        geom = el.get("geometry")
        if geom and isinstance(geom, list) and len(geom) >= 2:
            coords = [(float(p["lon"]), float(p["lat"])) for p in geom if "lat" in p and "lon" in p]
            if len(coords) >= 4 and coords[0] == coords[-1]:
                try:
                    polys.append(Polygon(coords))
                except Exception:
                    try:
                        lines.append(LineString(coords))
                    except Exception:
                        pass
            elif len(coords) >= 2:
                try:
                    lines.append(LineString(coords))
                except Exception:
                    pass
            continue
        # else fall back to 'center'
        ctr = el.get("center")
        if ctr and "lat" in ctr and "lon" in ctr:
            points.append(Point(float(ctr["lon"]), float(ctr["lat"])))
    return polys, lines, points


# ---------- NEW: verbose cache + Overpass logging ----------

def _load_or_query(name: str, query: str) -> gpd.GeoDataFrame:
    """
    Load cached GeoJSON if present; else run Overpass and cache a minimal GeoJSON.

    Logging behavior
    ----------------
    • Cache hit:
        INFO  [online-constraints] cache hit 'places_IRL_city_town.geojson' → 1234 geometry record(s)
    • Cache miss / Overpass:
        INFO  [online-constraints] Overpass 'places_IRL_city_town' → 1234 geometry record(s) (before cache)
        INFO  [online-constraints] cached 'places_IRL_city_town.geojson' to ... (1234 record(s))
    """
    fp = _cache_path(name)

    # --- Cache hit path ---
    if fp.exists():
        try:
            gdf = gpd.read_file(fp)
            log.info(
                "[online-constraints] cache hit '%s' → %d geometry record(s)",
                fp.name,
                len(gdf),
            )
            return gdf
        except Exception as e:
            log.warning(
                "[online-constraints] failed to read cache '%s': %s; refetching",
                fp,
                e,
            )

    # --- Overpass query path ---
    try:
        js = _http_post_overpass(query, timeout=300, tries_per_endpoint=2)
    except Exception as e:
        # Graceful fallback: return empty on hard failure (caller may retry via tiling)
        log.error("[online-constraints] Overpass error on '%s': %s", name, e)
        return gpd.GeoDataFrame(columns=["geometry"], crs=4326)

    polys, lines, points = _elements_to_geoms(js)
    geoms = []
    geoms.extend(polys)
    geoms.extend(lines)
    geoms.extend(points)

    if not geoms:
        log.info("[online-constraints] Overpass '%s' → 0 geometry record(s)", name)
        return gpd.GeoDataFrame(columns=["geometry"], crs=4326)

    gdf = gpd.GeoDataFrame(geometry=geoms, crs=4326)
    log.info(
        "[online-constraints] Overpass '%s' → %d geometry record(s) (before cache)",
        name,
        len(gdf),
    )

    try:
        gdf.to_file(fp, driver="GeoJSON")
        log.info(
            "[online-constraints] cached '%s' to %s (%d record(s))",
            name,
            fp,
            len(gdf),
        )
    except Exception as e:
        log.warning("[online-constraints] could not cache '%s': %s", name, e)

    return gdf


# ---- Query builders (with optional bbox) ----

def _bbox_clause(bbox: Optional[Tuple[float, float, float, float]]) -> str:
    """Return Overpass (south,west,north,east) bbox clause or empty string."""
    if not bbox:
        return ""
    minx, miny, maxx, maxy = bbox  # (lon_min, lat_min, lon_max, lat_max)
    s, w, n, e = miny, minx, maxy, maxx
    return f"({s:.6f},{w:.6f},{n:.6f},{e:.6f})"


def fetch_osm_cities(
    iso3: str,
    place_ranks: Tuple[str, ...] = ("city", "town"),
    min_population: Optional[int] = None,
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> gpd.GeoDataFrame:
    """
    Fetch OSM places for the given ranks as point features using 'out center tags'.
    Optionally restrict to a bounding box to reduce query size.
    """
    ranks = "|".join(place_ranks)
    area = _area_clause_iso3(iso3)
    bb = _bbox_clause(bbox)
    q = f"""
    [out:json][timeout:300];
    {area}
    (
      node["place"~"{ranks}"](area){bb};
      way["place"~"{ranks}"](area){bb};
      relation["place"~"{ranks}"](area){bb};
    );
    out center tags;
    """
    name = f"places_{iso3}_{ranks}" + (
        f"_bbox_{bbox[0]:.2f}_{bbox[1]:.2f}_{bbox[2]:.2f}_{bbox[3]:.2f}" if bbox else ""
    )
    g = _load_or_query(name, q)
    if g is None or g.empty:
        return g
    # Optional: filter by population when present (best-effort; tags may not be present in geometry-only cache)
    # As a lightweight approximation, we skip parsing tags here.
    return g[["geometry"]].copy()


def fetch_osm_polygons_by_tags(
    iso3: str,
    tags: List[Tuple[str, str]],
    name: str,
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> gpd.GeoDataFrame:
    """
    Fetch OSM ways/relations with 'out geom' for the provided tag pairs.
    Example tags: [("natural","glacier"), ("natural","wetland")]
    """
    area = _area_clause_iso3(iso3)
    bb = _bbox_clause(bbox)
    sel = []
    for k, v in tags:
        sel.append(
            f'way["{k}"="{v}"](area){bb}; relation["{k}"="{v}"](area){bb};'
        )
    sel_str = "\n      ".join(sel) if sel else ""
    q = f"""
    [out:json][timeout:300];
    {area}
    (
      {sel_str}
    );
    out geom;
    """
    name2 = name + (
        f"_bbox_{bbox[0]:.2f}_{bbox[1]:.2f}_{bbox[2]:.2f}_{bbox[3]:.2f}" if bbox else ""
    )
    return _load_or_query(name2, q)


def fetch_osm_urban_areas(
    iso3: str, bbox: Optional[Tuple[float, float, float, float]] = None
) -> gpd.GeoDataFrame:
    """Built-up areas: landuse=residential|commercial|industrial"""
    tags = [
        ("landuse", "residential"),
        ("landuse", "commercial"),
        ("landuse", "industrial"),
    ]
    return fetch_osm_polygons_by_tags(iso3, tags, name=f"urban_{iso3}", bbox=bbox)


def fetch_osm_protected_areas(
    iso3: str, bbox: Optional[Tuple[float, float, float, float]] = None
) -> gpd.GeoDataFrame:
    """Fetch boundary=protected_area (typically polygons/relations), out geom."""
    area = _area_clause_iso3(iso3)
    bb = _bbox_clause(bbox)
    q = f"""
    [out:json][timeout:300];
    {area}
    (
      way["boundary"="protected_area"](area){bb};
      relation["boundary"="protected_area"](area){bb};
    );
    out geom;
    """
    name = f"protected_{iso3}" + (
        f"_bbox_{bbox[0]:.2f}_{bbox[1]:.2f}_{bbox[2]:.2f}_{bbox[3]:.2f}" if bbox else ""
    )
    return _load_or_query(name, q)


def fetch_osm_natural_obstacles(
    iso3: str,
    natural_tags: List[str],
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> gpd.GeoDataFrame:
    """Fetch obstacles such as glaciers, cliffs, scree, wetlands etc."""
    tags = [("natural", t) for t in natural_tags]
    return fetch_osm_polygons_by_tags(
        iso3, tags, name=f"natural_{iso3}_{'_'.join(natural_tags)}", bbox=bbox
    )


# ---- Tiling helpers ----

def _country_union(country_gdf):
    try:
        return _safe_union(country_gdf.to_crs(4326).geometry.values)
    except Exception:
        # attempt to buffer(0) to fix possible invalid input
        return _safe_union(country_gdf.to_crs(4326).buffer(0).geometry.values)


def _iter_country_tiles(country_gdf, tile_deg: float = 1.0):
    """
    Yield bbox tuples (minx, miny, maxx, maxy) for tiles intersecting the country.
    Uses 1° tiles by default, which keeps Overpass responses small.
    """
    minx, miny, maxx, maxy = country_gdf.total_bounds
    # Guard against degenerate extents
    if maxx <= minx or maxy <= miny:
        return
    cu = _country_union(country_gdf)
    nx = max(1, int(math.ceil((maxx - minx) / float(tile_deg))))
    ny = max(1, int(math.ceil((maxy - miny) / float(tile_deg))))
    for ix in range(nx):
        x0 = minx + ix * tile_deg
        x1 = min(x0 + tile_deg, maxx)
        for iy in range(ny):
            y0 = miny + iy * tile_deg
            y1 = min(y0 + tile_deg, maxy)
            tile = _box(x0, y0, x1, y1)
            try:
                if not cu.intersects(tile):
                    continue
            except Exception:
                pass
            yield (x0, y0, x1, y1)


# ---- Buffer/union utilities ----

def _buffer_union(gdf: gpd.GeoDataFrame, buffer_m: float, metric_crs: str) -> Optional[object]:
    if gdf is None or gdf.empty:
        return None
    try:
        gm = gdf.to_crs(metric_crs)
        buf = float(buffer_m) if buffer_m else 0.0
        if buf != 0.0:
            gm = gm.buffer(buf)
            geoms = list(gm.geometry if hasattr(gm, "geometry") else gm)
        else:
            geoms = list(gm.geometry if hasattr(gm, "geometry") else gm)
        return _safe_union(geoms)
    except Exception:
        try:
            g = gdf.to_crs(metric_crs)
            return _safe_union(list(g.buffer(float(buffer_m)).geometry))
        except Exception:
            return None


# ---- Public API ----
# ---------- NEW: detailed per-spec / per-tile logging ----------

def build_osm_exclusion_union(
    country_gdf: gpd.GeoDataFrame,
    iso3: str,
    spec_list: List[Dict],
    metric_crs: str,
    tile_deg: float = 1.0,
):
    """
    Build a single shapely union in metric_crs for the configured OSM layers,
    clipped to the country border. To reduce Overpass load, this function
    queries in **tiles** (default 1°) and stitches the results.

    Logging highlights
    ------------------
    • Overall:
        INFO [online-constraints] building OSM exclusion union: 4 spec(s), 9 tile(s) per spec
    • Per spec:
        INFO [online-constraints] [1/4] starting spec type='cities' (buffer=1500.0 m)
        INFO [online-constraints]   type='cities' tile 1/9 → 1233 record(s) (spec total=1233, grand total=1233)
        ...
        INFO [online-constraints]   type='cities': merged 3291 raw record(s) before buffer/clip (specs remaining=3)
    • Final:
        INFO [online-constraints] final exclusion union built from 4 spec layer(s); grand total raw record(s)=18719
    """
    if not spec_list:
        log.info("[online-constraints] no OSM specs configured; nothing to exclude.")
        return None

    # Country union in metric CRS (for clipping)
    try:
        country_m = country_gdf.to_crs(metric_crs)
        cu = _safe_union(country_m.geometry.values)
    except Exception:
        country_m = country_gdf.to_crs(metric_crs)
        cu = _safe_union(country_m.buffer(0).geometry.values)

    unions: List[object] = []

    # Pre-compute tiles so we know how many “chunks” there are.
    tiles = list(_iter_country_tiles(country_gdf, tile_deg=tile_deg)) or [None]
    n_tiles = len(tiles)
    n_specs = len(spec_list)
    log.info(
        "[online-constraints] building OSM exclusion union: %d spec(s), %d tile(s) per spec",
        n_specs,
        n_tiles,
    )

    grand_features = 0  # all specs combined

    for i_spec, spec in enumerate(spec_list, 1):
        t = str(spec.get("type", "")).lower()
        buf = float(spec.get("buffer_m", 0.0) or 0.0)

        log.info(
            "[online-constraints] [%d/%d] starting spec type='%s' (buffer=%.1f m)",
            i_spec,
            n_specs,
            t,
            buf,
        )

        # Accumulate raw geoms across tiles to union once per layer
        layer_parts: List[gpd.GeoDataFrame] = []
        layer_features = 0  # per spec

        for i_tile, bbox in enumerate(tiles, 1):
            try:
                if t == "cities":
                    ranks = tuple(spec.get("place_ranks", ("city", "town")))
                    g = fetch_osm_cities(
                        iso3,
                        place_ranks=ranks,
                        min_population=spec.get("min_population"),
                        bbox=bbox,
                    )
                elif t == "urban_areas":
                    g = fetch_osm_urban_areas(iso3, bbox=bbox)
                elif t == "protected_areas":
                    g = fetch_osm_protected_areas(iso3, bbox=bbox)
                elif t == "osm_natural":
                    tags = spec.get("tags", [("natural", "glacier")])
                    # normalize tag tuples
                    if (
                        isinstance(tags, list)
                        and tags
                        and isinstance(tags[0], (list, tuple))
                        and len(tags[0]) == 2
                    ):
                        tag_pairs = [(str(k), str(v)) for (k, v) in tags]
                    else:
                        # allow tags=["glacier","cliff"]
                        tag_pairs = [("natural", str(v)) for v in list(tags)]
                    g = fetch_osm_polygons_by_tags(
                        iso3, tag_pairs, name=f"natural_{iso3}", bbox=bbox
                    )
                else:
                    g = gpd.GeoDataFrame(columns=["geometry"], crs=4326)
            except Exception as e:
                print(
                    f"[online-constraints] WARN: tile fetch failed for {t} {bbox}: {e}"
                )
                g = gpd.GeoDataFrame(columns=["geometry"], crs=4326)

            if g is not None and (not g.empty):
                layer_parts.append(g)
                n_new = len(g)
                layer_features += n_new
                grand_features += n_new
                log.info(
                    "[online-constraints]   type='%s' tile %d/%d → %d record(s) "
                    "(spec total=%d, grand total=%d)",
                    t,
                    i_tile,
                    n_tiles,
                    n_new,
                    layer_features,
                    grand_features,
                )

        if not layer_parts:
            log.info(
                "[online-constraints]   type='%s': 0 record(s) across all tiles → skipped. "
                "specs remaining=%d",
                t,
                n_specs - i_spec,
            )
            continue

        # Merge tile parts, buffer, and union once
        try:
            g_layer = gpd.GeoDataFrame(
                pd.concat(layer_parts, ignore_index=True), crs=4326
            )
        except Exception:
            # fall back: append geometries only
            geoms = []
            for gp in layer_parts:
                geoms.extend(list(getattr(gp, "geometry", [])))
            g_layer = gpd.GeoDataFrame(geometry=geoms, crs=4326)

        log.info(
            "[online-constraints]   type='%s': merged %d raw record(s) before buffer/clip "
            "(specs remaining=%d)",
            t,
            len(getattr(g_layer, "geometry", [])),
            n_specs - i_spec,
        )

        u = _buffer_union(g_layer, buffer_m=buf, metric_crs=metric_crs)

        if u and (not u.is_empty):
            try:
                unions.append(u.intersection(cu))
            except Exception:
                try:
                    unions.append(u.buffer(0).intersection(cu))
                except Exception:
                    unions.append(u)

    if not unions:
        log.info(
            "[online-constraints] all specs produced empty unions; returning None "
            "(grand total=%d record(s))",
            grand_features,
        )
        return None

    try:
        res = _safe_union(unions)
        log.info(
            "[online-constraints] final exclusion union built from %d spec layer(s); "
            "grand total raw record(s)=%d",
            n_specs,
            grand_features,
        )
        return res
    except Exception:
        geom = unions[0]
        for u in unions[1:]:
            try:
                geom = geom.union(u)
            except Exception:
                pass
        log.info(
            "[online-constraints] final exclusion union built via fallback union(); "
            "grand total raw record(s)=%d",
            grand_features,
        )
        return geom
