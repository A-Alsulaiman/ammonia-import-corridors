#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Create one combined horizontal box/whisker-bar figure for selected countries:

    LEFT PANEL  = shipping cost, USD/t NH3
    RIGHT PANEL = shipping emissions, tCO2/tNH3

For each country, the script:

1. Reads the same considered-country list pattern used in the other scripts:
       C:\Users\user\Documents\Project\AP_IRA_Model\List of Countries

   Expected layout:
       Column A = country folder/name
       Column C = ISO3

2. Reads the country-level workbook:
       C:\Users\user\Documents\Project\AP_IRA_Model\trade_version_results\<Country Name>\chosen_ports_of_top_locations.xlsx

   It uses the column:
       canonical_main_port

   Duplicate ports are removed, because several top locations can use the same port.

3. For each unique port, reads:
       C:\Users\user\Documents\Project\AP_IRA_Model\trade_version_results\<Country Name>\<canonical_main_port>\shipping_route_<canonical_main_port>_Rotterdam_summary.xlsx

   The script is tolerant to safe filename variants, e.g. spaces replaced by underscores.

4. Keeps only:
       decarbonisation_scenario == "bau"
       year in [2030, 2040]

5. Creates per-country/year statistics across that country's unique ports.

   Shipping cost values:
       cost_eur_per_t_low, cost_eur_per_t_mid, cost_eur_per_t_high
       then converted to USD/t NH3 using EUR_TO_USD_RATE.

   Emissions values:
       total_co2_t_low  / cargo_tonnes
       total_co2_t_mid  / cargo_tonnes
       total_co2_t_high / cargo_tonnes

   If the route summary does not explicitly include cargo_tonnes, the script
   infers it from:
       total_co2_t_mid = ef_g_per_tnm_mid / 1e6 * distance_nm * cargo_tonnes

   If that inference is not possible, it falls back to DEFAULT_CARGO_TONNES.

The horizontal box/whisker bars show:
    whisker min -> q25 box -> q50 median -> q75 box -> whisker max

Outputs are saved in PROJECT_DIR:
    - shipping_bau_cost_usd_emissions_2030_2040_by_country_600dpi.png
    - shipping_bau_port_level_values_usd_cost.csv
    - shipping_bau_country_quantile_summary_usd_cost.csv
