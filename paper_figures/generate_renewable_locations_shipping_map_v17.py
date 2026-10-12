#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate a 600-DPI PNG map using the same shipping-map background files/cache
used by Shipping_route_model.py.

The main map highlights the selected countries only:
- selected-country border = continuous grey line
- selected-country interior = hatched light-grey lines

The renewable optimisation locations are NOT plotted as red points on the main
map. Instead, the script creates mini country maps around the sides of the main
map. Each mini country map draws that country and shows its selected renewable
locations as red points. The mini maps use only the mainland by default,
can be forced to use uniform box sizes, and are packed tightly around the main
map so they form a square-like frame.

Save this file in:
    C:\\Users\\user\\Documents\\Project\\AP_IRA_Model
then run it in Spyder.

Country-list workbook layout:
    - Column A: country name / folder name, e.g. Australia, Chile, Egypt
    - Column C: ISO3, with heading ISO3, e.g. AUS, CHL, EGY

For each selected country, the script reads:
    <PROJECT_DIR>\\trade_version_results\\<Country name from column A>\\optimal_locations\\AP_location_dataset_<ISO3>_new.xlsx

The PNG output is saved in PROJECT_DIR, i.e. the same directory as this script
when the script is saved in the project folder.
"""

from __future__ import annotations

import json
import math
import re
import sys
import unicodedata
import urllib.request
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import ConnectionPatch


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

# Country-list workbook. Leave as None to auto-detect it. The script validates
# that the chosen workbook contains ISO3 values. If auto-detection picks the
# wrong file, set the path explicitly, for example:
# COUNTRY_LIST_PATH = PROJECT_DIR / "List of Countries - Copy (2).xlsx"
COUNTRY_LIST_PATH: Optional[Path] = None

# Output PNG. This saves in the same directory as this script / project root.
OUTPUT_DPI = 600
OUTPUT_PNG = PROJECT_DIR / "selected_renewable_optimisation_locations_map_600dpi.png"

# Main map / output controls.
# Red points are removed from the main figure by default and shown only in the
# side country callouts.
SHOW_POINTS_ON_MAIN_MAP = False
SHOW_POINT_LEGEND = False

# Red optimisation-point styling.
POINT_SIZE = 20              # main-map point size, only used if SHOW_POINTS_ON_MAIN_MAP=True
CALLOUT_POINT_SIZE = 20      # mini-map point size
POINT_ALPHA = 0.90
LABEL_POINTS = False         # set True only if you want labels beside dots
MAX_POINTS_TO_LABEL = 150    # safety limit if LABEL_POINTS=True

# Selected-country overlay styling on the MAIN MAP.
# Only the selected countries are highlighted. Their border is drawn as a
# continuous grey line, and their interior is hatched.
#
# USER TOGGLES FOR BORDER / HATCH APPEARANCE
# -----------------------------------------
# Border appearance
SELECTED_BORDER_COLOR = "0.55"      # e.g. "0.55", "grey", "#8a8a8a"
SELECTED_BORDER_LINEWIDTH = 1.15     # border thickness

# Hatch appearance
SELECTED_HATCH_COLOR = "0.82"       # e.g. "0.82", "lightgrey", "#d0d0d0"
SELECTED_HATCH_LINEWIDTH = 0.30      # thickness of the hatch strokes
SELECTED_HATCH_DENSITY = 4           # number of repeated hatch chars; higher = denser
SELECTED_HATCH_CHAR = "/"           # "/", "\", "|", "-", "+", "x", ".", "*"

# Mini country-map / callout controls.
CREATE_COUNTRY_CALLOUTS = True
CALLOUT_FIGSIZE = (17.0, 10.0)       # total PNG canvas size before dpi scaling
CALLOUT_PAGE_MARGIN = 0.020
CALLOUT_GAP = 0.01                  # target gap between neighbouring mini maps
CALLOUT_EQUAL_INTERPANEL_SPACING = True  # use the same gap on top/bottom/left/right for a cohesive frame
CALLOUT_SIDE_WIDTH = 0.170           # left/right side band width
CALLOUT_TOP_HEIGHT = 0.185           # top mini-map band height
CALLOUT_BOTTOM_HEIGHT = 0.185        # bottom mini-map band height
CALLOUT_PADDING_FRAC = 0.05          # padding around country in mini maps
CALLOUT_TITLE_FONTSIZE = 6.5
CALLOUT_BOX_COLOR = "0.70"
CALLOUT_BOX_LINEWIDTH = 0.45
CALLOUT_COUNTRY_FACE_COLOR = "0.96"  # use "none" for no fill
CALLOUT_COUNTRY_BORDER_COLOR = "0.40"
CALLOUT_COUNTRY_BORDER_LINEWIDTH = 0.70
CALLOUT_HATCH_COUNTRIES = False      # set True to hatch mini country maps too
CALLOUT_MAINLAND_ONLY = True         # True = mini maps use only the mainland / largest polygon

# Make all country boxes the same size when possible. If a side has too many
# countries to fit, the script scales all boxes down uniformly.
CALLOUT_UNIFORM_BOX_SIZE = True
CALLOUT_PANEL_WIDTH = 0.120          # base width for every mini-map box
CALLOUT_PANEL_HEIGHT = 0.105         # base height for every mini-map box

# How to place the side mini maps:
# - "balanced"  -> pack countries evenly around the four sides to form a neat square
# - "geographic" -> old behaviour, using the nearest side of the main map
CALLOUT_ASSIGNMENT_MODE = "balanced"

# Reduce connector crossings by ordering mini maps by their map position and by
# routing connector lines through the nearest main-map edge instead of drawing
# long straight lines through the centre of the figure.
CALLOUT_AVOID_CONNECTOR_CROSSING = True

# Reserve extra space around the main map so the side country boxes do not sit
# on top of the latitude / longitude tick labels or axis labels.
MAIN_MAP_LABEL_MARGIN_LEFT = 0.001
MAIN_MAP_LABEL_MARGIN_RIGHT = -0.03
MAIN_MAP_LABEL_MARGIN_TOP = -0.1
MAIN_MAP_LABEL_MARGIN_BOTTOM = -0.075

# Optional connector line from the main-map country to its mini country map.
DRAW_CALLOUT_CONNECTORS = True
CALLOUT_CONNECTOR_COLOR = "0.62"
CALLOUT_CONNECTOR_LINEWIDTH = 0.45
CALLOUT_CONNECTOR_ALPHA = 0.55
# Show the blowout line inside the main map as well, from the selected country
# toward the edge that connects to its mini box.
CALLOUT_CONNECTOR_SHOW_INSIDE_MAIN_MAP = True
# Short connector segment drawn INSIDE each mini-country box so the blowout
# line visibly touches the box edge.
CALLOUT_CONNECTOR_BOX_STUB_FRAC = 0.06

# Keep False to match Shipping_route_model.py as closely as possible: it lets
# the plotted land/lane background set the map extent. Set True for a simple
# global view.
FORCE_GLOBAL_EXTENT = False
GLOBAL_LON_MIN, GLOBAL_LON_MAX = -180.0, 180.0
GLOBAL_LAT_MIN, GLOBAL_LAT_MAX = -75.0, 75.0


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


def resolve_relative_path(path_value: object, base_dir: Path) -> Optional[Path]:
    """Resolve a path that may be absolute or relative to base_dir."""
    if is_blank(path_value):
        return None
    path = Path(str(path_value).strip())
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


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


# =============================================================================
# COUNTRY LIST
# =============================================================================

def iso3_from_cell(value: object) -> str:
    """Return a cleaned ISO3 code from a cell, or an empty string."""
    if is_blank(value):
        return ""

    text = str(value).strip().upper()

    # Avoid treating the header text "ISO3" as a country code.
    if normalize_header(text) in {"iso3", "iso", "countryiso3", "alpha3"}:
        return ""

    # Prefer standalone 3-letter tokens, e.g. "Australia - AUS" -> AUS.
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
    """Return True if a column contains at least min_hits ISO3-looking cells."""
    hits = 0
    for value in series.dropna().head(300):
        if iso3_from_cell(value):
            hits += 1
            if hits >= min_hits:
                return True
    return False


def detect_iso3_column(df: pd.DataFrame, allow_column_c_fallback: bool = True) -> Optional[str]:
    """Detect the ISO3 column, optionally including a fallback to Excel column C."""
    if df.empty:
        return None

    iso_col = pick_column(
        df,
        preferred_names=[
            "ISO3", "ISO 3", "ISO_3", "Alpha-3", "Alpha 3",
            "Country ISO3", "Country ISO 3", "Country ISO 3 Name",
            "Country ISO3 Name", "iso_3_name",
        ],
        contains_any=["iso3", "alpha3"],
        required=False,
        purpose="ISO3 column",
    )

    # Second fallback: any column containing both "iso" and "3" after normalization.
    if iso_col is None:
        for col in df.columns:
            norm = normalize_header(col)
            if "iso" in norm and "3" in norm:
                iso_col = col
                break

    # User-specified final fallback: ISO3 is in Excel column C.
    # Column C = third dataframe column after all-empty columns are removed.
    if iso_col is None and allow_column_c_fallback and df.shape[1] >= 3:
        col_c = df.columns[2]
        if looks_like_iso3_series(df[col_c], min_hits=1):
            iso_col = col_c

    return iso_col


def read_country_list_table_with_iso3(country_list_path: Path) -> Tuple[pd.DataFrame, str]:
    """
    Read the country list and return (df, iso_col).

    Robust handling for the workbook layout described by the user:
    - checks every Excel sheet,
    - tries normal and shifted header rows,
    - scans for a header named ISO3,
    - finally uses Excel column C directly if it contains ISO3-looking values.
    """
    suffix = country_list_path.suffix.lower()

    if suffix == ".csv":
        tried_columns: List[List[object]] = []
        for header_row in range(0, 20):
            try:
                df = pd.read_csv(country_list_path, header=header_row)
            except Exception:
                continue
            if df.empty:
                continue
            df = df.dropna(axis=1, how="all")
            tried_columns.append(list(df.columns))
            iso_col = detect_iso3_column(df, allow_column_c_fallback=True)
            if iso_col is not None:
                return df, iso_col

        raw = pd.read_csv(country_list_path, header=None).dropna(axis=1, how="all")
        if raw.shape[1] >= 3:
            df = raw.copy()
            df.columns = [f"Column_{idx + 1}" for idx in range(raw.shape[1])]
            iso_col = "Column_3"
            if looks_like_iso3_series(df[iso_col], min_hits=1):
                return df, iso_col

        raise KeyError(
            "Could not find an ISO3 column in the country-list CSV. "
            "Tried normal headers, alternate header rows, and column C. "
            f"Headers seen include: {tried_columns[:5]}"
        )

    if suffix not in {".xlsx", ".xls", ".xlsm"}:
        raise ValueError(f"Unsupported country-list file type: {country_list_path}")

    tried_columns: List[str] = []
    excel_file = pd.ExcelFile(country_list_path)

    for sheet_name in excel_file.sheet_names:
        # 1) Try several possible header rows.
        for header_row in range(0, 20):
            try:
                df = pd.read_excel(country_list_path, sheet_name=sheet_name, header=header_row)
            except Exception:
                continue
            if df.empty:
                continue
            df = df.dropna(axis=1, how="all")
            tried_columns.append(
                f"sheet={sheet_name!r}, header_row={header_row + 1}, columns={list(df.columns)}"
            )
            iso_col = detect_iso3_column(df, allow_column_c_fallback=True)
            if iso_col is not None:
                return df, iso_col

        # 2) Raw scan: find a cell whose header text is ISO3, then rebuild the
        # table using that row as the header.
        try:
            raw = pd.read_excel(country_list_path, sheet_name=sheet_name, header=None)
        except Exception:
            continue
        raw = raw.dropna(axis=1, how="all")
        if raw.empty:
            continue

        max_rows = min(60, raw.shape[0])
        for row_idx in range(max_rows):
            for col_idx in range(raw.shape[1]):
                cell_norm = normalize_header(raw.iat[row_idx, col_idx])
                if cell_norm == "iso3" or ("iso" in cell_norm and "3" in cell_norm):
                    header_values = list(raw.iloc[row_idx])
                    columns: List[str] = []
                    used: Dict[str, int] = {}
                    for idx, value in enumerate(header_values):
                        base = f"Column_{idx + 1}" if is_blank(value) else str(value).strip()
                        used[base] = used.get(base, 0) + 1
                        columns.append(base if used[base] == 1 else f"{base}_{used[base]}")

                    df = raw.iloc[row_idx + 1:].copy()
                    df.columns = columns
                    df = df.dropna(axis=1, how="all")
                    iso_col = detect_iso3_column(df, allow_column_c_fallback=False)
                    if iso_col is None and col_idx < len(df.columns):
                        iso_col = df.columns[col_idx]
                    if iso_col is not None:
                        print(
                            f"Detected ISO3 heading on sheet '{sheet_name}', "
                            f"Excel row {row_idx + 1}, column {col_idx + 1}."
                        )
                        return df, iso_col

        # 3) Last-resort fallback: Excel column C is the ISO3 column.
        if raw.shape[1] >= 3:
            df = raw.copy()
            df.columns = [f"Column_{idx + 1}" for idx in range(raw.shape[1])]
            iso_col = "Column_3"
            if looks_like_iso3_series(df[iso_col], min_hits=1):
                print(f"Using ISO3 values from Excel column C on sheet '{sheet_name}'.")
                return df, iso_col

    detail = "\n  - ".join(tried_columns[-12:])
    raise KeyError(
        "Could not find an ISO3 column in the country list. "
        "Tried every sheet, normal headers, alternate header rows, an ISO3-cell scan, "
        "and Excel column C.\n"
        f"File: {country_list_path}\n"
        f"Recent headers checked:\n  - {detail}"
    )


def load_country_list(country_list_path: Path) -> List[Dict[str, str]]:
    """
    Load selected country records from the country list.

    The country folder/name is always read from column A in the detected table.
    ISO3 is read from the detected ISO3 column, normally column C.
    """
    df, iso_col = read_country_list_table_with_iso3(country_list_path)
    if df.empty:
        raise ValueError(f"Country list is empty: {country_list_path}")
    if df.shape[1] < 1:
        raise ValueError(f"Country list has no usable columns: {country_list_path}")

    # User-specified layout: column A contains the country name/folder.
    country_col = df.columns[0]

    records: List[Dict[str, str]] = []
    seen_iso3 = set()
    for _, row in df.iterrows():
        iso3 = iso3_from_cell(row.get(iso_col, ""))
        if not iso3 or iso3 in seen_iso3:
            continue
        seen_iso3.add(iso3)

        country_name = clean_text(row.get(country_col, ""))
        if not country_name or normalize_header(country_name) in {"country", "countryname", "name"}:
            country_name = iso3

        records.append({"iso3": iso3, "country_name": country_name})

    if not records:
        raise ValueError(
            f"No ISO3 values were loaded from {country_list_path}. "
            f"The detected ISO3 column was {iso_col!r}."
        )
    return records


def _country_list_candidate_score(path: Path) -> Tuple[int, str]:
    """Sort country-list candidates: project-root files first, then shorter names."""
    stem = normalize_header(path.stem)
    score = 100
    if stem in {"countrylist", "countrylistcopy", "listofcountries"}:
        score -= 20
    if "country" in stem:
        score -= 10
    if path.parent == PROJECT_DIR:
        score -= 30
    return score, str(path).lower()


def locate_country_list(project_dir: Path, results_root: Path, explicit_path: Optional[Path]) -> Path:
    """Find a usable country-list workbook containing ISO3 values."""
    if explicit_path is not None:
        path = explicit_path if explicit_path.is_absolute() else (project_dir / explicit_path)
        path = path.resolve()
        if not path.exists():
            raise FileNotFoundError(f"COUNTRY_LIST_PATH does not exist: {path}")
        load_country_list(path)  # validate early
        return path

    extensions = {".xlsx", ".xls", ".xlsm", ".csv"}
    name_tokens = ["country_list", "country list", "countrylist", "list of countries", "listofcountries"]

    search_dirs: List[Path] = [project_dir]
    if results_root.exists():
        search_dirs.append(results_root)
        for child in sorted(results_root.iterdir()):
            if child.is_dir():
                opt_dir = child / "optimal_locations"
                if opt_dir.exists():
                    search_dirs.append(opt_dir)

    candidates: List[Path] = []
    for directory in search_dirs:
        if not directory.exists():
            continue
        try:
            iterator = directory.iterdir()
        except Exception:
            continue
        for path in iterator:
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            stem_norm = normalize_header(path.stem)
            if any(normalize_header(token) in stem_norm for token in name_tokens):
                candidates.append(path.resolve())

    # Recursive fallback under trade_version_results.
    if results_root.exists():
        try:
            for path in results_root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in extensions:
                    continue
                stem_norm = normalize_header(path.stem)
                if any(normalize_header(token) in stem_norm for token in name_tokens):
                    candidates.append(path.resolve())
        except Exception:
            pass

    # Remove duplicates and validate each candidate.
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

    message = (
        "Could not find a usable Country_list / List of Countries workbook with ISO3 values.\n"
        f"Searched in project root: {project_dir}\n"
        f"And under: {results_root}"
    )
    if rejected:
        message += "\nRejected candidate(s):\n  - " + "\n  - ".join(rejected[:20])
    raise FileNotFoundError(message)


# =============================================================================
# COUNTRY-SPECIFIC AP LOCATION WORKBOOKS
# =============================================================================

def find_ap_location_workbook(optimal_dir: Path, iso3: str) -> Path:
    """Find AP_location_dataset_<ISO3>_new.xlsx for a given ISO3."""
    exact_candidates = [
        optimal_dir / f"AP_location_dataset_{iso3}_new.xlsx",
        optimal_dir / f"AP_location_dataset_{iso3}_new.xls",
        optimal_dir / f"AP_location_dataset_{iso3}_new.xlsm",
    ]
    hit = first_existing(exact_candidates)
    if hit is not None:
        return hit

    iso_norm = normalize_header(iso3)
    candidates: List[Path] = []
    for path in optimal_dir.glob("AP_location_dataset*_new.xls*"):
        if iso_norm in normalize_header(path.stem):
            candidates.append(path)

    if candidates:
        return sorted(candidates)[0]

    raise FileNotFoundError(f"Could not find AP_location_dataset_{iso3}_new.xlsx in {optimal_dir}")


def find_ap_location_workbook_across_results(results_root: Path, iso3: str) -> Optional[Path]:
    """Fallback search for AP_location_dataset_<ISO3>_new under all result folders."""
    if not results_root.exists():
        return None

    exact_patterns = [
        f"*/optimal_locations/AP_location_dataset_{iso3}_new.xlsx",
        f"*/optimal_locations/AP_location_dataset_{iso3}_new.xls",
        f"*/optimal_locations/AP_location_dataset_{iso3}_new.xlsm",
    ]
    for pattern in exact_patterns:
        hits = sorted(path for path in results_root.glob(pattern) if path.is_file())
        if hits:
            return hits[0]

    iso_norm = normalize_header(iso3)
    hits = []
    for path in results_root.rglob("AP_location_dataset*_new.xls*"):
        if path.is_file() and iso_norm in normalize_header(path.stem):
            hits.append(path)
    if hits:
        return sorted(hits)[0]
    return None


def find_country_optimal_locations_dir(results_root: Path, country_name: str, iso3: str) -> Path:
    """
    Locate one country's optimal_locations folder.

    Primary expected path:
        <PROJECT_DIR>/trade_version_results/<Country name from column A>/optimal_locations
    """
    country_folder = clean_text(country_name)

    exact_candidates = [
        results_root / country_folder / "optimal_locations",
        results_root / iso3 / "optimal_locations",
    ]
    hit = first_existing(exact_candidates)
    if hit is not None:
        return hit

    target_country_key = normalize_header(country_folder)
    target_iso3_key = normalize_header(iso3)

    if results_root.exists():
        # Case/spacing/punctuation tolerant exact-key match.
        for child in sorted(results_root.iterdir()):
            if not child.is_dir():
                continue
            child_key = normalize_header(child.name)
            if child_key in {target_country_key, target_iso3_key}:
                opt_dir = child / "optimal_locations"
                if opt_dir.exists():
                    return opt_dir

        # More forgiving match for cases such as "United States" vs
        # "United States of America".
        partial_matches: List[Path] = []
        for child in sorted(results_root.iterdir()):
            if not child.is_dir():
                continue
            child_key = normalize_header(child.name)
            if target_country_key and (target_country_key in child_key or child_key in target_country_key):
                opt_dir = child / "optimal_locations"
                if opt_dir.exists():
                    partial_matches.append(opt_dir)
        if len(partial_matches) == 1:
            return partial_matches[0]

        # Last fallback: find the actual workbook anywhere under results_root.
        workbook = find_ap_location_workbook_across_results(results_root, iso3)
        if workbook is not None:
            return workbook.parent

    expected = results_root / country_folder / "optimal_locations"
    raise FileNotFoundError(
        f"Could not find optimal_locations folder for {country_name} ({iso3}). "
        f"Expected folder: {expected}"
    )


def load_location_points_for_country(optimal_dir: Path, record: Dict[str, str]) -> pd.DataFrame:
    """Read one country's AP_location_dataset workbook and return lat/lon rows."""
    iso3 = record["iso3"]
    country_name = record.get("country_name", iso3)
    workbook = find_ap_location_workbook(optimal_dir, iso3)
    df = read_table(workbook)
    if df.empty:
        print(f"WARNING: {workbook.name} is empty; skipping.")
        return pd.DataFrame()

    lat_col = pick_column(
        df,
        preferred_names=["lat", "latitude", "site_lat", "site latitude", "latitude_deg", "y"],
        contains_any=["latitude"],
        required=False,
        purpose="latitude column",
    )
    lon_col = pick_column(
        df,
        preferred_names=["lon", "long", "longitude", "site_lon", "site longitude", "longitude_deg", "x"],
        contains_any=["longitude"],
        required=False,
        purpose="longitude column",
    )

    if lat_col is None:
        for col in df.columns:
            if normalize_header(col) in {"lat", "latitude"}:
                lat_col = col
                break
    if lon_col is None:
        for col in df.columns:
            if normalize_header(col) in {"lon", "long", "longitude"}:
                lon_col = col
                break

    if lat_col is None or lon_col is None:
        raise KeyError(f"Could not find lat/lon columns in {workbook}. Available columns: {list(df.columns)}")

    city_col = pick_column(
        df,
        preferred_names=["City", "Site", "Location", "Location Name", "name"],
        contains_any=["city", "site", "location"],
        required=False,
        purpose="city/site label column",
    )

    # Build from lat/lon first so scalar metadata broadcasts correctly.
    out = pd.DataFrame({
        "lat": pd.to_numeric(df[lat_col], errors="coerce"),
        "lon": pd.to_numeric(df[lon_col], errors="coerce"),
    })
    out["iso3"] = iso3
    out["country"] = country_name
    out["label"] = df[city_col].astype(str) if city_col is not None else ""
    out["source_file"] = workbook.name
    out["source_dir"] = str(optimal_dir)
    out = out[["iso3", "country", "lat", "lon", "label", "source_file", "source_dir"]]

    before = len(out)
    out = out.dropna(subset=["lat", "lon"]).copy()
    out = out[(out["lat"].between(-90, 90)) & (out["lon"].between(-180, 180))].copy()
    dropped = before - len(out)
    if dropped:
        print(f"WARNING: dropped {dropped} invalid lat/lon row(s) from {workbook.name}")
    return out


