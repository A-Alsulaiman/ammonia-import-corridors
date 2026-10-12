#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate a 600-DPI PNG figure showing, for multiple selected countries, the
chosen renewable optimisation locations and the ports that connect to them.

Each selected country is drawn in its own square panel. In each panel:
    - the MAIN view is zoomed to the chosen-location / connected-port area,
      using a zoom based on the furthest location-to-port connection
    - grey country shape / border = selected country (only where visible in the
      zoomed main view)
    - red dots = chosen renewable optimisation locations
    - blue squares = selected/nearest/canonical ports
    - grey dashed lines = direct site-to-port distance/cost proxy, not a routed physical pipeline
    - a small inset in the cleanest available corner shows the whole country outline for context

Save this file in:
    C:\\Users\\user\\Documents\\Project\\AP_IRA_Model
then run it in Spyder.

Country-list workbook:
    - File name: List of Countries.xlsx, List of Countries.xlsm, List of Countries.xls, or List of Countries.csv
    - Column A: country name / folder name, e.g. Australia, Chile, Egypt
    - Column C: ISO3, with heading ISO3, e.g. AUS, CHL, EGY

For each selected country, the script reads:
    <PROJECT_DIR>\\trade_version_results\\<Country name from column A>\\chosen_ports_of_top_locations.xlsx

