#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Create one country-level dashboard PNG per selected country.

Dashboard layout:
    - country name at top left
    - wide main insight table across the upper part of the page
    - country map in the lower part of the page, with selected renewable locations and export ports
    - cost-breakdown stacked bars beside the map

The script follows the same project/country-list conventions as the other
AP_IRA_Model plotting scripts:

    Save this file in:
        C:\Users\user\Documents\Project\AP_IRA_Model
    then run it in Spyder.

Country-list workbook:
    - File name: List of Countries.xlsx, List of Countries.xlsm,
      List of Countries.xls, or List of Countries.csv
    - Column A: country name / folder name, e.g. Australia, Chile, Egypt
    - Column C: ISO3, with heading ISO3, e.g. AUS, CHL, EGY

For each selected country, the script reads:
    <PROJECT_DIR>\trade_version_results\<Country>\npv results\v14_DATASET_hourly_highgas.xlsx
    <PROJECT_DIR>\trade_version_results\<Country>\chosen_ports_of_top_locations.xlsx
    <PROJECT_DIR>\trade_version_results\<Country>\<selected_port>\shipping_route_<port>_Rotterdam_summary.xlsx/csv

Outputs are saved in:
    <PROJECT_DIR>\country_dashboard_cards

Main dashboard PNGs:
    country_dashboard_<country>.png

CSV outputs:
    country_dashboard_master_summary.csv
    country_dashboard_cost_breakdown.csv
    country_dashboard_map_points.csv
    country_dashboard_warnings.txt

Notes:
    - q50 NPV rank is computed against all countries loaded in this run, separately
      for each pathway and year. Rank 1 = highest q50 NPV.
    - Cost shares use only negative mean_effect_on_NPV components from the
      NPV_effects_average sheet.