def load_all_selected_locations(results_root: Path, country_list_path: Path) -> pd.DataFrame:
    countries = load_country_list(country_list_path)
    print(f"Loaded {len(countries)} selected country/countries from {country_list_path}")
    print("Reading each AP workbook from:")
    print(f"  {results_root}\\<Country name from column A>\\optimal_locations")

    frames: List[pd.DataFrame] = []
    missing: List[str] = []
    errors: List[str] = []

    for record in countries:
        iso3 = record["iso3"]
        country_name = record.get("country_name", iso3)
        try:
            optimal_dir = find_country_optimal_locations_dir(results_root, country_name, iso3)
            points = load_location_points_for_country(optimal_dir, record)
        except FileNotFoundError as exc:
            missing.append(f"{country_name} ({iso3}): {exc}")
            continue
        except Exception as exc:
            errors.append(f"{country_name} ({iso3}): {exc}")
            continue

        if not points.empty:
            frames.append(points)
            print(f"  {country_name} ({iso3}): {len(points)} location(s) from {optimal_dir}")
        else:
            print(f"  {country_name} ({iso3}): 0 location(s) from {optimal_dir}")

    if missing:
        print("\nWARNING: missing AP location workbook(s) or folders:")
        for message in missing:
            print("  - " + message)

    if errors:
        print("\nWARNING: AP workbook(s) found but could not be read:")
        for message in errors:
            print("  - " + message)

    if not frames:
        raise ValueError("No valid selected-location points were loaded.")

    return pd.concat(frames, ignore_index=True)