The PNG output is saved in PROJECT_DIR, i.e. the same directory as this script
when the script is saved in the project folder.
"""

from __future__ import annotations

import math
import re
import textwrap
import unicodedata
import urllib.request
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from shapely.geometry import Point
from shapely.ops import nearest_points
from mpl_toolkits.axes_grid1.inset_locator import inset_axes


# =============================================================================
# USER SETTINGS
# =============================================================================

# Default: folder containing this script. In Spyder, save this script in your
# project folder. If needed, replace this with an absolute path, for example:
# PROJECT_DIR = Path(r"C:\Users\user\Documents\Project\AP_IRA_Model")
try:
    PROJECT_DIR = Path(os.environ.get("AP_IRA_PROJECT_DIR", str(Path(__file__).resolve().parent.parent))).resolve()
except NameError:  # Spyder interactive fallback
    PROJECT_DIR = Path.cwd().resolve()

# Root folder containing one subfolder per selected country.
RESULTS_ROOT = PROJECT_DIR / "trade_version_results"

# Country-list workbook containing the selected country names.
# The input file is "List of Countries". The extension can be .xlsx, .xlsm, .xls,
# or .csv; the script will try these automatically if no extension is given.
COUNTRY_LIST_PATH: Optional[Path] = PROJECT_DIR / "List of Countries"

# Name of the workbook inside each country result folder.
PORTS_WORKBOOK_NAME = "chosen_ports_of_top_locations.xlsx"

# Prefer the optimiser's raw CSV output when it exists. This usually keeps more
# precision than the Excel files created for downstream use. The script falls
# back to PORTS_WORKBOOK_NAME automatically.
PREFER_OPTIMISATION_CSV = True
OPTIMISATION_CSV_NAME = "ammonia_delivered_costs.csv"
TOP_LOCATIONS_PER_COUNTRY: Optional[int] = 10  # normally optimiser TOPK; set None to plot all rows

# Plot-coordinate safety. These options do NOT change the optimiser results;
# they only keep the map from showing sites/ports slightly offshore because of
# coastal cells, WPI harbour coordinates, or rounded exported coordinates.
SNAP_LOCATIONS_TO_COUNTRY_FOR_PLOT = True
SNAP_PORTS_TO_COUNTRY_BOUNDARY_FOR_PLOT = True
PRINT_PLOT_SNAP_WARNINGS = True

# Output PNG. This saves in the same directory as this script / project root.
OUTPUT_DPI = 600
OUTPUT_PNG = PROJECT_DIR / "chosen_locations_and_ports_grid_map_600dpi.png"

# Grid controls. Leave GRID_COLUMNS=None to choose automatically.
GRID_COLUMNS: Optional[int] = None
PANEL_SIZE_INCHES = 3.25
FIGURE_TITLE = "Chosen Renewable Optimisation Locations and Connected Ports"

# Country geometry / zoom controls.
MAINLAND_ONLY = True          # use only the mainland / largest polygon for the inset map
ZOOM_TO_CONNECTED_SYSTEM = True   # main panel focuses on the location-port system, not the whole country
ZOOM_INCLUDE_ALL_CONNECTIONS = True  # keep all visible locations/ports inside the zoom if possible
PANEL_PADDING_FRAC = 0.10     # padding around the zoomed location/port system in each panel
MIN_PANEL_SPAN_DEG = 1.0      # minimum lon/lat span so tiny countries are not over-zoomed
COUNTRY_FACE_COLOR = "0.96"
COUNTRY_BORDER_COLOR = "0.40"
COUNTRY_BORDER_LINEWIDTH = 0.70
PANEL_BOX_COLOR = "0.74"
PANEL_BOX_LINEWIDTH = 0.55

# Context inset showing the whole country.
SHOW_COUNTRY_INSET = True
# "auto" chooses the cleanest corner per country. You can also force:
# "upper right", "upper left", "lower right", or "lower left".
INSET_LOCATION = "auto"
INSET_WIDTH_FRAC = 0.32
INSET_HEIGHT_FRAC = 0.32
INSET_MARGIN_FRAC = 0.025          # distance between inset and panel border
INSET_AVOID_MARGIN_FRAC = 0.010    # extra buffer used when testing overlaps
INSET_BORDER_COLOR = "0.65"
INSET_BORDER_LINEWIDTH = 0.45
INSET_COUNTRY_FACE_COLOR = "0.98"
INSET_COUNTRY_BORDER_COLOR = "0.45"
INSET_COUNTRY_BORDER_LINEWIDTH = 0.45
INSET_SHOW_ZOOM_BOX = True
INSET_ZOOM_BOX_COLOR = "0.35"
INSET_ZOOM_BOX_LINEWIDTH = 0.50

# Point/line styling.
LOCATION_POINT_SIZE = 20
LOCATION_COLOR = "red"
LOCATION_EDGE_COLOR = "black"
LOCATION_EDGE_WIDTH = 0.25
PORT_POINT_SIZE = 28
PORT_COLOR = "#1f77b4"
PORT_EDGE_COLOR = "black"
PORT_EDGE_WIDTH = 0.25
CONNECTION_COLOR = "0.60"
CONNECTION_LINEWIDTH = 0.50
CONNECTION_ALPHA = 0.60
CONNECTION_LINESTYLE = "--"       # visual link only; not a routed pipeline path

# Text and labelling controls.
# You can control all main text sizes here.
LABEL_PORTS = True
MAX_PORT_LABELS_PER_COUNTRY = 8
LABEL_LOCATIONS = False

# Country-panel heading text.
TITLE_FONTSIZE = 13.0
TITLE_PAD = 3

# Overall figure title.
SUPTITLE_FONTSIZE = 1.0

# Port and location labels inside each country panel.
PORT_LABEL_FONTSIZE = 8.0
LOCATION_LABEL_FONTSIZE = 4.8

# Port-label placement controls.
PORT_LABEL_BOX_FACE_COLOR = "white"
PORT_LABEL_BOX_EDGE_COLOR = "0.75"
PORT_LABEL_BOX_LINEWIDTH = 0.35
PORT_LABEL_BOX_ALPHA = 0.92
PORT_LABEL_BORDER_BUFFER = 0.035   # if a chosen label is this close to the panel border, draw a leader line
PORT_LABEL_POINT_BUFFER = 0.018    # padding used when testing whether the label covers red points
PORT_LABEL_ALLOW_ARROW_ON_BORDER = True
PORT_LABEL_ALLOW_ARROW_ON_OVERLAP = True
PORT_LABEL_AVOID_OTHER_LABELS = True
PORT_LABEL_LABEL_OVERLAP_PENALTY = 1000.0

# Keep long port names fully inside the panel even when PORT_LABEL_FONTSIZE is large.
PORT_LABEL_WRAP_CHARS = 16
PORT_LABEL_MAX_CHARS = 42
PORT_LABEL_INNER_MARGIN = 0.020

# Legend text and marker sizing.
SHOW_LEGEND = True
LEGEND_FONTSIZE = 15.0
LEGEND_MARKERSIZE = 6.0
LEGEND_NCOL = 1
LEGEND_LOCATION = "lower right"
LEGEND_BBOX_TO_ANCHOR = (1.0, 0.1)
LEGEND_RIGHT_SPACE_INCHES = 1.2

# If you later choose to show axis ticks/labels, you can use these.
# In the current script the panel axes are hidden, so these are not used.
PANEL_TICK_LABEL_FONTSIZE = 6.0
INSET_TICK_LABEL_FONTSIZE = 5.0


# =============================================================================
# SMALL HELPERS
# =============================================================================

def normalize_header(value: object) -> str:
    """Normalize a column/header/folder name for robust matching."""
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.strip().lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def is_blank(value: object) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except Exception:
        pass
    text = str(value).strip()
    return not text or text.lower() in {"nan", "none", "null"}


def clean_text(value: object) -> str:
    """Clean text from Excel while preserving case for folder matching."""
    if is_blank(value):
        return ""
    text = str(value).strip()
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\.0$", "", text)


def first_existing(paths: Iterable[Optional[Path]]) -> Optional[Path]:
    for path in paths:
        if path is not None and path.exists():
            return path
    return None


def read_table(path: Path) -> pd.DataFrame:
    """Read Excel or CSV into a DataFrame using the first row as headers."""
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls", ".xlsm"}:
        return pd.read_excel(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported table type: {path}")


def pick_column(
    df: pd.DataFrame,
    preferred_names: Sequence[str],
    contains_any: Sequence[str] = (),
    required: bool = True,
    purpose: str = "column",
) -> Optional[str]:
    """Pick a column by exact normalized name, then by substring hints."""
    normalized_to_original = {normalize_header(col): col for col in df.columns}

    for name in preferred_names:
        hit = normalized_to_original.get(normalize_header(name))
        if hit is not None:
            return hit

    contains_norm = [normalize_header(x) for x in contains_any if normalize_header(x)]
    if contains_norm:
        for norm, original in normalized_to_original.items():
            if any(token in norm for token in contains_norm):
                return original

    if required:
        raise KeyError(f"Could not find {purpose}. Available columns: {list(df.columns)}")
    return None


def columns_matching(df: pd.DataFrame, preferred_names: Sequence[str]) -> List[str]:
    """Return columns matching any preferred name, in preferred order."""
    normalized_to_original = {normalize_header(col): col for col in df.columns}
    hits: List[str] = []
    for name in preferred_names:
        hit = normalized_to_original.get(normalize_header(name))
        if hit is not None and hit not in hits:
            hits.append(hit)
    return hits


def first_non_null_series(df: pd.DataFrame, columns: Sequence[str]) -> pd.Series:
    """Return a row-wise first non-null value across columns."""
    if not columns:
        return pd.Series([pd.NA] * len(df), index=df.index)
    out = df[columns[0]].copy()
    for col in columns[1:]:
        out = out.where(~out.isna(), df[col])
    return out


def safe_float(value: object) -> Optional[float]:
    try:
        if is_blank(value):
            return None
        return float(value)
    except Exception:
        return None


def valid_lon_lat(lon: object, lat: object) -> bool:
    lon_f = safe_float(lon)
    lat_f = safe_float(lat)
    if lon_f is None or lat_f is None:
        return False
    return -180 <= lon_f <= 180 and -90 <= lat_f <= 90


# =============================================================================
# COUNTRY LIST
# =============================================================================

def iso3_from_cell(value: object) -> str:
    """Return a cleaned ISO3 code from a cell, or an empty string."""
    if is_blank(value):
        return ""

    text = str(value).strip().upper()
    if normalize_header(text) in {"iso3", "iso", "countryiso3", "alpha3"}:
        return ""

    tokens = re.findall(r"\b[A-Z]{3}\b", text)
    if tokens:
        candidate = tokens[-1]
    else:
        candidate = re.sub(r"[^A-Z]", "", text)

    if len(candidate) != 3:
        return ""
    if candidate in {"ISO", "NAN", "NON"}:
        return ""
    return candidate


def looks_like_iso3_series(series: pd.Series, min_hits: int = 1) -> bool:
    hits = 0
    for value in series.dropna().head(300):
        if iso3_from_cell(value):
            hits += 1
            if hits >= min_hits:
                return True
    return False


def detect_iso3_column(df: pd.DataFrame, allow_column_c_fallback: bool = True) -> Optional[str]:
    if df.empty:
        return None

    iso_col = pick_column(
        df,
        preferred_names=[
            "ISO3", "ISO 3", "ISO_3", "Alpha-3", "Alpha 3",
            "Country ISO3", "Country ISO 3", "Country ISO3 Name",
        ],
        contains_any=["iso3", "alpha3"],
        required=False,
        purpose="ISO3 column",
    )

    if iso_col is None:
        for col in df.columns:
            norm = normalize_header(col)
            if "iso" in norm and "3" in norm:
                iso_col = col
                break

    # User-specified final fallback: ISO3 is in Excel column C.
    if iso_col is None and allow_column_c_fallback and df.shape[1] >= 3:
        col_c = df.columns[2]
        if looks_like_iso3_series(df[col_c], min_hits=1):
            iso_col = col_c

    return iso_col


def read_country_list_table_with_iso3(country_list_path: Path) -> Tuple[pd.DataFrame, Optional[str]]:
    """Read the country list and return (df, iso_col)."""
    suffix = country_list_path.suffix.lower()

    if suffix == ".csv":
        for header_row in range(0, 20):
            try:
                df = pd.read_csv(country_list_path, header=header_row).dropna(axis=1, how="all")
            except Exception:
                continue
            if df.empty:
                continue
            iso_col = detect_iso3_column(df, allow_column_c_fallback=True)
            if iso_col is not None:
                return df, iso_col
        raw = pd.read_csv(country_list_path, header=None).dropna(axis=1, how="all")
        raw.columns = [f"Column_{idx + 1}" for idx in range(raw.shape[1])]
        iso_col = "Column_3" if raw.shape[1] >= 3 and looks_like_iso3_series(raw["Column_3"]) else None
        return raw, iso_col

    if suffix not in {".xlsx", ".xls", ".xlsm"}:
        raise ValueError(f"Unsupported country-list file type: {country_list_path}")

    excel_file = pd.ExcelFile(country_list_path)
    for sheet_name in excel_file.sheet_names:
        for header_row in range(0, 20):
            try:
                df = pd.read_excel(country_list_path, sheet_name=sheet_name, header=header_row)
            except Exception:
                continue
            df = df.dropna(axis=1, how="all")
            if df.empty:
                continue
            iso_col = detect_iso3_column(df, allow_column_c_fallback=True)
            if iso_col is not None:
                return df, iso_col

        # Raw fallback: column A country, column C ISO3 if present.
        try:
            raw = pd.read_excel(country_list_path, sheet_name=sheet_name, header=None)
        except Exception:
            continue
        raw = raw.dropna(axis=1, how="all")
        if raw.empty:
            continue
        raw.columns = [f"Column_{idx + 1}" for idx in range(raw.shape[1])]
        iso_col = "Column_3" if raw.shape[1] >= 3 and looks_like_iso3_series(raw["Column_3"]) else None
        return raw, iso_col

    raise ValueError(f"Country list is empty or unreadable: {country_list_path}")


def load_country_list(country_list_path: Path) -> List[Dict[str, str]]:
    """Load selected country records from the country list."""
    df, iso_col = read_country_list_table_with_iso3(country_list_path)
    if df.empty:
        raise ValueError(f"Country list is empty: {country_list_path}")

    country_col = df.columns[0]
    records: List[Dict[str, str]] = []
    seen_keys = set()

    for _, row in df.iterrows():
        country_name = clean_text(row.get(country_col, ""))
        if not country_name or normalize_header(country_name) in {"country", "countryname", "name"}:
            continue

        iso3 = iso3_from_cell(row.get(iso_col, "")) if iso_col is not None else ""
        key = iso3 or normalize_header(country_name)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)

        records.append({"iso3": iso3, "country_name": country_name})

    if not records:
        raise ValueError(f"No country records were loaded from {country_list_path}.")
    return records


def _country_list_candidate_score(path: Path) -> Tuple[int, str]:
    stem = normalize_header(path.stem)
    score = 100
    if path.parent == PROJECT_DIR:
        score -= 30
    if stem in {"countrylist", "listofcountries"}:
        score -= 20
    if "country" in stem:
        score -= 10
    return score, str(path).lower()


def locate_country_list(project_dir: Path, explicit_path: Optional[Path]) -> Optional[Path]:
    """Find a usable Country_list / List of Countries workbook."""
    extensions = [".xlsx", ".xlsm", ".xls", ".csv"]

    if explicit_path is not None:
        path = explicit_path if explicit_path.is_absolute() else (project_dir / explicit_path)
        path = path.resolve()

        candidates = [path]
        if path.suffix == "":
            candidates = [path.with_suffix(ext) for ext in extensions]

        tried: List[str] = []
        for candidate in candidates:
            tried.append(str(candidate))
            if candidate.exists():
                load_country_list(candidate)  # validate early
                return candidate

        raise FileNotFoundError(
            "COUNTRY_LIST_PATH does not exist. Tried:\n  - " + "\n  - ".join(tried)
        )

    extensions = set(extensions)
    name_tokens = ["country_list", "country list", "countrylist", "list of countries", "listofcountries"]

    candidates: List[Path] = []
    for directory in [project_dir, RESULTS_ROOT]:
        if not directory.exists():
            continue
        for path in directory.glob("*"):
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            stem_norm = normalize_header(path.stem)
            if any(normalize_header(token) in stem_norm for token in name_tokens):
                candidates.append(path.resolve())

    unique_candidates: List[Path] = []
    seen = set()
    for path in sorted(candidates, key=_country_list_candidate_score):
        if path not in seen:
            seen.add(path)
            unique_candidates.append(path)

    rejected: List[str] = []
    for path in unique_candidates:
        try:
            load_country_list(path)
            return path
        except Exception as exc:
            rejected.append(f"{path}: {exc}")

    if rejected:
        print("WARNING: found country-list candidates but none could be used:")
        for message in rejected[:10]:
            print("  - " + message)
    return None


def discover_country_records_from_results(results_root: Path) -> List[Dict[str, str]]:
    """Fallback: discover country folders that contain the ports workbook."""
    records: List[Dict[str, str]] = []
    if not results_root.exists():
        return records
    for child in sorted(results_root.iterdir()):
        if not child.is_dir():
            continue
        try:
            workbook = find_ports_workbook(child)
        except Exception:
            workbook = None
        if workbook is not None:
            records.append({"country_name": child.name, "iso3": ""})
    return records


# =============================================================================
# PORT WORKBOOK LOADING
# =============================================================================

def find_country_dir(results_root: Path, record: Dict[str, str]) -> Path:
    """Locate the result folder for one country."""
    country_name = clean_text(record.get("country_name", ""))
    iso3 = clean_text(record.get("iso3", ""))

    exact_candidates = [
        results_root / country_name,
        results_root / iso3 if iso3 else None,
    ]
    hit = first_existing(exact_candidates)
    if hit is not None:
        return hit

    target_country_key = normalize_header(country_name)
    target_iso3_key = normalize_header(iso3)

    if results_root.exists():
        for child in sorted(results_root.iterdir()):
            if not child.is_dir():
                continue
            child_key = normalize_header(child.name)
            if child_key in {target_country_key, target_iso3_key}:
                return child

        partial_matches: List[Path] = []
        for child in sorted(results_root.iterdir()):
            if not child.is_dir():
                continue
            child_key = normalize_header(child.name)
            if target_country_key and (target_country_key in child_key or child_key in target_country_key):
                partial_matches.append(child)
        if len(partial_matches) == 1:
            return partial_matches[0]

    expected = results_root / country_name
    raise FileNotFoundError(f"Could not find result folder for {country_name} ({iso3}). Expected: {expected}")


def _excel_ports_workbook_candidates(country_dir: Path) -> List[Path]:
    """Return Excel fallback candidates containing chosen port coordinates."""
    candidates: List[Path] = [country_dir / PORTS_WORKBOOK_NAME]
    patterns = [
        "chosen_ports_of_top_locations.xls*",
        "*chosen*ports*top*locations*.xls*",
        "*chosen*ports*locations*.xls*",
        "*chosen*port*.xls*",
    ]
    for pattern in patterns:
        candidates.extend(sorted(path for path in country_dir.glob(pattern) if path.is_file()))
    out: List[Path] = []
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists():
            out.append(candidate)
    return out


def _port_column_sets(df: pd.DataFrame) -> Tuple[List[str], List[str], List[str]]:
    """Return candidate port-name, port-lat, and port-lon columns."""
    port_name_cols = columns_matching(
        df,
        [
            "canonical_main_port", "canonical port", "canonical_port", "main_port", "main port",
            "nearest_port", "nearest port", "port_name", "port name", "port",
        ],
    )
    port_lat_cols = columns_matching(
        df,
        [
            "canonical_port_lat", "canonical port lat", "canonical_port_latitude",
            "port_lat", "port latitude", "nearest_port_lat", "nearest port lat",
            "nearest_port_latitude", "port_y",
        ],
    )
    port_lon_cols = columns_matching(
        df,
        [
            "canonical_port_lon", "canonical port lon", "canonical_port_longitude",
            "port_lon", "port longitude", "nearest_port_lon", "nearest port lon",
            "nearest_port_longitude", "port_x", "port_lng",
        ],
    )
    if not port_lat_cols:
        port_lat_cols = [col for col in df.columns if "port" in normalize_header(col) and "lat" in normalize_header(col)]
    if not port_lon_cols:
        port_lon_cols = [
            col for col in df.columns
            if "port" in normalize_header(col) and ("lon" in normalize_header(col) or "lng" in normalize_header(col))
        ]
    return port_name_cols, port_lat_cols, port_lon_cols


def _sort_and_limit_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Sort by delivered cost when available and keep the requested top rows."""
    out = df.copy()
    rank_col = pick_column(
        out,
        preferred_names=[
            "nh3_delivered_usd_per_t", "delivered_nh3_usd_per_t",
            "delivered_cost", "delivered cost", "score", "obj_cost",
        ],
        contains_any=["delivered", "score"],
        required=False,
        purpose="delivered-cost ranking column",
    )
    if rank_col is not None:
        out[rank_col] = pd.to_numeric(out[rank_col], errors="coerce")
        out = out.sort_values(rank_col, ascending=True, na_position="last").copy()
    if TOP_LOCATIONS_PER_COUNTRY is not None:
        out = out.head(int(TOP_LOCATIONS_PER_COUNTRY)).copy()
    return out