"""

from __future__ import annotations

import math
import os
import re
import textwrap
import unicodedata
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch, Patch, Rectangle

try:
    import geopandas as gpd
except Exception:  # pragma: no cover - lets the script still export non-map tables if geopandas is unavailable
    gpd = None

try:
    from shapely.geometry import Point
    from shapely.ops import nearest_points
except Exception:  # pragma: no cover
    Point = None
    nearest_points = None


# =============================================================================
# USER SETTINGS
# =============================================================================

# Default: folder containing this script. In Spyder, save this script in your
# project folder. If needed, replace this with an absolute path, for example:
# PROJECT_DIR = Path(r"C:\Users\user\Documents\Project\AP_IRA_Model")
try:
    _SCRIPT_DIR = Path(__file__).resolve().parent
except NameError:  # Spyder interactive fallback
    _SCRIPT_DIR = Path.cwd().resolve()

PROJECT_DIR = Path(os.environ.get("AP_IRA_PROJECT_DIR", str(_SCRIPT_DIR.parent))).resolve()
RESULTS_ROOT = PROJECT_DIR / "trade_version_results"
COUNTRY_LIST_PATH: Optional[Path] = PROJECT_DIR / "List of Countries"

OUTPUT_DIR = PROJECT_DIR / "country_dashboard_cards_eligible_only"
OUTPUT_DPI = 600
OUTPUT_PNG_STEM = "country_dashboard_eligible_only"
OUTPUT_MASTER_SUMMARY_CSV = OUTPUT_DIR / "country_dashboard_master_summary_eligible_only.csv"
OUTPUT_COST_BREAKDOWN_CSV = OUTPUT_DIR / "country_dashboard_cost_breakdown_eligible_only.csv"
OUTPUT_MAP_POINTS_CSV = OUTPUT_DIR / "country_dashboard_map_points_eligible_only.csv"
OUTPUT_WARNINGS_TXT = OUTPUT_DIR / "country_dashboard_warnings_eligible_only.txt"

# NPV/CI workbook settings.
NPV_RESULTS_FOLDER = "npv results"
NPV_WORKBOOK_STEM = "v14_DATASET_hourly_highgas"
EFFECTS_SHEET = "NPV_effects_average"
DESTINATION_PORT = "Rotterdam"

# Plotted year -> workbook time value.
YEAR_TO_TIME = {
    2030: 2027,
    2040: 2040,
}
YEARS_TO_PLOT = [2030, 2040]
SCENARIO_FILTER: Optional[str] = "New"
SIMULATION_MIN = 0
SIMULATION_MAX = 3999

# Shipping route scenario.
SHIPPING_DECARB_SCENARIO = "bau"
ALLOW_LOCATION_SHEET_SHIPPING_FALLBACK = True

# Carbon-intensity basis conversion.
# CI_same_year is interpreted as kg CO2/kg H2. Shipping is first computed as
# kg CO2/kg NH3, then converted to kg CO2/kg H2-equivalent using stoichiometry:
#     N2 + 3 H2 -> 2 NH3
MOLECULAR_WEIGHT_NH3_G_PER_MOL = 17.03052
MOLECULAR_WEIGHT_H2_G_PER_MOL = 2.01588
NH3_KG_PER_KG_H2_EQ = (2 * MOLECULAR_WEIGHT_NH3_G_PER_MOL) / (3 * MOLECULAR_WEIGHT_H2_G_PER_MOL)

EU_LOW_CARBON_LIMIT_KGCO2_PER_KGH2EQ = 3.38
EU_LOW_CARBON_LIMIT_DISPLAY_TEXT = f"{EU_LOW_CARBON_LIMIT_KGCO2_PER_KGH2EQ:.2f} kgCO₂/kgH₂"

# Pathways shown in the dashboard.
PATHWAYS = [
    {
        "key": "gray",
        "short_label": "Gray",
        "label": "Gray ammonia",
        "technology": "AP SMR",
        "npv_col": "AP_SMR_NPV",
        "ci_col": "AP SMR_CI",
        "color": "#666666",
    },
    {
        "key": "blue",
        "short_label": "Blue",
        "label": "Blue ammonia",
        "technology": "AP CCS",
        "npv_col": "AP_CCS_NPV",
        "ci_col": "AP CCS_CI",
        "color": "#1F77B4",
    },
    {
        "key": "green",
        "short_label": "Green",
        "label": "Green ammonia",
        "technology": "AP AEC",
        "npv_col": "AP_AEC_NPV",
        "ci_col": "AP AEC_CI",
        "color": "#39A65A",
    },
]
PATHWAY_ORDER = [p["key"] for p in PATHWAYS]
TECHNOLOGY_ORDER = [p["technology"] for p in PATHWAYS]
PATHWAY_BY_KEY = {p["key"]: p for p in PATHWAYS}
PATHWAY_BY_TECHNOLOGY = {p["technology"]: p for p in PATHWAYS}

# =============================================================================
# LITERATURE-BASED FEASIBILITY SCREEN
# =============================================================================
# This screen controls what is shown in the headline dashboard results. Screened-out
# country-pathway combinations are removed from dashboard tables, charts, and
# screened output CSVs. The workbook input data are still read normally.
APPLY_FEASIBILITY_SCREEN = True
DROP_SCREENED_OUT_FROM_OUTPUTS = True

# Turkey is intentionally allowed for blue ammonia here, per the latest decision.
FEASIBILITY_SCREEN_ELIGIBLE_ISO3 = {
    "gray": {"AUS", "EGY", "NOR", "OMN", "SAU", "TUR", "ARE", "USA"},
    "blue": {"AUS", "EGY", "NOR", "OMN", "SAU", "TUR", "ARE", "USA"},
    "green": "ALL",
}

COUNTRY_ALIAS_TO_ISO3 = {
    "australia": "AUS",
    "aus": "AUS",
    "chile": "CHL",
    "chl": "CHL",
    "egypt": "EGY",
    "egy": "EGY",
    "morocco": "MAR",
    "mar": "MAR",
    "namibia": "NAM",
    "nam": "NAM",
    "norway": "NOR",
    "nor": "NOR",
    "oman": "OMN",
    "omn": "OMN",
    "saudiarabia": "SAU",
    "saudi": "SAU",
    "sau": "SAU",
    "southafrica": "ZAF",
    "zaf": "ZAF",
    "turkey": "TUR",
    "turkiye": "TUR",
    "tur": "TUR",
    "unitedarabemirates": "ARE",
    "uae": "ARE",
    "are": "ARE",
    "unitedkingdom": "GBR",
    "greatbritain": "GBR",
    "uk": "GBR",
    "gbr": "GBR",
    "unitedstatesofamerica": "USA",
    "unitedstates": "USA",
    "usa": "USA",
    "us": "USA",
}


# Rank setting. Higher q50 NPV is better.
RANK_HIGHER_NPV_IS_BETTER = True

# Map input and display settings.
PORTS_WORKBOOK_NAME = "chosen_ports_of_top_locations.xlsx"
PREFER_OPTIMISATION_CSV = True
OPTIMISATION_CSV_NAME = "ammonia_delivered_costs.csv"
TOP_LOCATIONS_PER_COUNTRY: Optional[int] = 10
SNAP_LOCATIONS_TO_COUNTRY_FOR_PLOT = True
SNAP_PORTS_TO_COUNTRY_BOUNDARY_FOR_PLOT = True
MAINLAND_ONLY = True

# Figure sizing, layout, and typography.
# -----------------------------------------------------------------------------
# QUICK STYLE CONTROLS: edit this block for fast global font/layout changes.
# -----------------------------------------------------------------------------
FONT_FAMILY = "DejaVu Sans"
FONT_SCALE = 1.00

FIGURE_SIZE = (17.0, 10.0)
PAGE_BG = "#FFFFFF"
CARD_BG = "#FFFFFF"
CARD_EDGE = "#D7DEE8"
DARK = "#0B1F33"
MID_TEXT = "#566578"
GRID = "#D6DCE5"
HEADER_BG = "#0B1F33"
HEADER_TEXT = "#FFFFFF"
SUBHEADER_BG = "#F2F5F9"
NEGATIVE_VALUE_COLOR = "#C7352C"
POSITIVE_STATUS_COLOR = "#1B7F3A"
FAILED_STATUS_COLOR = "#B23A2D"

TITLE_FONTSIZE = 20 * FONT_SCALE
SUBTITLE_FONTSIZE = 15 * FONT_SCALE
SECTION_TITLE_FONTSIZE = 15 * FONT_SCALE
TABLE_HEADER_FONTSIZE = 12.0 * FONT_SCALE
TABLE_SUBHEADER_FONTSIZE = 10.0 * FONT_SCALE
TABLE_BODY_FONTSIZE = 9.5 * FONT_SCALE
TABLE_SMALL_FONTSIZE = 9.5 * FONT_SCALE
TABLE_TINY_FONTSIZE = 9.5 * FONT_SCALE
LEGEND_FONTSIZE = 9.0 * FONT_SCALE
AXIS_FONTSIZE = 9.0 * FONT_SCALE

# Main layout positions in figure coordinates.
# The insights table is intentionally wider and vertically tighter.
# Bottom row split: 30% map + 70% cost-breakdown card.
LEFT_COL_X = 0.045
PAGE_CONTENT_W = 0.920
BOTTOM_CARD_Y = 0.085
BOTTOM_CARD_H = 0.365
BOTTOM_CARD_GAP = 0.030
BOTTOM_MAP_SHARE = 0.30
BOTTOM_COST_SHARE = 0.70
BOTTOM_AVAILABLE_W = PAGE_CONTENT_W - BOTTOM_CARD_GAP
BOTTOM_MAP_W = BOTTOM_AVAILABLE_W * BOTTOM_MAP_SHARE
BOTTOM_COST_W = BOTTOM_AVAILABLE_W * BOTTOM_COST_SHARE

TABLE_RECT = (LEFT_COL_X, 0.500, PAGE_CONTENT_W, 0.390)
MAP_RECT = (LEFT_COL_X, BOTTOM_CARD_Y, BOTTOM_MAP_W, BOTTOM_CARD_H)
COST_RECT = (LEFT_COL_X + BOTTOM_MAP_W + BOTTOM_CARD_GAP, BOTTOM_CARD_Y, BOTTOM_COST_W, BOTTOM_CARD_H)

# Bottom legend controls.
# -----------------------------------------------------------------------------
# MAP LEGEND IMPORTANT:
# The map legend is deliberately anchored to the fixed MAP CARD coordinates,
# not to the country map plotting axes. This means the legend stays in exactly
# the same visual location for every country, even when country shape/aspect
# changes the inner map axes.
#
# Coordinates in MAP_LEGEND_BBOX_TO_ANCHOR are card-relative axes coordinates:
#   (0, 0) = bottom-left of the map card
#   (1, 1) = top-right of the map card
# Examples:
#   MAP_LEGEND_LOCATION = "lower left"
#   MAP_LEGEND_BBOX_TO_ANCHOR = (0.04, 0.06)
#   MAP_LEGEND_LOCATION = "upper right"
#   MAP_LEGEND_BBOX_TO_ANCHOR = (0.96, 0.94)
# Set MAP_LEGEND_BBOX_TO_ANCHOR = None to use only MAP_LEGEND_LOCATION on the card.
MAP_LEGEND_LOCATION = "lower left"
MAP_LEGEND_BBOX_TO_ANCHOR = (0.02, 0.02)
MAP_LEGEND_NCOL = 3
MAP_LEGEND_FRAMEON = False

# COST legend is drawn on the cost card axis.
# Examples:
#   COST_LEGEND_LOCATION = "lower left"
#   COST_LEGEND_BBOX_TO_ANCHOR = (0.02, 0.03)
#   COST_LEGEND_LOCATION = "upper center"
#   COST_LEGEND_BBOX_TO_ANCHOR = (0.50, 0.98)
COST_LEGEND_LOCATION = "lower center"
COST_LEGEND_BBOX_TO_ANCHOR = None
COST_LEGEND_NCOL = 3
COST_LEGEND_FRAMEON = False

# Cost chart settings.
COST_GROUP_ORDER = [
    "Capital & financing",
    "Operations & maintenance",
    "Energy & feedstocks",
    "Logistics & shipping",
    "General tax",
    "Carbon costs",
    "Other",
]
COST_GROUP_COLORS = {
    "Capital & financing": "#0B2B4C",
    "Operations & maintenance": "#1F78C1",
    "Energy & feedstocks": "#A6CEE3",
    "Logistics & shipping": "#3AAE9F",
    "General tax": "#F0A51A",
    "Carbon costs": "#4A4A4A",
    "Other": "#B7B7B7",
}
MIN_SEGMENT_LABEL_PCT = 5.0

# If True, the dashboard writes a small note under every figure saying that
# ranks are based on the countries loaded in this run.
SHOW_RANK_NOTE = True

# Warning/progress controls.
PRINT_PROGRESS = True
PRINT_WARNINGS = True
MAX_WARNINGS_TO_PRINT = 300

plt.rcParams.update({"font.family": FONT_FAMILY})


# =============================================================================
# PARAMETER GROUPING AND SHORT NAMES
# =============================================================================

PARAMETER_GROUPS: Dict[str, str] = {
    "CAPEX": "Capital & financing",
    "Financing": "Capital & financing",
    "Replacement costs": "Capital & financing",
    "Plant-based OPEX": "Operations & maintenance",
    "Wind and solar O&M": "Operations & maintenance",
    "Battery fixed O&M": "Operations & maintenance",
    "Battery variable O&M": "Operations & maintenance",
    "Distribution and R&D markup": "Operations & maintenance",
    "Feedstock price": "Energy & feedstocks",
    "Electricity cost": "Energy & feedstocks",
    "Transport to port": "Logistics & shipping",
    "Shipping cost": "Logistics & shipping",
    "CCS transport and storage": "Logistics & shipping",
    "Tax": "General tax",
    "Process carbon price": "Carbon costs",
    "Shipping carbon cost": "Carbon costs",
    "Embedded natural-gas carbon cost": "Carbon costs",
    "CBAM / border adjustment": "Carbon costs",
    "Tax credits and subsidies": "Other",
}

CARBON_DRIVER_PARAMETERS = {
    "supply_chain": "Embedded natural-gas carbon cost",
    "process": "Process carbon price",
    "shipping": "Shipping carbon cost",
}

PARAMETER_SHORT_NAMES = {
    "CAPEX": "CAPEX",
    "Financing": "Financing",
    "Replacement costs": "Replacement costs",
    "Plant-based OPEX": "Plant OPEX",
    "Wind and solar O&M": "Wind & solar O&M",
    "Battery fixed O&M": "Battery fixed O&M",
    "Battery variable O&M": "Battery variable O&M",
    "Distribution and R&D markup": "Distribution/R&D",
    "Feedstock price": "Feedstock",
    "Electricity cost": "Electricity",
    "Transport to port": "Transport to port",
    "Shipping cost": "Shipping cost",
    "CCS transport and storage": "CCS transport/storage",
    "Tax": "Tax",
    "Process carbon price": "Process carbon",
    "Shipping carbon cost": "Shipping carbon",
    "Embedded natural-gas carbon cost": "Supply-chain carbon",
    "CBAM / border adjustment": "CBAM",
    "Tax credits and subsidies": "Policy/subsidy",
}


# =============================================================================
# TEXT / PATH HELPERS
# =============================================================================


def normalize_header(value: object) -> str:
    """Normalize a text/header/folder name for robust matching."""
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


def safe_output_name_part(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text)
    text = text.strip("._ ")
    return text or "unnamed"


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


def read_first_matching_table(path: Path, required_columns: Sequence[str]) -> pd.DataFrame:
    """Read a CSV/Excel file, trying sheets/header rows until required columns exist."""
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


def columns_matching(df: pd.DataFrame, preferred_names: Sequence[str]) -> List[str]:
    normalized_to_original = {normalize_header(col): col for col in df.columns}
    hits: List[str] = []
    for name in preferred_names:
        hit = normalized_to_original.get(normalize_header(name))
        if hit is not None and hit not in hits:
            hits.append(hit)
    return hits


def first_non_null_series(df: pd.DataFrame, columns: Sequence[str]) -> pd.Series:
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


def normalized_scenario(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.strip().lower()
    text = text.replace("decarbonized", "decarbonised")
    return re.sub(r"[^a-z0-9]+", "", text)


def short_parameter_name(parameter: object) -> str:
    parameter_text = clean_text(parameter)
    return PARAMETER_SHORT_NAMES.get(parameter_text, parameter_text or "n/a")


def wrap_cell_text(text: object, width: int = 18) -> str:
    text_value = clean_text(text)
    if not text_value:
        return ""
    return textwrap.fill(text_value, width=width, break_long_words=False, break_on_hyphens=True)


def fmt_float(value: object, digits: int = 1, missing: str = "—") -> str:
    try:
        value_f = float(value)
    except Exception:
        return missing
    if not np.isfinite(value_f):
        return missing
    return f"{value_f:.{digits}f}"


def fmt_metric(value: object, digits_large: int = 1, digits_small: int = 2, missing: str = "—") -> str:
    try:
        value_f = float(value)
    except Exception:
        return missing
    if not np.isfinite(value_f):
        return missing
    digits = digits_small if abs(value_f) < 1.0 else digits_large
    return f"{value_f:.{digits}f}"


def fmt_pct(value: object, digits: int = 1, missing: str = "—") -> str:
    try:
        value_f = float(value)
    except Exception:
        return missing
    if not np.isfinite(value_f):
        return missing
    return f"{value_f:.{digits}f}%"


def country_key(country: str, iso3: str = "") -> str:
    return normalize_header(iso3) or normalize_header(country)


def record_key(record: Dict[str, str]) -> str:
    return country_key(record.get("country_name", ""), record.get("iso3", ""))


def country_label(country: str, iso3: str = "") -> str:
    return f"{country} ({iso3})" if iso3 else country


def canonical_country_iso3(country: object, iso3: object = "") -> str:
    """Return a robust ISO3-style key for the feasibility screen."""
    iso_text = clean_text(iso3).upper()
    iso_text = COUNTRY_ALIAS_TO_ISO3.get(normalize_header(iso_text), iso_text)
    if len(iso_text) == 3 and iso_text not in {"ISO", "NAN", "NON"}:
        return iso_text

    country_key_value = normalize_header(clean_text(country))
    return COUNTRY_ALIAS_TO_ISO3.get(country_key_value, country_key_value.upper())


def is_pathway_eligible(country: object, iso3: object, pathway_key: object) -> bool:
    """True when a country-pathway combination passes the feasibility screen."""
    if not APPLY_FEASIBILITY_SCREEN:
        return True

    pathway = str(pathway_key).strip().lower()
    eligible = FEASIBILITY_SCREEN_ELIGIBLE_ISO3.get(pathway, "ALL")
    if isinstance(eligible, str) and eligible.upper() == "ALL":
        return True

    country_iso3 = canonical_country_iso3(country, iso3)
    return country_iso3 in set(eligible)


def screening_status(country: object, iso3: object, pathway_key: object) -> str:
    return "eligible" if is_pathway_eligible(country, iso3, pathway_key) else "screened_out"


def apply_feasibility_screen_to_run_values(run_values: pd.DataFrame) -> pd.DataFrame:
    """Flag screened-out rows and remove them from screened outputs when enabled."""
    out = run_values.copy()

    if out.empty or not APPLY_FEASIBILITY_SCREEN:
        out["eligible_for_main_results"] = True
        out["screening_status"] = "eligible"
        return out

    out["eligible_for_main_results"] = out.apply(
        lambda row: is_pathway_eligible(row.get("country", ""), row.get("iso3", ""), row.get("pathway_key", "")),
        axis=1,
    )
    out["screening_status"] = np.where(out["eligible_for_main_results"], "eligible", "screened_out")

    if DROP_SCREENED_OUT_FROM_OUTPUTS:
        out = out.loc[out["eligible_for_main_results"]].copy()

    return out


def apply_feasibility_screen_to_cost_df(cost_df: pd.DataFrame) -> pd.DataFrame:
    """Add screen columns and remove ineligible rows from screened outputs when enabled."""
    out = cost_df.copy()
    if out.empty:
        return out

    if not APPLY_FEASIBILITY_SCREEN:
        out["eligible_for_main_results"] = True
        out["screening_status"] = "eligible"
        return out

    out["eligible_for_main_results"] = out.apply(
        lambda row: is_pathway_eligible(row.get("country", ""), row.get("iso3", ""), row.get("pathway_key", "")),
        axis=1,
    )
    out["screening_status"] = np.where(out["eligible_for_main_results"], "eligible", "screened_out")

    if DROP_SCREENED_OUT_FROM_OUTPUTS:
        out = out.loc[out["eligible_for_main_results"]].copy()

    return out


# =============================================================================
# COUNTRY LIST LOADING
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


def raw_country_tables(path: Path):
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
    # First pass: use Excel/CSV Column C ISO3 to identify real country rows.
    # Do not drop all-empty columns before this pass, because a blank Column B
    # should not shift Column C into position B.
    for sheet_name, raw in raw_country_tables(country_list_path):
        raw = raw.dropna(axis=0, how="all")
        if raw.empty or raw.shape[1] < 1:
            continue

        records: List[Dict[str, str]] = []
        if raw.shape[1] >= 3:
            for _, row in raw.iterrows():
                country_name = clean_text(row.iloc[0])
                iso3 = iso3_from_cell(row.iloc[2])
                if not iso3:
                    continue
                if not country_name or normalize_header(country_name) in {"country", "countryname", "name"}:
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
    """Fallback when no country-list file is found: discover country folders with NPV results."""
    records: List[Dict[str, str]] = []
    if not results_root.exists():
        return records
    for child in sorted(results_root.iterdir()):
        if not child.is_dir():
            continue
        if find_npv_workbook(child) is not None:
            records.append({"country_name": child.name, "iso3": ""})
    return records


# =============================================================================
# COUNTRY / FILE DISCOVERY
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
            if target_country_key and (target_country_key in child_key or child_key in target_country_key):
                partial_matches.append(child)
        if len(partial_matches) == 1:
            return partial_matches[0]

    expected = results_root / country_name
    raise FileNotFoundError(
        f"Could not find result folder for {country_name} ({iso3}). Expected: {expected}"
    )


def find_npv_workbook(country_dir: Path) -> Optional[Path]:
    """Find the v14_DATASET_hourly_highgas workbook inside a country folder."""
    npv_dir = country_dir / NPV_RESULTS_FOLDER
    candidates: List[Path] = []

    for directory in [npv_dir, country_dir]:
        if not directory.exists():
            continue
        for ext in [".xlsx", ".xlsm", ".xls", ".csv"]:
            candidates.append(directory / f"{NPV_WORKBOOK_STEM}{ext}")
        candidates.extend(sorted(directory.glob(f"{NPV_WORKBOOK_STEM}*.xls*")))
        candidates.extend(sorted(directory.glob(f"{NPV_WORKBOOK_STEM}*.csv")))
        candidates.extend(sorted(directory.glob("*DATASET*hourly*highgas*.xls*")))
        candidates.extend(sorted(directory.glob("*DATASET*hourly*highgas*.csv")))

    seen = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if path.exists() and path.is_file():
            return path
    return None


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


def find_shipping_summary_file(country_dir: Path, port_name: str, preferred_path: Optional[object] = None) -> Optional[Path]:
    """Find shipping_route_<port>_<DESTINATION>_summary.xlsx/csv for one port."""
    if not is_blank(preferred_path):
        candidate = Path(str(preferred_path))
        if candidate.exists() and candidate.is_file():
            return candidate

    port_clean = clean_text(port_name)
    port_safe = safe_output_name_part(port_clean)
    dest_safe = safe_output_name_part(DESTINATION_PORT)

    port_dirs = candidate_port_dirs(country_dir, port_clean)
    direct_candidates: List[Path] = []
    for ext in [".xlsx", ".xlsm", ".xls", ".csv"]:
        direct_candidates.extend([
            country_dir / port_clean / f"shipping_route_{port_clean}_{DESTINATION_PORT}_summary{ext}",
            country_dir / port_safe / f"shipping_route_{port_safe}_{dest_safe}_summary{ext}",
            country_dir / f"shipping_route_{port_clean}_{DESTINATION_PORT}_summary{ext}",
            country_dir / f"shipping_route_{port_safe}_{dest_safe}_summary{ext}",
        ])

    hit = first_existing(direct_candidates)
    if hit is not None:
        return hit

    patterns = [
        f"shipping_route*{port_safe}*{dest_safe}*summary*.xls*",
        f"shipping_route*{port_safe}*{dest_safe}*summary*.csv",
        f"*{port_safe}*{dest_safe}*summary*.xls*",
        f"*{port_safe}*{dest_safe}*summary*.csv",
        "*summary*.xls*",
        "*summary*.csv",
    ]

    for port_dir in port_dirs:
        for pattern in patterns:
            hits = sorted(path for path in port_dir.glob(pattern) if path.is_file())
            if hits:
                return hits[0]

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
# SHIPPING SUMMARY LOADING
# =============================================================================


def shipping_summary_required_columns() -> List[str]:
    return [
        "year",
        "decarbonisation_scenario",
        "distance_nm",
        "ef_g_per_tnm_mid",
    ]


def read_shipping_summary(path: Path) -> pd.DataFrame:
    try:
        return read_first_matching_table(path, required_columns=shipping_summary_required_columns())
    except Exception:
        return read_table(path)


def shipping_summary_column_map(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    return {
        "year": pick_column(df, ["year"], required=True, purpose="shipping year column"),
        "decarbonisation_scenario": pick_column(
            df,
            ["decarbonisation_scenario", "decarbonization_scenario", "scenario"],
            contains_any=["decarbonisation", "decarbonization"],
            required=True,
            purpose="shipping decarbonisation scenario column",
        ),
        "distance_nm": pick_column(df, ["distance_nm"], required=True, purpose="shipping distance_nm column"),
        "ef_g_per_tnm_mid": pick_column(
            df,
            ["ef_g_per_tnm_mid", "emission_factor_g_per_tnm_mid"],
            contains_any=["efgpertnm", "emissionfactor"],
            required=True,
            purpose="shipping ef_g_per_tnm_mid column",
        ),
        "route_scenario": pick_column(df, ["route_scenario", "scenario_name"], required=False),
    }


def shipping_ci_values_from_summary(summary_path: Path, plotted_year: int) -> Tuple[float, float, str]:
    """Return (kgCO2/kgNH3, kgCO2/kgH2-eq, source path) for one shipping summary."""
    df = read_shipping_summary(summary_path)
    cols = shipping_summary_column_map(df)

    work = df.copy()
    work["__year"] = pd.to_numeric(work[cols["year"]], errors="coerce").astype("Int64")
    work["__scenario"] = work[cols["decarbonisation_scenario"]].map(normalized_scenario)

    target_scenario = normalized_scenario(SHIPPING_DECARB_SCENARIO)
    work = work[(work["__year"] == int(plotted_year)) & (work["__scenario"] == target_scenario)].copy()

    if work.empty:
        raise ValueError(
            f"No shipping-summary row for year={plotted_year}, scenario={SHIPPING_DECARB_SCENARIO!r} in {summary_path}"
        )

    # If several rows exist, use the shortest available route as a deterministic choice.
    work["__distance_nm"] = pd.to_numeric(work[cols["distance_nm"]], errors="coerce")
    work = work.sort_values("__distance_nm", ascending=True, na_position="last")
    row = work.iloc[0]

    distance_nm = pd.to_numeric(row.get(cols["distance_nm"]), errors="coerce")
    ef_mid = pd.to_numeric(row.get(cols["ef_g_per_tnm_mid"]), errors="coerce")

    if pd.isna(distance_nm) or pd.isna(ef_mid):
        raise ValueError(f"Missing distance_nm or ef_g_per_tnm_mid in {summary_path}")

    kgco2_per_kgnh3 = float(ef_mid) * float(distance_nm) / 1e6
    kgco2_per_kgh2eq = kgco2_per_kgnh3 * float(NH3_KG_PER_KG_H2_EQ)
    return kgco2_per_kgnh3, kgco2_per_kgh2eq, str(summary_path)


def build_shipping_ci_lookup_for_country(
    country_dir: Path,
    locations_df: pd.DataFrame,
    years_to_plot: Sequence[int],
) -> Tuple[Dict[Tuple[str, int], Tuple[float, float, str]], List[str]]:
    """Build a {(port, year): (shipping_ci_kgNH3, shipping_ci_kgH2eq, source)} lookup."""
    warnings: List[str] = []
    lookup: Dict[Tuple[str, int], Tuple[float, float, str]] = {}

    selected_port_col = pick_column(
        locations_df,
        ["selected_port", "canonical_main_port", "canonical port", "nearest_port", "port"],
        contains_any=["selectedport", "canonical", "nearestport"],
        required=True,
        purpose="selected port column in Locations sheet",
    )
    summary_path_col = pick_column(
        locations_df,
        ["shipping_summary_path", "summary_path", "route_summary_path"],
        contains_any=["summarypath"],
        required=False,
        purpose="shipping summary path column",
    )

    port_summary_hint: Dict[str, object] = {}
    for _, row in locations_df.iterrows():
        port = clean_text(row.get(selected_port_col, ""))
        if not port:
            continue
        if summary_path_col is not None and port not in port_summary_hint:
            hint = row.get(summary_path_col, "")
            if not is_blank(hint):
                port_summary_hint[port] = hint

    unique_ports = sorted({clean_text(v) for v in locations_df[selected_port_col].dropna().tolist() if clean_text(v)})

    for port in unique_ports:
        summary_path = find_shipping_summary_file(country_dir, port, preferred_path=port_summary_hint.get(port))
        if summary_path is None:
            warnings.append(f"{country_dir.name} | {port}: could not find shipping summary file")
            continue

        for year in years_to_plot:
            try:
                ci_kgnh3, ci_kgh2eq, source = shipping_ci_values_from_summary(summary_path, int(year))
                lookup[(normalize_header(port), int(year))] = (ci_kgnh3, ci_kgh2eq, source)
            except Exception as exc:
                warnings.append(f"{country_dir.name} | {port} | {year}: could not compute shipping CI from {summary_path}: {exc}")

    return lookup, warnings


# =============================================================================
# NPV / CI WORKBOOK LOADING
# =============================================================================


def required_npv_columns() -> List[str]:
    return ["time", "scenario", "simulation"] + [p["npv_col"] for p in PATHWAYS]


def required_ci_columns() -> List[str]:
    return ["time", "scenario", "simulation"] + [p["ci_col"] for p in PATHWAYS]


def read_npv_inputs(workbook_path: Path) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    suffix = workbook_path.suffix.lower()
    if suffix not in {".xlsx", ".xlsm", ".xls"}:
        raise ValueError(f"NPV workbook must be Excel because sheets are required: {workbook_path}")

    npv = pd.read_excel(workbook_path, sheet_name="NPV")
    ci = pd.read_excel(workbook_path, sheet_name="CI_same_year")
    locations = pd.read_excel(workbook_path, sheet_name="Locations")

    for col in required_npv_columns():
        pick_column(npv, [col], required=True, purpose=f"NPV sheet column {col!r}")
    for col in required_ci_columns():
        pick_column(ci, [col], required=True, purpose=f"CI_same_year sheet column {col!r}")
    pick_column(locations, ["time"], required=True, purpose="Locations time column")
    pick_column(locations, ["simulation"], required=True, purpose="Locations simulation column")
    pick_column(
        locations,
        ["selected_port", "canonical_main_port", "nearest_port"],
        contains_any=["selectedport", "canonical", "nearestport"],
        required=True,
        purpose="Locations selected port column",
    )
    return npv, ci, locations


def apply_run_filters(df: pd.DataFrame, time_value: int, scenario_filter: Optional[str]) -> pd.DataFrame:
    time_col = pick_column(df, ["time"], required=True, purpose="time column")
    sim_col = pick_column(df, ["simulation"], required=True, purpose="simulation column")
    scenario_col = pick_column(df, ["scenario"], required=False, purpose="scenario column")

    out = df.copy()
    out["__time"] = pd.to_numeric(out[time_col], errors="coerce").astype("Int64")
    out["__simulation"] = pd.to_numeric(out[sim_col], errors="coerce").astype("Int64")

    mask = (out["__time"] == int(time_value))
    mask &= out["__simulation"].between(int(SIMULATION_MIN), int(SIMULATION_MAX))

    if scenario_filter is not None and scenario_col is not None:
        mask &= out[scenario_col].astype(str).map(normalized_scenario) == normalized_scenario(scenario_filter)

    return out.loc[mask].copy()


def load_run_level_values_for_country(
    record: Dict[str, str],
    country_order_value: int,
) -> Tuple[pd.DataFrame, List[str]]:
    """Load run-level NPV and total CI records for one country."""
    warnings: List[str] = []

    country_name = clean_text(record.get("country_name", ""))
    iso3 = clean_text(record.get("iso3", "")).upper()
    country_dir = find_country_dir(RESULTS_ROOT, record)
    workbook_path = find_npv_workbook(country_dir)

    if workbook_path is None:
        raise FileNotFoundError(
            f"Could not find {NPV_WORKBOOK_STEM}.xlsx in {country_dir / NPV_RESULTS_FOLDER}"
        )

    if PRINT_PROGRESS:
        print(f"  {country_label(country_name, iso3)}: reading NPV/CI workbook {workbook_path.name}")

    npv_df, ci_df, loc_df = read_npv_inputs(workbook_path)
    shipping_lookup, ship_warnings = build_shipping_ci_lookup_for_country(country_dir, loc_df, YEARS_TO_PLOT)
    warnings.extend(ship_warnings)

    npv_cols = {
        p["key"]: pick_column(npv_df, [p["npv_col"]], required=True, purpose=f"{p['label']} NPV column")
        for p in PATHWAYS
    }
    ci_cols = {
        p["key"]: pick_column(ci_df, [p["ci_col"]], required=True, purpose=f"{p['label']} CI column")
        for p in PATHWAYS
    }

    loc_port_col = pick_column(
        loc_df,
        ["selected_port", "canonical_main_port", "canonical port", "nearest_port", "port"],
        contains_any=["selectedport", "canonical", "nearestport"],
        required=True,
        purpose="selected port column in Locations sheet",
    )
    loc_shipping_fallback_col = pick_column(
        loc_df,
        ["shipping_co2_t_per_tnh3", "shipping_ci", "shipping_emissions_tco2_per_tnh3"],
        contains_any=["shippingco2", "shippingemissions"],
        required=False,
        purpose="Locations shipping CO2 fallback column",
    )

    records: List[Dict[str, object]] = []

    for plotted_year in YEARS_TO_PLOT:
        if int(plotted_year) not in YEAR_TO_TIME:
            warnings.append(f"{country_label(country_name, iso3)}: no YEAR_TO_TIME mapping for plotted year {plotted_year}")
            continue

        workbook_time = int(YEAR_TO_TIME[int(plotted_year)])
        npv_year = apply_run_filters(npv_df, workbook_time, SCENARIO_FILTER)
        ci_year = apply_run_filters(ci_df, workbook_time, SCENARIO_FILTER)
        loc_year = apply_run_filters(loc_df, workbook_time, SCENARIO_FILTER)

        if npv_year.empty:
            warnings.append(f"{country_label(country_name, iso3)} | {plotted_year}: no NPV rows after filtering")
            continue
        if ci_year.empty:
            warnings.append(f"{country_label(country_name, iso3)} | {plotted_year}: no CI_same_year rows after filtering")
            continue
        if loc_year.empty:
            warnings.append(f"{country_label(country_name, iso3)} | {plotted_year}: no Locations rows after filtering")
            continue

        npv_keep = ["__simulation"] + list(npv_cols.values())
        ci_keep = ["__simulation"] + list(ci_cols.values())
        loc_keep = ["__simulation", loc_port_col]
        if loc_shipping_fallback_col is not None:
            loc_keep.append(loc_shipping_fallback_col)

        merged = (
            npv_year[npv_keep]
            .merge(ci_year[ci_keep], on="__simulation", how="inner")
            .merge(loc_year[loc_keep], on="__simulation", how="left")
        )

        if merged.empty:
            warnings.append(f"{country_label(country_name, iso3)} | {plotted_year}: no merged NPV/CI/Location rows")
            continue

        merged["selected_port_clean"] = merged[loc_port_col].map(clean_text)
        merged["selected_port_key"] = merged["selected_port_clean"].map(normalize_header)

        def lookup_shipping(row: pd.Series) -> Tuple[float, float, str, str]:
            port = row.get("selected_port_clean", "")
            port_key_value = row.get("selected_port_key", "")
            if port_key_value:
                hit = shipping_lookup.get((port_key_value, int(plotted_year)))
                if hit is not None:
                    return float(hit[0]), float(hit[1]), str(hit[2]), "route_summary"

            if ALLOW_LOCATION_SHEET_SHIPPING_FALLBACK and loc_shipping_fallback_col is not None:
                fallback = pd.to_numeric(row.get(loc_shipping_fallback_col), errors="coerce")
                if pd.notna(fallback) and np.isfinite(float(fallback)):
                    fallback_kgnh3 = float(fallback)
                    fallback_kgh2eq = fallback_kgnh3 * float(NH3_KG_PER_KG_H2_EQ)
                    return fallback_kgnh3, fallback_kgh2eq, "Locations sheet fallback", "locations_fallback"

            return np.nan, np.nan, "", "missing"

        shipping_values = merged.apply(lookup_shipping, axis=1, result_type="expand")
        shipping_values.columns = [
            "shipping_ci_kgco2_per_kgnh3",
            "shipping_ci_kgco2_per_kgh2eq",
            "shipping_ci_source",
            "shipping_ci_source_type",
        ]
        merged = pd.concat([merged, shipping_values], axis=1)

        missing_shipping = int(merged["shipping_ci_kgco2_per_kgh2eq"].isna().sum())
        if missing_shipping:
            warnings.append(
                f"{country_label(country_name, iso3)} | {plotted_year}: {missing_shipping} run(s) missing shipping CI"
            )

        for pathway in PATHWAYS:
            key = pathway["key"]
            npv_col = npv_cols[key]
            ci_col = ci_cols[key]

            npv_values = pd.to_numeric(merged[npv_col], errors="coerce")
            production_ci = pd.to_numeric(merged[ci_col], errors="coerce")
            shipping_ci_kgnh3 = pd.to_numeric(merged["shipping_ci_kgco2_per_kgnh3"], errors="coerce")
            shipping_ci_kgh2eq = pd.to_numeric(merged["shipping_ci_kgco2_per_kgh2eq"], errors="coerce")
            total_ci = production_ci + shipping_ci_kgh2eq

            frame = pd.DataFrame({
                "country": country_name,
                "iso3": iso3,
                "country_key": country_key(country_name, iso3),
                "country_order": country_order_value,
                "year": int(plotted_year),
                "workbook_time": workbook_time,
                "scenario_filter": SCENARIO_FILTER if SCENARIO_FILTER is not None else "ALL",
                "simulation": merged["__simulation"].astype("Int64"),
                "pathway_key": key,
                "pathway": pathway["short_label"],
                "technology": pathway["technology"],
                "npv": npv_values,
                "production_ci_kgco2_per_kgh2": production_ci,
                "shipping_ci_kgco2_per_kgnh3": shipping_ci_kgnh3,
                "shipping_ci_kgco2_per_kgh2eq": shipping_ci_kgh2eq,
                "nh3_kg_per_kgh2eq_conversion_factor": float(NH3_KG_PER_KG_H2_EQ),
                "total_ci_kgco2_per_kgh2eq": total_ci,
                "selected_port": merged["selected_port_clean"],
                "shipping_ci_source": merged["shipping_ci_source"],
                "shipping_ci_source_type": merged["shipping_ci_source_type"],
                "npv_workbook": str(workbook_path),
            })

            frame = frame.dropna(subset=["npv", "total_ci_kgco2_per_kgh2eq"]).copy()
            records.extend(frame.to_dict("records"))

    out = pd.DataFrame(records)
    if PRINT_PROGRESS:
        print(f"    loaded {len(out)} run-level pathway row(s)")
    return out, warnings


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


def build_country_quantile_summary(run_values: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, object]] = []
    metric_specs = [
        ("npv", "npv", "USD/t NH3"),
        ("total_ci", "total_ci_kgco2_per_kgh2eq", "kgCO2e/kgH2-eq"),
        ("shipping_ci", "shipping_ci_kgco2_per_kgh2eq", "kgCO2e/kgH2-eq"),
    ]
    group_cols = ["country", "iso3", "country_key", "country_order", "year", "pathway_key", "pathway", "technology"]

    for keys, group in run_values.groupby(group_cols, dropna=False):
        country, iso3, ckey, country_order_value, year, pathway_key_value, pathway, technology = keys
        eligible_for_main_results = bool(group["eligible_for_main_results"].iloc[0]) if "eligible_for_main_results" in group.columns else True
        screen_status = str(group["screening_status"].iloc[0]) if "screening_status" in group.columns else "eligible"
        for metric, value_col, unit in metric_specs:
            qs = quantile_summary(group[value_col].tolist())
            rows.append({
                "country": country,
                "iso3": iso3,
                "country_key": ckey,
                "country_order": int(country_order_value),
                "year": int(year),
                "pathway_key": pathway_key_value,
                "pathway": pathway,
                "technology": technology,
                "eligible_for_main_results": eligible_for_main_results,
                "screening_status": screen_status,
                "metric": metric,
                "unit": unit,
                **qs,
            })

    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary

    # Add q50 NPV ranks, computed separately for each pathway and year.
    summary["q50_npv_rank"] = np.nan
    summary["q50_npv_rank_denominator"] = np.nan
    summary["q50_npv_rank_label"] = ""

    npv_mask = summary["metric"].eq("npv")
    npv_summary = summary.loc[npv_mask].copy()
    for (year, pathway_key_value), group in npv_summary.groupby(["year", "pathway_key"], dropna=False):
        valid = group["q50"].notna()
        denom = int(valid.sum())
        if denom == 0:
            continue
        ascending = not RANK_HIGHER_NPV_IS_BETTER
        ranks = group.loc[valid, "q50"].rank(method="min", ascending=ascending).astype(int)
        summary.loc[ranks.index, "q50_npv_rank"] = ranks
        summary.loc[group.index, "q50_npv_rank_denominator"] = denom
        for idx, rank_val in ranks.items():
            summary.loc[idx, "q50_npv_rank_label"] = f"{int(rank_val)}/{denom}"

    return summary


def load_all_run_values(records: Sequence[Dict[str, str]]) -> Tuple[pd.DataFrame, List[str]]:
    frames: List[pd.DataFrame] = []
    warnings: List[str] = []

    for order, record in enumerate(records):
        label = country_label(record.get("country_name", ""), record.get("iso3", ""))
        try:
            country_values, country_warnings = load_run_level_values_for_country(record, country_order_value=order)
            warnings.extend(country_warnings)
            if not country_values.empty:
                frames.append(country_values)
            else:
                warnings.append(f"{label}: no valid run-level rows")
        except Exception as exc:
            warnings.append(f"{label}: could not load NPV/CI values: {exc}")

    if not frames:
        raise ValueError("No valid NPV/CI run-level values were loaded.")

    out = pd.concat(frames, ignore_index=True)
    rows_before_screen = len(out)
    out = apply_feasibility_screen_to_run_values(out)
    if APPLY_FEASIBILITY_SCREEN:
        removed_rows = rows_before_screen - len(out)
        print(f"Feasibility screen applied: {removed_rows:,} run-level row(s) removed from dashboard headline results.")
    return out, warnings


# =============================================================================
# COST EFFECTS / BREAKDOWN LOADING
# =============================================================================


def load_effects_sheet(workbook_path: Path) -> pd.DataFrame:
    suffix = workbook_path.suffix.lower()
    if suffix == ".csv":
        raise ValueError(f"Workbook must be Excel for effects sheet, got CSV: {workbook_path}")
    return pd.read_excel(workbook_path, sheet_name=EFFECTS_SHEET)


def load_country_effects(record: Dict[str, str], country_order_value: int) -> pd.DataFrame:
    country_name = clean_text(record.get("country_name", ""))
    iso3 = clean_text(record.get("iso3", "")).upper()
    country_dir = find_country_dir(RESULTS_ROOT, record)
    workbook_path = find_npv_workbook(country_dir)
    if workbook_path is None:
        raise FileNotFoundError(f"Could not find {NPV_WORKBOOK_STEM}.xlsx in {country_dir / NPV_RESULTS_FOLDER}")

    df = load_effects_sheet(workbook_path)
    required_cols = [
        "time", "scenario", "technology", "parameter", "runs",
        "mean_effect_on_NPV", "mean_abs_normalized_effect",
        "mean_signed_normalized_effect", "mean_share_of_NPV", "parameter_rank",
    ]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise KeyError(f"Missing columns in {workbook_path.name}: {missing}")

    out = df.copy()
    out["country"] = country_name
    out["iso3"] = iso3
    out["country_key"] = country_key(country_name, iso3)
    out["country_order"] = country_order_value
    out["source_file"] = str(workbook_path)
    out["time"] = pd.to_numeric(out["time"], errors="coerce")
    out["runs"] = pd.to_numeric(out["runs"], errors="coerce")
    out["mean_effect_on_NPV"] = pd.to_numeric(out["mean_effect_on_NPV"], errors="coerce")
    out["mean_abs_normalized_effect"] = pd.to_numeric(out["mean_abs_normalized_effect"], errors="coerce")
    out["mean_signed_normalized_effect"] = pd.to_numeric(out["mean_signed_normalized_effect"], errors="coerce")
    out["mean_share_of_NPV"] = pd.to_numeric(out["mean_share_of_NPV"], errors="coerce")
    out["parameter_rank"] = pd.to_numeric(out["parameter_rank"], errors="coerce")
    out["parameter"] = out["parameter"].astype(str).str.strip()
    out["technology"] = out["technology"].astype(str).str.strip()
    out["scenario"] = out["scenario"].astype(str).str.strip()
    return out


def prepare_cost_breakdown(all_effects: pd.DataFrame) -> pd.DataFrame:
    df = all_effects.copy()
    if SCENARIO_FILTER is not None:
        df = df[df["scenario"].astype(str) == str(SCENARIO_FILTER)].copy()

    time_to_display_year = {int(v): int(k) for k, v in YEAR_TO_TIME.items()}
    df = df[df["technology"].isin(TECHNOLOGY_ORDER)].copy()
    df = df[df["time"].isin(time_to_display_year.keys())].copy()
    df = df[df["mean_effect_on_NPV"] < 0].copy()
    df["abs_cost_component"] = df["mean_effect_on_NPV"].abs()
    df = df[df["abs_cost_component"] > 0].copy()

    df["year"] = df["time"].astype(int).map(time_to_display_year)
    df["pathway_key"] = df["technology"].map(lambda t: PATHWAY_BY_TECHNOLOGY.get(t, {}).get("key", ""))
    df["pathway"] = df["technology"].map(lambda t: PATHWAY_BY_TECHNOLOGY.get(t, {}).get("short_label", t))
    df["parameter_group"] = df["parameter"].map(PARAMETER_GROUPS).fillna("Other")

    group_cols = [
        "country", "iso3", "country_key", "country_order",
        "technology", "pathway_key", "pathway", "year", "time",
        "parameter", "parameter_group",
    ]
    agg = (
        df.groupby(group_cols, dropna=False, as_index=False)
        .agg(
            abs_cost_component=("abs_cost_component", "sum"),
            mean_effect_on_NPV=("mean_effect_on_NPV", "sum"),
            mean_share_of_NPV=("mean_share_of_NPV", "sum"),
            parameter_rank=("parameter_rank", "min"),
            runs=("runs", "max"),
        )
    )

    totals = (
        agg.groupby(["country", "iso3", "country_key", "country_order", "technology", "pathway_key", "pathway", "year", "time"], as_index=False)
        .agg(total_negative_cost=("abs_cost_component", "sum"))
    )
    agg = agg.merge(
        totals,
        on=["country", "iso3", "country_key", "country_order", "technology", "pathway_key", "pathway", "year", "time"],
        how="left",
    )
    agg["share_pct_of_negative_cost"] = np.where(
        agg["total_negative_cost"] > 0,
        100.0 * agg["abs_cost_component"] / agg["total_negative_cost"],
        np.nan,
    )

    group_order = {group: i for i, group in enumerate(COST_GROUP_ORDER)}
    agg["group_sort"] = agg["parameter_group"].map(group_order).fillna(999).astype(int)
    agg["parameter_sort"] = agg["parameter_rank"].fillna(999).astype(float)
    return agg


def load_all_cost_breakdowns(records: Sequence[Dict[str, str]]) -> Tuple[pd.DataFrame, List[str]]:
    frames: List[pd.DataFrame] = []
    warnings: List[str] = []

    for order, record in enumerate(records):
        label = country_label(record.get("country_name", ""), record.get("iso3", ""))
        try:
            df = load_country_effects(record, country_order_value=order)
            if not df.empty:
                frames.append(df)
            else:
                warnings.append(f"{label}: NPV effects sheet is empty")
        except Exception as exc:
            warnings.append(f"{label}: could not load NPV effects: {exc}")

    if not frames:
        warnings.append("No NPV effects data could be loaded.")
        return pd.DataFrame(), warnings

    cost_df_raw = prepare_cost_breakdown(pd.concat(frames, ignore_index=True))
    rows_before_screen = len(cost_df_raw)
    cost_df = apply_feasibility_screen_to_cost_df(cost_df_raw)
    if APPLY_FEASIBILITY_SCREEN:
        removed_rows = rows_before_screen - len(cost_df)
        print(f"Feasibility screen applied: {removed_rows:,} cost-breakdown row(s) removed from dashboard charts and CSVs.")
    if cost_df.empty:
        warnings.append("No negative NPV-effect cost components were found after filtering.")
    return cost_df, warnings


def build_group_cost_summary(cost_df: pd.DataFrame) -> pd.DataFrame:
    if cost_df.empty:
        return pd.DataFrame()
    group_cols = [
        "country", "iso3", "country_key", "country_order",
        "year", "pathway_key", "pathway", "technology",
        "eligible_for_main_results", "screening_status", "parameter_group",
    ]
    out = (
        cost_df.groupby(group_cols, dropna=False, as_index=False)
        .agg(
            group_abs_cost_component=("abs_cost_component", "sum"),
            total_negative_cost=("total_negative_cost", "max"),
            group_sort=("group_sort", "min"),
        )
    )
    out["group_share_pct"] = np.where(
        out["total_negative_cost"] > 0,
        100.0 * out["group_abs_cost_component"] / out["total_negative_cost"],
        np.nan,
    )
    return out.sort_values(["country_order", "pathway_key", "year", "group_sort"]).copy()


def build_cost_driver_summary(cost_df: pd.DataFrame) -> pd.DataFrame:
    """Return one row per country/year/pathway with carbon shares and top cost items."""
    if cost_df.empty:
        return pd.DataFrame()

    rows: List[Dict[str, object]] = []
    group_cols = ["country", "iso3", "country_key", "country_order", "year", "pathway_key", "pathway", "technology", "eligible_for_main_results", "screening_status"]
    for keys, group in cost_df.groupby(group_cols, dropna=False):
        country, iso3, ckey, country_order_value, year, pathway_key_value, pathway, technology, eligible_for_main_results, screen_status = keys
        total_negative_cost = float(group["total_negative_cost"].max()) if not group.empty else np.nan

        def param_share(parameter_name: str) -> float:
            sub = group[group["parameter"].eq(parameter_name)]
            if sub.empty or not np.isfinite(total_negative_cost) or total_negative_cost <= 0:
                return 0.0
            return float(sub["abs_cost_component"].sum()) / total_negative_cost * 100.0

        top = group.sort_values(["abs_cost_component", "parameter_sort"], ascending=[False, True]).head(3)
        top_params = [short_parameter_name(p) for p in top["parameter"].tolist()]
        while len(top_params) < 3:
            top_params.append("")

        rows.append({
            "country": country,
            "iso3": iso3,
            "country_key": ckey,
            "country_order": int(country_order_value),
            "year": int(year),
            "pathway_key": pathway_key_value,
            "pathway": pathway,
            "technology": technology,
            "eligible_for_main_results": bool(eligible_for_main_results),
            "screening_status": screen_status,
            "total_negative_cost": total_negative_cost,
            "supply_chain_carbon_share_pct": param_share(CARBON_DRIVER_PARAMETERS["supply_chain"]),
            "process_carbon_share_pct": param_share(CARBON_DRIVER_PARAMETERS["process"]),
            "shipping_carbon_share_pct": param_share(CARBON_DRIVER_PARAMETERS["shipping"]),
            "top_cost_item_1": top_params[0],
            "top_cost_item_2": top_params[1],
            "top_cost_item_3": top_params[2],
            "top_cost_items": "\n".join([f"{i + 1}. {p}" for i, p in enumerate(top_params) if p]),
        })
    return pd.DataFrame(rows)


# =============================================================================
# LOCATION / PORT DATA FOR MAP
# =============================================================================


def _excel_ports_workbook_candidates(country_dir: Path) -> List[Path]:
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


def _sort_and_limit_location_rows(df: pd.DataFrame) -> pd.DataFrame:
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
            if out["__fallback_port_lat"].notna().any():
                return out
        except Exception:
            continue
    return raw_df


def find_ports_data_file(country_dir: Path) -> Optional[Path]:
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


def load_map_points_for_country(record: Dict[str, str], country_order_value: int) -> Tuple[pd.DataFrame, Optional[str]]:
    """Read one country's chosen-location/port data and return normalized map rows."""
    country_name = clean_text(record.get("country_name", ""))
    iso3 = clean_text(record.get("iso3", "")).upper()
    country_dir = find_country_dir(RESULTS_ROOT, record)
    source_path = find_ports_data_file(country_dir)
    if source_path is None:
        raise FileNotFoundError(f"Could not find {OPTIMISATION_CSV_NAME} or {PORTS_WORKBOOK_NAME} in {country_dir}")

    df = read_table(source_path)
    if df.empty:
        return pd.DataFrame(), str(source_path)

    is_raw_csv = source_path.suffix.lower() == ".csv" and "delivered" in normalize_header(source_path.stem)
    df = _sort_and_limit_location_rows(df)
    if is_raw_csv:
        df = _fill_raw_port_coordinates_from_excel(df, country_dir)

    lat_col = pick_column(
        df,
        preferred_names=["lat", "latitude", "location_lat", "location latitude", "site_lat", "site latitude"],
        required=False,
        purpose="location latitude column",
    )
    lon_col = pick_column(
        df,
        preferred_names=["lon", "long", "longitude", "location_lon", "location longitude", "site_lon", "site longitude"],
        required=False,
        purpose="location longitude column",
    )
    if lat_col is None or lon_col is None:
        raise KeyError(f"Could not find location lat/lon columns in {source_path}. Available columns: {list(df.columns)}")

    port_name_cols, port_lat_cols, port_lon_cols = _port_column_sets(df)
    if not port_lat_cols and "__fallback_port_lat" in df.columns:
        port_lat_cols = ["__fallback_port_lat"]
    if not port_lon_cols and "__fallback_port_lon" in df.columns:
        port_lon_cols = ["__fallback_port_lon"]

    delivered_col = pick_column(
        df,
        preferred_names=["nh3_delivered_usd_per_t", "delivered_nh3_usd_per_t", "delivered_cost", "delivered cost"],
        contains_any=["delivered"],
        required=False,
        purpose="delivered cost column",
    )

    out = pd.DataFrame({
        "country": country_name,
        "iso3": iso3,
        "country_key": country_key(country_name, iso3),
        "country_order": country_order_value,
        "lat": pd.to_numeric(df[lat_col], errors="coerce"),
        "lon": pd.to_numeric(df[lon_col], errors="coerce"),
        "port_name": first_non_null_series(df, port_name_cols).astype(str) if port_name_cols else "",
        "port_lat": pd.to_numeric(first_non_null_series(df, port_lat_cols), errors="coerce") if port_lat_cols else np.nan,
        "port_lon": pd.to_numeric(first_non_null_series(df, port_lon_cols), errors="coerce") if port_lon_cols else np.nan,
        "nh3_delivered_usd_per_t": pd.to_numeric(df[delivered_col], errors="coerce") if delivered_col is not None else np.nan,
        "source_file": str(source_path),
        "source_kind": "full_precision_optimiser_csv" if is_raw_csv else "chosen_ports_workbook",
    })

    out["port_name"] = out["port_name"].map(lambda x: "" if is_blank(x) or str(x).lower() == "nan" else clean_text(x))
    before = len(out)
    out = out.dropna(subset=["lat", "lon"]).copy()
    out = out[(out["lat"].between(-90, 90)) & (out["lon"].between(-180, 180))].copy()
    dropped = before - len(out)
    if dropped and PRINT_PROGRESS:
        print(f"WARNING: dropped {dropped} invalid location row(s) from {source_path.name}")
    return out, str(source_path)


