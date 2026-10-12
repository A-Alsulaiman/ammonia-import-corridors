#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Create one combined LCOE-vs-transport scatter graph for all considered countries.

    x-axis: LCOE, lcoe_usd_per_mwh
    y-axis: NH3 transport cost proxy, nh3_transport_usd_per_t

This version does NOT draw Pareto-front lines. Instead, it highlights the best
N locations for each country in deep country colors.

Default ranking:
    best = lowest nh3_prod_cost_usd_per_t + nh3_transport_usd_per_t

Note:
    The ranking still uses NH3 production + transport costs, because those are
    both USD/t NH3. The x-axis is changed only for plotting/visualisation.

Expected country data path:
    C:\\Users\\user\\Documents\\Project\\AP_IRA_Model\\trade_version_results\\<country name>\\optimal_locations

The script reads the considered countries from "List of Countries" using:
    Column A = country folder/name
    Column C = ISO3

If no country-list file is found, the script falls back to discovering country
folders under trade_version_results.
"""

from __future__ import annotations

import re
import unicodedata
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.colors import to_rgba
from matplotlib.patches import Ellipse


# =============================================================================
# USER SETTINGS
# =============================================================================

PROJECT_DIR = Path(os.environ.get("AP_IRA_PROJECT_DIR", str(Path(__file__).resolve().parent.parent))).resolve()
RESULTS_ROOT = PROJECT_DIR / "trade_version_results"

# Use no extension here; the script will try .xlsx, .xlsm, .xls, and .csv.
COUNTRY_LIST_PATH: Optional[Path] = PROJECT_DIR / "List of Countries"

OPTIMAL_LOCATIONS_FOLDER = "optimal_locations"

PROD_COL = "nh3_prod_cost_usd_per_t"
LCOE_COL = "lcoe_usd_per_mwh"
TRANSPORT_COL = "nh3_transport_usd_per_t"
DELIVERED_COL = "nh3_delivered_usd_per_t"

# Plot-axis columns and labels.
PLOT_X_COL = LCOE_COL
PLOT_Y_COL = "transport_cost_proxy_usd_per_t"
PLOT_X_AXIS_LABEL = "LCOE, USD/MWh"
PLOT_Y_AXIS_LABEL = "NH3 transport cost proxy, USD/t NH3"
PLOT_TITLE_X_NAME = "LCOE"
PLOT_TITLE_Y_NAME = "NH3 Transport Cost Proxy"

# Number of best locations to highlight per country.
TOP_N_LOCATIONS = 10

# Ranking options:
#   "sum_prod_transport"    -> lowest production + transport proxy; default
#   "delivered_if_available" -> lowest nh3_delivered_usd_per_t if available, else production + transport
#   "normalized_sum"        -> equal-weight min-max normalized production + transport within each country
#
# The ranking remains production+transport based. The plotted x-axis is LCOE.
RANKING_MODE = "sum_prod_transport"

OUTPUT_DPI = 600
OUTPUT_PNG = PROJECT_DIR / "best_10_locations_lcoe_vs_transport_all_countries_600dpi.png"

# Optional CSV exports.
SAVE_BEST10_CSV = True
SAVE_ALL_POINTS_CSV = True
OUTPUT_BEST10_CSV = PROJECT_DIR / "best_10_locations_lcoe_vs_transport_all_countries.csv"
OUTPUT_ALL_POINTS_CSV = PROJECT_DIR / "all_points_with_best10_flag.csv"

# Plot options.
SHOW_ALL_POINTS = True                # True = faint scatter for all feasible points
BACKGROUND_POINTS_BY_COUNTRY = True   # Plot all points in faint country colors
LABEL_BEST10_POINTS = False           # Kept for optional debugging; top points are not highlighted separately
AXES_START_AT_ZERO = False            # True = force both axes to start at zero

# Manual axis-limit controls. Use None for automatic matplotlib limits.
# Examples:
#   X_AXIS_MIN = 0
#   X_AXIS_MAX = 80
#   Y_AXIS_MIN = 0
#   Y_AXIS_MAX = 250
X_AXIS_MIN = None
X_AXIS_MAX = None
Y_AXIS_MIN = None
Y_AXIS_MAX = None

# Reference market range for Grey Ammonia FOB Middle East.
# This is disabled by default because the x-axis is now LCOE (USD/MWh),
# while this FOB ammonia reference is in USD/t NH3.
SHOW_FOB_ME_REFERENCE = False
FOB_ME_VALUES_USD_PER_T = [369.77, 430.87, 493.00]   # Sep-25, Oct-25, Nov-25
FOB_ME_SERIES_CODE = "Gray ammonia | FOB Middle East"
FOB_ME_SERIES_NAME = "Gray ammonia | FOB Middle East"
FOB_ME_PERIOD_LABEL = "Sep–Nov 2025"
FOB_ME_BAND_COLOR = "lightgrey"
FOB_ME_BAND_ALPHA = 0.35
FOB_ME_RANGE_EDGE_COLOR = "0.75"
FOB_ME_RANGE_EDGE_ALPHA = 0.9
FOB_ME_RANGE_EDGE_WIDTH = 1.2
FOB_ME_LABEL_FONTSIZE = 8.5
FOB_ME_LABEL_Y_POSITION_AXES = 0.8   # 0=bottom of axes, 1=top of axes

FIGURE_SIZE = (12.5, 7.5)
ALL_POINT_SIZE = 10
ALL_POINT_ALPHA = 0.18
ALL_POINT_COLOR = "0.65"
BEST_POINT_SIZE = 72
BEST_POINT_ALPHA = 0.95
BEST_POINT_EDGE_COLOR = "black"
BEST_POINT_EDGE_WIDTH = 0.65
RANK_LABEL_FONTSIZE = 13.0
GRID_ALPHA = 0.25

# Country-cluster oval options.
DRAW_COUNTRY_OVALS = True             # Draw a rounded/oval shape around each country's best locations
OVAL_FILL_ALPHA = 0.08                # Fill transparency of the oval
OVAL_EDGE_ALPHA = 0.95                # Edge transparency of the oval
OVAL_LINEWIDTH = 2.0                  # Border width of the oval
OVAL_PADDING_FRACTION = 0.18          # Extra padding added around the best-point range
OVAL_MIN_SIZE_FRACTION = 0.018        # Minimum oval width/height as fraction of overall axis span
SHOW_COUNTRY_NAME_NEAR_OVAL = True    # Write the country name near the oval
COUNTRY_NAME_FONTSIZE = 13.0
COUNTRY_NAME_OFFSET_POINTS = (4, 3)   # Base offset of the country label from the oval edge
COUNTRY_NAME_USE_ISO3 = False         # True -> use ISO3 instead of full country name when available
COUNTRY_LABEL_MAX_TRIES = 60          # Number of distance steps tested per label
COUNTRY_LABEL_STEP_POINTS = 5         # Distance increase when pushing labels away
COUNTRY_LABEL_BOX_ALPHA = 0.72        # White label background alpha
DRAW_LEADER_LINES_FOR_MOVED_LABELS = True
SHOW_LEGEND = False                   # Usually not needed when country names are shown near the ovals

# File search behavior inside each country's optimal_locations folder.
# The script first tries these exact names, then scans any Excel/CSV file that
# contains the required columns.
PREFERRED_COST_FILE_NAMES = [
    "ammonia_delivered_costs.xlsx",
    "ammonia_delivered_costs.xlsm",
    "ammonia_delivered_costs.xls",
    "ammonia_delivered_costs.csv",
]

COST_FILE_PATTERNS = [
    "*ammonia*delivered*cost*.xls*",
    "*ammonia*delivered*cost*.csv",
    "*delivered*cost*.xls*",
    "*delivered*cost*.csv",
    "*cost*.xls*",
    "*cost*.csv",
    "*.xlsx",
    "*.xlsm",
    "*.xls",
    "*.csv",
]

# Keep False because you said the files are in optimal_locations.
# Set True only if some cost files are one level higher in the country folder.
SEARCH_PARENT_COUNTRY_FOLDER_TOO = False

# Try shifted headers in case an Excel sheet has a title row above the actual headers.
HEADER_ROWS_TO_TRY = range(0, 6)


# =============================================================================
# TEXT / PATH HELPERS
# =============================================================================

def normalize_header(value: object) -> str:
    """Normalize a text value for robust matching."""
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
    """Clean text from Excel/CSV while preserving case for folder matching."""
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


def iso3_from_cell(value: object) -> str:
    """Return a cleaned ISO3 code from a cell, or an empty string."""
    if is_blank(value):
        return ""

    text = str(value).strip().upper()

    # Avoid treating header text as an ISO3 code.
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


def country_label(country: str, iso3: str = "") -> str:
    return f"{country} ({iso3})" if iso3 else country


# =============================================================================
# COUNTRY LIST LOADING
# =============================================================================

def locate_country_list(
    project_dir: Path,
    results_root: Path,
    explicit_path: Optional[Path],
) -> Optional[Path]:
    """Find the country-list workbook/CSV."""
    extensions = [".xlsx", ".xlsm", ".xls", ".csv"]

    candidates: List[Path] = []

    if explicit_path is not None:
        path = explicit_path if explicit_path.is_absolute() else project_dir / explicit_path
        path = path.resolve()
        if path.suffix:
            candidates.append(path)
        else:
            candidates.extend([path.with_suffix(ext) for ext in extensions])

    # Auto-detect fallback.
    name_tokens = [
        "country_list",
        "country list",
        "countrylist",
        "list of countries",
        "listofcountries",
    ]

    for directory in [project_dir, results_root]:
        if not directory.exists():
            continue
        for path in directory.glob("*"):
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            stem_norm = normalize_header(path.stem)
            if any(normalize_header(token) in stem_norm for token in name_tokens):
                candidates.append(path.resolve())

    seen = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if path.exists():
            return path

    return None


def raw_country_tables(path: Path):
    """Yield raw headerless tables from a country-list Excel/CSV file."""
    suffix = path.suffix.lower()

    if suffix == ".csv":
        yield "csv", pd.read_csv(path, header=None)
        return

    if suffix in {".xlsx", ".xlsm", ".xls"}:
        xls = pd.ExcelFile(path)
        for sheet in xls.sheet_names:
            yield sheet, pd.read_excel(path, sheet_name=sheet, header=None)
        return

    raise ValueError(f"Unsupported country-list file type: {path}")


def deduplicate_records(records: List[Dict[str, str]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen = set()

    for rec in records:
        country = clean_text(rec.get("country_name", ""))
        iso3 = clean_text(rec.get("iso3", "")).upper()
        key = iso3 or normalize_header(country)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"country_name": country or iso3, "iso3": iso3})

    return out


def load_country_list(country_list_path: Path) -> List[Dict[str, str]]:
    """
    Load considered countries.

    Preferred layout:
        Column A = country folder/name
        Column C = ISO3

    The ISO3 filter helps skip title/header rows.
    """
    # First pass: use Column C ISO3 to identify real country rows.
    for sheet_name, raw in raw_country_tables(country_list_path):
        raw = raw.dropna(axis=1, how="all")
        if raw.empty or raw.shape[1] < 1:
            continue

        records: List[Dict[str, str]] = []

        if raw.shape[1] >= 3:
            for _, row in raw.iterrows():
                country_name = clean_text(row.iloc[0])
                iso3 = iso3_from_cell(row.iloc[2])

                if not iso3:
                    continue

                if not country_name or normalize_header(country_name) in {
                    "country",
                    "countryname",
                    "name",
                }:
                    country_name = iso3

                records.append({"country_name": country_name, "iso3": iso3})

        records = deduplicate_records(records)
        if records:
            print(f"Loaded {len(records)} country/countries from {country_list_path} [{sheet_name}]")
            return records

    # Fallback: use nonblank values in Column A.
    for sheet_name, raw in raw_country_tables(country_list_path):
        raw = raw.dropna(axis=1, how="all")
        if raw.empty or raw.shape[1] < 1:
            continue

        records = []
        for _, row in raw.iterrows():
            country_name = clean_text(row.iloc[0])
            if not country_name:
                continue
            if normalize_header(country_name) in {"country", "countryname", "name"}:
                continue
            records.append({"country_name": country_name, "iso3": ""})

        records = deduplicate_records(records)
        if records:
            print(
                f"Loaded {len(records)} country/countries from Column A fallback "
                f"in {country_list_path} [{sheet_name}]"
            )
            return records

    raise ValueError(f"No countries could be loaded from {country_list_path}")


def discover_country_records_from_results(results_root: Path) -> List[Dict[str, str]]:
    """Fallback when no country-list file is found."""
    records: List[Dict[str, str]] = []
    if not results_root.exists():
        return records

    for child in sorted(results_root.iterdir()):
        if not child.is_dir():
            continue
        opt_dir = child / OPTIMAL_LOCATIONS_FOLDER
        if opt_dir.exists():
            records.append({"country_name": child.name, "iso3": ""})

    return records


# =============================================================================
# COUNTRY FOLDER / COST FILE LOADING
# =============================================================================

def find_country_optimal_locations_dir(
    results_root: Path,
    country_name: str,
    iso3: str = "",
) -> Path:
    """
    Locate:
        trade_version_results/<country_name>/optimal_locations
    """
    country_folder = clean_text(country_name)
    iso3 = clean_text(iso3).upper()

    exact_candidates = [
        results_root / country_folder / OPTIMAL_LOCATIONS_FOLDER,
        results_root / iso3 / OPTIMAL_LOCATIONS_FOLDER if iso3 else None,
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
                opt_dir = child / OPTIMAL_LOCATIONS_FOLDER
                if opt_dir.exists():
                    return opt_dir

        # Partial match fallback.
        partial_matches: List[Path] = []
        for child in sorted(results_root.iterdir()):
            if not child.is_dir():
                continue
            child_key = normalize_header(child.name)
            if target_country_key and (
                target_country_key in child_key or child_key in target_country_key
            ):
                opt_dir = child / OPTIMAL_LOCATIONS_FOLDER
                if opt_dir.exists():
                    partial_matches.append(opt_dir)

        if len(partial_matches) == 1:
            return partial_matches[0]

    expected = results_root / country_folder / OPTIMAL_LOCATIONS_FOLDER
    raise FileNotFoundError(
        f"Could not find optimal_locations folder for {country_name} ({iso3}). "
        f"Expected: {expected}"
    )


def required_cost_columns(df: pd.DataFrame) -> Optional[Tuple[str, str]]:
    """
    Return the actual DataFrame column names for PROD_COL and TRANSPORT_COL,
    allowing minor capitalization/spacing differences.
    """
    norm_to_original = {normalize_header(col): col for col in df.columns}

    prod = norm_to_original.get(normalize_header(PROD_COL))
    transport = norm_to_original.get(normalize_header(TRANSPORT_COL))

    if prod is None or transport is None:
        return None

    return prod, transport


def read_cost_table_from_path(path: Path) -> Tuple[pd.DataFrame, str, str, str]:
    """
    Read a CSV/Excel file and return:
        dataframe, production-column-name, transport-column-name, sheet-name
    """
    suffix = path.suffix.lower()

    if suffix == ".csv":
        last_error = None
        for header_row in HEADER_ROWS_TO_TRY:
            try:
                df = pd.read_csv(path, header=header_row)
            except Exception as exc:
                last_error = exc
                continue

            cols = required_cost_columns(df)
            if cols is not None:
                return df, cols[0], cols[1], ""

        raise KeyError(
            f"Required columns not found in CSV: {path}. "
            f"Last read error: {last_error}"
        )

    if suffix in {".xlsx", ".xlsm", ".xls"}:
        xls = pd.ExcelFile(path)
        for sheet_name in xls.sheet_names:
            for header_row in HEADER_ROWS_TO_TRY:
                try:
                    df = pd.read_excel(path, sheet_name=sheet_name, header=header_row)
                except Exception:
                    continue

                cols = required_cost_columns(df)
                if cols is not None:
                    return df, cols[0], cols[1], sheet_name

        raise KeyError(f"Required columns not found in Excel workbook: {path}")

    raise ValueError(f"Unsupported cost file type: {path}")


def unique_existing_paths(paths: Iterable[Path]) -> List[Path]:
    out: List[Path] = []
    seen = set()

    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        if path.exists() and path.is_file():
            out.append(path)

    return out


def cost_file_candidates(optimal_dir: Path) -> List[Path]:
    """Return candidate cost files in priority order."""
    directories = [optimal_dir]
    if SEARCH_PARENT_COUNTRY_FOLDER_TOO:
        directories.append(optimal_dir.parent)

    candidates: List[Path] = []

    for directory in directories:
        for name in PREFERRED_COST_FILE_NAMES:
            candidates.append(directory / name)

        for pattern in COST_FILE_PATTERNS:
            candidates.extend(sorted(directory.glob(pattern)))

    return unique_existing_paths(candidates)


def find_cost_table(optimal_dir: Path) -> Tuple[pd.DataFrame, Path, str, str, str]:
    """
    Find the first CSV/Excel file in optimal_dir that contains the required
    cost proxy columns.
    """
    rejected: List[str] = []

    for path in cost_file_candidates(optimal_dir):
        try:
            df, prod_col, transport_col, sheet_name = read_cost_table_from_path(path)
            return df, path, prod_col, transport_col, sheet_name
        except Exception as exc:
            rejected.append(f"{path.name}: {exc}")

    details = "\n  - ".join(rejected[:12]) if rejected else "No Excel/CSV files found."
    raise FileNotFoundError(
        f"No usable cost file with columns {PROD_COL!r} and {TRANSPORT_COL!r} "
        f"was found in {optimal_dir}.\nChecked/rejected:\n  - {details}"
    )


def maybe_numeric_series(series: pd.Series, column_name: str) -> pd.Series:
    """Convert obvious numeric optional columns while leaving text columns alone."""
    norm = normalize_header(column_name)
    numeric_tokens = [
        "cost", "usd", "lat", "lon", "lng", "distance", "km", "lcoe",
        "coeff", "rank", "score", "mwh", "t", "portlat", "portlon",
    ]
    if any(token in norm for token in numeric_tokens):
        converted = pd.to_numeric(series, errors="coerce")
        # Keep converted values when at least one value was successfully parsed.
        if converted.notna().any():
            return converted
    return series


def load_cost_points_for_country(
    record: Dict[str, str],
    country_order: int,
) -> pd.DataFrame:
    """Load production/transport cost points for one country."""
    country_name = clean_text(record.get("country_name", ""))
    iso3 = clean_text(record.get("iso3", "")).upper()

    optimal_dir = find_country_optimal_locations_dir(
        RESULTS_ROOT,
        country_name=country_name,
        iso3=iso3,
    )

    df, source_path, prod_col, transport_col, sheet_name = find_cost_table(optimal_dir)

    norm_to_original = {normalize_header(col): col for col in df.columns}
    lcoe_col = norm_to_original.get(normalize_header(LCOE_COL))
    if lcoe_col is None:
        raise KeyError(
            f"Required x-axis column {LCOE_COL!r} was not found in {source_path}. "
            f"Available columns: {list(df.columns)}"
        )

    out = pd.DataFrame({
        "country": country_name,
        "iso3": iso3,
        "country_order": country_order,
        "source_file": str(source_path),
        "source_sheet": sheet_name,
        "source_row_index": df.index,
        "lcoe_usd_per_mwh": pd.to_numeric(df[lcoe_col], errors="coerce"),
        "prod_cost_proxy_usd_per_t": pd.to_numeric(df[prod_col], errors="coerce"),
        "transport_cost_proxy_usd_per_t": pd.to_numeric(df[transport_col], errors="coerce"),
    })

    # Optional useful columns copied to the CSV outputs, when present.
    optional_cols = [
        DELIVERED_COL,
        "lat",
        "lon",
        "latitude",
        "longitude",
        "site_lat",
        "site_lon",
        "location_lat",
        "location_lon",
        "nearest_port",
        "nearest_port_country",
        "distance_km",
        "pipeline_coeff_usd_per_tkm",
        "port_lat",
        "port_lon",
        "canonical_port_lat",
        "canonical_port_lon",
        "location_id",
        "site",
        "city",
        "rank",
    ]

    for col in optional_cols:
        original = norm_to_original.get(normalize_header(col))
        if original is not None and col not in out.columns:
            out[col] = maybe_numeric_series(df[original], original)

    before = len(out)
    out = out.dropna(
        subset=[
            "lcoe_usd_per_mwh",
            "prod_cost_proxy_usd_per_t",
            "transport_cost_proxy_usd_per_t",
        ]
    ).copy()

    out = out[
        np.isfinite(out["lcoe_usd_per_mwh"])
        & np.isfinite(out["prod_cost_proxy_usd_per_t"])
        & np.isfinite(out["transport_cost_proxy_usd_per_t"])
    ].copy()

    dropped = before - len(out)
    if dropped:
        print(f"  {country_label(country_name, iso3)}: dropped {dropped} invalid LCOE/cost row(s).")

    print(
        f"  {country_label(country_name, iso3)}: "
        f"{len(out)} valid point(s) from {source_path.name}"
        + (f" [{sheet_name}]" if sheet_name else "")
    )

    return out


# =============================================================================
# BEST-10 RANKING
# =============================================================================

def minmax_normalize(series: pd.Series) -> pd.Series:
    """Min-max normalize a series. Constant series become zero."""
    values = pd.to_numeric(series, errors="coerce")
    min_val = values.min(skipna=True)
    max_val = values.max(skipna=True)
    span = max_val - min_val
    if pd.isna(span) or span <= 0:
        return pd.Series(0.0, index=series.index)
    return (values - min_val) / span


def ranking_score_for_country(country_df: pd.DataFrame) -> pd.Series:
    """Return the ranking score used to choose the best locations."""
    x = pd.to_numeric(country_df["prod_cost_proxy_usd_per_t"], errors="coerce")
    y = pd.to_numeric(country_df["transport_cost_proxy_usd_per_t"], errors="coerce")

    mode = str(RANKING_MODE).strip().lower()

    if mode == "normalized_sum":
        return minmax_normalize(x) + minmax_normalize(y)

    if mode == "delivered_if_available" and DELIVERED_COL in country_df.columns:
        delivered = pd.to_numeric(country_df[DELIVERED_COL], errors="coerce")
        if delivered.notna().any():
            return delivered.where(delivered.notna(), x + y)

    # Default: both proxies are USD/t, so their sum is the total cost proxy.
    return x + y


def ranking_description() -> str:
    mode = str(RANKING_MODE).strip().lower()
    if mode == "normalized_sum":
        return "lowest within-country normalized production + transport score"
    if mode == "delivered_if_available":
        return "lowest delivered cost if available, otherwise production + transport"
    return "lowest production + transport proxy"


def add_best_location_flags(all_points: pd.DataFrame) -> pd.DataFrame:
    """Add ranking score, best-rank, and best-location flag to all points."""
    out = all_points.copy()
    out["best_rank_score"] = np.nan
    out["best_location_rank"] = pd.NA
    out["is_best_location"] = False

    ordered_keys = (
        out.sort_values("country_order")["country_key"]
        .drop_duplicates()
        .tolist()
    )

    for key in ordered_keys:
        country_idx = out.index[out["country_key"] == key]
        country_df = out.loc[country_idx].copy()
        if country_df.empty:
            continue

        score = ranking_score_for_country(country_df)
        out.loc[country_idx, "best_rank_score"] = score

        ranked = country_df.assign(best_rank_score=score)
        ranked = ranked.sort_values(
            [
                "best_rank_score",
                "prod_cost_proxy_usd_per_t",
                "transport_cost_proxy_usd_per_t",
                "point_id",
            ],
            ascending=[True, True, True, True],
            kind="mergesort",
        )

        best_idx = ranked.head(int(TOP_N_LOCATIONS)).index.tolist()
        out.loc[best_idx, "is_best_location"] = True
        out.loc[best_idx, "best_location_rank"] = list(range(1, len(best_idx) + 1))

    return out


# =============================================================================
# PLOTTING
# =============================================================================

def darken_color(color, factor: float = 0.80):
    """Return a darker RGBA version of a matplotlib color."""
    r, g, b, a = to_rgba(color)
    return (max(r * factor, 0), max(g * factor, 0), max(b * factor, 0), a)


def color_luminance(color) -> float:
    """Approximate perceived luminance of a matplotlib color."""
    r, g, b, _a = to_rgba(color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def build_deep_country_colors(n: int):
    """Build enough deep, visually distinct country colors."""
    colors = []

    # These palettes contain many strong categorical colors.
    for cmap_name in ["tab10", "Dark2", "Set1", "tab20", "tab20b", "tab20c"]:
        cmap = plt.get_cmap(cmap_name)
        for i in range(cmap.N):
            c = cmap(i)
            # Skip very pale colors; darken medium/light colors slightly.
            lum = color_luminance(c)
            if lum > 0.88:
                continue
            factor = 0.72 if lum > 0.68 else 0.88
            colors.append(darken_color(c, factor=factor))

    # Remove near-duplicates while preserving order.
    unique = []
    seen = set()
    for c in colors:
        key = tuple(round(v, 3) for v in c[:3])
        if key not in seen:
            seen.add(key)
            unique.append(c)

    if n <= len(unique):
        return unique[:n]

    # Fallback for very many countries.
    cmap = plt.get_cmap("nipy_spectral")
    extra = [darken_color(cmap(i), factor=0.80) for i in np.linspace(0.05, 0.95, n - len(unique))]
    return unique + extra


def country_plot_name(country: str, iso3: str = "") -> str:
    """Return the plotted country label."""
    if COUNTRY_NAME_USE_ISO3 and iso3:
        return iso3
    return country


def add_country_best_oval(
    ax,
    best_df: pd.DataFrame,
    color,
    x_col: str,
    y_col: str,
    x_span_global: float,
    y_span_global: float,
):
    """Draw an oval around the best locations of one country and return metadata for label placement."""
    x = pd.to_numeric(best_df[x_col], errors="coerce").to_numpy(dtype=float)
    y = pd.to_numeric(best_df[y_col], errors="coerce").to_numpy(dtype=float)

    x = x[np.isfinite(x)]
    y = y[np.isfinite(y)]

    if len(x) == 0 or len(y) == 0:
        return None

    x_min = float(np.min(x))
    x_max = float(np.max(x))
    y_min = float(np.min(y))
    y_max = float(np.max(y))

    x_range = x_max - x_min
    y_range = y_max - y_min

    min_width = max(float(x_span_global) * OVAL_MIN_SIZE_FRACTION, 1e-6)
    min_height = max(float(y_span_global) * OVAL_MIN_SIZE_FRACTION, 1e-6)

    width = max(x_range * (1 + 2 * OVAL_PADDING_FRACTION), min_width)
    height = max(y_range * (1 + 2 * OVAL_PADDING_FRACTION), min_height)

    center_x = 0.5 * (x_min + x_max)
    center_y = 0.5 * (y_min + y_max)

    edge_color = to_rgba(color, OVAL_EDGE_ALPHA)
    face_color = to_rgba(color, OVAL_FILL_ALPHA)

    ellipse = Ellipse(
        (center_x, center_y),
        width=width,
        height=height,
        angle=0.0,
        facecolor=face_color,
        edgecolor=edge_color,
        linewidth=OVAL_LINEWIDTH,
        zorder=3,
    )
    ax.add_patch(ellipse)

    return {
        "ellipse": ellipse,
        "center_x": center_x,
        "center_y": center_y,
        "width": width,
        "height": height,
        "label_anchor": (center_x + width / 2.0, center_y + height / 2.0),
    }


def _choose_label_directions(center_x: float, center_y: float, x_mid: float, y_mid: float):
    """
    Candidate label directions around an oval.

    The first directions point away from the plot centre so labels tend to sit
    just outside their own country oval. Other directions are tested afterwards.
    """
    sx = 1 if center_x >= x_mid else -1
    sy = 1 if center_y >= y_mid else -1

    primary = [
        (sx, sy),
        (sx, 0),
        (0, sy),
        (-sx, sy),
        (sx, -sy),
        (-sx, 0),
        (0, -sy),
        (-sx, -sy),
    ]

    all_dirs = [
        (1, 1), (1, 0), (1, -1),
        (0, 1),          (0, -1),
        (-1, 1), (-1, 0), (-1, -1),
    ]

    out = []
    seen = set()
    for item in primary + all_dirs:
        if item not in seen:
            out.append(item)
            seen.add(item)
    return out


def _bbox_overlaps_any(target_bbox, bbox_list) -> bool:
    for bbox in bbox_list:
        if target_bbox.overlaps(bbox):
            return True
    return False


def _bbox_overlaps_ellipse(target_bbox, ellipse_bbox) -> bool:
    """
    Approximate whether a display-space label bbox overlaps an axis-aligned ellipse.

    This is less conservative than testing against the ellipse's rectangular
    bounding box, so labels can sit closer to the curved oval corners without
    visually covering the oval.
    """
    if not target_bbox.overlaps(ellipse_bbox):
        return False

    cx = 0.5 * (ellipse_bbox.x0 + ellipse_bbox.x1)
    cy = 0.5 * (ellipse_bbox.y0 + ellipse_bbox.y1)
    rx = max(0.5 * (ellipse_bbox.x1 - ellipse_bbox.x0), 1e-9)
    ry = max(0.5 * (ellipse_bbox.y1 - ellipse_bbox.y0), 1e-9)

    # If the ellipse centre is inside the label rectangle, they overlap.
    if target_bbox.x0 <= cx <= target_bbox.x1 and target_bbox.y0 <= cy <= target_bbox.y1:
        return True

    # Nearest point on the label bbox to the ellipse centre.
    nearest_x = min(max(cx, target_bbox.x0), target_bbox.x1)
    nearest_y = min(max(cy, target_bbox.y0), target_bbox.y1)

    return ((nearest_x - cx) / rx) ** 2 + ((nearest_y - cy) / ry) ** 2 <= 1.0


def _bbox_overlaps_any_ellipse(target_bbox, ellipse_bboxes) -> bool:
    return any(_bbox_overlaps_ellipse(target_bbox, ellipse_bbox) for ellipse_bbox in ellipse_bboxes)


def _anchor_for_direction(spec: dict, dx_sign: int, dy_sign: int) -> Tuple[float, float]:
    """
    Put the leader-line anchor on the oval edge nearest the proposed label.
    """
    cx = float(spec["center_x"])
    cy = float(spec["center_y"])
    width = float(spec["width"])
    height = float(spec["height"])

    ax = cx + dx_sign * width / 2.0 if dx_sign != 0 else cx
    ay = cy + dy_sign * height / 2.0 if dy_sign != 0 else cy

    return ax, ay


def _offset_for_direction(dx_sign: int, dy_sign: int, step: float) -> Tuple[float, float]:
    """
    Offset text outward from the oval edge.

    Direction signs define the side of the oval. The step is additional distance
    in points. A zero component stays centred on that axis.
    """
    base_x, base_y = COUNTRY_NAME_OFFSET_POINTS

    if dx_sign > 0:
        off_x = base_x + step
    elif dx_sign < 0:
        off_x = -(base_x + step)
    else:
        off_x = 0

    if dy_sign > 0:
        off_y = base_y + step
    elif dy_sign < 0:
        off_y = -(base_y + step)
    else:
        off_y = 0

    return float(off_x), float(off_y)


def _alignment_for_offset(off_x: float, off_y: float) -> Tuple[str, str]:
    ha = "left" if off_x >= 0 else "right"
    va = "bottom" if off_y >= 0 else "top"
    if abs(off_x) < 1e-9:
        ha = "center"
    if abs(off_y) < 1e-9:
        va = "center"
    return ha, va


def _draw_country_label_annotation(
    ax,
    label: str,
    color,
    anchor_xy: Tuple[float, float],
    offset_xy: Tuple[float, float],
    with_leader_line: bool = False,
):
    off_x, off_y = offset_xy
    ha, va = _alignment_for_offset(off_x, off_y)

    arrowprops = None
    if with_leader_line:
        arrowprops = dict(
            arrowstyle='-',
            color=color,
            lw=1.2,
            shrinkA=3,
            shrinkB=3,
            alpha=0.95,
        )

    return ax.annotate(
        label,
        xy=anchor_xy,
        xytext=offset_xy,
        textcoords="offset points",
        fontsize=COUNTRY_NAME_FONTSIZE,
        color=color,
        ha=ha,
        va=va,
        zorder=6,
        bbox=dict(
            boxstyle="round,pad=0.20",
            facecolor="white",
            edgecolor="none",
            alpha=COUNTRY_LABEL_BOX_ALPHA,
        ),
        arrowprops=arrowprops,
    )


def place_country_labels_after_ovals(ax, fig, label_specs: List[dict]) -> None:
    """
    Place country names after all ovals are drawn.

    Labels are allowed to cover the translucent point cloud, but they are not
    allowed to cover any oval or an already placed country name. Among all valid
    candidates, the nearest one to the oval edge is chosen.
    """
    if not label_specs or not SHOW_COUNTRY_NAME_NEAR_OVAL:
        return

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()

    # Use actual oval collision, not point collision. Labels may sit over points.
    ellipse_bboxes = [
        spec["ellipse"].get_window_extent(renderer=renderer).expanded(1.01, 1.02)
        for spec in label_specs
    ]

    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    x_mid = 0.5 * (xlim[0] + xlim[1])
    y_mid = 0.5 * (ylim[0] + ylim[1])

    placed_label_bboxes = []

    for spec in label_specs:
        label = spec["name"]
        color = spec["color"]
        center_x = float(spec["center_x"])
        center_y = float(spec["center_y"])

        candidate_dirs = _choose_label_directions(center_x, center_y, x_mid, y_mid)
        candidates = []

        for ring in range(COUNTRY_LABEL_MAX_TRIES):
            step = COUNTRY_LABEL_STEP_POINTS * ring
            for dir_priority, (dx_sign, dy_sign) in enumerate(candidate_dirs):
                anchor_xy = _anchor_for_direction(spec, dx_sign, dy_sign)
                offset_xy = _offset_for_direction(dx_sign, dy_sign, step)

                # Distance priority: nearest label first, then preferred direction.
                distance = float(np.hypot(offset_xy[0], offset_xy[1]))
                candidates.append((distance, ring, dir_priority, anchor_xy, offset_xy))

        candidates.sort(key=lambda item: (item[0], item[1], item[2]))

        chosen_ann = None
        chosen_bbox = None
        chosen_offset = None
        chosen_anchor = None

        for _distance, _ring, _dir_priority, anchor_xy, offset_xy in candidates:
            ann = _draw_country_label_annotation(
                ax=ax,
                label=label,
                color=color,
                anchor_xy=anchor_xy,
                offset_xy=offset_xy,
                with_leader_line=False,
            )
            fig.canvas.draw()
            bbox = ann.get_window_extent(renderer=renderer).expanded(1.01, 1.04)

            overlaps_oval = _bbox_overlaps_any_ellipse(bbox, ellipse_bboxes)
            overlaps_label = _bbox_overlaps_any(bbox, placed_label_bboxes)

            if not overlaps_oval and not overlaps_label:
                chosen_ann = ann
                chosen_bbox = bbox
                chosen_offset = offset_xy
                chosen_anchor = anchor_xy
                break

            ann.remove()

        if chosen_ann is None:
            # Last resort: use the closest preferred direction, far enough away
            # that it should clear the oval. This may overlap another label only
            # in very crowded plots.
            dx_sign, dy_sign = candidate_dirs[0]
            step = COUNTRY_LABEL_MAX_TRIES * COUNTRY_LABEL_STEP_POINTS
            chosen_anchor = _anchor_for_direction(spec, dx_sign, dy_sign)
            chosen_offset = _offset_for_direction(dx_sign, dy_sign, step)
            chosen_ann = _draw_country_label_annotation(
                ax=ax,
                label=label,
                color=color,
                anchor_xy=chosen_anchor,
                offset_xy=chosen_offset,
                with_leader_line=False,
            )
            fig.canvas.draw()
            chosen_bbox = chosen_ann.get_window_extent(renderer=renderer).expanded(1.01, 1.04)

        if DRAW_LEADER_LINES_FOR_MOVED_LABELS and chosen_offset is not None and chosen_anchor is not None:
            off_x, off_y = chosen_offset
            moved_farther_than_base = (
                abs(off_x) > COUNTRY_NAME_OFFSET_POINTS[0] + 1e-9
                or abs(off_y) > COUNTRY_NAME_OFFSET_POINTS[1] + 1e-9
            )
            if moved_farther_than_base:
                chosen_ann.remove()
                chosen_ann = _draw_country_label_annotation(
                    ax=ax,
                    label=label,
                    color=color,
                    anchor_xy=chosen_anchor,
                    offset_xy=chosen_offset,
                    with_leader_line=True,
                )
                fig.canvas.draw()
                chosen_bbox = chosen_ann.get_window_extent(renderer=renderer).expanded(1.01, 1.04)

        placed_label_bboxes.append(chosen_bbox)



def draw_fob_me_reference(ax) -> None:
    """Draw the Grey Ammonia FOB Middle East Sep–Nov 2025 reference range on the x-axis."""
    if not SHOW_FOB_ME_REFERENCE:
        return

    values = [float(v) for v in FOB_ME_VALUES_USD_PER_T if pd.notna(v)]
    if not values:
        return

    cfr_min = float(np.min(values))
    cfr_max = float(np.max(values))
    cfr_mid = 0.5 * (cfr_min + cfr_max)

    # Range band across the entire plot height.
    ax.axvspan(
        cfr_min, cfr_max,
        facecolor=FOB_ME_BAND_COLOR,
        edgecolor='none',
        alpha=FOB_ME_BAND_ALPHA,
        zorder=0,
    )

    # Edge lines for the range only (no center line).
    ax.axvline(
        cfr_min,
        color=FOB_ME_RANGE_EDGE_COLOR,
        alpha=FOB_ME_RANGE_EDGE_ALPHA,
        linewidth=FOB_ME_RANGE_EDGE_WIDTH,
        zorder=1,
    )
    ax.axvline(
        cfr_max,
        color=FOB_ME_RANGE_EDGE_COLOR,
        alpha=FOB_ME_RANGE_EDGE_ALPHA,
        linewidth=FOB_ME_RANGE_EDGE_WIDTH,
        zorder=1,
    )

    label = f"{FOB_ME_SERIES_NAME}\nRange {cfr_min:.0f}–{cfr_max:.0f} USD/t\n({FOB_ME_PERIOD_LABEL})"

    ax.text(
        cfr_mid,
        FOB_ME_LABEL_Y_POSITION_AXES,
        label,
        transform=ax.get_xaxis_transform(),
        ha='center',
        va='center',
        fontsize=FOB_ME_LABEL_FONTSIZE,
        color='0.35',
        bbox=dict(boxstyle='round,pad=0.18', facecolor='white', edgecolor='none', alpha=0.75),
        zorder=7,
    )


def apply_manual_axis_limits(ax) -> None:
    """Apply user-controlled axis limits while preserving auto limits when set to None."""
    current_xmin, current_xmax = ax.get_xlim()
    current_ymin, current_ymax = ax.get_ylim()

    xmin = current_xmin if X_AXIS_MIN is None else X_AXIS_MIN
    xmax = current_xmax if X_AXIS_MAX is None else X_AXIS_MAX
    ymin = current_ymin if Y_AXIS_MIN is None else Y_AXIS_MIN
    ymax = current_ymax if Y_AXIS_MAX is None else Y_AXIS_MAX

    if xmin is not None and xmax is not None and xmin >= xmax:
        raise ValueError(f"Invalid x-axis limits: X_AXIS_MIN ({xmin}) must be smaller than X_AXIS_MAX ({xmax}).")
    if ymin is not None and ymax is not None and ymin >= ymax:
        raise ValueError(f"Invalid y-axis limits: Y_AXIS_MIN ({ymin}) must be smaller than Y_AXIS_MAX ({ymax}).")

    ax.set_xlim(left=xmin, right=xmax)
    ax.set_ylim(bottom=ymin, top=ymax)


def plot_combined_best_locations(
    all_points: pd.DataFrame,
    best_points: pd.DataFrame,
    output_png: Path,
) -> None:
    x_col = PLOT_X_COL
    y_col = PLOT_Y_COL

    plot_keys = (
        all_points.sort_values("country_order")["country_key"]
        .drop_duplicates()
        .tolist()
    )

    colors = build_deep_country_colors(len(plot_keys))
    markers = ["o", "s", "^", "D", "v", "P", "X", "*", "<", ">", "h", "8"]

    fig, ax = plt.subplots(figsize=FIGURE_SIZE)

    x_span_global = float(all_points[x_col].max() - all_points[x_col].min())
    y_span_global = float(all_points[y_col].max() - all_points[y_col].min())
    if not np.isfinite(x_span_global) or x_span_global <= 0:
        x_span_global = 1.0
    if not np.isfinite(y_span_global) or y_span_global <= 0:
        y_span_global = 1.0

    # Faint background: all locations.
    if SHOW_ALL_POINTS:
        for i, key in enumerate(plot_keys):
            country_df = all_points.loc[all_points["country_key"] == key].copy()
            if country_df.empty:
                continue

            marker = markers[i % len(markers)]
            bg_color = colors[i] if BACKGROUND_POINTS_BY_COUNTRY else ALL_POINT_COLOR

            ax.scatter(
                country_df[x_col],
                country_df[y_col],
                s=ALL_POINT_SIZE,
                alpha=ALL_POINT_ALPHA,
                color=bg_color,
                marker=marker,
                linewidths=0,
                label=None,
                zorder=1,
            )

    # Draw ovals for the best N points; do not draw separate highlighted top points.
    label_specs = []
    for i, key in enumerate(plot_keys):
        country_df = all_points.loc[all_points["country_key"] == key].copy()
        best_df = best_points.loc[best_points["country_key"] == key].copy()

        if country_df.empty or best_df.empty:
            continue

        country = str(country_df["country"].iloc[0])
        iso3 = str(country_df["iso3"].iloc[0])

        color = colors[i]

        best_df = best_df.sort_values("best_location_rank", ascending=True)

        if DRAW_COUNTRY_OVALS:
            oval_info = add_country_best_oval(
                ax=ax,
                best_df=best_df,
                color=color,
                x_col=x_col,
                y_col=y_col,
                x_span_global=x_span_global,
                y_span_global=y_span_global,
            )
            if oval_info is not None:
                oval_info["name"] = country_plot_name(country, iso3)
                oval_info["color"] = color
                label_specs.append(oval_info)

        if LABEL_BEST10_POINTS:
            for _, row in best_df.iterrows():
                rank_value = row.get("best_location_rank", "")
                if pd.isna(rank_value):
                    continue
                ax.annotate(
                    str(int(rank_value)),
                    xy=(float(row[x_col]), float(row[y_col])),
                    xytext=(4, 4),
                    textcoords="offset points",
                    fontsize=RANK_LABEL_FONTSIZE,
                    color=color,
                    zorder=5,
                )

    place_country_labels_after_ovals(ax=ax, fig=fig, label_specs=label_specs)

    ax.set_xlabel(PLOT_X_AXIS_LABEL)
    ax.set_ylabel(PLOT_Y_AXIS_LABEL)
    ax.set_title(
        f"Country areas for best {TOP_N_LOCATIONS} locations: "
        f"{PLOT_TITLE_X_NAME} vs {PLOT_TITLE_Y_NAME}"
    )

    ax.grid(True, alpha=GRID_ALPHA)

    if AXES_START_AT_ZERO:
        if X_AXIS_MIN is None:
            ax.set_xlim(left=0)
        if Y_AXIS_MIN is None:
            ax.set_ylim(bottom=0)

    apply_manual_axis_limits(ax)
    draw_fob_me_reference(ax)

    # Legend outside the plot area (optional).
    if SHOW_LEGEND:
        n_countries = len(plot_keys)
        legend_cols = 2 if n_countries > 14 else 1

        ax.legend(
            loc="center left",
            bbox_to_anchor=(1.02, 0.5),
            frameon=True,
            fontsize=8,
            ncol=legend_cols,
            title=f"Deep colors = best {TOP_N_LOCATIONS}\nRanking: {ranking_description()}",
            title_fontsize=8,
        )

    fig.tight_layout()
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=OUTPUT_DPI, bbox_inches="tight")
    plt.close(fig)


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    print("Project directory:", PROJECT_DIR)
    print("Results root:", RESULTS_ROOT)
    print("Output PNG:", OUTPUT_PNG)
    print(f"Highlighting best {TOP_N_LOCATIONS} location(s) per country.")
    print("Plot x-axis column:", PLOT_X_COL)
    print("Plot y-axis column:", PLOT_Y_COL)
    print("Ranking mode:", RANKING_MODE)
    print("Ranking meaning:", ranking_description())
    print("Manual x-axis limits:", (X_AXIS_MIN, X_AXIS_MAX))
    print("Manual y-axis limits:", (Y_AXIS_MIN, Y_AXIS_MAX))
    if SHOW_FOB_ME_REFERENCE:
        cfr_vals = [float(v) for v in FOB_ME_VALUES_USD_PER_T]
        print(
            "FOB Middle East reference range (USD/t):",
            {
                "min": min(cfr_vals),
                "max": max(cfr_vals),
            },
        )

    if not RESULTS_ROOT.exists():
        raise FileNotFoundError(
            "RESULTS_ROOT does not exist. Check PROJECT_DIR.\n"
            f"Current RESULTS_ROOT: {RESULTS_ROOT}"
        )

    country_list_path = locate_country_list(
        project_dir=PROJECT_DIR,
        results_root=RESULTS_ROOT,
        explicit_path=COUNTRY_LIST_PATH,
    )

    if country_list_path is not None:
        print(f"Using country list: {country_list_path}")
        records = load_country_list(country_list_path)
    else:
        print("No country list found. Discovering country folders under trade_version_results.")
        records = discover_country_records_from_results(RESULTS_ROOT)

    if not records:
        raise ValueError("No considered countries were found.")

    print(f"Considered countries: {len(records)}")
    print("Reading cost files from:")
    print(f"  {RESULTS_ROOT}\\<Country name>\\{OPTIMAL_LOCATIONS_FOLDER}")

    frames: List[pd.DataFrame] = []
    skipped: List[str] = []

    for order, record in enumerate(records):
        label = country_label(record.get("country_name", ""), record.get("iso3", ""))
        try:
            country_points = load_cost_points_for_country(record, country_order=order)
            if not country_points.empty:
                frames.append(country_points)
            else:
                skipped.append(f"{label}: no valid rows")
        except Exception as exc:
            skipped.append(f"{label}: {exc}")

    if skipped:
        print("\nWARNING: skipped country/countries:")
        for item in skipped:
            print("  - " + item)

    if not frames:
        raise ValueError("No valid production/transport cost points were loaded.")

    all_points = pd.concat(frames, ignore_index=True)
    all_points["point_id"] = np.arange(len(all_points))

    all_points["country_key"] = all_points.apply(
        lambda row: normalize_header(row["iso3"]) or normalize_header(row["country"]),
        axis=1,
    )

    # Select and flag the best N locations per country.
    all_points = add_best_location_flags(all_points)
    best_points = all_points.loc[all_points["is_best_location"]].copy()
    best_points = best_points.sort_values(
        ["country_order", "best_location_rank", "best_rank_score"]
    )

    # Summary.
    summary_rows = []
    for key in (
        all_points.sort_values("country_order")["country_key"]
        .drop_duplicates()
        .tolist()
    ):
        country_df = all_points.loc[all_points["country_key"] == key]
        best_df = best_points.loc[best_points["country_key"] == key]

        summary_rows.append({
            "country": country_label(str(country_df["country"].iloc[0]), str(country_df["iso3"].iloc[0])),
            "all_points": len(country_df),
            "highlighted_best_points": len(best_df),
            "best_rank_score_min": best_df["best_rank_score"].min() if not best_df.empty else np.nan,
            "best_rank_score_max": best_df["best_rank_score"].max() if not best_df.empty else np.nan,
        })

    summary = pd.DataFrame(summary_rows)

    print("\nBest-location summary:")
    print(summary.to_string(index=False))

    # Exports.
    if SAVE_BEST10_CSV:
        best_points.to_csv(OUTPUT_BEST10_CSV, index=False)
        print(f"\nSaved best-location CSV:\n{OUTPUT_BEST10_CSV}")

    if SAVE_ALL_POINTS_CSV:
        all_points.to_csv(OUTPUT_ALL_POINTS_CSV, index=False)
        print(f"\nSaved all points with best-location flag CSV:\n{OUTPUT_ALL_POINTS_CSV}")

    # Plot.
    plot_combined_best_locations(
        all_points=all_points,
        best_points=best_points,
        output_png=OUTPUT_PNG,
    )

    print(f"\nSaved combined best-location graph:\n{OUTPUT_PNG}")


if __name__ == "__main__":
    main()