def _fill_raw_port_coordinates_from_excel(raw_df: pd.DataFrame, country_dir: Path) -> pd.DataFrame:
    """Fill missing port coordinates from the Excel fallback while keeping raw site lat/lon."""
    raw_name_cols, raw_port_lat_cols, raw_port_lon_cols = _port_column_sets(raw_df)
    if raw_port_lat_cols and raw_port_lon_cols:
        return raw_df

    for excel_path in _excel_ports_workbook_candidates(country_dir):
        try:
            fb = read_table(excel_path)
            fb_name_cols, fb_lat_cols, fb_lon_cols = _port_column_sets(fb)
            if not raw_name_cols or not fb_name_cols or not fb_lat_cols or not fb_lon_cols:
                continue

            fb_map = pd.DataFrame({
                "key": first_non_null_series(fb, fb_name_cols).map(clean_text).map(normalize_header),
                "lat": pd.to_numeric(first_non_null_series(fb, fb_lat_cols), errors="coerce"),
                "lon": pd.to_numeric(first_non_null_series(fb, fb_lon_cols), errors="coerce"),
            }).dropna(subset=["lat", "lon"])
            fb_map = fb_map[fb_map["key"] != ""].drop_duplicates("key")
            if fb_map.empty:
                continue

            raw_keys = first_non_null_series(raw_df, raw_name_cols).map(clean_text).map(normalize_header)
            lat_lookup = dict(zip(fb_map["key"], fb_map["lat"]))
            lon_lookup = dict(zip(fb_map["key"], fb_map["lon"]))

            out = raw_df.copy()
            out["__fallback_port_lat"] = raw_keys.map(lat_lookup)
            out["__fallback_port_lon"] = raw_keys.map(lon_lookup)
            matched = int(out["__fallback_port_lat"].notna().sum())
            if matched:
                print(
                    f"    Filled port coordinates for {matched} raw optimiser row(s) from {excel_path.name}; "
                    "site lat/lon remain from the full-precision optimiser CSV."
                )
                return out
        except Exception as exc:
            print(f"    WARNING: could not use {excel_path.name} to fill port coordinates: {exc}")
    return raw_df


def find_ports_workbook(country_dir: Path) -> Optional[Path]:
    """Find the best location/port data file inside one country folder."""
    candidates: List[Path] = []

    if PREFER_OPTIMISATION_CSV:
        candidates.append(country_dir / OPTIMISATION_CSV_NAME)
        candidates.extend(sorted(path for path in country_dir.glob("*ammonia*delivered*cost*.csv") if path.is_file()))
        candidates.extend(sorted(path for path in country_dir.glob("*delivered*cost*.csv") if path.is_file()))

    candidates.extend(_excel_ports_workbook_candidates(country_dir))

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists():
            return candidate
    return None


def load_ports_for_country(results_root: Path, record: Dict[str, str]) -> Tuple[pd.DataFrame, Path]:
    """Read one country's chosen-location/port data and return normalized rows."""
    country_name = record.get("country_name", "")
    iso3 = record.get("iso3", "")
    country_dir = find_country_dir(results_root, record)
    source_path = find_ports_workbook(country_dir)
    if source_path is None:
        raise FileNotFoundError(f"Could not find {OPTIMISATION_CSV_NAME} or {PORTS_WORKBOOK_NAME} in {country_dir}")

    df = read_table(source_path)
    if df.empty:
        print(f"WARNING: {source_path} is empty; skipping.")
        return pd.DataFrame(), source_path

    is_raw_csv = source_path.suffix.lower() == ".csv" and "delivered" in normalize_header(source_path.stem)
    df = _sort_and_limit_rows(df)
    if is_raw_csv:
        df = _fill_raw_port_coordinates_from_excel(df, country_dir)

    lat_col = pick_column(
        df,
        preferred_names=["lat", "latitude", "location_lat", "location latitude", "site_lat", "site latitude"],
        contains_any=[],
        required=False,
        purpose="location latitude column",
    )
    lon_col = pick_column(
        df,
        preferred_names=["lon", "long", "longitude", "location_lon", "location longitude", "site_lon", "site longitude"],
        contains_any=[],
        required=False,
        purpose="location longitude column",
    )

    if lat_col is None or lon_col is None:
        raise KeyError(f"Could not find location lat/lon columns in {source_path}. Available columns: {list(df.columns)}")

    port_name_cols, port_lat_cols, port_lon_cols = _port_column_sets(df)

    if not port_lat_cols or not port_lon_cols:
        raise KeyError(f"Could not find port lat/lon columns in {source_path}. Available columns: {list(df.columns)}")

    out = pd.DataFrame({
        "country": country_name,
        "iso3": iso3,
        "lat": pd.to_numeric(df[lat_col], errors="coerce"),
        "lon": pd.to_numeric(df[lon_col], errors="coerce"),
        "port_name": first_non_null_series(df, port_name_cols).astype(str) if port_name_cols else "",
        "port_lat": pd.to_numeric(first_non_null_series(df, port_lat_cols), errors="coerce"),
        "port_lon": pd.to_numeric(first_non_null_series(df, port_lon_cols), errors="coerce"),
        "source_file": source_path.name,
        "source_dir": str(country_dir),
        "source_kind": "full_precision_optimiser_csv" if is_raw_csv else "chosen_ports_workbook",
    })

    if "distance_km" in df.columns:
        out["distance_km"] = pd.to_numeric(df["distance_km"], errors="coerce")
    if "nh3_delivered_usd_per_t" in df.columns:
        out["nh3_delivered_usd_per_t"] = pd.to_numeric(df["nh3_delivered_usd_per_t"], errors="coerce")

    # Clean text and remove invalid location coordinates.
    out["port_name"] = out["port_name"].map(lambda x: "" if is_blank(x) or str(x).lower() == "nan" else clean_text(x))
    before = len(out)
    out = out.dropna(subset=["lat", "lon"]).copy()
    out = out[(out["lat"].between(-90, 90)) & (out["lon"].between(-180, 180))].copy()
    dropped = before - len(out)
    if dropped:
        print(f"WARNING: dropped {dropped} invalid location row(s) from {source_path.name}")

    # Keep rows even if port coordinates are missing; only the connector/port is skipped for those rows.
    return out, source_path