"""

from __future__ import annotations

import math
import re
import unicodedata
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle


# =============================================================================
# USER SETTINGS
# =============================================================================

PROJECT_DIR = Path(os.environ.get("AP_IRA_PROJECT_DIR", str(Path(__file__).resolve().parent.parent))).resolve()
RESULTS_ROOT = PROJECT_DIR / "trade_version_results"

# Use no extension here; the script will try .xlsx, .xlsm, .xls, and .csv.
COUNTRY_LIST_PATH: Optional[Path] = PROJECT_DIR / "List of Countries"

CHOSEN_PORTS_FILE_STEM = "chosen_ports_of_top_locations"
CANONICAL_PORT_COLUMN = "canonical_main_port"
DESTINATION_PORT = "Rotterdam"

YEARS_TO_PLOT = [2030, 2040]
DECARB_SCENARIO = "bau"

# Default from Shipping_route_model.py. Used only when the cargo cannot be
# inferred from ef_g_per_tnm_mid, distance_nm, and total_co2_t_mid.
DEFAULT_CARGO_TONNES = 50_000.0

# Shipping cost conversion. Route summary files report cost in EUR/t NH3;
# the graph and exported cost metric are converted to USD/t NH3.
EUR_TO_USD_RATE = 1.0821

# How to form the distribution for q25/q50/q75:
#   "low_mid_high" -> use low, mid, high from each port summary row
#   "mid_only"     -> use only mid values from each port
DISTRIBUTION_VALUE_SOURCE = "low_mid_high"

# Country order:
#   "country_list"       -> same as the List of Countries workbook
#   "shipping_cost_2030" -> sort by 2030 shipping-cost q50
#   "emissions_2030"     -> sort by 2030 emissions q50
#
# With COUNTRY_SORT_LOWEST_AT_BOTTOM=True, the countries with the lowest q50
# shipping cost are placed at the bottom of the horizontal bar chart, and the
# highest q50 shipping-cost countries are placed at the top.
COUNTRY_SORT_MODE = "shipping_cost_2030"
COUNTRY_SORT_LOWEST_AT_BOTTOM = True

OUTPUT_DPI = 600
OUTPUT_PNG = PROJECT_DIR / "shipping_bau_cost_usd_emissions_2030_2040_by_country_600dpi.png"
OUTPUT_PORT_VALUES_CSV = PROJECT_DIR / "shipping_bau_port_level_values_usd_cost.csv"
OUTPUT_COUNTRY_SUMMARY_CSV = PROJECT_DIR / "shipping_bau_country_quantile_summary_usd_cost.csv"

SAVE_PORT_VALUES_CSV = True
SAVE_COUNTRY_SUMMARY_CSV = True

# Plot sizing. Height is automatically increased if many countries are loaded.
FIGURE_WIDTH = 15.5
BASE_FIGURE_HEIGHT = 7.0
HEIGHT_PER_COUNTRY = 0.42

BAR_HEIGHT = 0.23
YEAR_OFFSET = 0.16
WHISKER_LINEWIDTH = 1.3
BOX_LINEWIDTH = 1.0
MEDIAN_LINEWIDTH = 1.6
CAP_HEIGHT_FRACTION = 0.75
GRID_ALPHA = 0.25

# Year colors. Change these if you want a different palette.
YEAR_COLORS = {
    2030: "tab:blue",
    2040: "tab:orange",
}
YEAR_ALPHA = 0.72
WHISKER_ALPHA = 0.95

# Axis controls. Leave None for automatic scaling.
COST_X_AXIS_MIN = 0
COST_X_AXIS_MAX = None
EMISSIONS_X_AXIS_MIN = 0
EMISSIONS_X_AXIS_MAX = None

COST_X_AXIS_LABEL = "Shipping cost, USD/t NH3"
EMISSIONS_X_AXIS_LABEL = "Shipping emissions, tCO2/tNH3"
FIGURE_TITLE = "BAU shipping cost (USD) and emissions by country and year"

SHOW_PORT_COUNTS_IN_LABELS = True
SHOW_ISO3_IN_AXIS_LABELS = False      # False = country name only on y-axis labels
SHOW_LEGEND = True
LEGEND_LOCATION = "lower center"
LEGEND_BBOX_TO_ANCHOR = (0.5, -0.06)
LEGEND_NCOL = 2

# Printing/progress controls.
PRINT_PROGRESS = True


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


def safe_output_name_part(value: object) -> str:
    """Filesystem-safe token matching the style used by the shipping route script."""
    text = "" if value is None else str(value)
    text = text.strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text)
    text = text.strip("._ ")
    return text or "unnamed"


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


def country_label(country: str, iso3: str = "") -> str:
    return f"{country} ({iso3})" if iso3 else country


def pick_column(
    df: pd.DataFrame,
    preferred_names: Sequence[str],
    contains_any: Sequence[str] = (),
    required: bool = True,
    purpose: str = "column",
) -> Optional[str]:
    """Pick a column by normalized exact name, then by substring hints."""
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


def read_table(path: Path) -> pd.DataFrame:
    """Read Excel or CSV into a DataFrame using the first row as headers."""
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls", ".xlsm"}:
        return pd.read_excel(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported table type: {path}")


def read_first_matching_table(path: Path, required_columns: Sequence[str]) -> pd.DataFrame:
    """
    Read a CSV/Excel file, trying sheets/header rows until all required columns exist.
    Header-row shifting is included because some exported workbooks may contain title rows.
    """
    suffix = path.suffix.lower()
    required_norm = {normalize_header(col) for col in required_columns}

    if suffix == ".csv":
        for header_row in range(0, 6):
            try:
                df = pd.read_csv(path, header=header_row)
            except Exception:
                continue
            cols_norm = {normalize_header(col) for col in df.columns}
            if required_norm.issubset(cols_norm):
                return df
        raise KeyError(f"Required columns {list(required_columns)} not found in {path}")

    if suffix in {".xlsx", ".xls", ".xlsm"}:
        xls = pd.ExcelFile(path)
        for sheet in xls.sheet_names:
            for header_row in range(0, 6):
                try:
                    df = pd.read_excel(path, sheet_name=sheet, header=header_row)
                except Exception:
                    continue
                cols_norm = {normalize_header(col) for col in df.columns}
                if required_norm.issubset(cols_norm):
                    return df
        raise KeyError(f"Required columns {list(required_columns)} not found in {path}")

    raise ValueError(f"Unsupported table type: {path}")


# =============================================================================
# COUNTRY LIST LOADING
# =============================================================================

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


def discover_country_records_from_results(results_root: Path) -> List[Dict[str, str]]:
    """Fallback when no country-list file is found: discover folders with chosen ports."""
    records: List[Dict[str, str]] = []
    if not results_root.exists():
        return records

    for child in sorted(results_root.iterdir()):
        if not child.is_dir():
            continue
        if find_chosen_ports_workbook(child) is not None:
            records.append({"country_name": child.name, "iso3": ""})

    return records


# =============================================================================
# COUNTRY / PORT FILE DISCOVERY
# =============================================================================

def find_country_dir(results_root: Path, record: Dict[str, str]) -> Path:
    """Locate one country's result folder."""
    country_name = clean_text(record.get("country_name", ""))
    iso3 = clean_text(record.get("iso3", "")).upper()

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
            if target_country_key and (
                target_country_key in child_key or child_key in target_country_key
            ):
                partial_matches.append(child)

        if len(partial_matches) == 1:
            return partial_matches[0]

    expected = results_root / country_name
    raise FileNotFoundError(
        f"Could not find result folder for {country_name} ({iso3}). Expected: {expected}"
    )