def load_all_map_points(records: Sequence[Dict[str, str]]) -> Tuple[pd.DataFrame, List[str]]:
    frames: List[pd.DataFrame] = []
    warnings: List[str] = []
    for order, record in enumerate(records):
        label = country_label(record.get("country_name", ""), record.get("iso3", ""))
        try:
            df, source = load_map_points_for_country(record, country_order_value=order)
            if not df.empty:
                frames.append(df)
                if PRINT_PROGRESS:
                    print(f"  {label}: {len(df)} map location row(s) from {Path(source).name if source else 'unknown'}")
            else:
                warnings.append(f"{label}: no valid map rows")
        except Exception as exc:
            warnings.append(f"{label}: could not load map points: {exc}")
    if not frames:
        return pd.DataFrame(), warnings
    return pd.concat(frames, ignore_index=True), warnings


# =============================================================================
# COUNTRY BOUNDARIES
# =============================================================================


def load_country_boundaries():
    """Load Natural Earth 10m admin-0 country boundaries for the dashboard map."""
    if gpd is None:
        raise ImportError("geopandas is not installed")

    url = "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries.zip"
    cache_candidates = [
        PROJECT_DIR / "Trade_version" / ".ocean_cache" / "ne_10m_admin_0_countries.zip",
        PROJECT_DIR / "trade_version" / ".ocean_cache" / "ne_10m_admin_0_countries.zip",
        PROJECT_DIR / ".ocean_cache" / "ne_10m_admin_0_countries.zip",
        PROJECT_DIR / ".data" / "ne_10m_admin_0_countries.zip",
    ]

    cached_file = first_existing(cache_candidates)
    if cached_file is None:
        hits = []
        try:
            hits.extend(PROJECT_DIR.rglob("ne_10m_admin_0_countries*.zip"))
            hits.extend(PROJECT_DIR.rglob("*10m*admin_0_countries*.shp"))
        except Exception:
            pass
        hits = [path for path in hits if path.exists()]
        cached_file = sorted(hits)[0] if hits else None

    if cached_file is not None:
        path_for_gpd = f"zip://{cached_file}" if cached_file.suffix.lower() == ".zip" else str(cached_file)
        print(f"Loading 10m country-boundary layer from: {cached_file}")
        world = gpd.read_file(path_for_gpd)
    else:
        target_zip = cache_candidates[0]
        target_zip.parent.mkdir(parents=True, exist_ok=True)
        try:
            print(f"Downloading 10m country-boundary layer to: {target_zip}")
            urllib.request.urlretrieve(url, target_zip)
            world = gpd.read_file(f"zip://{target_zip}")
        except Exception as exc:
            raise FileNotFoundError(
                "Could not load the Natural Earth 10m country-boundary layer. "
                "Run once with internet access or place ne_10m_admin_0_countries.zip "
                "in Trade_version/.ocean_cache. The dashboard does not fall back to 110m."
            ) from exc

    if world.crs is None:
        world = world.set_crs(epsg=4326)
    elif world.crs.to_epsg() != 4326:
        world = world.to_crs(epsg=4326)
    return world