def load_all_country_ports(results_root: Path, records: Sequence[Dict[str, str]]) -> Tuple[pd.DataFrame, List[Dict[str, str]]]:
    """Load all countries with chosen ports workbooks."""
    frames: List[pd.DataFrame] = []
    usable_records: List[Dict[str, str]] = []
    missing: List[str] = []
    errors: List[str] = []

    print("Reading chosen port/location workbooks from:")
    print(f"  {results_root}\\<Country name>\\{PORTS_WORKBOOK_NAME}")

    for record in records:
        country_name = record.get("country_name", "")
        iso3 = record.get("iso3", "")
        label = f"{country_name} ({iso3})" if iso3 else country_name
        try:
            df, workbook = load_ports_for_country(results_root, record)
        except FileNotFoundError as exc:
            missing.append(f"{label}: {exc}")
            continue
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            continue

        if not df.empty:
            frames.append(df)
            usable_records.append(record)
            unique_ports = df.dropna(subset=["port_lat", "port_lon"]).drop_duplicates(subset=["port_name", "port_lat", "port_lon"])
            print(f"  {label}: {len(df)} location(s), {len(unique_ports)} unique port(s) from {workbook}")
        else:
            print(f"  {label}: 0 location(s)")

    if missing:
        print("\nWARNING: missing chosen ports input file(s):")
        for item in missing:
            print("  - " + item)

    if errors:
        print("\nWARNING: input file(s) found but could not be read:")
        for item in errors:
            print("  - " + item)

    if not frames:
        raise ValueError("No valid chosen location / port rows were loaded.")

    return pd.concat(frames, ignore_index=True), usable_records


# =============================================================================
# COUNTRY BOUNDARIES
# =============================================================================

def load_country_boundaries() -> gpd.GeoDataFrame:
    """
    Load a country-boundary layer used for each square panel.

    Priority order:
    1. GeoPandas built-in Natural Earth countries, when available.
    2. A cached Natural Earth admin-0 countries ZIP under the project folder.
    3. Download Natural Earth admin-0 countries ZIP into Trade_version/.ocean_cache.
    """
    try:
        world_path = gpd.datasets.get_path("naturalearth_lowres")
        world = gpd.read_file(world_path)
    except Exception:
        url = (
            "https://naturalearth.s3.amazonaws.com/110m_cultural/"
            "ne_110m_admin_0_countries.zip"
        )

        cache_candidates = [
            PROJECT_DIR / "Trade_version" / ".ocean_cache" / "ne_110m_admin_0_countries.zip",
            PROJECT_DIR / "trade_version" / ".ocean_cache" / "ne_110m_admin_0_countries.zip",
            PROJECT_DIR / ".ocean_cache" / "ne_110m_admin_0_countries.zip",
            PROJECT_DIR / ".data" / "ne_10m_admin_0_countries.zip",
            PROJECT_DIR / ".data" / "ne_110m_admin_0_countries.zip",
        ]

        cached_zip = first_existing(cache_candidates)
        if cached_zip is None:
            hits = []
            try:
                hits.extend(PROJECT_DIR.rglob("ne_*_admin_0_countries*.zip"))
                hits.extend(PROJECT_DIR.rglob("*admin_0_countries*.shp"))
            except Exception:
                pass
            hits = [path for path in hits if path.exists()]
            cached_zip = sorted(hits)[0] if hits else None

        if cached_zip is not None:
            path_for_gpd = f"zip://{cached_zip}" if cached_zip.suffix.lower() == ".zip" else str(cached_zip)
            print(f"Loading country-boundary layer from cached Natural Earth file: {cached_zip}")
            world = gpd.read_file(path_for_gpd)
        else:
            target_zip = cache_candidates[0]
            target_zip.parent.mkdir(parents=True, exist_ok=True)
            try:
                print(f"Downloading country-boundary layer to: {target_zip}")
                urllib.request.urlretrieve(url, target_zip)
                world = gpd.read_file(f"zip://{target_zip}")
            except Exception as exc:
                raise FileNotFoundError(
                    "Could not load a country-boundary layer. GeoPandas did not provide "
                    "naturalearth_lowres, no cached admin-0 countries file was found, "
                    "and the Natural Earth download failed. Run once with internet access "
                    "or place ne_110m_admin_0_countries.zip in Trade_version/.ocean_cache."
                ) from exc

    if world.crs is None:
        world = world.set_crs(epsg=4326)
    elif world.crs.to_epsg() != 4326:
        world = world.to_crs(epsg=4326)
    return world


def _find_first_column(df: gpd.GeoDataFrame, candidates: Sequence[str]) -> Optional[str]:
    norm_map = {normalize_header(col): col for col in df.columns}
    for candidate in candidates:
        hit = norm_map.get(normalize_header(candidate))
        if hit is not None:
            return hit
    return None


def country_boundary_match_columns(countries_gdf: gpd.GeoDataFrame) -> Tuple[Optional[str], List[str]]:
    iso_col = _find_first_column(
        countries_gdf,
        ["iso_a3", "ISO_A3", "ADM0_A3", "adm0_a3", "SOV_A3", "GU_A3"],
    )
    name_cols = [
        col for col in [
            _find_first_column(countries_gdf, ["name", "NAME"]),
            _find_first_column(countries_gdf, ["admin", "ADMIN"]),
            _find_first_column(countries_gdf, ["sovereignt", "SOVEREIGNT"]),
            _find_first_column(countries_gdf, ["geounit", "GEOUNIT"]),
        ] if col is not None
    ]
    return iso_col, name_cols


def match_country_boundary_rows(
    countries_gdf: gpd.GeoDataFrame,
    record: Dict[str, str],
    iso_col: Optional[str],
    name_cols: Sequence[str],
) -> gpd.GeoDataFrame:
    iso_key = normalize_header(record.get("iso3", ""))
    country_key = normalize_header(record.get("country_name", ""))

    mask = pd.Series(False, index=countries_gdf.index)

    if iso_col is not None and iso_key:
        mask = mask | countries_gdf[iso_col].astype(str).map(normalize_header).isin({iso_key})

    if country_key:
        for col in name_cols:
            mask = mask | countries_gdf[col].astype(str).map(normalize_header).isin({country_key})

    rows = countries_gdf.loc[mask]
    if not rows.empty:
        return rows

    if country_key:
        partial_mask = pd.Series(False, index=countries_gdf.index)
        for col in name_cols:
            norm_values = countries_gdf[col].astype(str).map(normalize_header)
            partial_mask = partial_mask | norm_values.map(
                lambda value: bool(value) and (country_key in value or value in country_key)
            )
        rows = countries_gdf.loc[partial_mask]

    return rows


def union_geometries(geometries: gpd.GeoSeries):
    """Compatibility wrapper for Shapely 1.x and 2.x geometry union."""
    try:
        return geometries.union_all()
    except Exception:
        return geometries.unary_union


def mainland_geometry(geometry):
    """
    Return the mainland / largest polygon component of a country geometry.

    For MultiPolygons or GeometryCollections, this keeps only the largest polygon
    by area, which is a practical proxy for the continental/mainland part.
    """
    if geometry is None:
        return geometry

    try:
        if geometry.is_empty:
            return geometry
    except Exception:
        return geometry

    try:
        geom_type = getattr(geometry, "geom_type", "")

        if geom_type == "Polygon":
            return geometry

        if geom_type == "MultiPolygon":
            polygons = list(getattr(geometry, "geoms", []))
            return max(polygons, key=lambda g: float(g.area)) if polygons else geometry

        if geom_type == "GeometryCollection":
            polygons = [g for g in getattr(geometry, "geoms", []) if getattr(g, "geom_type", "") == "Polygon"]
            if polygons:
                return max(polygons, key=lambda g: float(g.area))
            multipolygons = [g for g in getattr(geometry, "geoms", []) if getattr(g, "geom_type", "") == "MultiPolygon"]
            if multipolygons:
                parts = []
                for mp in multipolygons:
                    parts.extend(list(getattr(mp, "geoms", [])))
                if parts:
                    return max(parts, key=lambda g: float(g.area))
    except Exception:
        pass

    return geometry


def build_country_geometry_lookup(
    countries_gdf: gpd.GeoDataFrame,
    records: Sequence[Dict[str, str]],
) -> Dict[str, object]:
    """Return a dict keyed by ISO3 or country name, with one geometry per country."""
    iso_col, name_cols = country_boundary_match_columns(countries_gdf)
    lookup: Dict[str, object] = {}
    unmatched: List[str] = []

    for record in records:
        country_name = record.get("country_name", "")
        iso3 = record.get("iso3", "")
        rows = match_country_boundary_rows(countries_gdf, record, iso_col, name_cols)
        if rows.empty:
            unmatched.append(f"{country_name} ({iso3})" if iso3 else country_name)
            continue
        geometry = union_geometries(rows.geometry)
        if MAINLAND_ONLY:
            geometry = mainland_geometry(geometry)

        key = record_key(record)
        lookup[key] = geometry

    if unmatched:
        print("WARNING: these countries could not be matched to a country boundary:")
        for item in unmatched:
            print("  - " + item)

    return lookup


def record_key(record: Dict[str, str]) -> str:
    return normalize_header(record.get("iso3", "")) or normalize_header(record.get("country_name", ""))


# =============================================================================
# PLOTTING
# =============================================================================

def set_panel_frame(ax) -> None:
    ax.set_xticks([])
    ax.set_yticks([])
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color(PANEL_BOX_COLOR)
        spine.set_linewidth(PANEL_BOX_LINEWIDTH)


def format_port_label(label: str) -> str:
    """Shorten and wrap a port label so it can stay inside the country panel."""
    label = clean_text(label)
    if PORT_LABEL_MAX_CHARS is not None and len(label) > int(PORT_LABEL_MAX_CHARS):
        label = label[: max(int(PORT_LABEL_MAX_CHARS) - 1, 1)].rstrip() + "…"
    wrap_chars = max(int(PORT_LABEL_WRAP_CHARS), 6)
    return textwrap.fill(label, width=wrap_chars, break_long_words=False, break_on_hyphens=True)