def find_chosen_ports_workbook(country_dir: Path) -> Optional[Path]:
    """Find chosen_ports_of_top_locations Excel/CSV inside a country folder."""
    candidates: List[Path] = []
    for ext in [".xlsx", ".xlsm", ".xls", ".csv"]:
        candidates.append(country_dir / f"{CHOSEN_PORTS_FILE_STEM}{ext}")

    patterns = [
        "chosen_ports_of_top_locations.xls*",
        "chosen_ports_of_top_locations.csv",
        "*chosen*ports*top*locations*.xls*",
        "*chosen*ports*top*locations*.csv",
        "*chosen*port*.xls*",
        "*chosen*port*.csv",
    ]
    for pattern in patterns:
        candidates.extend(sorted(country_dir.glob(pattern)))

    seen = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if path.exists() and path.is_file():
            return path
    return None


def load_unique_ports_from_chosen_workbook(country_dir: Path) -> pd.DataFrame:
    """
    Return one row per unique canonical port from chosen_ports_of_top_locations.
    Includes how many top locations mapped to that port.
    """
    workbook = find_chosen_ports_workbook(country_dir)
    if workbook is None:
        raise FileNotFoundError(f"Could not find {CHOSEN_PORTS_FILE_STEM}.xlsx/csv in {country_dir}")

    df = read_first_matching_table(workbook, required_columns=[CANONICAL_PORT_COLUMN])
    port_col = pick_column(
        df,
        preferred_names=[CANONICAL_PORT_COLUMN, "canonical port", "canonical_port", "main_port", "main port", "nearest_port"],
        contains_any=["canonical", "mainport", "nearestport"],
        required=True,
        purpose="canonical main port column",
    )

    ports = pd.DataFrame({
        "canonical_main_port": df[port_col].map(clean_text),
    })
    ports = ports[ports["canonical_main_port"] != ""].copy()
    if ports.empty:
        raise ValueError(f"No nonblank ports found in column {port_col!r} from {workbook}")

    counts = (
        ports.groupby("canonical_main_port", as_index=False)
        .size()
        .rename(columns={"size": "chosen_location_count"})
        .sort_values("canonical_main_port")
    )
    counts["chosen_ports_source_file"] = str(workbook)
    return counts


def candidate_port_dirs(country_dir: Path, port_name: str) -> List[Path]:
    """Return possible directories for a port under one country folder."""
    port_clean = clean_text(port_name)
    port_safe = safe_output_name_part(port_clean)
    candidates = [
        country_dir / port_clean,
        country_dir / port_safe,
        country_dir / port_clean.replace(" ", "_"),
    ]

    target_keys = {normalize_header(port_clean), normalize_header(port_safe)}
    try:
        for child in country_dir.iterdir():
            if child.is_dir() and normalize_header(child.name) in target_keys:
                candidates.append(child)
    except Exception:
        pass

    out: List[Path] = []
    seen = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if path.exists() and path.is_dir():
            out.append(path)
    return out


def find_shipping_summary_file(country_dir: Path, port_name: str) -> Optional[Path]:
    """Find shipping_route_<port>_<DESTINATION>_summary.xlsx/csv for one port."""
    port_clean = clean_text(port_name)
    port_safe = safe_output_name_part(port_clean)
    dest_safe = safe_output_name_part(DESTINATION_PORT)

    port_dirs = candidate_port_dirs(country_dir, port_clean)
    direct_country_candidates: List[Path] = []
    for ext in [".xlsx", ".xlsm", ".xls", ".csv"]:
        direct_country_candidates.extend([
            country_dir / port_clean / f"shipping_route_{port_clean}_{DESTINATION_PORT}_summary{ext}",
            country_dir / port_safe / f"shipping_route_{port_safe}_{dest_safe}_summary{ext}",
            country_dir / f"shipping_route_{port_clean}_{DESTINATION_PORT}_summary{ext}",
            country_dir / f"shipping_route_{port_safe}_{dest_safe}_summary{ext}",
        ])

    hit = first_existing(direct_country_candidates)
    if hit is not None:
        return hit

    patterns = [
        f"shipping_route*{port_safe}*{dest_safe}*summary*.xls*",
        f"shipping_route*{port_safe}*{dest_safe}*summary*.csv",
        f"*{port_safe}*{dest_safe}*summary*.xls*",
        f"*{port_safe}*{dest_safe}*summary*.csv",
        f"*summary*.xls*",
        f"*summary*.csv",
    ]

    for port_dir in port_dirs:
        for pattern in patterns:
            hits = sorted(path for path in port_dir.glob(pattern) if path.is_file())
            if hits:
                return hits[0]

    # Recursive fallback under the country folder.
    port_key = normalize_header(port_clean)
    dest_key = normalize_header(DESTINATION_PORT)
    recursive_hits: List[Path] = []
    try:
        for path in country_dir.rglob("*summary*"):
            if not path.is_file() or path.suffix.lower() not in {".xlsx", ".xlsm", ".xls", ".csv"}:
                continue
            path_key = normalize_header(str(path.relative_to(country_dir)))
            if port_key in path_key and dest_key in path_key:
                recursive_hits.append(path)
    except Exception:
        pass

    if recursive_hits:
        return sorted(recursive_hits)[0]

    return None