def _find_first_column(df, candidates: Sequence[str]) -> Optional[str]:
    norm_map = {normalize_header(col): col for col in df.columns}
    for candidate in candidates:
        hit = norm_map.get(normalize_header(candidate))
        if hit is not None:
            return hit
    return None


def country_boundary_match_columns(countries_gdf) -> Tuple[Optional[str], List[str]]:
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


def match_country_boundary_rows(countries_gdf, record: Dict[str, str], iso_col: Optional[str], name_cols: Sequence[str]):
    iso_key = normalize_header(record.get("iso3", ""))
    country_name_key = normalize_header(record.get("country_name", ""))
    mask = pd.Series(False, index=countries_gdf.index)

    if iso_col is not None and iso_key:
        mask = mask | countries_gdf[iso_col].astype(str).map(normalize_header).isin({iso_key})
    if country_name_key:
        for col in name_cols:
            mask = mask | countries_gdf[col].astype(str).map(normalize_header).isin({country_name_key})

    rows = countries_gdf.loc[mask]
    if not rows.empty:
        return rows

    if country_name_key:
        partial_mask = pd.Series(False, index=countries_gdf.index)
        for col in name_cols:
            norm_values = countries_gdf[col].astype(str).map(normalize_header)
            partial_mask = partial_mask | norm_values.map(
                lambda value: bool(value) and (country_name_key in value or value in country_name_key)
            )
        rows = countries_gdf.loc[partial_mask]
    return rows