def estimate_label_bbox_axes(
    text_value: str,
    anchor_x: float,
    anchor_y: float,
    ha: str,
    va: str,
    fontsize: float,
) -> Tuple[float, float, float, float]:
    """Approximate a text-label bounding box in axes-fraction coordinates."""
    lines = str(text_value).splitlines() or [str(text_value)]
    max_len = max(len(line) for line in lines)
    n_lines = max(len(lines), 1)

    # Conservative sizing, tuned so labels remain inside even with fontsize=8+.
    width = 0.022 + 0.0115 * max_len * (fontsize / 6.0)
    height = 0.022 + 0.030 * n_lines * (fontsize / 6.0)

    # A single label should not occupy the entire country panel.
    width = min(width, 0.62)
    height = min(height, 0.36)

    if ha == "left":
        left = anchor_x
        right = anchor_x + width
    elif ha == "right":
        left = anchor_x - width
        right = anchor_x
    else:
        left = anchor_x - width / 2.0
        right = anchor_x + width / 2.0

    if va == "bottom":
        bottom = anchor_y
        top = anchor_y + height
    elif va == "top":
        bottom = anchor_y - height
        top = anchor_y
    else:
        bottom = anchor_y - height / 2.0
        top = anchor_y + height / 2.0

    return float(left), float(bottom), float(right), float(top)


def choose_port_label_layout(
    port_row: pd.Series,
    label: str,
    country_df: pd.DataFrame,
    ports_df: pd.DataFrame,
    main_bounds: Tuple[float, float, float, float],
    placed_label_bboxes: Optional[List[Tuple[float, float, float, float]]] = None,
) -> Dict[str, object]:
    """Choose a label position inside the panel while avoiding data as much as possible."""
    px = float(port_row["port_lon"])
    py = float(port_row["port_lat"])
    px_ax, py_ax = axes_xy_from_data(px, py, main_bounds)
    fs = float(PORT_LABEL_FONTSIZE)

    candidates = [
        {"name": "upper right", "ax": px_ax + 0.028, "ay": py_ax + 0.022, "ha": "left", "va": "bottom"},
        {"name": "upper left", "ax": px_ax - 0.028, "ay": py_ax + 0.022, "ha": "right", "va": "bottom"},
        {"name": "lower right", "ax": px_ax + 0.028, "ay": py_ax - 0.022, "ha": "left", "va": "top"},
        {"name": "lower left", "ax": px_ax - 0.028, "ay": py_ax - 0.022, "ha": "right", "va": "top"},
        {"name": "right", "ax": px_ax + 0.035, "ay": py_ax, "ha": "left", "va": "center"},
        {"name": "left", "ax": px_ax - 0.035, "ay": py_ax, "ha": "right", "va": "center"},
        {"name": "top", "ax": px_ax, "ay": py_ax + 0.028, "ha": "center", "va": "bottom"},
        {"name": "bottom", "ax": px_ax, "ay": py_ax - 0.028, "ha": "center", "va": "top"},
    ]

    best = None
    plot_bbox = (0.0, 0.0, 1.0, 1.0)
    point_buffer = float(PORT_LABEL_POINT_BUFFER)
    placed_label_bboxes = [] if placed_label_bboxes is None else list(placed_label_bboxes)

    for cand in candidates:
        bbox = estimate_label_bbox_axes(label, cand["ax"], cand["ay"], cand["ha"], cand["va"], fs)
        score = 0.0

        # Heavy penalty if outside the panel.
        overflow = max(0.0, -bbox[0]) + max(0.0, -bbox[1]) + max(0.0, bbox[2] - 1.0) + max(0.0, bbox[3] - 1.0)
        score += overflow * 3000.0

        red_overlap = 0
        for _, row in country_df.iterrows():
            lon = safe_float(row.get("lon"))
            lat = safe_float(row.get("lat"))
            if lon is None or lat is None:
                continue
            x, y = axes_xy_from_data(lon, lat, main_bounds)
            if point_inside_bbox(x, y, (bbox[0] - point_buffer, bbox[1] - point_buffer, bbox[2] + point_buffer, bbox[3] + point_buffer)):
                red_overlap += 1
                score += 200.0

        port_overlap = 0
        for _, prow in ports_df.iterrows():
            lon = safe_float(prow.get("port_lon"))
            lat = safe_float(prow.get("port_lat"))
            if lon is None or lat is None:
                continue
            x, y = axes_xy_from_data(lon, lat, main_bounds)
            if point_inside_bbox(x, y, bbox):
                # Do not punish the source port too harshly because the label may sit near it.
                same = abs(lon - px) < 1e-9 and abs(lat - py) < 1e-9
                score += 18.0 if same else 80.0
                port_overlap += 0 if same else 1

        # Penalize if a connection line cuts through the label.
        connected = connection_rows(country_df)
        for _, row in connected.iterrows():
            x1, y1 = axes_xy_from_data(float(row["lon"]), float(row["lat"]), main_bounds)
            x2, y2 = axes_xy_from_data(float(row["port_lon"]), float(row["port_lat"]), main_bounds)
            if segment_intersects_bbox(x1, y1, x2, y2, bbox):
                score += 70.0

        # Prefer slightly shorter displacement from the port symbol.
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        score += math.hypot(cx - px_ax, cy - py_ax) * 12.0

        # Border proximity.
        border_dist = min(bbox[0], bbox[1], 1.0 - bbox[2], 1.0 - bbox[3])
        border_touch = border_dist <= float(PORT_LABEL_BORDER_BUFFER)
        if border_touch:
            score += 8.0

        # Strongly avoid overlapping already-placed port labels.
        label_overlap_count = 0
        if PORT_LABEL_AVOID_OTHER_LABELS and placed_label_bboxes:
            expanded_bbox = (
                bbox[0] - point_buffer * 0.35,
                bbox[1] - point_buffer * 0.35,
                bbox[2] + point_buffer * 0.35,
                bbox[3] + point_buffer * 0.35,
            )
            for existing_bbox in placed_label_bboxes:
                if bbox_intersects(expanded_bbox, existing_bbox):
                    label_overlap_count += 1
                    score += float(PORT_LABEL_LABEL_OVERLAP_PENALTY)

        cand_result = dict(cand)
        cand_result.update({
            "bbox": bbox,
            "score": score,
            "red_overlap": red_overlap,
            "port_overlap": port_overlap,
            "border_touch": border_touch,
            "label_overlap_count": label_overlap_count,
        })
        if best is None or cand_result["score"] < best["score"]:
            best = cand_result

    assert best is not None
    # Strongly clamp final anchor inside the plot area. This prevents large-font
    # labels from being clipped by the country-box border.
    margin = float(PORT_LABEL_INNER_MARGIN)
    for _ in range(3):
        left, bottom, right, top = best["bbox"]
        dx = 0.0
        dy = 0.0
        if left < margin:
            dx = margin - left
        elif right > 1.0 - margin:
            dx = (1.0 - margin) - right
        if bottom < margin:
            dy = margin - bottom
        elif top > 1.0 - margin:
            dy = (1.0 - margin) - top

        best["ax"] = float(best["ax"] + dx)
        best["ay"] = float(best["ay"] + dy)
        best["bbox"] = estimate_label_bbox_axes(label, best["ax"], best["ay"], best["ha"], best["va"], fs)

    return best


def draw_port_labels(
    ax,
    ports_df: pd.DataFrame,
    country_df: pd.DataFrame,
    main_bounds: Tuple[float, float, float, float],
) -> None:
    """Draw port labels inside each panel, using a nearby clean position when possible."""
    if not LABEL_PORTS or ports_df.empty:
        return

    placed_label_bboxes: List[Tuple[float, float, float, float]] = []
    label_rows: List[Tuple[pd.Series, str]] = []
    for _, row in ports_df.head(MAX_PORT_LABELS_PER_COUNTRY).iterrows():
        raw_label = str(row.get("port_name", "")).strip()
        if not raw_label:
            continue
        label_rows.append((row, format_port_label(raw_label)))

    # Place larger/longer labels first so smaller ones can work around them.
    label_rows.sort(key=lambda item: len(str(item[1])), reverse=True)

    for row, label in label_rows:
        layout = choose_port_label_layout(row, label, country_df, ports_df, main_bounds, placed_label_bboxes=placed_label_bboxes)
        need_arrow = False
        if PORT_LABEL_ALLOW_ARROW_ON_BORDER and bool(layout.get("border_touch", False)):
            need_arrow = True
        if PORT_LABEL_ALLOW_ARROW_ON_OVERLAP and int(layout.get("red_overlap", 0)) > 0:
            need_arrow = True

        arrowprops = None
        if need_arrow:
            arrowprops = dict(
                arrowstyle='-',
                color=PORT_COLOR,
                lw=0.45,
                shrinkA=1.5,
                shrinkB=1.5,
                alpha=0.80,
            )

        ax.annotate(
            label,
            xy=(float(row["port_lon"]), float(row["port_lat"])),
            xycoords='data',
            xytext=(float(layout["ax"]), float(layout["ay"])),
            textcoords=ax.transAxes,
            ha=str(layout["ha"]),
            va=str(layout["va"]),
            fontsize=PORT_LABEL_FONTSIZE,
            color=PORT_COLOR,
            zorder=7,
            bbox=dict(
                boxstyle='round,pad=0.15',
                facecolor=PORT_LABEL_BOX_FACE_COLOR,
                edgecolor=PORT_LABEL_BOX_EDGE_COLOR,
                linewidth=PORT_LABEL_BOX_LINEWIDTH,
                alpha=PORT_LABEL_BOX_ALPHA,
            ),
            arrowprops=arrowprops,
            clip_on=False,
            annotation_clip=False,
        )
        if "bbox" in layout:
            placed_label_bboxes.append(tuple(layout["bbox"]))