# =============================================================================
# SHIPPING-MAP BACKGROUND
# =============================================================================

def load_json_if_exists(path: Optional[Path]) -> Dict:
    if path is None or not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def locate_shipping_config(project_dir: Path) -> Optional[Path]:
    """Find the base shipping config, not a per-port generated config if possible."""
    candidates = [
        project_dir / "shipping_route_config.json",
        project_dir / "Trade_version" / "shipping_route_config.json",
        project_dir / "trade_version" / "shipping_route_config.json",
    ]
    hit = first_existing(candidates)
    if hit is not None:
        return hit

    try:
        for path in project_dir.rglob("shipping_route_config.json"):
            return path
    except Exception:
        pass
    return None


def locate_lanes_path(project_dir: Path, config: Dict, config_dir: Path) -> Path:
    cfg_path = resolve_relative_path(config.get("lanes"), config_dir)
    candidates = [
        cfg_path,
        project_dir / "Trade_version" / "Shipping_Lanes_v1.geojson",
        project_dir / "trade_version" / "Shipping_Lanes_v1.geojson",
        project_dir / "Shipping_Lanes_v1.geojson",
    ]
    hit = first_existing(candidates)
    if hit is not None:
        return hit

    try:
        for path in project_dir.rglob("Shipping_Lanes_v1.geojson"):
            return path
    except Exception:
        pass

    raise FileNotFoundError(
        "Could not find Shipping_Lanes_v1.geojson. Set the 'lanes' path in "
        "shipping_route_config.json or edit locate_lanes_path()."
    )