def union_geometries(geometries):
    try:
        return geometries.union_all()
    except Exception:
        return geometries.unary_union


def mainland_geometry(geometry):
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
    except Exception:
        pass
    return geometry


def build_country_geometry_lookup(countries_gdf, records: Sequence[Dict[str, str]]) -> Dict[str, object]:
    iso_col, name_cols = country_boundary_match_columns(countries_gdf)
    lookup: Dict[str, object] = {}
    unmatched: List[str] = []
    for record in records:
        rows = match_country_boundary_rows(countries_gdf, record, iso_col, name_cols)
        if rows.empty:
            unmatched.append(country_label(record.get("country_name", ""), record.get("iso3", "")))
            continue
        geometry = union_geometries(rows.geometry)
        if MAINLAND_ONLY:
            geometry = mainland_geometry(geometry)
        lookup[record_key(record)] = geometry
    if unmatched and PRINT_WARNINGS:
        print("WARNING: countries not matched to boundary layer:")
        for item in unmatched[:50]:
            print("  - " + item)
    return lookup


def snap_point_to_geometry_obj(point: Point, geometry):
    """Snap a point to the nearest point on the country polygon / multipolygon."""
    try:
        if geometry is None or point is None or point.is_empty:
            return point
        if geometry.covers(point):
            return point
        return nearest_points(point, geometry)[1]
    except Exception:
        return point