def unique_ports(country_df: pd.DataFrame) -> pd.DataFrame:
    port_df = country_df.dropna(subset=["port_lat", "port_lon"]).copy()
    port_df = port_df[(port_df["port_lat"].between(-90, 90)) & (port_df["port_lon"].between(-180, 180))].copy()
    if port_df.empty:
        return port_df
    # Rounded coordinate columns avoid tiny floating differences creating duplicate port markers.
    port_df["_lat_round"] = port_df["port_lat"].round(6)
    port_df["_lon_round"] = port_df["port_lon"].round(6)
    port_df = port_df.drop_duplicates(subset=["port_name", "_lat_round", "_lon_round"]).copy()
    return port_df.drop(columns=["_lat_round", "_lon_round"], errors="ignore")


def snap_point_to_geometry(point: Point, geometry) -> Point:
    """Snap point to nearest point on polygon/multipolygon geometry."""
    try:
        if geometry is None or point is None or point.is_empty:
            return point
        if geometry.covers(point):
            return point
        return nearest_points(point, geometry)[1]
    except Exception:
        return point


def snap_point_to_boundary(point: Point, geometry) -> Point:
    """Snap point to nearest point on a country boundary."""
    try:
        if geometry is None or point is None or point.is_empty:
            return point
        boundary = geometry.boundary
        if boundary is None or boundary.is_empty:
            return point
        return nearest_points(point, boundary)[1]
    except Exception:
        return point


def adjusted_country_df_for_plot(country_df: pd.DataFrame, geometry, title: str = "") -> pd.DataFrame:
    """
    Return a plotting-only copy with optional land/shore snapping.

    The optimiser values remain in raw_lat/raw_lon/raw_port_lat/raw_port_lon.
    The displayed lat/lon columns are adjusted only for the figure.
    """
    df = country_df.copy()
    if df.empty or geometry is None:
        return df

    for col in ["lat", "lon", "port_lat", "port_lon"]:
        if col in df.columns and f"raw_{col}" not in df.columns:
            df[f"raw_{col}"] = df[col]

    site_snaps = 0
    port_snaps = 0

    if SNAP_LOCATIONS_TO_COUNTRY_FOR_PLOT:
        new_lats = []
        new_lons = []
        for _, row in df.iterrows():
            lat = safe_float(row.get("lat"))
            lon = safe_float(row.get("lon"))
            if lat is None or lon is None:
                new_lats.append(row.get("lat"))
                new_lons.append(row.get("lon"))
                continue
            p = Point(lon, lat)
            q = snap_point_to_geometry(p, geometry)
            if q is not None and not q.is_empty:
                if abs(q.x - lon) > 1e-10 or abs(q.y - lat) > 1e-10:
                    site_snaps += 1
                new_lats.append(float(q.y))
                new_lons.append(float(q.x))
            else:
                new_lats.append(lat)
                new_lons.append(lon)
        df["lat"] = new_lats
        df["lon"] = new_lons

    if SNAP_PORTS_TO_COUNTRY_BOUNDARY_FOR_PLOT:
        new_plats = []
        new_plons = []
        for _, row in df.iterrows():
            plat = safe_float(row.get("port_lat"))
            plon = safe_float(row.get("port_lon"))
            if plat is None or plon is None:
                new_plats.append(row.get("port_lat"))
                new_plons.append(row.get("port_lon"))
                continue
            p = Point(plon, plat)
            q = snap_point_to_boundary(p, geometry)
            if q is not None and not q.is_empty:
                if abs(q.x - plon) > 1e-10 or abs(q.y - plat) > 1e-10:
                    port_snaps += 1
                new_plats.append(float(q.y))
                new_plons.append(float(q.x))
            else:
                new_plats.append(plat)
                new_plons.append(plon)
        df["port_lat"] = new_plats
        df["port_lon"] = new_plons

    if PRINT_PLOT_SNAP_WARNINGS and (site_snaps or port_snaps):
        label = f" for {title}" if title else ""
        print(f"[plot snap]{label}: adjusted {site_snaps} site point(s), {port_snaps} port point(s) for display only.")

    return df


def connection_rows(country_df: pd.DataFrame) -> pd.DataFrame:
    """Rows with valid location and valid connected port coordinates."""
    connected = country_df.dropna(subset=["lat", "lon", "port_lat", "port_lon"]).copy()
    connected = connected[
        connected["lat"].between(-90, 90)
        & connected["lon"].between(-180, 180)
        & connected["port_lat"].between(-90, 90)
        & connected["port_lon"].between(-180, 180)
    ].copy()
    return connected


def focus_pair_bounds(country_df: pd.DataFrame) -> Optional[Tuple[float, float, float, float]]:
    """
    Return bounds based on the furthest location-to-port connection.

    The main panel is intended to focus on the active chosen-location / port
    system rather than on the whole country. The anchor span is defined by the
    furthest location-port pair, optionally expanded to include all loaded
    locations and ports for that country.
    """
    connected = connection_rows(country_df)
    if connected.empty:
        return None

    # Approximate lon scaling by cos(latitude) so distances are more realistic.
    lat_mid = float((connected["lat"].mean() + connected["port_lat"].mean()) / 2.0)
    cos_lat = max(math.cos(math.radians(lat_mid)), 0.2)
    dx = (connected["lon"] - connected["port_lon"]) * cos_lat
    dy = connected["lat"] - connected["port_lat"]
    dist2 = dx * dx + dy * dy
    furthest_idx = dist2.idxmax()
    furthest = connected.loc[furthest_idx]

    xs = [float(furthest["lon"]), float(furthest["port_lon"])]
    ys = [float(furthest["lat"]), float(furthest["port_lat"])]

    if ZOOM_INCLUDE_ALL_CONNECTIONS:
        xs.extend(pd.to_numeric(country_df["lon"], errors="coerce").dropna().astype(float).tolist())
        ys.extend(pd.to_numeric(country_df["lat"], errors="coerce").dropna().astype(float).tolist())
        port_rows = country_df.dropna(subset=["port_lon", "port_lat"])
        if not port_rows.empty:
            xs.extend(pd.to_numeric(port_rows["port_lon"], errors="coerce").dropna().astype(float).tolist())
            ys.extend(pd.to_numeric(port_rows["port_lat"], errors="coerce").dropna().astype(float).tolist())

    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    xspan = max(xmax - xmin, MIN_PANEL_SPAN_DEG)
    yspan = max(ymax - ymin, MIN_PANEL_SPAN_DEG)
    span = max(xspan, yspan)
    pad = max(span * PANEL_PADDING_FRAC, 0.10)
    span = span + 2 * pad
    xmid = (xmin + xmax) / 2.0
    ymid = (ymin + ymax) / 2.0
    return xmid - span / 2.0, xmid + span / 2.0, ymid - span / 2.0, ymid + span / 2.0


def inset_bounds_from_geometry(geometry) -> Tuple[float, float, float, float]:
    """Bounds for the whole-country inset."""
    if geometry is None:
        return -180, 180, -90, 90

    try:
        xmin, ymin, xmax, ymax = geometry.bounds
        xspan = max(float(xmax - xmin), MIN_PANEL_SPAN_DEG)
        yspan = max(float(ymax - ymin), MIN_PANEL_SPAN_DEG)
        span = max(xspan, yspan)
        pad = max(span * 0.06, 0.05)
        span = span + 2 * pad
        xmid = (float(xmin) + float(xmax)) / 2.0
        ymid = (float(ymin) + float(ymax)) / 2.0
        return xmid - span / 2.0, xmid + span / 2.0, ymid - span / 2.0, ymid + span / 2.0
    except Exception:
        return -180, 180, -90, 90


def axes_xy_from_data(x: float, y: float, main_bounds: Tuple[float, float, float, float]) -> Tuple[float, float]:
    """Convert data coordinates to axes-fraction coordinates for the main panel."""
    xmin, xmax, ymin, ymax = main_bounds
    xspan = max(float(xmax - xmin), 1e-12)
    yspan = max(float(ymax - ymin), 1e-12)
    return (float(x) - float(xmin)) / xspan, (float(y) - float(ymin)) / yspan


def inset_rect_for_location(location: str) -> Tuple[float, float, float, float]:
    """Return inset rectangle in axes coordinates: x0, y0, width, height."""
    loc = str(location).strip().lower()
    w = float(INSET_WIDTH_FRAC)
    h = float(INSET_HEIGHT_FRAC)
    m = float(INSET_MARGIN_FRAC)

    if loc == "upper left":
        return m, 1.0 - h - m, w, h
    if loc == "lower left":
        return m, m, w, h
    if loc == "lower right":
        return 1.0 - w - m, m, w, h
    # Default: upper right.
    return 1.0 - w - m, 1.0 - h - m, w, h


def rect_to_bbox(rect: Tuple[float, float, float, float], margin: float = 0.0) -> Tuple[float, float, float, float]:
    """Convert x0, y0, width, height to left, bottom, right, top, with margin."""
    x0, y0, w, h = rect
    return (
        float(x0) - margin,
        float(y0) - margin,
        float(x0) + float(w) + margin,
        float(y0) + float(h) + margin,
    )


def point_inside_bbox(x: float, y: float, bbox: Tuple[float, float, float, float]) -> bool:
    left, bottom, right, top = bbox
    return left <= float(x) <= right and bottom <= float(y) <= top