def load_land_background(project_dir: Path, lanes_path: Path, config: Dict, config_dir: Path) -> gpd.GeoDataFrame:
    """Load the same Natural Earth land polygons used by Shipping_route_model.py."""
    local_land_path = resolve_relative_path(config.get("land_shapefile_path"), config_dir)
    cache_dir = resolve_relative_path(config.get("ocean_cache_dir"), config_dir)
    if cache_dir is None:
        cache_dir = lanes_path.parent / ".ocean_cache"

    if local_land_path is not None and local_land_path.exists():
        path_for_gpd = str(local_land_path)
    else:
        cached_zip = cache_dir / "ne_10m_land.zip"
        if not cached_zip.exists():
            # Search the project in case the config is absent but the cache exists.
            hits = sorted(project_dir.rglob("ne_10m_land.zip"))
            if hits:
                cached_zip = hits[0]
            else:
                raise FileNotFoundError(
                    "Could not find Natural Earth land cache ne_10m_land.zip. "
                    "Run Shipping_route_model.py once, or set land_shapefile_path "
                    "in shipping_route_config.json."
                )
        path_for_gpd = f"zip://{cached_zip}"

    print(f"Loading Natural Earth land polygons from {path_for_gpd} ...")
    land_gdf = gpd.read_file(path_for_gpd)
    if land_gdf.crs is None or land_gdf.crs.to_epsg() != 4326:
        land_gdf = land_gdf.to_crs(epsg=4326)
    print(f"Loaded land polygons for map background (features: {len(land_gdf)})")
    return land_gdf


def load_shipping_background(project_dir: Path) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, Path]:
    """Load shipping lanes and land/coastline layers."""
    config_path = locate_shipping_config(project_dir)
    config_dir = config_path.parent if config_path is not None else project_dir
    config = load_json_if_exists(config_path)

    if config_path is not None:
        print(f"Using shipping config: {config_path}")
    else:
        print("No base shipping_route_config.json found; locating map files directly.")

    lanes_path = locate_lanes_path(project_dir, config, config_dir)
    print(f"Loading shipping lanes: {lanes_path}")
    lanes_gdf = gpd.read_file(lanes_path)
    if lanes_gdf.crs is None:
        lanes_gdf = lanes_gdf.set_crs(epsg=4326)
    elif lanes_gdf.crs.to_epsg() != 4326:
        lanes_gdf = lanes_gdf.to_crs(epsg=4326)

    land_gdf = load_land_background(project_dir, lanes_path, config, config_dir)
    return lanes_gdf, land_gdf, lanes_path


# =============================================================================
# COUNTRY BOUNDARY OVERLAYS AND CALLOUT MINI-MAPS
# =============================================================================