def snap_point_to_boundary_obj(point: Point, geometry):
    """Snap a point to the nearest point on the country boundary."""
    try:
        if geometry is None or point is None or point.is_empty:
            return point
        boundary = geometry.boundary
        if boundary is None or boundary.is_empty:
            return point
        return nearest_points(point, boundary)[1]
    except Exception:
        return point


def snap_lon_lat_to_geometry(lon: float, lat: float, geometry) -> Tuple[float, float]:
    if Point is None or nearest_points is None or geometry is None:
        return lon, lat
    try:
        point = Point(float(lon), float(lat))
        snapped = snap_point_to_geometry_obj(point, geometry)
        if snapped is None or snapped.is_empty:
            return lon, lat
        return float(snapped.x), float(snapped.y)
    except Exception:
        return lon, lat


def snap_lon_lat_to_boundary(lon: float, lat: float, geometry) -> Tuple[float, float]:
    if Point is None or nearest_points is None or geometry is None:
        return lon, lat
    try:
        point = Point(float(lon), float(lat))
        snapped = snap_point_to_boundary_obj(point, geometry)
        if snapped is None or snapped.is_empty:
            return lon, lat
        return float(snapped.x), float(snapped.y)
    except Exception:
        return lon, lat


# =============================================================================
# DASHBOARD DATA ASSEMBLY
# =============================================================================


def build_dashboard_master_summary(
    quantile_summary_df: pd.DataFrame,
    cost_driver_summary: pd.DataFrame,
) -> pd.DataFrame:
    """Create a compact country/year/pathway table used by the dashboard and CSV export."""
    if quantile_summary_df.empty:
        return pd.DataFrame()

    base_cols = ["country", "iso3", "country_key", "country_order", "year", "pathway_key", "pathway", "technology"]
    pivot_rows: List[pd.DataFrame] = []
    for metric in ["npv", "total_ci", "shipping_ci"]:
        sub = quantile_summary_df[quantile_summary_df["metric"].eq(metric)].copy()
        if sub.empty:
            continue
        keep_cols = base_cols + ["q25", "q50", "q75", "whisker_low", "whisker_high", "n_values", "unit"]
        if metric == "npv":
            keep_cols += ["q50_npv_rank", "q50_npv_rank_denominator", "q50_npv_rank_label"]
        out = sub[keep_cols].copy()
        rename_map = {
            "q25": f"{metric}_q25",
            "q50": f"{metric}_q50",
            "q75": f"{metric}_q75",
            "whisker_low": f"{metric}_min",
            "whisker_high": f"{metric}_max",
            "n_values": f"{metric}_n",
            "unit": f"{metric}_unit",
        }
        out = out.rename(columns=rename_map)
        pivot_rows.append(out)

    if not pivot_rows:
        return pd.DataFrame()

    master = pivot_rows[0]
    for add in pivot_rows[1:]:
        master = master.merge(add, on=base_cols, how="outer")

    if not cost_driver_summary.empty:
        merge_cols = base_cols
        driver_cols = merge_cols + [
            "total_negative_cost",
            "supply_chain_carbon_share_pct",
            "process_carbon_share_pct",
            "shipping_carbon_share_pct",
            "top_cost_item_1",
            "top_cost_item_2",
            "top_cost_item_3",
            "top_cost_items",
        ]
        master = master.merge(cost_driver_summary[driver_cols], on=merge_cols, how="left")

    master["eligible_for_main_results"] = master.apply(
        lambda row: is_pathway_eligible(row.get("country", ""), row.get("iso3", ""), row.get("pathway_key", "")),
        axis=1,
    )
    master["screening_status"] = np.where(master["eligible_for_main_results"], "eligible", "screened_out")

    if DROP_SCREENED_OUT_FROM_OUTPUTS:
        master = master.loc[master["eligible_for_main_results"]].copy()

    return master.sort_values(["country_order", "pathway_key", "year"]).copy()


# =============================================================================
# PLOTTING HELPERS
# =============================================================================


def add_card(fig: plt.Figure, rect: Tuple[float, float, float, float], zorder: int = -10) -> None:
    patch = FancyBboxPatch(
        (rect[0], rect[1]), rect[2], rect[3],
        transform=fig.transFigure,
        boxstyle="round,pad=0.006,rounding_size=0.010",
        facecolor=CARD_BG,
        edgecolor=CARD_EDGE,
        linewidth=0.9,
        zorder=zorder,
    )
    fig.patches.append(patch)


def setup_card_axis(fig: plt.Figure, rect: Tuple[float, float, float, float]) -> plt.Axes:
    add_card(fig, rect)
    ax = fig.add_axes(rect)
    ax.set_facecolor("none")
    return ax


def clear_axis(ax: plt.Axes) -> None:
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)


def draw_panel_title(ax: plt.Axes, title: str) -> None:
    ax.text(0.018, 0.985, title, transform=ax.transAxes, ha="left", va="top", fontsize=SECTION_TITLE_FONTSIZE, fontweight="bold", color=DARK)


def draw_country_map_panel(ax: plt.Axes, country_name: str, map_df: pd.DataFrame, geometry) -> None:
    clear_axis(ax)
    draw_panel_title(ax, "Map")

    plot_df = map_df.copy()
    if not plot_df.empty:
        if geometry is not None:
            if SNAP_LOCATIONS_TO_COUNTRY_FOR_PLOT:
                snapped = plot_df.apply(
                    lambda r: snap_lon_lat_to_geometry(r["lon"], r["lat"], geometry),
                    axis=1,
                    result_type="expand",
                )
                snapped.columns = ["plot_lon", "plot_lat"]
                plot_df = pd.concat([plot_df, snapped], axis=1)
            else:
                plot_df["plot_lon"] = plot_df["lon"]
                plot_df["plot_lat"] = plot_df["lat"]

            if SNAP_PORTS_TO_COUNTRY_BOUNDARY_FOR_PLOT:
                mask = plot_df["port_lon"].notna() & plot_df["port_lat"].notna()
                for idx, row in plot_df.loc[mask].iterrows():
                    plon, plat = snap_lon_lat_to_boundary(row["port_lon"], row["port_lat"], geometry)
                    plot_df.loc[idx, "plot_port_lon"] = plon
                    plot_df.loc[idx, "plot_port_lat"] = plat
                plot_df["plot_port_lon"] = plot_df["plot_port_lon"].where(plot_df["plot_port_lon"].notna(), plot_df["port_lon"])
                plot_df["plot_port_lat"] = plot_df["plot_port_lat"].where(plot_df["plot_port_lat"].notna(), plot_df["port_lat"])
            else:
                plot_df["plot_port_lon"] = plot_df["port_lon"]
                plot_df["plot_port_lat"] = plot_df["port_lat"]
        else:
            plot_df["plot_lon"] = plot_df["lon"]
            plot_df["plot_lat"] = plot_df["lat"]
            plot_df["plot_port_lon"] = plot_df["port_lon"]
            plot_df["plot_port_lat"] = plot_df["port_lat"]

    # Draw the map content in an inset to keep the title comfortably inside the card.
    map_ax = ax.inset_axes([0.03, 0.05, 0.94, 0.86])
    clear_axis(map_ax)

    bounds_source = []
    if geometry is not None and gpd is not None:
        try:
            gdf = gpd.GeoDataFrame([{"geometry": geometry}], geometry="geometry", crs="EPSG:4326")
            gdf.plot(ax=map_ax, facecolor="#F7F9FC", edgecolor="#3D4856", linewidth=0.9, zorder=1)
            minx, miny, maxx, maxy = geometry.bounds
            bounds_source.append((minx, miny, maxx, maxy))
        except Exception:
            pass

    if not plot_df.empty:
        connected = plot_df.dropna(subset=["plot_port_lon", "plot_port_lat"]).copy()
        for _, row in connected.iterrows():
            map_ax.plot(
                [float(row["plot_lon"]), float(row["plot_port_lon"])],
                [float(row["plot_lat"]), float(row["plot_port_lat"])],
                color="#98A3B3",
                linewidth=0.7,
                linestyle="--",
                alpha=0.55,
                zorder=2,
            )

        map_ax.scatter(
            plot_df["plot_lon"], plot_df["plot_lat"],
            s=38,
            marker="o",
            facecolor="#F28E2B",
            edgecolor="white",
            linewidth=0.7,
            alpha=0.95,
            zorder=4,
            label="Renewable sites",
        )

        ports = connected.drop_duplicates(subset=["port_name", "plot_port_lon", "plot_port_lat"]).copy()
        if not ports.empty:
            map_ax.scatter(
                ports["plot_port_lon"], ports["plot_port_lat"],
                s=85,
                marker="*",
                facecolor="#1F77B4",
                edgecolor="#0B1F33",
                linewidth=0.5,
                zorder=5,
                label="Sea export ports",
            )

        top_ring_df = plot_df.copy()
        if top_ring_df["nh3_delivered_usd_per_t"].notna().any():
            top_ring_df = top_ring_df.sort_values("nh3_delivered_usd_per_t", ascending=True).head(min(3, len(top_ring_df)))
        else:
            top_ring_df = top_ring_df.head(min(3, len(top_ring_df)))
        if not top_ring_df.empty:
            map_ax.scatter(
                top_ring_df["plot_lon"], top_ring_df["plot_lat"],
                s=72,
                marker="o",
                facecolor="none",
                edgecolor="#0B1F33",
                linewidth=1.2,
                zorder=6,
                label="Top sites",
            )

        lons = plot_df["plot_lon"].dropna().tolist() + plot_df["plot_port_lon"].dropna().tolist()
        lats = plot_df["plot_lat"].dropna().tolist() + plot_df["plot_port_lat"].dropna().tolist()
        if lons and lats:
            bounds_source.append((min(lons), min(lats), max(lons), max(lats)))

    if bounds_source:
        minx = min(b[0] for b in bounds_source)
        miny = min(b[1] for b in bounds_source)
        maxx = max(b[2] for b in bounds_source)
        maxy = max(b[3] for b in bounds_source)
        span_x = max(maxx - minx, 0.5)
        span_y = max(maxy - miny, 0.5)
        pad_x = span_x * 0.10
        pad_y = span_y * 0.10
        map_ax.set_xlim(minx - pad_x, maxx + pad_x)
        map_ax.set_ylim(miny - pad_y, maxy + pad_y)
    else:
        map_ax.text(0.5, 0.5, "No map data", ha="center", va="center", transform=map_ax.transAxes, fontsize=11, color=MID_TEXT)

    map_ax.set_aspect("equal", adjustable="box")
    map_ax.grid(False)
    map_ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)

    handles = [
        Line2D([0], [0], marker="o", linestyle="None", markerfacecolor="#F28E2B", markeredgecolor="white", markersize=7, label="Renewable sites"),
        Line2D([0], [0], marker="*", linestyle="None", markerfacecolor="#1F77B4", markeredgecolor="#0B1F33", markersize=10, label="Sea export ports"),
        Line2D([0], [0], marker="o", linestyle="None", markerfacecolor="none", markeredgecolor="#0B1F33", markersize=7, label="Top delivered-cost sites"),
    ]
    # Draw the map legend on a transparent full-card overlay axis, not on
    # map_ax. This prevents the legend from shifting when map_ax changes shape
    # because of aspect-ratio differences between countries.
    legend_host_ax = ax.inset_axes([0.0, 0.0, 1.0, 1.0], zorder=50)
    legend_host_ax.patch.set_alpha(0.0)
    clear_axis(legend_host_ax)

    map_legend_kwargs = {
        "handles": handles,
        "loc": MAP_LEGEND_LOCATION,
        "fontsize": LEGEND_FONTSIZE,
        "ncol": MAP_LEGEND_NCOL,
        "frameon": MAP_LEGEND_FRAMEON,
        "facecolor": "white",
        "edgecolor": CARD_EDGE,
        "framealpha": 0.92,
        "borderpad": 0.5,
        "handlelength": 1.2,
        "bbox_transform": legend_host_ax.transAxes,
    }
    if MAP_LEGEND_BBOX_TO_ANCHOR is not None:
        map_legend_kwargs["bbox_to_anchor"] = MAP_LEGEND_BBOX_TO_ANCHOR
    legend = legend_host_ax.legend(**map_legend_kwargs)
    if legend is not None:
        legend.set_zorder(1000)