# =============================================================================
# ROUTE SUMMARY LOADING AND EMISSIONS CONVERSION
# =============================================================================

def normalized_scenario(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.strip().lower()
    text = text.replace("decarbonized", "decarbonised")
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def route_summary_required_columns() -> List[str]:
    return [
        "year",
        "decarbonisation_scenario",
        "cost_eur_per_t_low",
        "cost_eur_per_t_mid",
        "cost_eur_per_t_high",
        "total_co2_t_low",
        "total_co2_t_mid",
        "total_co2_t_high",
    ]


def read_route_summary(path: Path) -> pd.DataFrame:
    """Read one shipping route summary file with tolerant column names."""
    # We only require stable exact names that are written by Shipping_route_model.py.
    try:
        return read_first_matching_table(path, required_columns=route_summary_required_columns())
    except Exception:
        # Fallback: read the first sheet/table and let pick_column explain any missing columns later.
        return read_table(path)


def infer_cargo_tonnes(row: pd.Series, columns: Dict[str, Optional[str]]) -> float:
    """Infer cargo tonnes for total_co2_t -> tCO2/tNH3 conversion."""
    # 1) Direct cargo column if present.
    for key in ["cargo_tonnes", "cargo_tons", "cargo_t", "cargo"]:
        col = columns.get(key)
        if col is not None:
            value = pd.to_numeric(row.get(col), errors="coerce")
            if pd.notna(value) and float(value) > 0:
                return float(value)

    # 2) Infer from total_co2 = ef / 1e6 * distance_nm * cargo_tonnes.
    co2_mid_col = columns.get("total_co2_t_mid")
    ef_mid_col = columns.get("ef_g_per_tnm_mid")
    distance_col = columns.get("distance_nm")

    if co2_mid_col is not None and ef_mid_col is not None and distance_col is not None:
        co2_mid = pd.to_numeric(row.get(co2_mid_col), errors="coerce")
        ef_mid = pd.to_numeric(row.get(ef_mid_col), errors="coerce")
        distance_nm = pd.to_numeric(row.get(distance_col), errors="coerce")
        denom = (float(ef_mid) / 1e6) * float(distance_nm) if pd.notna(ef_mid) and pd.notna(distance_nm) else np.nan
        if pd.notna(co2_mid) and pd.notna(denom) and denom > 0:
            cargo = float(co2_mid) / float(denom)
            if np.isfinite(cargo) and cargo > 0:
                return cargo

    # 3) Fallback.
    return float(DEFAULT_CARGO_TONNES)


def summary_column_map(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    """Map normalized logical names to actual DataFrame columns."""
    mapping = {
        "year": pick_column(df, ["year"], required=True, purpose="year column"),
        "decarbonisation_scenario": pick_column(
            df,
            ["decarbonisation_scenario", "decarbonization_scenario", "scenario"],
            contains_any=["decarbonisation", "decarbonization"],
            required=True,
            purpose="decarbonisation scenario column",
        ),
        "cost_eur_per_t_low": pick_column(df, ["cost_eur_per_t_low"], required=True, purpose="cost low column"),
        "cost_eur_per_t_mid": pick_column(df, ["cost_eur_per_t_mid"], required=True, purpose="cost mid column"),
        "cost_eur_per_t_high": pick_column(df, ["cost_eur_per_t_high"], required=True, purpose="cost high column"),
        "total_co2_t_low": pick_column(df, ["total_co2_t_low"], required=True, purpose="total CO2 low column"),
        "total_co2_t_mid": pick_column(df, ["total_co2_t_mid"], required=True, purpose="total CO2 mid column"),
        "total_co2_t_high": pick_column(df, ["total_co2_t_high"], required=True, purpose="total CO2 high column"),
        "ef_g_per_tnm_mid": pick_column(df, ["ef_g_per_tnm_mid", "emission_factor_g_per_tnm_mid"], required=False),
        "distance_nm": pick_column(df, ["distance_nm"], required=False),
        "route_scenario": pick_column(df, ["route_scenario", "scenario_name"], required=False),
        "cargo_tonnes": pick_column(df, ["cargo_tonnes", "cargo tonnes", "cargo_tons", "cargo_t", "cargo"], required=False),
    }
    return mapping


def convert_eur_to_usd(value: object) -> float:
    """Convert a numeric EUR value to USD using EUR_TO_USD_RATE."""
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.notna(numeric) and np.isfinite(float(numeric)):
        return float(numeric) * float(EUR_TO_USD_RATE)
    return np.nan


def values_from_low_mid_high(low: float, mid: float, high: float) -> List[float]:
    values = []
    mode = str(DISTRIBUTION_VALUE_SOURCE).strip().lower()
    if mode == "mid_only":
        candidates = [mid]
    elif mode == "low_mid_high":
        candidates = [low, mid, high]
    else:
        raise ValueError("DISTRIBUTION_VALUE_SOURCE must be 'low_mid_high' or 'mid_only'.")

    for value in candidates:
        value = pd.to_numeric(value, errors="coerce")
        if pd.notna(value) and np.isfinite(float(value)):
            values.append(float(value))
    return values


def extract_port_year_records(
    country_record: Dict[str, str],
    country_order: int,
    port_name: str,
    chosen_location_count: int,
    summary_path: Path,
) -> List[Dict[str, object]]:
    """Extract BAU 2030/2040 cost and emissions values from one route summary."""
    df = read_route_summary(summary_path)
    cols = summary_column_map(df)

    year_col = cols["year"]
    scen_col = cols["decarbonisation_scenario"]

    work = df.copy()
    work["__year"] = pd.to_numeric(work[year_col], errors="coerce").astype("Int64")
    work["__scenario"] = work[scen_col].map(normalized_scenario)

    target_scenario = normalized_scenario(DECARB_SCENARIO)
    work = work[
        work["__year"].isin([int(y) for y in YEARS_TO_PLOT])
        & (work["__scenario"] == target_scenario)
    ].copy()

    records: List[Dict[str, object]] = []

    for _, row in work.iterrows():
        year = int(row["__year"])
        cargo_tonnes = infer_cargo_tonnes(row, cols)

        cost_low_eur = pd.to_numeric(row.get(cols["cost_eur_per_t_low"]), errors="coerce")
        cost_mid_eur = pd.to_numeric(row.get(cols["cost_eur_per_t_mid"]), errors="coerce")
        cost_high_eur = pd.to_numeric(row.get(cols["cost_eur_per_t_high"]), errors="coerce")

        cost_low_usd = convert_eur_to_usd(cost_low_eur)
        cost_mid_usd = convert_eur_to_usd(cost_mid_eur)
        cost_high_usd = convert_eur_to_usd(cost_high_eur)

        co2_low = pd.to_numeric(row.get(cols["total_co2_t_low"]), errors="coerce")
        co2_mid = pd.to_numeric(row.get(cols["total_co2_t_mid"]), errors="coerce")
        co2_high = pd.to_numeric(row.get(cols["total_co2_t_high"]), errors="coerce")

        emissions_low = float(co2_low) / cargo_tonnes if pd.notna(co2_low) and cargo_tonnes > 0 else np.nan
        emissions_mid = float(co2_mid) / cargo_tonnes if pd.notna(co2_mid) and cargo_tonnes > 0 else np.nan
        emissions_high = float(co2_high) / cargo_tonnes if pd.notna(co2_high) and cargo_tonnes > 0 else np.nan

        route_scenario = ""
        if cols.get("route_scenario") is not None:
            route_scenario = clean_text(row.get(cols["route_scenario"], ""))

        common = {
            "country": clean_text(country_record.get("country_name", "")),
            "iso3": clean_text(country_record.get("iso3", "")).upper(),
            "country_order": country_order,
            "canonical_main_port": port_name,
            "chosen_location_count_for_port": chosen_location_count,
            "year": year,
            "decarbonisation_scenario": DECARB_SCENARIO,
            "route_scenario": route_scenario,
            "summary_file": str(summary_path),
            "cargo_tonnes_used": cargo_tonnes,
            "eur_to_usd_rate_used": EUR_TO_USD_RATE,
        }

        records.append({
            **common,
            "metric": "shipping_cost_usd_per_t",
            "value_low": cost_low_usd,
            "value_mid": cost_mid_usd,
            "value_high": cost_high_usd,
            "unit": "USD/t NH3",
        })

        records.append({
            **common,
            "metric": "shipping_emissions_tco2_per_tnh3",
            "value_low": emissions_low,
            "value_mid": emissions_mid,
            "value_high": emissions_high,
            "unit": "tCO2/tNH3",
        })

    return records


def load_all_port_level_values(records: Sequence[Dict[str, str]]) -> Tuple[pd.DataFrame, List[str]]:
    """Load all country/port route-summary values."""
    output_records: List[Dict[str, object]] = []
    warnings: List[str] = []

    for country_order, record in enumerate(records):
        country = clean_text(record.get("country_name", ""))
        iso3 = clean_text(record.get("iso3", "")).upper()
        label = country_label(country, iso3)

        try:
            country_dir = find_country_dir(RESULTS_ROOT, record)
            ports_df = load_unique_ports_from_chosen_workbook(country_dir)
        except Exception as exc:
            warnings.append(f"{label}: could not load chosen ports: {exc}")
            continue

        if PRINT_PROGRESS:
            print(f"  {label}: {len(ports_df)} unique port(s) from {country_dir.name}")

        for _, port_row in ports_df.iterrows():
            port = clean_text(port_row["canonical_main_port"])
            count = int(port_row.get("chosen_location_count", 0))

            summary_path = find_shipping_summary_file(country_dir, port)
            if summary_path is None:
                warnings.append(
                    f"{label} | {port}: could not find shipping_route_{port}_{DESTINATION_PORT}_summary.xlsx/csv"
                )
                continue

            try:
                port_records = extract_port_year_records(
                    country_record=record,
                    country_order=country_order,
                    port_name=port,
                    chosen_location_count=count,
                    summary_path=summary_path,
                )
            except Exception as exc:
                warnings.append(f"{label} | {port}: could not read summary {summary_path}: {exc}")
                continue

            if not port_records:
                warnings.append(
                    f"{label} | {port}: no rows for year(s) {YEARS_TO_PLOT} and scenario {DECARB_SCENARIO!r}"
                )
                continue

            output_records.extend(port_records)

    if not output_records:
        raise ValueError("No valid shipping route values were loaded. Check paths, port names, and summary files.")

    return pd.DataFrame(output_records), warnings


# =============================================================================
# QUANTILE SUMMARY
# =============================================================================

def expand_values(row: pd.Series) -> List[float]:
    return values_from_low_mid_high(row.get("value_low"), row.get("value_mid"), row.get("value_high"))


def quantile_summary(values: Sequence[float]) -> Dict[str, float]:
    arr = np.array([float(v) for v in values if pd.notna(v) and np.isfinite(float(v))], dtype=float)
    if arr.size == 0:
        return {
            "whisker_low": np.nan,
            "q25": np.nan,
            "q50": np.nan,
            "q75": np.nan,
            "whisker_high": np.nan,
            "n_values": 0,
        }
    return {
        "whisker_low": float(np.min(arr)),
        "q25": float(np.quantile(arr, 0.25)),
        "q50": float(np.quantile(arr, 0.50)),
        "q75": float(np.quantile(arr, 0.75)),
        "whisker_high": float(np.max(arr)),
        "n_values": int(arr.size),
    }


def build_country_quantile_summary(port_values: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, object]] = []

    group_cols = ["country", "iso3", "country_order", "year", "metric", "unit"]
    for keys, group in port_values.groupby(group_cols, dropna=False):
        country, iso3, country_order, year, metric, unit = keys

        values: List[float] = []
        for _, row in group.iterrows():
            values.extend(expand_values(row))

        qs = quantile_summary(values)
        unique_ports = group["canonical_main_port"].dropna().astype(str).nunique()
        chosen_location_count_total = pd.to_numeric(
            group.drop_duplicates("canonical_main_port")["chosen_location_count_for_port"],
            errors="coerce",
        ).sum()

        rows.append({
            "country": country,
            "iso3": iso3,
            "country_order": int(country_order),
            "year": int(year),
            "metric": metric,
            "unit": unit,
            "unique_ports": int(unique_ports),
            "chosen_location_count_total": int(chosen_location_count_total) if pd.notna(chosen_location_count_total) else 0,
            **qs,
        })

    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    return summary.sort_values(["country_order", "year", "metric"]).reset_index(drop=True)


def ordered_country_keys(summary: pd.DataFrame) -> List[Tuple[str, str]]:
    """
    Return ordered list of (country, iso3) pairs.

    For horizontal charts, y=0 is later inverted so the first item appears at
    the top. Therefore, to show lowest q50 at the bottom and highest q50 at the
    top, we sort the ranking values in descending order.
    """
    countries = summary[["country", "iso3", "country_order"]].drop_duplicates().copy()
    mode = str(COUNTRY_SORT_MODE).strip().lower()

    if mode == "country_list":
        countries = countries.sort_values("country_order")
    elif mode in {"shipping_cost_2030", "emissions_2030"}:
        metric = (
            "shipping_cost_usd_per_t"
            if mode == "shipping_cost_2030"
            else "shipping_emissions_tco2_per_tnh3"
        )
        ranking = summary[
            (summary["year"].astype(int) == 2030)
            & (summary["metric"].astype(str) == metric)
        ][["country", "iso3", "q50"]].copy()

        countries = countries.merge(ranking, on=["country", "iso3"], how="left")

        # Matplotlib's y-axis is inverted later in plot_combined_horizontal_bars().
        # If the user wants lowest-at-bottom, we put the highest q50 first so it
        # appears at the top after inversion.
        ascending = not bool(COUNTRY_SORT_LOWEST_AT_BOTTOM)
        countries = countries.sort_values(
            ["q50", "country_order"],
            ascending=[ascending, True],
            na_position="last",
        )
    else:
        raise ValueError(
            "COUNTRY_SORT_MODE must be 'country_list', 'shipping_cost_2030', or 'emissions_2030'."
        )

    return [(str(row["country"]), str(row["iso3"])) for _, row in countries.iterrows()]


# =============================================================================
# PLOTTING
# =============================================================================

def get_stat_row(summary: pd.DataFrame, country: str, iso3: str, year: int, metric: str) -> Optional[pd.Series]:
    sub = summary[
        (summary["country"].astype(str) == str(country))
        & (summary["iso3"].astype(str) == str(iso3))
        & (summary["year"].astype(int) == int(year))
        & (summary["metric"].astype(str) == metric)
    ]
    if sub.empty:
        return None
    return sub.iloc[0]


def draw_horizontal_quantile_bar(
    ax,
    y: float,
    stats: pd.Series,
    color,
    height: float = BAR_HEIGHT,
    label: Optional[str] = None,
) -> None:
    """Draw one horizontal q25/q50/q75 box with min/max whiskers."""
    x_min = float(stats["whisker_low"])
    x_q25 = float(stats["q25"])
    x_q50 = float(stats["q50"])
    x_q75 = float(stats["q75"])
    x_max = float(stats["whisker_high"])

    if not all(np.isfinite(v) for v in [x_min, x_q25, x_q50, x_q75, x_max]):
        return

    cap_h = height * CAP_HEIGHT_FRACTION

    # Whisker line.
    ax.plot(
        [x_min, x_max],
        [y, y],
        color=color,
        linewidth=WHISKER_LINEWIDTH,
        alpha=WHISKER_ALPHA,
        zorder=2,
    )
    ax.plot([x_min, x_min], [y - cap_h / 2.0, y + cap_h / 2.0], color=color, linewidth=WHISKER_LINEWIDTH, zorder=2)
    ax.plot([x_max, x_max], [y - cap_h / 2.0, y + cap_h / 2.0], color=color, linewidth=WHISKER_LINEWIDTH, zorder=2)

    # q25-q75 box.
    box_left = min(x_q25, x_q75)
    box_width = abs(x_q75 - x_q25)
    if box_width <= 1e-12:
        # Degenerate box: draw a thick line instead.
        ax.plot([x_q25, x_q75], [y, y], color=color, linewidth=BOX_LINEWIDTH + 2.0, alpha=YEAR_ALPHA, zorder=3)
    else:
        rect = Rectangle(
            (box_left, y - height / 2.0),
            box_width,
            height,
            facecolor=color,
            edgecolor=color,
            linewidth=BOX_LINEWIDTH,
            alpha=YEAR_ALPHA,
            zorder=3,
            label=label,
        )
        ax.add_patch(rect)

    # Median.
    ax.plot(
        [x_q50, x_q50],
        [y - height / 2.0, y + height / 2.0],
        color="black",
        linewidth=MEDIAN_LINEWIDTH,
        alpha=0.85,
        zorder=4,
    )


def metric_title(metric: str) -> str:
    if metric == "shipping_cost_usd_per_t":
        return "Shipping cost"
    if metric == "shipping_emissions_tco2_per_tnh3":
        return "Shipping emissions"
    return metric


def metric_axis_label(metric: str) -> str:
    if metric == "shipping_cost_usd_per_t":
        return COST_X_AXIS_LABEL
    if metric == "shipping_emissions_tco2_per_tnh3":
        return EMISSIONS_X_AXIS_LABEL
    return metric


def apply_axis_limits(ax, metric: str) -> None:
    if metric == "shipping_cost_usd_per_t":
        xmin = COST_X_AXIS_MIN
        xmax = COST_X_AXIS_MAX
    else:
        xmin = EMISSIONS_X_AXIS_MIN
        xmax = EMISSIONS_X_AXIS_MAX

    current_xmin, current_xmax = ax.get_xlim()
    if xmin is None:
        xmin = current_xmin
    if xmax is None:
        xmax = current_xmax
    if xmin >= xmax:
        raise ValueError(f"Invalid axis limits for {metric}: min {xmin} must be below max {xmax}.")
    ax.set_xlim(xmin, xmax)


def country_display_labels(summary: pd.DataFrame, country_keys: List[Tuple[str, str]]) -> List[str]:
    """Build y-axis country labels without ISO3 unless explicitly requested."""
    labels = []

    for country, iso3 in country_keys:
        if SHOW_ISO3_IN_AXIS_LABELS:
            base = country_label(country, iso3)
        else:
            base = str(country)

        if SHOW_PORT_COUNTS_IN_LABELS:
            sub = summary[
                (summary["country"].astype(str) == str(country))
                & (summary["iso3"].astype(str) == str(iso3))
            ]
            n_ports = int(sub["unique_ports"].max()) if not sub.empty else 0
            port_word = "port" if n_ports == 1 else "ports"
            base = f"{base}  ({n_ports} {port_word} avg.)"

        labels.append(base)

    return labels



def plot_combined_horizontal_bars(summary: pd.DataFrame, output_png: Path) -> None:
    metrics = ["shipping_cost_usd_per_t", "shipping_emissions_tco2_per_tnh3"]
    country_keys = ordered_country_keys(summary)
    if not country_keys:
        raise ValueError("No countries available for plotting.")

    n_countries = len(country_keys)
    fig_height = max(BASE_FIGURE_HEIGHT, 1.8 + HEIGHT_PER_COUNTRY * n_countries)

    fig, axes = plt.subplots(
        nrows=1,
        ncols=2,
        figsize=(FIGURE_WIDTH, fig_height),
        sharey=True,
    )

    if not isinstance(axes, np.ndarray):
        axes = np.array([axes])

    y_base = np.arange(n_countries, dtype=float)
    year_offsets = {}
    if len(YEARS_TO_PLOT) == 1:
        year_offsets[int(YEARS_TO_PLOT[0])] = 0.0
    else:
        # Two years by default: first slightly above, second slightly below.
        for i, year in enumerate(YEARS_TO_PLOT):
            centered = i - (len(YEARS_TO_PLOT) - 1) / 2.0
            year_offsets[int(year)] = centered * (2.0 * YEAR_OFFSET)

    for ax, metric in zip(axes, metrics):
        for country_idx, (country, iso3) in enumerate(country_keys):
            for year in YEARS_TO_PLOT:
                stats = get_stat_row(summary, country, iso3, int(year), metric)
                if stats is None:
                    continue
                y = y_base[country_idx] + year_offsets[int(year)]
                color = YEAR_COLORS.get(int(year), None)
                draw_horizontal_quantile_bar(ax, y, stats, color=color, height=BAR_HEIGHT)

        ax.set_title(metric_title(metric))
        ax.set_xlabel(metric_axis_label(metric))
        ax.grid(True, axis="x", alpha=GRID_ALPHA)
        ax.set_axisbelow(True)
        apply_axis_limits(ax, metric)

    labels = country_display_labels(summary, country_keys)
    axes[0].set_yticks(y_base)
    axes[0].set_yticklabels(labels)
    axes[0].invert_yaxis()
    axes[0].set_ylabel("Country")

    # Explain the visual encoding in a small note.
    note = (
        "Box = q25–q75; black line = q50; whiskers = min–max across unique country ports "
        f"using {DISTRIBUTION_VALUE_SOURCE.replace('_', '/')} values. "
        f"Scenario: {DECARB_SCENARIO.upper()}."
    )
    fig.suptitle(FIGURE_TITLE, y=0.995, fontsize=13)
    fig.text(0.5, 0.015, note, ha="center", va="bottom", fontsize=8.5)

    if SHOW_LEGEND:
        handles = []
        for year in YEARS_TO_PLOT:
            color = YEAR_COLORS.get(int(year), None)
            handles.append(Patch(facecolor=color, edgecolor=color, alpha=YEAR_ALPHA, label=str(year)))
        handles.append(Line2D([0], [0], color="black", lw=MEDIAN_LINEWIDTH, label="q50"))
        fig.legend(
            handles=handles,
            loc=LEGEND_LOCATION,
            bbox_to_anchor=LEGEND_BBOX_TO_ANCHOR,
            ncol=LEGEND_NCOL + 1,
            frameon=True,
            fontsize=9,
        )

    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, 0.04, 1, 0.97))
    fig.savefig(output_png, dpi=OUTPUT_DPI, bbox_inches="tight")
    plt.close(fig)


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    print("Project directory:", PROJECT_DIR)
    print("Results root:", RESULTS_ROOT)
    print("Output PNG:", OUTPUT_PNG)
    print("Years:", YEARS_TO_PLOT)
    print("Scenario:", DECARB_SCENARIO)
    print("Distribution value source:", DISTRIBUTION_VALUE_SOURCE)
    print(f"Shipping cost conversion: 1 EUR = {EUR_TO_USD_RATE:.4f} USD")

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
        print("No country list found. Discovering country folders with chosen_ports_of_top_locations.")
        records = discover_country_records_from_results(RESULTS_ROOT)

    if not records:
        raise ValueError("No considered countries were found.")

    print(f"Considered countries: {len(records)}")
    print("Reading chosen ports and shipping summaries from:")
    print(f"  {RESULTS_ROOT}\\<Country name>\\{CHOSEN_PORTS_FILE_STEM}.xlsx")
    print(f"  {RESULTS_ROOT}\\<Country name>\\<canonical_main_port>\\shipping_route_<canonical_main_port>_{DESTINATION_PORT}_summary.xlsx")

    port_values, warnings = load_all_port_level_values(records)

    if warnings:
        print("\nWARNING: skipped/missing items:")
        for item in warnings:
            print("  - " + item)

    if port_values.empty:
        raise ValueError("No port-level route values were loaded.")

    summary = build_country_quantile_summary(port_values)
    if summary.empty:
        raise ValueError("No country quantile summary could be built.")

    print("\nLoaded port-level rows:", len(port_values))
    print("Country/year/metric summary rows:", len(summary))
    print("Countries with data:", summary[["country", "iso3"]].drop_duplicates().shape[0])

    print("\nSummary preview:")
    preview_cols = [
        "country", "iso3", "year", "metric", "unique_ports",
        "whisker_low", "q25", "q50", "q75", "whisker_high",
    ]
    print(summary[preview_cols].head(40).to_string(index=False))

    if SAVE_PORT_VALUES_CSV:
        OUTPUT_PORT_VALUES_CSV.parent.mkdir(parents=True, exist_ok=True)
        port_values.to_csv(OUTPUT_PORT_VALUES_CSV, index=False)
        print(f"\nSaved port-level values CSV:\n{OUTPUT_PORT_VALUES_CSV}")

    if SAVE_COUNTRY_SUMMARY_CSV:
        OUTPUT_COUNTRY_SUMMARY_CSV.parent.mkdir(parents=True, exist_ok=True)
        summary.to_csv(OUTPUT_COUNTRY_SUMMARY_CSV, index=False)
        print(f"\nSaved country quantile summary CSV:\n{OUTPUT_COUNTRY_SUMMARY_CSV}")

    plot_combined_horizontal_bars(summary, OUTPUT_PNG)
    print(f"\nSaved combined horizontal bar figure:\n{OUTPUT_PNG}")


if __name__ == "__main__":
    main()