def load_country_boundaries() -> gpd.GeoDataFrame:
    """
    Load a country-boundary layer used only for border overlays and mini maps.

    Priority order:
    1. GeoPandas built-in Natural Earth countries, when available.
    2. A cached Natural Earth admin-0 countries ZIP under the project folder.
    3. Download Natural Earth admin-0 countries ZIP into Trade_version/.ocean_cache.
    """
    # GeoPandas <= 0.12 typically ships this dataset locally.
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
            print(f"Loading country-boundary overlay from cached Natural Earth file: {cached_zip}")
            world = gpd.read_file(path_for_gpd)
        else:
            target_zip = cache_candidates[0]
            target_zip.parent.mkdir(parents=True, exist_ok=True)
            try:
                print(f"Downloading country-boundary overlay to: {target_zip}")
                urllib.request.urlretrieve(url, target_zip)
                world = gpd.read_file(f"zip://{target_zip}")
            except Exception as exc:
                raise FileNotFoundError(
                    "Could not load a country-boundary layer for the selected-country "
                    "mini maps. GeoPandas did not provide naturalearth_lowres, no "
                    "cached ne_110m_admin_0_countries.zip was found in the project, "
                    "and the Natural Earth download failed. Run once with internet "
                    "access or place ne_110m_admin_0_countries.zip in "
                    "Trade_version/.ocean_cache."
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
    """Return the ISO column and name-like columns available in the boundary layer."""
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
    """Find the boundary row(s) matching one selected country record."""
    iso_key = normalize_header(record.get("iso3", ""))
    country_key = normalize_header(record.get("country_name", ""))

    mask = pd.Series(False, index=countries_gdf.index)

    # First priority: ISO3 match.
    if iso_col is not None and iso_key:
        mask = mask | countries_gdf[iso_col].astype(str).map(normalize_header).isin({iso_key})

    # Second priority: exact country/name/admin match.
    exact_name_keys = {country_key}
    exact_name_keys = {key for key in exact_name_keys if key}
    for col in name_cols:
        if exact_name_keys:
            mask = mask | countries_gdf[col].astype(str).map(normalize_header).isin(exact_name_keys)

    rows = countries_gdf.loc[mask]
    if not rows.empty:
        return rows

    # Third priority: tolerant partial name match for cases such as
    # "United States" vs "United States of America".
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


def build_selected_country_boundaries(
    countries_gdf: gpd.GeoDataFrame,
    selected_records: Sequence[Dict[str, str]],
) -> gpd.GeoDataFrame:
    """
    Return one dissolved boundary feature per selected country.

    The returned GeoDataFrame has:
        selected_iso3, selected_country_name, geometry
    """
    if countries_gdf.empty or not selected_records:
        return gpd.GeoDataFrame(
            columns=["selected_iso3", "selected_country_name", "geometry"],
            geometry="geometry",
            crs=countries_gdf.crs,
        )

    iso_col, name_cols = country_boundary_match_columns(countries_gdf)
    output_rows: List[Dict[str, object]] = []
    unmatched: List[str] = []

    for record in selected_records:
        iso3 = record.get("iso3", "")
        country_name = record.get("country_name", iso3)
        rows = match_country_boundary_rows(countries_gdf, record, iso_col, name_cols)

        if rows.empty:
            unmatched.append(f"{country_name} ({iso3})")
            continue

        geometry = union_geometries(rows.geometry)
        output_rows.append({
            "selected_iso3": iso3,
            "selected_country_name": country_name,
            "geometry": geometry,
        })

    if unmatched:
        print("WARNING: these selected countries could not be matched to a country boundary:")
        for item in unmatched:
            print("  - " + item)

    selected_gdf = gpd.GeoDataFrame(output_rows, geometry="geometry", crs=countries_gdf.crs)
    if not selected_gdf.empty:
        print(f"Matched {len(selected_gdf)} selected country boundary feature(s).")
    return selected_gdf


def build_hatch_pattern(hatch_char: str, density: int) -> str:
    """Build a matplotlib hatch pattern from a character and density setting."""
    hatch_char = str(hatch_char or "/").strip() or "/"
    density = max(1, int(density))
    return hatch_char * density


def combined_map_extent(
    lanes_gdf: gpd.GeoDataFrame,
    land_gdf: gpd.GeoDataFrame,
) -> Tuple[float, float, float, float]:
    """Return (xmin, xmax, ymin, ymax) used to decide the closest side."""
    if FORCE_GLOBAL_EXTENT:
        return GLOBAL_LON_MIN, GLOBAL_LON_MAX, GLOBAL_LAT_MIN, GLOBAL_LAT_MAX

    bounds: List[Tuple[float, float, float, float]] = []
    for gdf in [land_gdf, lanes_gdf]:
        try:
            if gdf is not None and not gdf.empty:
                minx, miny, maxx, maxy = gdf.total_bounds
                bounds.append((float(minx), float(miny), float(maxx), float(maxy)))
        except Exception:
            pass

    if not bounds:
        return GLOBAL_LON_MIN, GLOBAL_LON_MAX, GLOBAL_LAT_MIN, GLOBAL_LAT_MAX

    xmin = min(item[0] for item in bounds)
    ymin = min(item[1] for item in bounds)
    xmax = max(item[2] for item in bounds)
    ymax = max(item[3] for item in bounds)
    return xmin, xmax, ymin, ymax


def geometry_anchor_lon_lat(geometry) -> Tuple[float, float]:
    """Return a stable point inside or near the geometry for side assignment/connectors."""
    try:
        point = geometry.representative_point()
    except Exception:
        point = geometry.centroid
    return float(point.x), float(point.y)


def assign_callout_sides(
    selected_countries_gdf: gpd.GeoDataFrame,
    map_extent: Tuple[float, float, float, float],
) -> Dict[str, List[Dict[str, object]]]:
    """
    Assign each selected country to one of the four callout sides.

    Two modes are supported:
    - CALLOUT_ASSIGNMENT_MODE == "geographic": assign to nearest side
    - CALLOUT_ASSIGNMENT_MODE == "balanced": distribute evenly around the four
      sides to create a compact square-like frame around the main map
    """
    groups: Dict[str, List[Dict[str, object]]] = {
        "left": [],
        "right": [],
        "top": [],
        "bottom": [],
    }
    if selected_countries_gdf.empty:
        return groups

    xmin, xmax, ymin, ymax = map_extent
    xspan = max(xmax - xmin, 1e-9)
    yspan = max(ymax - ymin, 1e-9)

    panel_records: List[Dict[str, object]] = []
    for _, row in selected_countries_gdf.iterrows():
        geometry = row.geometry
        if geometry is None or geometry.is_empty:
            continue

        lon, lat = geometry_anchor_lon_lat(geometry)
        xnorm = (lon - xmin) / xspan
        ynorm = (lat - ymin) / yspan

        panel_records.append({
            "anchor_lon": lon,
            "anchor_lat": lat,
            "selected_iso3": row.get("selected_iso3", ""),
            "selected_country_name": row.get("selected_country_name", ""),
            "geometry": geometry,
            "xnorm": xnorm,
            "ynorm": ynorm,
        })

    mode = str(CALLOUT_ASSIGNMENT_MODE).strip().lower()

    if mode == "balanced":
        # Make a balanced square-like frame, but keep the countries in their
        # geographic circular order around the map. This avoids the most common
        # source of connector crossings: placing countries alphabetically around
        # the frame.
        order = ["top", "right", "bottom", "left"]

        def perimeter_key(item: Dict[str, object]) -> float:
            xnorm = float(item.get("xnorm", 0.5))
            ynorm = float(item.get("ynorm", 0.5))
            theta = math.atan2(ynorm - 0.5, xnorm - 0.5)
            # Clockwise angle measured from north. Start around the north-west
            # corner so the top-side panels run west-to-east.
            clockwise_from_north = (math.pi / 2.0 - theta) % (2.0 * math.pi)
            start_at_north_west = 7.0 * math.pi / 4.0
            return (clockwise_from_north - start_at_north_west) % (2.0 * math.pi)

        panel_records.sort(key=perimeter_key)

        n = len(panel_records)
        base = n // 4
        remainder = n % 4
        counts = {side: base for side in order}
        for side in order[:remainder]:
            counts[side] += 1

        idx = 0
        for side in order:
            for _ in range(counts[side]):
                if idx >= n:
                    break
                rec = dict(panel_records[idx])
                rec["side"] = side
                groups[side].append(rec)
                idx += 1

        # Within each side, match the panel order to the countries' positions.
        # This makes connectors on the same side run in parallel rather than
        # crossing each other.
        groups["top"].sort(key=lambda item: float(item.get("anchor_lon", 0.0)))
        groups["bottom"].sort(key=lambda item: float(item.get("anchor_lon", 0.0)))
        groups["left"].sort(key=lambda item: -float(item.get("anchor_lat", 0.0)))
        groups["right"].sort(key=lambda item: -float(item.get("anchor_lat", 0.0)))
    else:
        for rec in panel_records:
            distances = {
                "left": rec["xnorm"],
                "right": 1.0 - rec["xnorm"],
                "bottom": rec["ynorm"],
                "top": 1.0 - rec["ynorm"],
            }
            side = min(distances, key=distances.get)
            rec["side"] = side
            groups[side].append(rec)

        groups["left"].sort(key=lambda item: -float(item["anchor_lat"]))
        groups["right"].sort(key=lambda item: -float(item["anchor_lat"]))
        groups["top"].sort(key=lambda item: float(item["anchor_lon"]))
        groups["bottom"].sort(key=lambda item: float(item["anchor_lon"]))

    print(f"Mini country-map side assignment mode: {mode}")
    for side in ["left", "right", "top", "bottom"]:
        names = [f"{item['selected_country_name']} ({item['selected_iso3']})" for item in groups[side]]
        print(f"  {side}: {', '.join(names) if names else 'none'}")

    return groups


def compute_main_axes_rect(side_groups: Dict[str, List[Dict[str, object]]]) -> Tuple[float, float, float, float]:
    """Compute the central main-map rectangle in figure coordinates."""
    left_band = CALLOUT_SIDE_WIDTH if side_groups.get("left") else 0.0
    right_band = CALLOUT_SIDE_WIDTH if side_groups.get("right") else 0.0
    top_band = CALLOUT_TOP_HEIGHT if side_groups.get("top") else 0.0
    bottom_band = CALLOUT_BOTTOM_HEIGHT if side_groups.get("bottom") else 0.0

    left_gap = CALLOUT_GAP if left_band else 0.0
    right_gap = CALLOUT_GAP if right_band else 0.0
    top_gap = CALLOUT_GAP if top_band else 0.0
    bottom_gap = CALLOUT_GAP if bottom_band else 0.0

    # Keep a safety buffer around the main axes so the side country boxes do
    # not overlap the longitude/latitude tick labels or axis labels.
    left = CALLOUT_PAGE_MARGIN + left_band + left_gap + MAIN_MAP_LABEL_MARGIN_LEFT
    bottom = CALLOUT_PAGE_MARGIN + bottom_band + bottom_gap + MAIN_MAP_LABEL_MARGIN_BOTTOM
    right = CALLOUT_PAGE_MARGIN + right_band + right_gap + MAIN_MAP_LABEL_MARGIN_RIGHT
    top = CALLOUT_PAGE_MARGIN + top_band + top_gap + MAIN_MAP_LABEL_MARGIN_TOP

    width = 1.0 - left - right
    height = 1.0 - bottom - top

    # Defensive fallback if the side bands are too large.
    if width <= 0.25 or height <= 0.25:
        print("WARNING: callout margins are too large; using a safer central map layout.")
        return 0.20, 0.22, 0.60, 0.54

    return left, bottom, width, height


def get_callout_band_rects(
    side_groups: Dict[str, List[Dict[str, object]]],
    main_rect: Tuple[float, float, float, float],
) -> Dict[str, Tuple[float, float, float, float]]:
    """Return the available band rectangle for each callout side."""
    main_x, main_y, main_w, main_h = main_rect
    bands: Dict[str, Tuple[float, float, float, float]] = {}

    if side_groups.get("left"):
        bands["left"] = (
            CALLOUT_PAGE_MARGIN,
            main_y,
            max(main_x - CALLOUT_PAGE_MARGIN - CALLOUT_GAP - MAIN_MAP_LABEL_MARGIN_LEFT, 0.03),
            main_h,
        )

    if side_groups.get("right"):
        x0 = main_x + main_w + CALLOUT_GAP + MAIN_MAP_LABEL_MARGIN_RIGHT
        bands["right"] = (
            x0,
            main_y,
            max(1.0 - CALLOUT_PAGE_MARGIN - x0, 0.03),
            main_h,
        )

    if side_groups.get("top"):
        y0 = main_y + main_h + CALLOUT_GAP + MAIN_MAP_LABEL_MARGIN_TOP
        bands["top"] = (
            main_x,
            y0,
            main_w,
            max(1.0 - CALLOUT_PAGE_MARGIN - y0, 0.03),
        )

    if side_groups.get("bottom"):
        bands["bottom"] = (
            main_x,
            CALLOUT_PAGE_MARGIN,
            main_w,
            max(main_y - CALLOUT_PAGE_MARGIN - CALLOUT_GAP - MAIN_MAP_LABEL_MARGIN_BOTTOM, 0.03),
        )

    return bands


def compute_uniform_callout_panel_size(
    side_groups: Dict[str, List[Dict[str, object]]],
    band_rects: Dict[str, Tuple[float, float, float, float]],
) -> Tuple[float, float]:
    """
    Compute one width/height used for every mini country box.

    The size is chosen as large as possible while still fitting every used side,
    with a fixed aspect ratio from CALLOUT_PANEL_WIDTH / CALLOUT_PANEL_HEIGHT.
    This makes the frame tight and maximises country drawing space.
    """
    base_w = max(float(CALLOUT_PANEL_WIDTH), 1e-9)
    base_h = max(float(CALLOUT_PANEL_HEIGHT), 1e-9)
    ratio = base_w / base_h
    gap = CALLOUT_GAP

    max_scale = float("inf")

    for side, items in side_groups.items():
        count = len(items)
        if count <= 0 or side not in band_rects:
            continue
        _x0, _y0, band_w, band_h = band_rects[side]
        band_w = max(float(band_w), 1e-9)
        band_h = max(float(band_h), 1e-9)

        if side in {"left", "right"}:
            max_h = max((band_h - gap * (count - 1)) / count, 1e-9)
            max_w = band_w
        else:
            max_w = max((band_w - gap * (count - 1)) / count, 1e-9)
            max_h = band_h

        # With width = ratio * height.
        max_scale_side = min(max_w / base_w, max_h / base_h)
        max_scale = min(max_scale, max_scale_side)

    if max_scale == float("inf"):
        max_scale = 1.0

    # Do not allow absurdly tiny boxes.
    max_scale = max(max_scale, 0.25)
    panel_w = max(base_w * max_scale, 0.040)
    panel_h = max(base_h * max_scale, 0.040)
    return panel_w, panel_h


def distribute_panel_rects(
    count: int,
    band_rect: Tuple[float, float, float, float],
    orientation: str,
    panel_size: Optional[Tuple[float, float]] = None,
    gap: Optional[float] = None,
) -> List[Tuple[float, float, float, float]]:
    """Split a side band into tightly packed mini-panel rectangles."""
    if count <= 0:
        return []

    x0, y0, width, height = band_rect
    gap = CALLOUT_GAP if gap is None else float(gap)
    rects: List[Tuple[float, float, float, float]] = []

    if panel_size is None:
        panel_width = width if orientation == "vertical" else max((width - gap * (count - 1)) / count, 0.045)
        panel_height = max((height - gap * (count - 1)) / count, 0.040) if orientation == "vertical" else height
    else:
        panel_width, panel_height = panel_size

    panel_width = min(panel_width, width)
    panel_height = min(panel_height, height)

    if orientation == "vertical":
        total_height = count * panel_height + gap * (count - 1)
        start_y = y0 + max((height - total_height) / 2.0, 0.0)
        start_x = x0 + max((width - panel_width) / 2.0, 0.0)
        for idx in range(count):
            y = start_y + total_height - (idx + 1) * panel_height - idx * gap
            rects.append((start_x, y, panel_width, panel_height))
    else:
        total_width = count * panel_width + gap * (count - 1)
        start_x = x0 + max((width - total_width) / 2.0, 0.0)
        start_y = y0 + max((height - panel_height) / 2.0, 0.0)
        for idx in range(count):
            x = start_x + idx * (panel_width + gap)
            rects.append((x, start_y, panel_width, panel_height))

    return rects


def compute_common_interpanel_gap(
    side_groups: Dict[str, List[Dict[str, object]]],
    band_rects: Dict[str, Tuple[float, float, float, float]],
    panel_size: Optional[Tuple[float, float]],
) -> float:
    """
    Return one common gap shared by all four sides.

    This makes the spacing between mini-country boxes on the top/bottom match the
    spacing on the left/right, creating a more cohesive square frame.
    """
    if panel_size is None or not CALLOUT_EQUAL_INTERPANEL_SPACING:
        return CALLOUT_GAP

    panel_w, panel_h = panel_size
    feasible_gap = float("inf")

    for side, items in side_groups.items():
        count = len(items)
        if count <= 1 or side not in band_rects:
            continue

        _x0, _y0, band_w, band_h = band_rects[side]
        if side in {"left", "right"}:
            avail = max(float(band_h) - count * panel_h, 0.0)
        else:
            avail = max(float(band_w) - count * panel_w, 0.0)

        feasible_gap = min(feasible_gap, avail / (count - 1))

    if feasible_gap == float("inf"):
        feasible_gap = CALLOUT_GAP

    common_gap = max(min(CALLOUT_GAP, feasible_gap), 0.0)
    return common_gap


def compute_callout_panel_rects(
    side_groups: Dict[str, List[Dict[str, object]]],
    main_rect: Tuple[float, float, float, float],
) -> Dict[str, List[Tuple[float, float, float, float]]]:
    """Return panel rectangles for each side."""
    rects_by_side: Dict[str, List[Tuple[float, float, float, float]]] = {
        "left": [],
        "right": [],
        "top": [],
        "bottom": [],
    }

    band_rects = get_callout_band_rects(side_groups, main_rect)
    uniform_panel_size = None
    if CALLOUT_UNIFORM_BOX_SIZE:
        uniform_panel_size = compute_uniform_callout_panel_size(side_groups, band_rects)
        print(
            "Using uniform mini-map box size: "
            f"{uniform_panel_size[0]:.3f} x {uniform_panel_size[1]:.3f} (figure coordinates)"
        )

    common_gap = compute_common_interpanel_gap(side_groups, band_rects, uniform_panel_size)
    print(f"Using common inter-panel gap on all sides: {common_gap:.4f}")

    if side_groups.get("left"):
        rects_by_side["left"] = distribute_panel_rects(
            len(side_groups["left"]),
            band_rects["left"],
            "vertical",
            uniform_panel_size,
            gap=common_gap,
        )

    if side_groups.get("right"):
        rects_by_side["right"] = distribute_panel_rects(
            len(side_groups["right"]),
            band_rects["right"],
            "vertical",
            uniform_panel_size,
            gap=common_gap,
        )

    if side_groups.get("top"):
        rects_by_side["top"] = distribute_panel_rects(
            len(side_groups["top"]),
            band_rects["top"],
            "horizontal",
            uniform_panel_size,
            gap=common_gap,
        )

    if side_groups.get("bottom"):
        rects_by_side["bottom"] = distribute_panel_rects(
            len(side_groups["bottom"]),
            band_rects["bottom"],
            "horizontal",
            uniform_panel_size,
            gap=common_gap,
        )

    return rects_by_side


def set_panel_frame(ax) -> None:
    """Give a mini-map panel a subtle frame while hiding ticks."""
    ax.set_xticks([])
    ax.set_yticks([])
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color(CALLOUT_BOX_COLOR)
        spine.set_linewidth(CALLOUT_BOX_LINEWIDTH)


def add_red_points(
    ax,
    points: pd.DataFrame,
    point_size: float,
    zorder: int = 30,
) -> None:
    """Add renewable optimisation locations as red dots."""
    if points.empty:
        return
    ax.scatter(
        points["lon"].to_numpy(),
        points["lat"].to_numpy(),
        s=point_size,
        marker="o",
        facecolor="red",
        edgecolor="black",
        linewidth=0.25,
        alpha=POINT_ALPHA,
        zorder=zorder,
    )


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


def clamp_float(value: float, low: float, high: float) -> float:
    """Clamp a float into [low, high]."""
    return max(float(low), min(float(high), float(value)))


def draw_callout_connector(
    fig,
    main_ax,
    callout_ax,
    panel_record: Dict[str, object],
    rect: Tuple[float, float, float, float],
) -> None:
    """Draw a connector from the main map to one mini country map."""
    side = str(panel_record.get("side", ""))

    # IMPORTANT:
    # The mini-map axes can be resized by set_aspect(..., adjustable="box").
    # Therefore the original rect passed to fig.add_axes() is not always the
    # actual visible country-box position. Use the active axes position here so
    # connector endpoints land on the visible box border.
    try:
        main_ax.apply_aspect()
        callout_ax.apply_aspect()
    except Exception:
        pass

    try:
        anchor_fig = fig.transFigure.inverted().transform(
            main_ax.transData.transform((float(panel_record["anchor_lon"]), float(panel_record["anchor_lat"])))
        )
        anchor_x, anchor_y = float(anchor_fig[0]), float(anchor_fig[1])
    except Exception:
        main_bbox = main_ax.get_position()
        anchor_x = float((main_bbox.x0 + main_bbox.x1) / 2.0)
        anchor_y = float((main_bbox.y0 + main_bbox.y1) / 2.0)

    main_bbox = main_ax.get_position()
    x0, y0, x1, y1 = main_bbox.x0, main_bbox.y0, main_bbox.x1, main_bbox.y1

    # Use the ACTIVE callout axes position, not the original requested rect.
    try:
        callout_bbox = callout_ax.get_position()
        rect_x, rect_y = float(callout_bbox.x0), float(callout_bbox.y0)
        rect_w = float(callout_bbox.x1 - callout_bbox.x0)
        rect_h = float(callout_bbox.y1 - callout_bbox.y0)
    except Exception:
        rect_x, rect_y, rect_w, rect_h = rect

    def add_box_touch_stub(side_name: str) -> None:
        """
        Draw a short connector segment inside the mini panel.

        This makes the connector visibly touch the mini-country box even when
        the outside figure-level line is partly hidden by the axes patch/spine.
        """
        stub = clamp_float(CALLOUT_CONNECTOR_BOX_STUB_FRAC, 0.0, 0.45)

        if side_name == "left":
            xs, ys = [1.0, 1.0 - stub], [0.5, 0.5]
        elif side_name == "right":
            xs, ys = [0.0, stub], [0.5, 0.5]
        elif side_name == "top":
            xs, ys = [0.5, 0.5], [0.0, stub]
        elif side_name == "bottom":
            xs, ys = [0.5, 0.5], [1.0, 1.0 - stub]
        else:
            return

        stub_line = Line2D(
            xs,
            ys,
            transform=callout_ax.transAxes,
            color=CALLOUT_CONNECTOR_COLOR,
            linewidth=CALLOUT_CONNECTOR_LINEWIDTH,
            alpha=CALLOUT_CONNECTOR_ALPHA,
            zorder=100,
            clip_on=False,
        )
        callout_ax.add_line(stub_line)

    if side == "left":
        edge_pt = (x0, clamp_float(anchor_y, y0, y1))
        callout_pt = (rect_x + rect_w, rect_y + rect_h / 2.0)
        xyB = (1.0, 0.5)
    elif side == "right":
        edge_pt = (x1, clamp_float(anchor_y, y0, y1))
        callout_pt = (rect_x, rect_y + rect_h / 2.0)
        xyB = (0.0, 0.5)
    elif side == "top":
        edge_pt = (clamp_float(anchor_x, x0, x1), y1)
        callout_pt = (rect_x + rect_w / 2.0, rect_y)
        xyB = (0.5, 0.0)
    elif side == "bottom":
        edge_pt = (clamp_float(anchor_x, x0, x1), y0)
        callout_pt = (rect_x + rect_w / 2.0, rect_y + rect_h)
        xyB = (0.5, 1.0)
    else:
        edge_pt = (clamp_float(anchor_x, x0, x1), clamp_float(anchor_y, y0, y1))
        callout_pt = (rect_x + rect_w / 2.0, rect_y + rect_h / 2.0)
        xyB = (0.5, 0.5)

    if not CALLOUT_AVOID_CONNECTOR_CROSSING:
        # Direct mode: connect directly to the mini-box edge instead of its centre.
        connector = ConnectionPatch(
            xyA=(float(panel_record["anchor_lon"]), float(panel_record["anchor_lat"])),
            coordsA=main_ax.transData,
            xyB=xyB,
            coordsB=callout_ax.transAxes,
            axesA=main_ax,
            axesB=callout_ax,
            color=CALLOUT_CONNECTOR_COLOR,
            linewidth=CALLOUT_CONNECTOR_LINEWIDTH,
            alpha=CALLOUT_CONNECTOR_ALPHA,
            zorder=100,
            clip_on=False,
        )
        fig.add_artist(connector)
        add_box_touch_stub(side)
        return

    # Crossing-avoidance mode: route the connector in two segments. The first
    # segment is visible INSIDE the main map (anchor point -> chosen map edge),
    # and the second segment runs from that edge to the mini-map box.
    if CALLOUT_CONNECTOR_SHOW_INSIDE_MAIN_MAP:
        inside_line = Line2D(
            [anchor_x, edge_pt[0]],
            [anchor_y, edge_pt[1]],
            transform=fig.transFigure,
            color=CALLOUT_CONNECTOR_COLOR,
            linewidth=CALLOUT_CONNECTOR_LINEWIDTH,
            alpha=CALLOUT_CONNECTOR_ALPHA,
            zorder=100,
            clip_on=False,
        )
        fig.add_artist(inside_line)

    outside_line = Line2D(
        [edge_pt[0], callout_pt[0]],
        [edge_pt[1], callout_pt[1]],
        transform=fig.transFigure,
        color=CALLOUT_CONNECTOR_COLOR,
        linewidth=CALLOUT_CONNECTOR_LINEWIDTH,
        alpha=CALLOUT_CONNECTOR_ALPHA,
        zorder=100,
        clip_on=False,
    )
    fig.add_artist(outside_line)
    add_box_touch_stub(side)


def plot_country_callout(
    fig,
    main_ax,
    panel_record: Dict[str, object],
    rect: Tuple[float, float, float, float],
    points_df: pd.DataFrame,
):
    """Draw one mini country map and return its axis."""
    callout_ax = fig.add_axes(rect)
    iso3 = str(panel_record.get("selected_iso3", ""))
    country_name = str(panel_record.get("selected_country_name", iso3))
    geometry = panel_record.get("geometry")
    if CALLOUT_MAINLAND_ONLY:
        geometry = mainland_geometry(geometry)

    country_gdf = gpd.GeoDataFrame(
        [{"selected_iso3": iso3, "selected_country_name": country_name, "geometry": geometry}],
        geometry="geometry",
        crs="EPSG:4326",
    )

    country_points = points_df.loc[points_df["iso3"].astype(str).str.upper() == iso3.upper()].copy()
    hatch_pattern = build_hatch_pattern(SELECTED_HATCH_CHAR, SELECTED_HATCH_DENSITY)

    try:
        if CALLOUT_HATCH_COUNTRIES:
            with plt.rc_context({"hatch.linewidth": SELECTED_HATCH_LINEWIDTH}):
                country_gdf.plot(
                    ax=callout_ax,
                    facecolor="none" if CALLOUT_COUNTRY_FACE_COLOR == "none" else CALLOUT_COUNTRY_FACE_COLOR,
                    edgecolor=SELECTED_HATCH_COLOR,
                    linewidth=0.0,
                    hatch=hatch_pattern,
                    zorder=1,
                )
        else:
            country_gdf.plot(
                ax=callout_ax,
                facecolor="none" if CALLOUT_COUNTRY_FACE_COLOR == "none" else CALLOUT_COUNTRY_FACE_COLOR,
                edgecolor="none",
                linewidth=0.0,
                zorder=1,
            )

        country_gdf.boundary.plot(
            ax=callout_ax,
            color=CALLOUT_COUNTRY_BORDER_COLOR,
            linewidth=CALLOUT_COUNTRY_BORDER_LINEWIDTH,
            zorder=2,
        )
    except Exception as exc:
        print(f"WARNING: could not draw mini country map for {country_name} ({iso3}): {exc}")

    add_red_points(callout_ax, country_points, CALLOUT_POINT_SIZE, zorder=5)

    # Set mini-map bounds from the displayed country geometry only.
    # If CALLOUT_MAINLAND_ONLY=True, the zoom is locked to the mainland / largest
    # polygon rather than including offshore territories or islands.
    try:
        xmin, ymin, xmax, ymax = country_gdf.total_bounds
        xspan = max(float(xmax - xmin), 1e-6)
        yspan = max(float(ymax - ymin), 1e-6)
        xpad = max(xspan * CALLOUT_PADDING_FRAC, 0.15)
        ypad = max(yspan * CALLOUT_PADDING_FRAC, 0.15)
        callout_ax.set_xlim(float(xmin) - xpad, float(xmax) + xpad)
        callout_ax.set_ylim(float(ymin) - ypad, float(ymax) + ypad)
    except Exception:
        pass

    callout_ax.set_aspect("equal", adjustable="box")
    set_panel_frame(callout_ax)
    callout_ax.set_title(f"{country_name} ({iso3})", fontsize=CALLOUT_TITLE_FONTSIZE, pad=2)

    if DRAW_CALLOUT_CONNECTORS:
        try:
            draw_callout_connector(fig, main_ax, callout_ax, panel_record, rect)
        except Exception as exc:
            print(f"WARNING: could not draw connector for {country_name} ({iso3}): {exc}")

    return callout_ax


def plot_all_country_callouts(
    fig,
    main_ax,
    side_groups: Dict[str, List[Dict[str, object]]],
    rects_by_side: Dict[str, List[Tuple[float, float, float, float]]],
    points_df: pd.DataFrame,
) -> None:
    """Draw all side mini maps."""
    for side in ["left", "right", "top", "bottom"]:
        panels = side_groups.get(side, [])
        rects = rects_by_side.get(side, [])
        for panel_record, rect in zip(panels, rects):
            plot_country_callout(fig, main_ax, panel_record, rect, points_df)


# =============================================================================
# PLOTTING
# =============================================================================

def plot_selected_locations(
    lanes_gdf: gpd.GeoDataFrame,
    land_gdf: gpd.GeoDataFrame,
    points_df: pd.DataFrame,
    selected_country_records: Sequence[Dict[str, str]],
    output_png: Path,
    dpi: int = 600,
) -> Path:
    """Render the shipping-map background plus side mini country maps."""
    if points_df.empty:
        raise ValueError("No selected locations to plot.")

    countries_gdf = load_country_boundaries()
    selected_countries_gdf = build_selected_country_boundaries(countries_gdf, selected_country_records)
    map_extent = combined_map_extent(lanes_gdf, land_gdf)

    if CREATE_COUNTRY_CALLOUTS and not selected_countries_gdf.empty:
        side_groups = assign_callout_sides(selected_countries_gdf, map_extent)
        main_rect = compute_main_axes_rect(side_groups)
    else:
        side_groups = {"left": [], "right": [], "top": [], "bottom": []}
        # Close to the old shipping-route figure layout.
        main_rect = (0.08, 0.16, 0.86, 0.74)

    fig = plt.figure(figsize=CALLOUT_FIGSIZE if CREATE_COUNTRY_CALLOUTS else (14, 6.7))
    ax = fig.add_axes(main_rect)

    # Same background style used by Shipping_route_model.plot_routes().
    try:
        land_gdf.boundary.plot(
            ax=ax,
            linewidth=0.5,
            alpha=0.65,
            color="#5DA5DA",
            zorder=1,
        )
    except Exception as exc:
        print(f"WARNING: could not draw land/coastline background: {exc}")

    try:
        lanes_gdf.plot(
            ax=ax,
            linewidth=0.25,
            alpha=0.18,
            color="0.70",
            zorder=2,
        )
    except Exception as exc:
        print(f"WARNING: could not draw shipping-lane background: {exc}")

    # Overlay selected countries on the MAIN MAP: hatched interior plus continuous grey border.
    try:
        if not selected_countries_gdf.empty:
            hatch_pattern = build_hatch_pattern(SELECTED_HATCH_CHAR, SELECTED_HATCH_DENSITY)

            with plt.rc_context({"hatch.linewidth": SELECTED_HATCH_LINEWIDTH}):
                selected_countries_gdf.plot(
                    ax=ax,
                    facecolor="none",
                    edgecolor=SELECTED_HATCH_COLOR,
                    linewidth=0.0,
                    hatch=hatch_pattern,
                    alpha=1.0,
                    zorder=9,
                )

            selected_countries_gdf.boundary.plot(
                ax=ax,
                linewidth=SELECTED_BORDER_LINEWIDTH,
                color=SELECTED_BORDER_COLOR,
                linestyle="solid",
                alpha=1.0,
                zorder=12,
            )
    except Exception as exc:
        print(f"WARNING: could not draw selected-country overlays: {exc}")

    # Red points are removed from the main map by default. Turn this on only if
    # you also want to see them on the main map.
    if SHOW_POINTS_ON_MAIN_MAP:
        add_red_points(ax, points_df, POINT_SIZE, zorder=30)

        if LABEL_POINTS:
            label_count = min(len(points_df), MAX_POINTS_TO_LABEL)
            if len(points_df) > MAX_POINTS_TO_LABEL:
                print(
                    f"LABEL_POINTS=True, but only labelling the first {MAX_POINTS_TO_LABEL} "
                    f"of {len(points_df)} points."
                )
            for _, row in points_df.head(label_count).iterrows():
                label = str(row.get("label", "")).strip()
                prefix = str(row.get("iso3", "")).strip()
                text = f"{prefix}-{label}" if label else prefix
                ax.annotate(
                    text,
                    (float(row["lon"]), float(row["lat"])),
                    xytext=(2, 2),
                    textcoords="offset points",
                    fontsize=5,
                    zorder=31,
                )

    if FORCE_GLOBAL_EXTENT:
        ax.set_xlim(GLOBAL_LON_MIN, GLOBAL_LON_MAX)
        ax.set_ylim(GLOBAL_LAT_MIN, GLOBAL_LAT_MAX)

    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title("Selected Renewable Optimisation Countries and Location Callouts")

    if CREATE_COUNTRY_CALLOUTS and not selected_countries_gdf.empty:
        rects_by_side = compute_callout_panel_rects(side_groups, main_rect)
        plot_all_country_callouts(fig, ax, side_groups, rects_by_side, points_df)

    if SHOW_POINT_LEGEND:
        point_legend_handle = Line2D(
            [0],
            [0],
            marker="o",
            color="none",
            markerfacecolor="red",
            markeredgecolor="black",
            markersize=6,
            label="Selected renewable optimisation location",
        )
        ax.legend(
            handles=[point_legend_handle],
            loc="upper center",
            bbox_to_anchor=(0.5, -0.10),
            frameon=True,
            framealpha=0.94,
            fontsize=9,
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
    print("Output PNG:", OUTPUT_PNG)

    if not RESULTS_ROOT.exists():
        raise FileNotFoundError(
            "RESULTS_ROOT does not exist. Edit PROJECT_DIR or RESULTS_ROOT at the top of this script.\n"
            f"Current value: {RESULTS_ROOT}"
        )

    country_list_path = locate_country_list(PROJECT_DIR, RESULTS_ROOT, COUNTRY_LIST_PATH)
    print(f"Using country list: {country_list_path}")

    selected_country_records = load_country_list(country_list_path)
    print(f"Selected countries in list: {len(selected_country_records)}")

    points_df = load_all_selected_locations(RESULTS_ROOT, country_list_path)
    print(
        f"Loaded {len(points_df)} valid selected location point(s) "
        f"from {points_df['iso3'].nunique()} country/countries."
    )

    lanes_gdf, land_gdf, _lanes_path = load_shipping_background(PROJECT_DIR)
    saved_path = plot_selected_locations(
        lanes_gdf=lanes_gdf,
        land_gdf=land_gdf,
        points_df=points_df,
        selected_country_records=selected_country_records,
        output_png=OUTPUT_PNG,
        dpi=OUTPUT_DPI,
    )

    print(f"\nSaved 600-DPI PNG map to:\n{saved_path}")


if __name__ == "__main__":
    main()