def draw_cost_breakdown_panel(ax: plt.Axes, country_group_cost: pd.DataFrame) -> None:
    clear_axis(ax)
    draw_panel_title(ax, "Breakdown of cost items (%)")

    if country_group_cost.empty:
        ax.text(0.5, 0.5, "No cost-breakdown data", ha="center", va="center", transform=ax.transAxes, fontsize=11, color=MID_TEXT)
        return

    available_pathways = [
        pathway for pathway in PATHWAYS
        if pathway["key"] in set(country_group_cost["pathway_key"].dropna().astype(str))
    ]
    if not available_pathways:
        ax.text(0.5, 0.5, "No eligible cost-breakdown data", ha="center", va="center", transform=ax.transAxes, fontsize=11, color=MID_TEXT)
        return

    # Keep all axis labels fully inside the card.
    plot_ax = ax.inset_axes([0.13, 0.22, 0.83, 0.58])
    labels: List[str] = []
    bar_specs: List[Tuple[int, str]] = []
    for year in YEARS_TO_PLOT:
        for pathway in available_pathways:
            labels.append(f"{year} {pathway['short_label']}")
            bar_specs.append((int(year), pathway["key"]))

    y_pos = np.arange(len(bar_specs))[::-1]
    for y, (year, pathway_key_value) in zip(y_pos, bar_specs):
        sub = country_group_cost[
            (country_group_cost["year"].eq(int(year)))
            & (country_group_cost["pathway_key"].eq(pathway_key_value))
        ].copy()
        left = 0.0
        for group_name in COST_GROUP_ORDER:
            val = 0.0
            if not sub.empty:
                vals = sub.loc[sub["parameter_group"].eq(group_name), "group_share_pct"]
                if not vals.empty and pd.notna(vals.iloc[0]):
                    val = float(vals.iloc[0])
            if val <= 0:
                continue
            color = COST_GROUP_COLORS.get(group_name, COST_GROUP_COLORS["Other"])
            plot_ax.barh(y, val, left=left, height=0.62, color=color, edgecolor="white", linewidth=0.5)
            if val >= MIN_SEGMENT_LABEL_PCT:
                text_color = "white" if group_name in {"Capital & financing", "Operations & maintenance", "Carbon costs"} else DARK
                plot_ax.text(left + val / 2, y, f"{val:.1f}", ha="center", va="center", fontsize=TABLE_TINY_FONTSIZE, color=text_color, fontweight="bold")
            left += val

    plot_ax.set_xlim(0, 100)
    plot_ax.set_xticks([0, 20, 40, 60, 80, 100])
    plot_ax.set_xticklabels(["0%", "20%", "40%", "60%", "80%", "100%"], fontsize=AXIS_FONTSIZE)
    plot_ax.set_yticks(y_pos)
    plot_ax.set_yticklabels(labels, fontsize=AXIS_FONTSIZE)
    plot_ax.grid(axis="x", alpha=0.18)
    plot_ax.tick_params(axis="y", length=0, pad=6)
    plot_ax.tick_params(axis="x", pad=2)
    for spine in ["top", "right", "left"]:
        plot_ax.spines[spine].set_visible(False)
    plot_ax.spines["bottom"].set_color(GRID)

    pathways_per_year = len(available_pathways)
    if len(YEARS_TO_PLOT) > 1 and pathways_per_year > 0 and len(bar_specs) > pathways_per_year:
        plot_ax.axhline(y_pos[pathways_per_year - 1] - 0.5, color=GRID, linewidth=0.8, linestyle="--")

    legend_handles = [
        Patch(facecolor=COST_GROUP_COLORS[group], edgecolor="none", label=group)
        for group in COST_GROUP_ORDER
        if group != "Other" or country_group_cost["parameter_group"].eq("Other").any()
    ]
    cost_legend_kwargs = {
        "handles": legend_handles,
        "loc": COST_LEGEND_LOCATION,
        "ncol": COST_LEGEND_NCOL,
        "frameon": COST_LEGEND_FRAMEON,
        "fontsize": LEGEND_FONTSIZE,
        "handlelength": 1.2,
        "columnspacing": 0.9,
    }
    if COST_LEGEND_BBOX_TO_ANCHOR is not None:
        cost_legend_kwargs["bbox_to_anchor"] = COST_LEGEND_BBOX_TO_ANCHOR
    ax.legend(**cost_legend_kwargs)

def get_metric_row(master: pd.DataFrame, country_key_value: str, pathway_key_value: str, year: int) -> pd.Series:
    rows = master[
        master["country_key"].eq(country_key_value)
        & master["pathway_key"].eq(pathway_key_value)
        & master["year"].eq(int(year))
    ]
    if rows.empty:
        return pd.Series(dtype=object)
    return rows.iloc[0]


def text_color_for_value(value: object, metric: str) -> str:
    try:
        value_f = float(value)
    except Exception:
        return DARK
    if not np.isfinite(value_f):
        return DARK
    if metric == "npv" and value_f < 0:
        return NEGATIVE_VALUE_COLOR
    return DARK


def draw_table_cell(
    ax: plt.Axes,
    x: float,
    y: float,
    w: float,
    h: float,
    text: str,
    fontsize: float = TABLE_BODY_FONTSIZE,
    color: str = DARK,
    weight: str = "normal",
    ha: str = "center",
    va: str = "center",
    facecolor: Optional[str] = None,
    edgecolor: str = GRID,
    linewidth: float = 0.6,
    pad_x: float = 0.008,
    zorder: int = 1,
    linespacing: float = 1.25,
) -> None:
    if facecolor is not None:
        ax.add_patch(Rectangle((x, y), w, h, facecolor=facecolor, edgecolor=edgecolor, linewidth=linewidth, zorder=zorder))
    else:
        ax.add_patch(Rectangle((x, y), w, h, facecolor="none", edgecolor=edgecolor, linewidth=linewidth, zorder=zorder))

    if ha == "left":
        tx = x + pad_x
    elif ha == "right":
        tx = x + w - pad_x
    else:
        tx = x + w / 2
    ax.text(tx, y + h / 2, text, ha=ha, va=va, fontsize=fontsize, color=color, fontweight=weight, wrap=True, zorder=zorder + 1, linespacing=linespacing)