def bbox_intersects(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> bool:
    a_left, a_bottom, a_right, a_top = a
    b_left, b_bottom, b_right, b_top = b
    return not (a_right < b_left or b_right < a_left or a_top < b_bottom or b_top < a_bottom)


def segment_intersects_bbox(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    bbox: Tuple[float, float, float, float],
) -> bool:
    """
    Return True if the segment intersects the bbox.

    Uses the Liang-Barsky clipping test, robust for vertical / horizontal lines.
    Coordinates are expected to be in axes-fraction space.
    """
    left, bottom, right, top = bbox

    if point_inside_bbox(x1, y1, bbox) or point_inside_bbox(x2, y2, bbox):
        return True

    dx = float(x2) - float(x1)
    dy = float(y2) - float(y1)
    p = [-dx, dx, -dy, dy]
    q = [float(x1) - left, right - float(x1), float(y1) - bottom, top - float(y1)]

    u1 = 0.0
    u2 = 1.0
    for pi, qi in zip(p, q):
        if abs(pi) < 1e-12:
            if qi < 0:
                return False
        else:
            t = qi / pi
            if pi < 0:
                u1 = max(u1, t)
            else:
                u2 = min(u2, t)
            if u1 > u2:
                return False
    return True


def score_inset_location(
    location: str,
    main_bounds: Tuple[float, float, float, float],
    country_df: pd.DataFrame,
) -> float:
    """
    Score an inset location.

    Lower is better. The scoring penalizes overlaps with chosen locations, ports,
    port labels, and location-port connection lines.
    """
    rect = inset_rect_for_location(location)
    bbox = rect_to_bbox(rect, margin=float(INSET_AVOID_MARGIN_FRAC))
    score = 0.0

    if country_df.empty:
        return score

    # Location points.
    for _, row in country_df.iterrows():
        lon = safe_float(row.get("lon"))
        lat = safe_float(row.get("lat"))
        if lon is None or lat is None:
            continue
        x, y = axes_xy_from_data(lon, lat, main_bounds)
        if point_inside_bbox(x, y, bbox):
            score += 120.0

    # Port points and approximate label footprints.
    ports = unique_ports(country_df)
    if not ports.empty:
        for _, row in ports.iterrows():
            lon = safe_float(row.get("port_lon"))
            lat = safe_float(row.get("port_lat"))
            if lon is None or lat is None:
                continue
            x, y = axes_xy_from_data(lon, lat, main_bounds)
            if point_inside_bbox(x, y, bbox):
                score += 140.0

        if LABEL_PORTS:
            for _, row in ports.head(MAX_PORT_LABELS_PER_COUNTRY).iterrows():
                label = str(row.get("port_name", "")).strip()
                if not label:
                    continue
                lon = safe_float(row.get("port_lon"))
                lat = safe_float(row.get("port_lat"))
                if lon is None or lat is None:
                    continue
                x, y = axes_xy_from_data(lon, lat, main_bounds)

                # Port labels are drawn with a small positive offset. Approximate
                # their footprint in axes coordinates so the inset avoids them.
                label_w = min(0.02 + 0.0075 * len(label), 0.28)
                label_h = 0.060
                label_bbox = (x + 0.006, y + 0.006, x + label_w, y + label_h)
                if bbox_intersects(label_bbox, bbox):
                    score += 90.0

    # Connection lines.
    connected = connection_rows(country_df)
    for _, row in connected.iterrows():
        x1, y1 = axes_xy_from_data(float(row["lon"]), float(row["lat"]), main_bounds)
        x2, y2 = axes_xy_from_data(float(row["port_lon"]), float(row["port_lat"]), main_bounds)
        if segment_intersects_bbox(x1, y1, x2, y2, bbox):
            score += 80.0

    # Small secondary density penalty: if all corners are collision-free, this
    # prefers the corner furthest from the data cluster.
    points: List[Tuple[float, float]] = []
    for _, row in country_df.iterrows():
        lon = safe_float(row.get("lon"))
        lat = safe_float(row.get("lat"))
        if lon is not None and lat is not None:
            points.append(axes_xy_from_data(lon, lat, main_bounds))
    if not ports.empty:
        for _, row in ports.iterrows():
            lon = safe_float(row.get("port_lon"))
            lat = safe_float(row.get("port_lat"))
            if lon is not None and lat is not None:
                points.append(axes_xy_from_data(lon, lat, main_bounds))

    if points:
        x0, y0, w, h = rect
        cx = x0 + w / 2.0
        cy = y0 + h / 2.0
        nearest = min(math.hypot(px - cx, py - cy) for px, py in points)
        score += max(0.0, 1.0 - nearest) * 2.0

    return score


def choose_inset_location(main_bounds: Tuple[float, float, float, float], country_df: pd.DataFrame) -> str:
    """
    Choose the best inset location for one country panel.

    If INSET_LOCATION is not "auto", that fixed location is used.
    """
    requested = str(INSET_LOCATION).strip().lower()
    valid_locations = ["upper right", "upper left", "lower right", "lower left"]

    if requested in valid_locations:
        return requested

    scores = [(score_inset_location(loc, main_bounds, country_df), loc) for loc in valid_locations]
    scores.sort(key=lambda item: (item[0], valid_locations.index(item[1])))
    return scores[0][1]


def maybe_add_country_inset(ax, geometry, main_bounds, country_df: pd.DataFrame) -> None:
    """Add a small whole-country inset in the cleanest available panel corner."""
    if not SHOW_COUNTRY_INSET or geometry is None:
        return

    location = choose_inset_location(main_bounds, country_df)
    rect = inset_rect_for_location(location)

    try:
        # Axes-coordinate placement lets each country panel choose a different
        # exact corner while using the same size.
        inset_ax = ax.inset_axes(rect)
    except Exception:
        loc_map = {
            "upper right": 1,
            "upper left": 2,
            "lower left": 3,
            "lower right": 4,
        }
        inset_ax = inset_axes(
            ax,
            width=f"{INSET_WIDTH_FRAC * 100:.1f}%",
            height=f"{INSET_HEIGHT_FRAC * 100:.1f}%",
            loc=loc_map.get(location, 1),
            borderpad=0.45,
        )

    try:
        country_gdf = gpd.GeoDataFrame([{"geometry": geometry}], geometry="geometry", crs="EPSG:4326")
        country_gdf.plot(
            ax=inset_ax,
            facecolor="none" if INSET_COUNTRY_FACE_COLOR == "none" else INSET_COUNTRY_FACE_COLOR,
            edgecolor="none",
            linewidth=0.0,
            zorder=1,
        )
        country_gdf.boundary.plot(
            ax=inset_ax,
            color=INSET_COUNTRY_BORDER_COLOR,
            linewidth=INSET_COUNTRY_BORDER_LINEWIDTH,
            zorder=2,
        )
    except Exception:
        pass

    # Tiny points in the inset for context.
    ports = unique_ports(country_df)
    if not ports.empty:
        inset_ax.scatter(
            ports["port_lon"].to_numpy(),
            ports["port_lat"].to_numpy(),
            s=max(PORT_POINT_SIZE * 0.20, 6),
            marker="s",
            facecolor=PORT_COLOR,
            edgecolor=PORT_EDGE_COLOR,
            linewidth=max(PORT_EDGE_WIDTH * 0.6, 0.15),
            zorder=4,
        )
    if not country_df.empty:
        inset_ax.scatter(
            country_df["lon"].to_numpy(),
            country_df["lat"].to_numpy(),
            s=max(LOCATION_POINT_SIZE * 0.18, 5),
            marker="o",
            facecolor=LOCATION_COLOR,
            edgecolor=LOCATION_EDGE_COLOR,
            linewidth=max(LOCATION_EDGE_WIDTH * 0.6, 0.15),
            zorder=5,
        )

    # Draw zoom window on the inset.
    if INSET_SHOW_ZOOM_BOX and main_bounds is not None:
        xmin, xmax, ymin, ymax = main_bounds
        inset_ax.add_patch(
            Rectangle(
                (xmin, ymin),
                xmax - xmin,
                ymax - ymin,
                fill=False,
                edgecolor=INSET_ZOOM_BOX_COLOR,
                linewidth=INSET_ZOOM_BOX_LINEWIDTH,
                zorder=6,
            )
        )

    ixmin, ixmax, iymin, iymax = inset_bounds_from_geometry(geometry)
    inset_ax.set_xlim(ixmin, ixmax)
    inset_ax.set_ylim(iymin, iymax)
    inset_ax.set_aspect("equal", adjustable="box")
    try:
        inset_ax.set_box_aspect(1)
    except Exception:
        pass
    inset_ax.set_xticks([])
    inset_ax.set_yticks([])
    inset_ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    for spine in inset_ax.spines.values():
        spine.set_visible(True)
        spine.set_color(INSET_BORDER_COLOR)
        spine.set_linewidth(INSET_BORDER_LINEWIDTH)


def panel_bounds(geometry, country_df: pd.DataFrame) -> Tuple[float, float, float, float]:
    """
    Return square lon/lat bounds for one panel.

    By default, the main panel focuses on the active location-port system,
    anchored by the furthest location-to-port connection. If that cannot be
    computed, the function falls back to the older full-data bounds.
    """
    if ZOOM_TO_CONNECTED_SYSTEM:
        focus_bounds = focus_pair_bounds(country_df)
        if focus_bounds is not None:
            return focus_bounds

    xs: List[float] = []
    ys: List[float] = []

    try:
        xmin, ymin, xmax, ymax = geometry.bounds
        xs.extend([float(xmin), float(xmax)])
        ys.extend([float(ymin), float(ymax)])
    except Exception:
        pass

    if not country_df.empty:
        xs.extend(pd.to_numeric(country_df["lon"], errors="coerce").dropna().astype(float).tolist())
        ys.extend(pd.to_numeric(country_df["lat"], errors="coerce").dropna().astype(float).tolist())

        port_rows = country_df.dropna(subset=["port_lon", "port_lat"])
        if not port_rows.empty:
            xs.extend(pd.to_numeric(port_rows["port_lon"], errors="coerce").dropna().astype(float).tolist())
            ys.extend(pd.to_numeric(port_rows["port_lat"], errors="coerce").dropna().astype(float).tolist())

    if not xs or not ys:
        return -180, 180, -90, 90

    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)

    xspan = max(xmax - xmin, MIN_PANEL_SPAN_DEG)
    yspan = max(ymax - ymin, MIN_PANEL_SPAN_DEG)
    span = max(xspan, yspan)
    pad = max(span * PANEL_PADDING_FRAC, 0.10)
    span = span + 2 * pad

    xmid = (xmin + xmax) / 2.0
    ymid = (ymin + ymax) / 2.0
    return xmid - span / 2.0, xmid + span / 2.0, ymid - span / 2.0, ymid + span / 2.0