def draw_main_insights_table(ax: plt.Axes, country_name: str, country_master: pd.DataFrame) -> None:
    clear_axis(ax)
    draw_panel_title(ax, "Main insights")

    if country_master.empty:
        ax.text(0.5, 0.5, "No NPV/CI summary data", ha="center", va="center", transform=ax.transAxes, fontsize=11, color=MID_TEXT)
        return

    pathways_to_show = [
        pathway for pathway in PATHWAYS
        if pathway["key"] in set(country_master["pathway_key"].dropna().astype(str))
    ]
    if not pathways_to_show:
        ax.text(0.5, 0.5, "No eligible pathway results", ha="center", va="center", transform=ax.transAxes, fontsize=11, color=MID_TEXT)
        return

    left = 0.00
    bottom = 0.025
    width = 1.00
    height = 0.905

    col_widths = [0.090, 0.112, 0.062, 0.062, 0.090, 0.090, 0.073, 0.073, 0.118, 0.118, 0.110, 0.110]
    s = sum(col_widths)
    col_widths = [w / s * width for w in col_widths]
    x_edges = [left]
    for w in col_widths:
        x_edges.append(x_edges[-1] + w)

    header_top_h = 0.115
    header_sub_h = 0.070
    row_h = (height - header_top_h - header_sub_h) / float(len(pathways_to_show))
    y_top = bottom + height
    y_header_top = y_top - header_top_h
    y_header_sub = y_header_top - header_sub_h

    ax.add_patch(Rectangle((left, bottom), width, height, facecolor="none", edgecolor=CARD_EDGE, linewidth=0.9))

    header_groups = [
        (0, 1, "Pathway"),
        (1, 1, "q50 NPV rank\n2030 / 2040"),
        (2, 2, "q50 NPV\n(USD/t)"),
        (4, 2, "Total CI q50\n(kg CO₂e/kg H₂-eq)"),
        (6, 2, "Shipping CI q50\n(kg CO₂e/kg H₂-eq)"),
        (8, 2, "Carbon share of total cost (%)\nSupply / Process / Shipping"),
        (10, 2, "Biggest cost items"),
    ]
    for start_col, span, label in header_groups:
        x = x_edges[start_col]
        w = x_edges[start_col + span] - x
        draw_table_cell(ax, x, y_header_top, w, header_top_h, label, fontsize=TABLE_HEADER_FONTSIZE, color=HEADER_TEXT, weight="bold", facecolor=HEADER_BG, edgecolor="#6B7890", linewidth=0.55, linespacing=1.25)

    subheaders = ["", "", "2030", "2040", "2030", "2040", "2030", "2040", "2030", "2040", "2030", "2040"]
    for i, label in enumerate(subheaders):
        draw_table_cell(ax, x_edges[i], y_header_sub, col_widths[i], header_sub_h, label, fontsize=TABLE_SUBHEADER_FONTSIZE, color=DARK, weight="bold", facecolor=SUBHEADER_BG, edgecolor=GRID, linewidth=0.55)

    ckey = country_master["country_key"].iloc[0]

    for row_idx, pathway in enumerate(pathways_to_show):
        y0 = y_header_sub - (row_idx + 1) * row_h
        row_color = pathway["color"]
        pathway_key_value = pathway["key"]

        r2030 = get_metric_row(country_master, ckey, pathway_key_value, 2030)
        r2040 = get_metric_row(country_master, ckey, pathway_key_value, 2040)

        pathway_text_color = "black" if pathway_key_value == "gray" else "white"
        draw_table_cell(
            ax, x_edges[0], y0, col_widths[0], row_h, pathway["short_label"],
            fontsize=TABLE_BODY_FONTSIZE, color=pathway_text_color, weight="bold", facecolor=row_color, edgecolor=GRID, linewidth=0.6
        )

        rank2030 = clean_text(r2030.get("q50_npv_rank_label", "")) if not r2030.empty else ""
        rank2040 = clean_text(r2040.get("q50_npv_rank_label", "")) if not r2040.empty else ""
        rank_text = f"2030: {rank2030 or '—'}\n2040: {rank2040 or '—'}"
        draw_table_cell(ax, x_edges[1], y0, col_widths[1], row_h, rank_text, fontsize=TABLE_SMALL_FONTSIZE, color=DARK, facecolor="white", linespacing=1.35)

        draw_table_cell(ax, x_edges[2], y0, col_widths[2], row_h, fmt_float(r2030.get("npv_q50", np.nan), 1), fontsize=TABLE_BODY_FONTSIZE, color=text_color_for_value(r2030.get("npv_q50", np.nan), "npv"), facecolor="white")
        draw_table_cell(ax, x_edges[3], y0, col_widths[3], row_h, fmt_float(r2040.get("npv_q50", np.nan), 1), fontsize=TABLE_BODY_FONTSIZE, color=text_color_for_value(r2040.get("npv_q50", np.nan), "npv"), facecolor="white")

        for col_idx, row in [(4, r2030), (5, r2040)]:
            ci_val = row.get("total_ci_q50", np.nan) if not row.empty else np.nan
            ci_color = DARK
            if pd.notna(ci_val) and np.isfinite(float(ci_val)):
                passes_eu_limit = float(ci_val) <= EU_LOW_CARBON_LIMIT_KGCO2_PER_KGH2EQ
                relation = "<" if passes_eu_limit else ">"
                status = "Pass" if passes_eu_limit else "Fail"
                ci_text = f"{fmt_metric(ci_val)}\n{relation}{EU_LOW_CARBON_LIMIT_DISPLAY_TEXT}\n{status}"
                ci_color = POSITIVE_STATUS_COLOR if passes_eu_limit else FAILED_STATUS_COLOR
            else:
                ci_text = "—"
            draw_table_cell(ax, x_edges[col_idx], y0, col_widths[col_idx], row_h, ci_text, fontsize=TABLE_TINY_FONTSIZE, color=ci_color, facecolor="white", linespacing=1.33)

        draw_table_cell(ax, x_edges[6], y0, col_widths[6], row_h, fmt_metric(r2030.get("shipping_ci_q50", np.nan), digits_large=2, digits_small=2), fontsize=TABLE_BODY_FONTSIZE, color=DARK, facecolor="white")
        draw_table_cell(ax, x_edges[7], y0, col_widths[7], row_h, fmt_metric(r2040.get("shipping_ci_q50", np.nan), digits_large=2, digits_small=2), fontsize=TABLE_BODY_FONTSIZE, color=DARK, facecolor="white")

        carbon_2030 = " / ".join([
            fmt_float(r2030.get("supply_chain_carbon_share_pct", np.nan), 1),
            fmt_float(r2030.get("process_carbon_share_pct", np.nan), 1),
            fmt_float(r2030.get("shipping_carbon_share_pct", np.nan), 1),
        ])
        carbon_2040 = " / ".join([
            fmt_float(r2040.get("supply_chain_carbon_share_pct", np.nan), 1),
            fmt_float(r2040.get("process_carbon_share_pct", np.nan), 1),
            fmt_float(r2040.get("shipping_carbon_share_pct", np.nan), 1),
        ])
        draw_table_cell(ax, x_edges[8], y0, col_widths[8], row_h, carbon_2030, fontsize=TABLE_SMALL_FONTSIZE, color=DARK, facecolor="white", linespacing=1.28)
        draw_table_cell(ax, x_edges[9], y0, col_widths[9], row_h, carbon_2040, fontsize=TABLE_SMALL_FONTSIZE, color=DARK, facecolor="white", linespacing=1.28)

        top2030 = str(r2030.get("top_cost_items", "")).strip() if not r2030.empty else ""
        top2040 = str(r2040.get("top_cost_items", "")).strip() if not r2040.empty else ""
        if top2030.lower() == "nan":
            top2030 = ""
        if top2040.lower() == "nan":
            top2040 = ""
        draw_table_cell(ax, x_edges[10], y0, col_widths[10], row_h, top2030 or "—", fontsize=TABLE_SMALL_FONTSIZE, color=DARK, facecolor="white", ha="left", pad_x=0.010, linespacing=1.32)
        draw_table_cell(ax, x_edges[11], y0, col_widths[11], row_h, top2040 or "—", fontsize=TABLE_SMALL_FONTSIZE, color=DARK, facecolor="white", ha="left", pad_x=0.010, linespacing=1.32)

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)


# =============================================================================
# DASHBOARD PLOT
# =============================================================================


def plot_country_dashboard(
    record: Dict[str, str],
    master_summary: pd.DataFrame,
    group_cost_summary: pd.DataFrame,
    map_points: pd.DataFrame,
    geometry_lookup: Dict[str, object],
    output_dir: Path,
) -> Path:
    country_name = clean_text(record.get("country_name", ""))
    iso3 = clean_text(record.get("iso3", "")).upper()
    ckey = record_key(record)
    label = country_name or iso3 or ckey

    country_master = master_summary[master_summary["country_key"].eq(ckey)].copy()
    if country_master.empty:
        fallback_key = normalize_header(country_name)
        country_master = master_summary[master_summary["country_key"].eq(fallback_key)].copy()
    country_group_cost = group_cost_summary[group_cost_summary["country_key"].eq(ckey)].copy() if not group_cost_summary.empty else pd.DataFrame()
    country_map_df = map_points[map_points["country_key"].eq(ckey)].copy() if not map_points.empty else pd.DataFrame()
    geometry = geometry_lookup.get(ckey)

    fig = plt.figure(figsize=FIGURE_SIZE, facecolor=PAGE_BG)

    fig.text(LEFT_COL_X, 0.952, label, fontsize=TITLE_FONTSIZE, fontweight="bold", color=DARK, ha="left", va="top")

    ax_table = setup_card_axis(fig, TABLE_RECT)
    draw_main_insights_table(ax_table, label, country_master)

    ax_map = setup_card_axis(fig, MAP_RECT)
    draw_country_map_panel(ax_map, label, country_map_df, geometry)

    ax_cost = setup_card_axis(fig, COST_RECT)
    draw_cost_breakdown_panel(ax_cost, country_group_cost)

    if SHOW_RANK_NOTE:
        fig.text(
            LEFT_COL_X,
            0.035,
            "Note: q50 NPV rank is computed only across eligible countries loaded in this run; rank 1 = highest q50 NPV. Ineligible country-pathway combinations are removed by the feasibility screen.",
            fontsize=8.5 * FONT_SCALE,
            color=MID_TEXT,
            ha="left",
            va="bottom",
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{OUTPUT_PNG_STEM}_{safe_output_name_part(label)}.png"
    fig.savefig(out_path, dpi=OUTPUT_DPI, bbox_inches="tight")
    plt.close(fig)
    return out_path.resolve()


# =============================================================================
# MAIN
# =============================================================================


def main() -> None:
    print("Project directory:", PROJECT_DIR)
    print("Results root:", RESULTS_ROOT)
    print("Output directory:", OUTPUT_DIR)
    print("Pathways:", ", ".join([p["short_label"] for p in PATHWAYS]))

    if not RESULTS_ROOT.exists():
        raise FileNotFoundError(
            "RESULTS_ROOT does not exist. Edit PROJECT_DIR or RESULTS_ROOT at the top of this script.\n"
            f"Current value: {RESULTS_ROOT}"
        )

    country_list_path = locate_country_list(PROJECT_DIR, RESULTS_ROOT, COUNTRY_LIST_PATH)
    if country_list_path is not None:
        print(f"Using country list: {country_list_path}")
        records = load_country_list(country_list_path)
    else:
        print("No usable country list found. Discovering country folders under trade_version_results.")
        records = discover_country_records_from_results(RESULTS_ROOT)

    if not records:
        raise ValueError("No considered countries were found.")

    print(f"Considered countries: {len(records)}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_warnings: List[str] = []

    print("\nLoading NPV/CI data...")
    run_values, warnings = load_all_run_values(records)
    all_warnings.extend(warnings)
    quant_summary = build_country_quantile_summary(run_values)
    if quant_summary.empty:
        raise ValueError("No country quantile summary could be built.")
    print(f"Loaded run-level rows: {len(run_values):,}")
    print(f"Quantile summary rows: {len(quant_summary):,}")

    print("\nLoading cost-structure data...")
    cost_breakdown, warnings = load_all_cost_breakdowns(records)
    all_warnings.extend(warnings)
    group_cost_summary = build_group_cost_summary(cost_breakdown)
    cost_driver_summary = build_cost_driver_summary(cost_breakdown)
    print(f"Cost-breakdown rows: {len(cost_breakdown):,}")

    print("\nLoading map location/port data...")
    map_points, warnings = load_all_map_points(records)
    all_warnings.extend(warnings)
    print(f"Map rows: {len(map_points):,}")

    print("\nLoading country boundaries...")
    geometry_lookup: Dict[str, object] = {}
    try:
        countries_gdf = load_country_boundaries()
        geometry_lookup = build_country_geometry_lookup(countries_gdf, records)
        print(f"Boundary geometries loaded for {len(geometry_lookup)} country/countries.")
    except Exception as exc:
        all_warnings.append(f"Could not load country boundaries: {exc}")
        print(f"WARNING: could not load country boundaries; maps will still show points where available. Details: {exc}")

    master_summary = build_dashboard_master_summary(quant_summary, cost_driver_summary)
    if master_summary.empty:
        raise ValueError("Dashboard master summary is empty.")

    # Save CSV outputs.
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_MASTER_SUMMARY_CSV.parent.mkdir(parents=True, exist_ok=True)
    master_summary.to_csv(OUTPUT_MASTER_SUMMARY_CSV, index=False)
    print(f"\nSaved master summary CSV:\n{OUTPUT_MASTER_SUMMARY_CSV}")

    if not cost_breakdown.empty:
        OUTPUT_COST_BREAKDOWN_CSV.parent.mkdir(parents=True, exist_ok=True)
        cost_breakdown.to_csv(OUTPUT_COST_BREAKDOWN_CSV, index=False)
        print(f"Saved cost-breakdown CSV:\n{OUTPUT_COST_BREAKDOWN_CSV}")
    if not map_points.empty:
        OUTPUT_MAP_POINTS_CSV.parent.mkdir(parents=True, exist_ok=True)
        map_points.to_csv(OUTPUT_MAP_POINTS_CSV, index=False)
        print(f"Saved map-points CSV:\n{OUTPUT_MAP_POINTS_CSV}")

    # Create one dashboard per country with at least some summary data.
    output_paths: List[Path] = []
    dashboard_keys = set(master_summary["country_key"].dropna().astype(str).tolist())
    for record in records:
        ckey = record_key(record)
        if ckey not in dashboard_keys:
            all_warnings.append(f"{country_label(record.get('country_name', ''), record.get('iso3', ''))}: skipped dashboard because no master summary rows were available")
            continue
        try:
            out = plot_country_dashboard(
                record=record,
                master_summary=master_summary,
                group_cost_summary=group_cost_summary,
                map_points=map_points,
                geometry_lookup=geometry_lookup,
                output_dir=OUTPUT_DIR,
            )
            output_paths.append(out)
            print(f"Saved dashboard: {out}")
        except Exception as exc:
            all_warnings.append(f"{country_label(record.get('country_name', ''), record.get('iso3', ''))}: could not plot dashboard: {exc}")

    if all_warnings:
        OUTPUT_WARNINGS_TXT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_WARNINGS_TXT.write_text("\n".join(all_warnings), encoding="utf-8")
        if PRINT_WARNINGS:
            print("\nWARNING summary:")
            for item in all_warnings[:MAX_WARNINGS_TO_PRINT]:
                print("  - " + item)
            if len(all_warnings) > MAX_WARNINGS_TO_PRINT:
                print(f"  ... {len(all_warnings) - MAX_WARNINGS_TO_PRINT} more warning(s) not printed")
            print(f"\nSaved warning log:\n{OUTPUT_WARNINGS_TXT}")

    if not output_paths:
        raise ValueError("No dashboard PNGs were created. Check the warning log and input paths.")

    print(f"\nCreated {len(output_paths)} country dashboard PNG(s) in:\n{OUTPUT_DIR}")


if __name__ == "__main__":
    main()