def plot_one_country_panel(
    ax,
    record: Dict[str, str],
    country_df: pd.DataFrame,
    geometry,
) -> None:
    """Draw one square country panel."""
    country_name = record.get("country_name", "")
    iso3 = record.get("iso3", "")
    title = f"{country_name} ({iso3})" if iso3 else country_name

    # Use a plotting-only copy so the visual points are snapped to land/shore
    # where necessary, while raw optimiser coordinates remain available in
    # raw_lat/raw_lon/raw_port_lat/raw_port_lon.
    country_df = adjusted_country_df_for_plot(country_df, geometry, title=title)

    if geometry is not None:
        try:
            country_gdf = gpd.GeoDataFrame([{"geometry": geometry}], geometry="geometry", crs="EPSG:4326")
            country_gdf.plot(
                ax=ax,
                facecolor="none" if COUNTRY_FACE_COLOR == "none" else COUNTRY_FACE_COLOR,
                edgecolor="none",
                linewidth=0.0,
                zorder=1,
            )
            country_gdf.boundary.plot(
                ax=ax,
                color=COUNTRY_BORDER_COLOR,
                linewidth=COUNTRY_BORDER_LINEWIDTH,
                zorder=2,
            )
        except Exception as exc:
            print(f"WARNING: could not draw country geometry for {title}: {exc}")

    # Draw connection lines underneath markers.
    connected = connection_rows(country_df)
    for _, row in connected.iterrows():
        ax.plot(
            [float(row["lon"]), float(row["port_lon"])],
            [float(row["lat"]), float(row["port_lat"])],
            color=CONNECTION_COLOR,
            linewidth=CONNECTION_LINEWIDTH,
            linestyle=CONNECTION_LINESTYLE,
            alpha=CONNECTION_ALPHA,
            zorder=3,
        )

    # Ports.
    ports = unique_ports(country_df)
    if not ports.empty:
        ax.scatter(
            ports["port_lon"].to_numpy(),
            ports["port_lat"].to_numpy(),
            s=PORT_POINT_SIZE,
            marker="s",
            facecolor=PORT_COLOR,
            edgecolor=PORT_EDGE_COLOR,
            linewidth=PORT_EDGE_WIDTH,
            zorder=5,
        )

    # Chosen renewable locations.
    if not country_df.empty:
        ax.scatter(
            country_df["lon"].to_numpy(),
            country_df["lat"].to_numpy(),
            s=LOCATION_POINT_SIZE,
            marker="o",
            facecolor=LOCATION_COLOR,
            edgecolor=LOCATION_EDGE_COLOR,
            linewidth=LOCATION_EDGE_WIDTH,
            zorder=6,
        )

    xmin, xmax, ymin, ymax = panel_bounds(geometry, country_df)

    # Labels.
    if LABEL_PORTS and not ports.empty:
        draw_port_labels(ax, ports, country_df, (xmin, xmax, ymin, ymax))

    if LABEL_LOCATIONS and not country_df.empty:
        for idx, row in country_df.iterrows():
            ax.annotate(
                str(idx + 1),
                (float(row["lon"]), float(row["lat"])),
                xytext=(2, 2),
                textcoords="offset points",
                fontsize=LOCATION_LABEL_FONTSIZE,
                color=LOCATION_COLOR,
                zorder=7,
            )

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal", adjustable="box")
    try:
        ax.set_box_aspect(1)
    except Exception:
        pass
    set_panel_frame(ax)
    ax.set_title(title, fontsize=TITLE_FONTSIZE, pad=TITLE_PAD)

    # Add small whole-country inset for context. The country name remains outside
    # the panel as the panel title.
    maybe_add_country_inset(ax, geometry, (xmin, xmax, ymin, ymax), country_df)


def plot_all_countries_grid(
    points_df: pd.DataFrame,
    records: Sequence[Dict[str, str]],
    geometry_lookup: Dict[str, object],
    output_png: Path,
    dpi: int = 600,
) -> Path:
    """Create the full grid figure."""
    # Keep only records with data, in country-list order.
    plot_records: List[Dict[str, str]] = []
    for record in records:
        key = record_key(record)
        country_df = points_df.loc[
            (points_df["iso3"].astype(str).str.upper() == record.get("iso3", "").upper())
            if record.get("iso3") else
            (points_df["country"].astype(str).map(normalize_header) == normalize_header(record.get("country_name", "")))
        ]
        if not country_df.empty:
            plot_records.append(record)

    n = len(plot_records)
    if n == 0:
        raise ValueError("No countries with loaded data to plot.")

    if GRID_COLUMNS is None:
        ncols = max(1, math.ceil(math.sqrt(n)))
    else:
        ncols = max(1, int(GRID_COLUMNS))
    nrows = math.ceil(n / ncols)

    fig_width = ncols * PANEL_SIZE_INCHES + (LEGEND_RIGHT_SPACE_INCHES if SHOW_LEGEND else 0.0)
    fig_height = nrows * PANEL_SIZE_INCHES + (0.35 if SHOW_LEGEND else 0.0)
    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(fig_width, fig_height),
        squeeze=False,
        constrained_layout=True,
    )
    fig.set_constrained_layout_pads(
    w_pad=-0.5,
    h_pad=0.02,
    wspace=-0.5,
    hspace=0.04,
    )

    for ax in axes.ravel():
        ax.set_visible(False)

    for idx, record in enumerate(plot_records):
        ax = axes.ravel()[idx]
        ax.set_visible(True)
        key = record_key(record)
        country_name_norm = normalize_header(record.get("country_name", ""))
        iso3 = record.get("iso3", "")
        if iso3:
            country_df = points_df.loc[points_df["iso3"].astype(str).str.upper() == iso3.upper()].copy()
        else:
            country_df = points_df.loc[points_df["country"].astype(str).map(normalize_header) == country_name_norm].copy()

        geometry = geometry_lookup.get(key)
        plot_one_country_panel(ax, record, country_df, geometry)

    fig.suptitle(FIGURE_TITLE, fontsize=SUPTITLE_FONTSIZE)

    if SHOW_LEGEND:
        handles = [
            Line2D([0], [0], marker="o", color="none", markerfacecolor=LOCATION_COLOR,
                   markeredgecolor=LOCATION_EDGE_COLOR, markersize=LEGEND_MARKERSIZE,
                   label="Chosen renewable optimisation location"),
            Line2D([0], [0], marker="s", color="none", markerfacecolor=PORT_COLOR,
                   markeredgecolor=PORT_EDGE_COLOR, markersize=LEGEND_MARKERSIZE,
                   label="Connected port"),
            Line2D([0], [0], color=CONNECTION_COLOR, linewidth=CONNECTION_LINEWIDTH,
                   alpha=CONNECTION_ALPHA, label="Location-port link (straight-line cost basis)"),
        ]
        fig.legend(
            handles=handles,
            loc=LEGEND_LOCATION,
            ncol=LEGEND_NCOL,
            frameon=True,
            framealpha=0.95,
            fontsize=LEGEND_FONTSIZE,
            bbox_to_anchor=LEGEND_BBOX_TO_ANCHOR,
            borderaxespad=0.0,
        )

    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return output_png.resolve()


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    print("Project directory:", PROJECT_DIR)
    print("Results root:", RESULTS_ROOT)
    print("Preferred optimiser CSV:", OPTIMISATION_CSV_NAME if PREFER_OPTIMISATION_CSV else "disabled")
    print("Fallback ports workbook:", PORTS_WORKBOOK_NAME)
    print("Output PNG:", OUTPUT_PNG)

    if not RESULTS_ROOT.exists():
        raise FileNotFoundError(
            "RESULTS_ROOT does not exist. Edit PROJECT_DIR or RESULTS_ROOT at the top of this script.\n"
            f"Current value: {RESULTS_ROOT}"
        )

    country_list_path = locate_country_list(PROJECT_DIR, COUNTRY_LIST_PATH)
    if country_list_path is not None:
        print(f"Using country list: {country_list_path}")
        records = load_country_list(country_list_path)
    else:
        print("No usable country list found. Discovering country folders with chosen ports workbooks.")
        records = discover_country_records_from_results(RESULTS_ROOT)

    if not records:
        raise ValueError(
            "No selected countries found. Provide a Country_list / List of Countries workbook, "
            "or ensure country folders contain chosen_ports_of_top_locations.xlsx."
        )

    print(f"Selected/discovered countries: {len(records)}")
    ports_df, usable_records = load_all_country_ports(RESULTS_ROOT, records)
    print(
        f"Loaded {len(ports_df)} chosen location row(s) "
        f"from {len(usable_records)} country/countries."
    )

    try:
        countries_gdf = load_country_boundaries()
        geometry_lookup = build_country_geometry_lookup(countries_gdf, usable_records)
    except Exception as exc:
        print(
            "WARNING: could not load country boundaries. "
            "The figure will still show the chosen locations, ports, and connection lines, "
            f"but country outlines will be omitted. Details: {exc}"
        )
        geometry_lookup = {}

    saved_path = plot_all_countries_grid(
        points_df=ports_df,
        records=usable_records,
        geometry_lookup=geometry_lookup,
        output_png=OUTPUT_PNG,
        dpi=OUTPUT_DPI,
    )

    print(f"\nSaved 600-DPI PNG figure to:\n{saved_path}")


if __name__ == "__main__":
    main()
