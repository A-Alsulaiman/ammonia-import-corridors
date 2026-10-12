#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Create a combined NPV + carbon-intensity box/whisker figure for selected countries.

Figure layout:
    rows    = ammonia pathway / technology
              Gray ammonia, Blue ammonia, Green ammonia
    columns = NPV, Total carbon intensity

Each horizontal box/whisker bar shows:
    whisker low = controlled by WHISKER_MODE
    q25         = 25th percentile
    q50         = median
    q75         = 75th percentile
    whisker high= controlled by WHISKER_MODE

For every country and year, the script uses simulations 0..3999.

Input NPV workbook for each country:
    C:\Users\user\Documents\Project\AP_IRA_Model\trade_version_results\<Country Name>\npv results\v14_DATASET_hourly_highgas.xlsx

Workbook sheets used:
    NPV
        AP_SMR_NPV -> Gray ammonia
        AP_CCS_NPV -> Blue ammonia
        AP_AEC_NPV -> Green ammonia

    NPV_parameter_effects
        effect_on_NPV rows are summed by time/scenario/simulation/technology
        to create an optional NPV-without-carbon-effect value. The summing
        excludes Process carbon price, Shipping carbon cost, and Embedded
        natural-gas carbon cost. AP BH2S is ignored.

    CI_same_year
        AP SMR_CI -> Gray ammonia production CI, kg CO2/kg H2
        AP CCS_CI -> Blue ammonia production CI, kg CO2/kg H2
        AP AEC_CI -> Green ammonia production CI, kg CO2/kg H2

    Locations
        simulation -> matched to CI_same_year simulation
        selected_port -> used to find the shipping route summary for that run

Year mapping:
    plotted year 2030 uses workbook time = 2027
    plotted year 2040 uses workbook time = 2040

Shipping emissions:
    For each selected_port and plotted year, the script reads:
        <Country folder>\<selected_port>\shipping_route_<selected_port>_Rotterdam_summary.xlsx/csv

    It keeps:
        decarbonisation_scenario == "bau"
        year == 2030 or 2040

    and computes:
        shipping_kgCO2_per_kgNH3 = ef_g_per_tnm_mid * distance_nm / 1e6

    Because CI_same_year is already in kg CO2/kg H2, the shipping value is
    converted from kg CO2/kg NH3 to kg CO2/kg H2-equivalent using ammonia
    stoichiometry before being added:
        shipping_kgCO2_per_kgH2eq = shipping_kgCO2_per_kgNH3 * NH3_KG_PER_KG_H2_EQ
        total_CI_kgCO2_per_kgH2eq = production_CI_kgCO2_per_kgH2 + shipping_kgCO2_per_kgH2eq

Outputs are saved in PROJECT_DIR:
    - npv_ci_pathway_boxwhiskers_2030_2040_600dpi.png
    - npv_ci_run_level_values.csv
    - npv_ci_country_quantile_summary.csv
"""

from __future__ import annotations

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

NPV_RESULTS_FOLDER = "npv results"
NPV_WORKBOOK_STEM = "v14_DATASET_hourly_highgas"
NPV_PARAMETER_EFFECTS_SHEET = "NPV_parameter_effects"
DESTINATION_PORT = "Rotterdam"

# Plotted year -> workbook time value.
# Per your setup: plotted 2030 uses time=2027, plotted 2040 uses time=2040.
YEAR_TO_TIME = {
    2030: 2027,
    2040: 2040,
}
YEARS_TO_PLOT = [2030, 2040]

# Scenario filter in the NPV/CI/Locations sheets. Set None to keep all scenarios.
SCENARIO_FILTER: Optional[str] = "New"

SIMULATION_MIN = 0
SIMULATION_MAX = 3999

# Shipping route scenario.
SHIPPING_DECARB_SCENARIO = "bau"

# If a port summary file is missing, this can fall back to the Locations-sheet
# column shipping_co2_t_per_tnh3, when available. Set False for strict route-summary-only behavior.
ALLOW_LOCATION_SHEET_SHIPPING_FALLBACK = True

# Carbon-intensity basis conversion.
# CI_same_year is interpreted as kg CO2/kg H2. Shipping is first computed as
# kg CO2/kg NH3, then converted to kg CO2/kg H2-equivalent using stoichiometry:
#     N2 + 3 H2 -> 2 NH3
MOLECULAR_WEIGHT_NH3_G_PER_MOL = 17.03052
MOLECULAR_WEIGHT_H2_G_PER_MOL = 2.01588
NH3_KG_PER_KG_H2_EQ = (2 * MOLECULAR_WEIGHT_NH3_G_PER_MOL) / (3 * MOLECULAR_WEIGHT_H2_G_PER_MOL)

# EU low-carbon hydrogen reference line for the carbon-intensity panels.
# Keep this user-editable because regulatory wording/implementation can evolve.
SHOW_EU_LOW_CARBON_LIMIT = True
EU_LOW_CARBON_LIMIT_KGCO2_PER_KGH2EQ = 3.38
EU_LOW_CARBON_LIMIT_LABEL = "EU CI H2 limit"
EU_LOW_CARBON_LIMIT_COLOR = "green"
EU_LOW_CARBON_LIMIT_LINESTYLE = "--"
EU_LOW_CARBON_LIMIT_LINEWIDTH = 1.8
EU_LOW_CARBON_LIMIT_ALPHA = 0.90
EU_LOW_CARBON_LIMIT_LABEL_FONTSIZE = 8.0

# Pathway definitions.
PATHWAYS = [
    {
        "key": "gray",
        "label": "Gray ammonia",
        "npv_col": "AP_SMR_NPV",
        "ci_col": "AP SMR_CI",
        "effects_technology": "AP SMR",
        "color": "0.40",
    },
    {
        "key": "blue",
        "label": "Blue ammonia",
        "npv_col": "AP_CCS_NPV",
        "ci_col": "AP CCS_CI",
        "effects_technology": "AP CCS",
        "color": "tab:blue",
    },
    {
        "key": "green",
        "label": "Green ammonia",
        "npv_col": "AP_AEC_NPV",
        "ci_col": "AP AEC_CI",
        "effects_technology": "AP AEC",
        "color": "tab:green",
    },
]

OUTPUT_DPI = 600
OUTPUT_PNG = PROJECT_DIR / "npv_ci_pathway_boxwhiskers_2030_2040_eligible_only_600dpi.png"
OUTPUT_RUN_VALUES_CSV = PROJECT_DIR / "npv_ci_run_level_values_eligible_only.csv"
OUTPUT_SUMMARY_CSV = PROJECT_DIR / "npv_ci_country_quantile_summary_eligible_only.csv"

SAVE_RUN_VALUES_CSV = True
SAVE_SUMMARY_CSV = True

# Country order options:
#   "country_list"       -> same as List of Countries workbook
#   "npv_gray_2030"      -> sort by 2030 Gray-ammonia NPV q50, descending by default
#   "ci_gray_2030"       -> sort by 2030 Gray-ammonia total-CI q50, descending by default
COUNTRY_SORT_MODE = "country_list"
COUNTRY_SORT_DESCENDING = False

# Axis controls. Leave None for automatic scaling.
NPV_X_AXIS_MIN = -250
NPV_X_AXIS_MAX = 100
CI_X_AXIS_MIN = 0
CI_X_AXIS_MAX = None

# Whisker controls.
# Options:
#   "minmax"      -> original behavior: whiskers are real min and real max
#   "clip"        -> whiskers are real min/max, but capped by your manual limits
#   "percentile"  -> whiskers use percentiles, e.g. 5th and 95th percentile
#   "manual"      -> whiskers use exactly the manual limits below, unless that
#                    would make the whisker smaller than the q25-q75 box
WHISKER_MODE = "percentile"

# Used when WHISKER_MODE = "clip" or "manual".
# Set a side to None if you do not want to control that side.
NPV_WHISKER_MIN: Optional[float] = -300
NPV_WHISKER_MAX: Optional[float] = None

CI_WHISKER_MIN: Optional[float] = 0
CI_WHISKER_MAX: Optional[float] = None

# Used when WHISKER_MODE = "percentile".
# Values must be between 0 and 1.
NPV_WHISKER_LOW_QUANTILE = 0.1
NPV_WHISKER_HIGH_QUANTILE = 0.9

CI_WHISKER_LOW_QUANTILE = 0.1
CI_WHISKER_HIGH_QUANTILE = 0.9

NPV_X_AXIS_LABEL = "NPV"
CI_X_AXIS_LABEL = "Carbon intensity, kg CO₂/kg H₂-eq"
FIGURE_TITLE = "NPV and total kgH₂-equivalent carbon intensity by pathway, country, and year"

SHOW_ISO3_IN_AXIS_LABELS = False
SHOW_LEGEND = True
LEGEND_LOCATION = "lower center"
LEGEND_BBOX_TO_ANCHOR = (0.5, 0.022)
LEGEND_NCOL = 5

# Bottom note controls.
# Coordinates are figure-level: x=0 is the left edge of the figure, y=0 is the
# bottom edge. With NOTE_ALIGN_WITH_NPV_LEFT_EDGE=True, NOTE_X_FIGURE is only a
# fallback and the note starts at the left edge of the NPV plotting area, after
# the country names.
SHOW_BOTTOM_NOTE = True
NOTE_ALIGN_WITH_NPV_LEFT_EDGE = True
NOTE_X_FIGURE = 0.15
NOTE_X_OFFSET = 0.0
NOTE_Y = 0.018
NOTE_HORIZONTAL_ALIGNMENT = "left"
NOTE_VERTICAL_ALIGNMENT = "top"
NOTE_FONTSIZE = 8.3
NOTE_FONT_FAMILY: Optional[str] = None  # Example: "Arial" or "DejaVu Sans"
NOTE_FONT_STYLE = "normal"
NOTE_FONT_WEIGHT = "normal"
NOTE_COLOR = "0.25"
NOTE_LINESPACING = 1.25

# Plot sizing. Height is automatically increased if many countries are loaded.
FIGURE_WIDTH = 16.0
BASE_FIGURE_HEIGHT = 12.0
HEIGHT_PER_COUNTRY_PER_PATHWAY = 0.19

BAR_HEIGHT = 0.22
YEAR_OFFSET = 0.15
WHISKER_LINEWIDTH = 0.5
BOX_LINEWIDTH = 1.0
MEDIAN_LINEWIDTH = 1.6
CAP_HEIGHT_FRACTION = 0.75
GRID_ALPHA = 0.25
YEAR_ALPHA = 0.72
WHISKER_ALPHA = 0.95

YEAR_COLORS = {
    2030: "tab:blue",
    2040: "tab:orange",
}

# Extra NPV bars computed from NPV_parameter_effects after removing carbon effects.
# Default plotting is intentionally limited to Gray ammonia, which adds the 3rd
# and 4th NPV bars in the Gray ammonia NPV panel for 2030 and 2040. The values
# are still calculated for all PATHWAYS above, so AP BH2S is ignored.
SHOW_NPV_WITHOUT_CARBON_EFFECT = True
NPV_WITHOUT_CARBON_EFFECT_METRIC = "npv_without_carbon_effect"
NPV_WITHOUT_CARBON_EFFECT_LABEL = "NPV without carbon effect"
NPV_WITHOUT_CARBON_EFFECT_PATHWAY_KEYS_TO_PLOT = {"gray"}
NPV_WITHOUT_CARBON_EFFECT_EXCLUDED_PARAMETERS = (
    "Process carbon price",
    "Shipping carbon cost",
    "Embedded natural-gas carbon cost",
)
NPV_WITHOUT_CARBON_EFFECT_HATCH = "////"
NPV_WITHOUT_CARBON_EFFECT_FACECOLOR = "white"
NPV_WITHOUT_CARBON_EFFECT_WHISKER_LINESTYLE = "--"
NPV_WITHOUT_CARBON_EFFECT_ALPHA = 0.90
NPV_WITHOUT_CARBON_EFFECT_BAR_HEIGHT = BAR_HEIGHT * 0.85
NPV_WITHOUT_CARBON_EFFECT_OFFSET_SPACING = 0.24

# =============================================================================
# LITERATURE-BASED FEASIBILITY SCREEN
# =============================================================================
# The screen controls what is shown in the headline result figures. The modelled
# values are still loaded from the workbooks, but country-pathway combinations
# that are not eligible are removed from screened plots and screened output CSVs.
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


# Printing/progress controls.
PRINT_PROGRESS = True
PRINT_WARNINGS = True


# =============================================================================
# TEXT / PATH HELPERS
# =============================================================================

def normalize_header(value: object) -> str:
    """Normalize a text/header value for robust matching."""
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
    """Filesystem-safe token matching the style used by the shipping route script."""
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
    if SHOW_ISO3_IN_AXIS_LABELS and iso3:
        return f"{country} ({iso3})"
    return country


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


def normalized_scenario(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.strip().lower()
    text = text.replace("decarbonized", "decarbonised")
    return re.sub(r"[^a-z0-9]+", "", text)


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
    """Fallback when no country-list file is found: discover country folders with npv results."""
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
    """Find v14_DATASET_hourly_highgas workbook inside the country's npv results folder."""
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
    """Read one shipping route summary file with tolerant column names."""
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


def shipping_ci_values_from_summary(
    summary_path: Path,
    plotted_year: int,
) -> Tuple[float, float, str]:
    """
    Compute shipping CI from a route summary.

    First compute the natural shipping basis:
        kgCO2/kgNH3 = ef_g_per_tnm_mid * distance_nm / 1e6

    Then convert to the plot/model CI basis:
        kgCO2/kgH2-eq = kgCO2/kgNH3 * NH3_KG_PER_KG_H2_EQ
    """
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
) -> Tuple[Dict[Tuple[str, int], Tuple[float, str]], List[str]]:
    """Build a {(port, year): (shipping_ci, source)} lookup for all selected ports in a country."""
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


def required_npv_parameter_effects_columns() -> List[str]:
    return [
        "time",
        "scenario",
        "simulation",
        "technology",
        "parameter",
        "effect_on_NPV",
    ]


def pathway_effect_technology_map() -> Dict[str, str]:
    """Map normalized NPV_parameter_effects technology names to pathway keys."""
    mapping: Dict[str, str] = {}
    for pathway in PATHWAYS:
        key = str(pathway["key"])
        candidates = [
            pathway.get("effects_technology", ""),
            str(pathway.get("npv_col", "")).replace("_NPV", "").replace("_", " "),
            str(pathway.get("ci_col", "")).replace("_CI", ""),
        ]
        for candidate in candidates:
            normalized = normalize_header(candidate)
            if normalized:
                mapping[normalized] = key
    return mapping


def read_npv_inputs(workbook_path: Path) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Optional[pd.DataFrame]]:
    """Read NPV, CI_same_year, Locations, and optional NPV_parameter_effects sheets."""
    suffix = workbook_path.suffix.lower()
    if suffix not in {".xlsx", ".xlsm", ".xls"}:
        raise ValueError(f"NPV workbook must be Excel because sheets are required: {workbook_path}")

    npv = pd.read_excel(workbook_path, sheet_name="NPV")
    ci = pd.read_excel(workbook_path, sheet_name="CI_same_year")
    locations = pd.read_excel(workbook_path, sheet_name="Locations")

    npv_parameter_effects: Optional[pd.DataFrame]
    try:
        npv_parameter_effects = pd.read_excel(workbook_path, sheet_name=NPV_PARAMETER_EFFECTS_SHEET)
    except ValueError:
        npv_parameter_effects = None

    # Validate expected columns with tolerant names.
    for col in required_npv_columns():
        pick_column(npv, [col], required=True, purpose=f"NPV sheet column {col!r}")
    for col in required_ci_columns():
        pick_column(ci, [col], required=True, purpose=f"CI_same_year sheet column {col!r}")
    pick_column(locations, ["time"], required=True, purpose="Locations time column")
    pick_column(locations, ["simulation"], required=True, purpose="Locations simulation column")
    pick_column(locations, ["selected_port", "canonical_main_port", "nearest_port"], contains_any=["selectedport"], required=True, purpose="Locations selected port column")

    if npv_parameter_effects is not None:
        for col in required_npv_parameter_effects_columns():
            pick_column(
                npv_parameter_effects,
                [col],
                required=True,
                purpose=f"{NPV_PARAMETER_EFFECTS_SHEET} sheet column {col!r}",
            )

    return npv, ci, locations, npv_parameter_effects


def apply_run_filters(df: pd.DataFrame, time_value: int, scenario_filter: Optional[str]) -> pd.DataFrame:
    """Filter one NPV/CI/Locations table by time, scenario, and simulation range."""
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

    out = out.loc[mask].copy()
    return out


def build_npv_without_carbon_effects_for_year(
    effects_df: Optional[pd.DataFrame],
    time_value: int,
    scenario_filter: Optional[str],
) -> Tuple[pd.DataFrame, List[str]]:
    """
    Sum effect_on_NPV by run and technology, excluding carbon-effect parameters.

    The returned table has one row per simulation/pathway in PATHWAYS. AP BH2S
    and any other technology not listed in PATHWAYS are ignored.
    """
    warnings: List[str] = []

    if not SHOW_NPV_WITHOUT_CARBON_EFFECT or effects_df is None:
        return pd.DataFrame(), warnings

    try:
        technology_col = pick_column(effects_df, ["technology"], required=True, purpose="NPV parameter effects technology column")
        parameter_col = pick_column(effects_df, ["parameter"], required=True, purpose="NPV parameter effects parameter column")
        effect_col = pick_column(effects_df, ["effect_on_NPV"], required=True, purpose="NPV parameter effects effect_on_NPV column")
        npv_col = pick_column(effects_df, ["NPV"], required=False, purpose="NPV parameter effects NPV column")
    except Exception as exc:
        warnings.append(f"could not read {NPV_PARAMETER_EFFECTS_SHEET}: {exc}")
        return pd.DataFrame(), warnings

    try:
        work = apply_run_filters(effects_df, int(time_value), scenario_filter)
    except Exception as exc:
        warnings.append(f"could not filter {NPV_PARAMETER_EFFECTS_SHEET}: {exc}")
        return pd.DataFrame(), warnings

    if work.empty:
        return pd.DataFrame(), warnings

    tech_to_pathway = pathway_effect_technology_map()
    carbon_parameter_keys = {normalize_header(name) for name in NPV_WITHOUT_CARBON_EFFECT_EXCLUDED_PARAMETERS}

    work = work.copy()
    work["__technology_key"] = work[technology_col].map(normalize_header)
    work["pathway_key"] = work["__technology_key"].map(tech_to_pathway)
    work = work.loc[work["pathway_key"].notna()].copy()

    if work.empty:
        warnings.append(
            f"no matching technologies from PATHWAYS were found in {NPV_PARAMETER_EFFECTS_SHEET} "
            f"for time={time_value}"
        )
        return pd.DataFrame(), warnings

    work["__parameter_key"] = work[parameter_col].map(normalize_header)
    work["__effect_on_npv"] = pd.to_numeric(work[effect_col], errors="coerce")
    work["__is_excluded_carbon_parameter"] = work["__parameter_key"].isin(carbon_parameter_keys)

    valid_effects = work.dropna(subset=["__effect_on_npv"]).copy()
    if valid_effects.empty:
        warnings.append(f"all effect_on_NPV values are blank/non-numeric in {NPV_PARAMETER_EFFECTS_SHEET} for time={time_value}")
        return pd.DataFrame(), warnings

    group_cols = ["__simulation", "pathway_key"]

    included = valid_effects.loc[~valid_effects["__is_excluded_carbon_parameter"]].copy()
    no_carbon = (
        included.groupby(group_cols, dropna=False, as_index=False)
        .agg(
            npv_without_carbon_effect=("__effect_on_npv", "sum"),
            npv_without_carbon_effect_n_parameters=("__effect_on_npv", "count"),
        )
    )

    carbon_excluded = (
        valid_effects.loc[valid_effects["__is_excluded_carbon_parameter"]]
        .groupby(group_cols, dropna=False, as_index=False)
        .agg(npv_excluded_carbon_effect=("__effect_on_npv", "sum"))
    )

    full_sum = (
        valid_effects.groupby(group_cols, dropna=False, as_index=False)
        .agg(npv_effects_full_sum=("__effect_on_npv", "sum"))
    )

    out = no_carbon.merge(carbon_excluded, on=group_cols, how="left").merge(full_sum, on=group_cols, how="left")
    out["npv_excluded_carbon_effect"] = out["npv_excluded_carbon_effect"].fillna(0.0)
    out["workbook_time"] = int(time_value)
    out["npv_without_carbon_effect_source"] = NPV_PARAMETER_EFFECTS_SHEET

    if npv_col is not None:
        npv_reference = (
            valid_effects.groupby(group_cols, dropna=False, as_index=False)
            .agg(npv_from_parameter_effects_sheet=(npv_col, "first"))
        )
        out = out.merge(npv_reference, on=group_cols, how="left")
    else:
        out["npv_from_parameter_effects_sheet"] = np.nan

    return out, warnings


def lookup_series_by_simulation(
    source: pd.DataFrame,
    pathway_key: str,
    value_col: str,
    target_simulations: pd.Series,
) -> pd.Series:
    """Return a Series aligned to target_simulations by simulation number."""
    if source.empty or value_col not in source.columns:
        return pd.Series(np.nan, index=target_simulations.index, dtype=float)

    sub = source.loc[source["pathway_key"].astype(str).eq(str(pathway_key)), ["__simulation", value_col]].copy()
    if sub.empty:
        return pd.Series(np.nan, index=target_simulations.index, dtype=float)

    sub["__simulation_int"] = pd.to_numeric(sub["__simulation"], errors="coerce")
    sub = sub.dropna(subset=["__simulation_int"]).copy()
    sub["__simulation_int"] = sub["__simulation_int"].astype(int)
    lookup = dict(zip(sub["__simulation_int"], pd.to_numeric(sub[value_col], errors="coerce")))

    target = pd.to_numeric(target_simulations, errors="coerce")
    return target.map(lambda value: lookup.get(int(value), np.nan) if pd.notna(value) else np.nan)


def load_run_level_values_for_country(
    record: Dict[str, str],
    country_order: int,
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
        print(f"  {country_label(country_name, iso3)}: reading {workbook_path}")

    npv_df, ci_df, loc_df, npv_parameter_effects_df = read_npv_inputs(workbook_path)
    if SHOW_NPV_WITHOUT_CARBON_EFFECT and npv_parameter_effects_df is None:
        warnings.append(
            f"{country_label(country_name, iso3)}: sheet {NPV_PARAMETER_EFFECTS_SHEET!r} not found; "
            "NPV-without-carbon-effect bars cannot be calculated for this country"
        )

    shipping_lookup, ship_warnings = build_shipping_ci_lookup_for_country(country_dir, loc_df, YEARS_TO_PLOT)
    warnings.extend(ship_warnings)

    # Resolve actual column names once.
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
        npv_without_carbon_year, no_carbon_warnings = build_npv_without_carbon_effects_for_year(
            npv_parameter_effects_df,
            workbook_time,
            SCENARIO_FILTER,
        )
        warnings.extend(
            f"{country_label(country_name, iso3)} | {plotted_year}: {message}"
            for message in no_carbon_warnings
        )

        if npv_year.empty:
            warnings.append(f"{country_label(country_name, iso3)} | {plotted_year}: no NPV rows after filtering")
            continue
        if ci_year.empty:
            warnings.append(f"{country_label(country_name, iso3)} | {plotted_year}: no CI_same_year rows after filtering")
            continue
        if loc_year.empty:
            warnings.append(f"{country_label(country_name, iso3)} | {plotted_year}: no Locations rows after filtering")
            continue

        # Keep columns needed for merge.
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
            port_key = row.get("selected_port_key", "")
            if port_key:
                hit = shipping_lookup.get((port_key, int(plotted_year)))
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

            npv_without_carbon_values = lookup_series_by_simulation(
                npv_without_carbon_year,
                key,
                "npv_without_carbon_effect",
                merged["__simulation"],
            )
            npv_excluded_carbon_effect = lookup_series_by_simulation(
                npv_without_carbon_year,
                key,
                "npv_excluded_carbon_effect",
                merged["__simulation"],
            )
            npv_effects_full_sum = lookup_series_by_simulation(
                npv_without_carbon_year,
                key,
                "npv_effects_full_sum",
                merged["__simulation"],
            )
            npv_from_parameter_effects_sheet = lookup_series_by_simulation(
                npv_without_carbon_year,
                key,
                "npv_from_parameter_effects_sheet",
                merged["__simulation"],
            )
            npv_without_carbon_n_parameters = lookup_series_by_simulation(
                npv_without_carbon_year,
                key,
                "npv_without_carbon_effect_n_parameters",
                merged["__simulation"],
            )

            frame = pd.DataFrame({
                "country": country_name,
                "iso3": iso3,
                "country_order": country_order,
                "year": int(plotted_year),
                "workbook_time": workbook_time,
                "scenario_filter": SCENARIO_FILTER if SCENARIO_FILTER is not None else "ALL",
                "simulation": merged["__simulation"].astype("Int64"),
                "pathway_key": key,
                "pathway": pathway["label"],
                "npv": npv_values,
                "npv_without_carbon_effect": npv_without_carbon_values,
                "npv_excluded_carbon_effect": npv_excluded_carbon_effect,
                "npv_effects_full_sum": npv_effects_full_sum,
                "npv_from_parameter_effects_sheet": npv_from_parameter_effects_sheet,
                "npv_without_carbon_effect_n_parameters": npv_without_carbon_n_parameters,
                "npv_without_carbon_effect_source": np.where(
                    pd.to_numeric(npv_without_carbon_values, errors="coerce").notna(),
                    NPV_PARAMETER_EFFECTS_SHEET,
                    "",
                ),
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


# =============================================================================
# QUANTILE SUMMARY
# =============================================================================

def metric_whisker_limits(metric: str) -> Tuple[Optional[float], Optional[float]]:
    """Return manual whisker min/max settings for one metric."""
    if metric in {"npv", NPV_WITHOUT_CARBON_EFFECT_METRIC}:
        return NPV_WHISKER_MIN, NPV_WHISKER_MAX
    if metric == "total_ci":
        return CI_WHISKER_MIN, CI_WHISKER_MAX
    return None, None


def metric_whisker_quantiles(metric: str) -> Tuple[float, float]:
    """Return lower/upper quantile settings for one metric."""
    if metric in {"npv", NPV_WITHOUT_CARBON_EFFECT_METRIC}:
        return float(NPV_WHISKER_LOW_QUANTILE), float(NPV_WHISKER_HIGH_QUANTILE)
    if metric == "total_ci":
        return float(CI_WHISKER_LOW_QUANTILE), float(CI_WHISKER_HIGH_QUANTILE)
    return 0.0, 1.0


def quantile_summary(values: Sequence[float], metric: str = "") -> Dict[str, float]:
    """
    Compute q25/q50/q75 plus configurable whiskers.

    WHISKER_MODE controls the low/high whisker calculation:
        - minmax: original real min/max
        - clip: real min/max capped by manual limits
        - percentile: lower/upper percentile whiskers
        - manual: exactly manual limits, unless that would make the whisker
          smaller than the q25-q75 box
    """
    numeric = pd.to_numeric(pd.Series(list(values)), errors="coerce")
    arr = numeric.dropna().to_numpy(dtype=float)
    arr = arr[np.isfinite(arr)]

    if arr.size == 0:
        return {
            "whisker_low": np.nan,
            "q25": np.nan,
            "q50": np.nan,
            "q75": np.nan,
            "whisker_high": np.nan,
            "n_values": 0,
        }

    q25 = float(np.quantile(arr, 0.25))
    q50 = float(np.quantile(arr, 0.50))
    q75 = float(np.quantile(arr, 0.75))

    mode = str(WHISKER_MODE).strip().lower()

    if mode == "minmax":
        whisker_low = float(np.min(arr))
        whisker_high = float(np.max(arr))

    elif mode == "clip":
        raw_low = float(np.min(arr))
        raw_high = float(np.max(arr))
        user_low, user_high = metric_whisker_limits(metric)

        whisker_low = raw_low if user_low is None else max(raw_low, float(user_low))
        whisker_high = raw_high if user_high is None else min(raw_high, float(user_high))

    elif mode == "percentile":
        low_q, high_q = metric_whisker_quantiles(metric)

        if not (0.0 <= low_q <= high_q <= 1.0):
            raise ValueError(
                f"Invalid whisker quantiles for {metric}: "
                f"{low_q}, {high_q}. They must satisfy 0 <= low <= high <= 1."
            )

        whisker_low = float(np.quantile(arr, low_q))
        whisker_high = float(np.quantile(arr, high_q))

    elif mode == "manual":
        user_low, user_high = metric_whisker_limits(metric)

        whisker_low = float(np.min(arr)) if user_low is None else float(user_low)
        whisker_high = float(np.max(arr)) if user_high is None else float(user_high)

    else:
        raise ValueError(
            "WHISKER_MODE must be 'minmax', 'clip', 'percentile', or 'manual'."
        )

    if whisker_low > whisker_high:
        raise ValueError(
            f"Invalid whisker range for {metric}: "
            f"whisker_low={whisker_low}, whisker_high={whisker_high}"
        )

    # Keep the drawing geometrically valid: whiskers should not sit inside
    # the q25-q75 box. For example, if you set a manual max below q75,
    # the plotted upper whisker will be lifted to q75.
    whisker_low = min(float(whisker_low), q25)
    whisker_high = max(float(whisker_high), q75)

    return {
        "whisker_low": float(whisker_low),
        "q25": q25,
        "q50": q50,
        "q75": q75,
        "whisker_high": float(whisker_high),
        "n_values": int(arr.size),
    }


def build_country_quantile_summary(run_values: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, object]] = []

    metric_specs = [
        ("npv", "npv", NPV_X_AXIS_LABEL),
        ("total_ci", "total_ci_kgco2_per_kgh2eq", CI_X_AXIS_LABEL),
    ]
    if SHOW_NPV_WITHOUT_CARBON_EFFECT and NPV_WITHOUT_CARBON_EFFECT_METRIC in run_values.columns:
        metric_specs.append((NPV_WITHOUT_CARBON_EFFECT_METRIC, NPV_WITHOUT_CARBON_EFFECT_METRIC, NPV_X_AXIS_LABEL))

    group_cols = ["country", "iso3", "country_order", "year", "pathway_key", "pathway"]
    for keys, group in run_values.groupby(group_cols, dropna=False):
        country, iso3, country_order, year, pathway_key, pathway = keys
        eligible_for_main_results = bool(group["eligible_for_main_results"].iloc[0]) if "eligible_for_main_results" in group.columns else True
        screen_status = str(group["screening_status"].iloc[0]) if "screening_status" in group.columns else "eligible"

        for metric, value_col, unit in metric_specs:
            if value_col not in group.columns:
                continue
            if not pd.to_numeric(group[value_col], errors="coerce").notna().any():
                continue
            qs = quantile_summary(group[value_col].tolist(), metric=metric)
            rows.append({
                "country": country,
                "iso3": iso3,
                "country_order": int(country_order),
                "year": int(year),
                "pathway_key": pathway_key,
                "pathway": pathway,
                "eligible_for_main_results": eligible_for_main_results,
                "screening_status": screen_status,
                "metric": metric,
                "unit": unit,
                **qs,
            })

    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    return summary.sort_values(["pathway_key", "country_order", "year", "metric"]).reset_index(drop=True)


def ordered_country_keys(summary: pd.DataFrame) -> List[Tuple[str, str]]:
    """Return ordered list of (country, iso3) pairs."""
    countries = summary[["country", "iso3", "country_order"]].drop_duplicates().copy()
    mode = str(COUNTRY_SORT_MODE).strip().lower()

    if mode == "country_list":
        countries = countries.sort_values("country_order")
    elif mode in {"npv_gray_2030", "ci_gray_2030"}:
        metric = "npv" if mode == "npv_gray_2030" else "total_ci"
        ranking = summary[
            (summary["year"].astype(int) == 2030)
            & (summary["pathway_key"].astype(str) == "gray")
            & (summary["metric"].astype(str) == metric)
        ][["country", "iso3", "q50"]].copy()

        countries = countries.merge(ranking, on=["country", "iso3"], how="left")
        countries = countries.sort_values(
            ["q50", "country_order"],
            ascending=[not bool(COUNTRY_SORT_DESCENDING), True],
            na_position="last",
        )
    else:
        raise ValueError(
            "COUNTRY_SORT_MODE must be 'country_list', 'npv_gray_2030', or 'ci_gray_2030'."
        )

    return [(str(row["country"]), str(row["iso3"])) for _, row in countries.iterrows()]


# =============================================================================
# PLOTTING
# =============================================================================

def get_stat_row(
    summary: pd.DataFrame,
    country: str,
    iso3: str,
    year: int,
    pathway_key: str,
    metric: str,
) -> Optional[pd.Series]:
    sub = summary[
        (summary["country"].astype(str) == str(country))
        & (summary["iso3"].astype(str) == str(iso3))
        & (summary["year"].astype(int) == int(year))
        & (summary["pathway_key"].astype(str) == str(pathway_key))
        & (summary["metric"].astype(str) == str(metric))
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
    facecolor=None,
    edgecolor=None,
    hatch: Optional[str] = None,
    whisker_linestyle: str = "-",
    box_alpha: float = YEAR_ALPHA,
) -> None:
    """Draw one horizontal q25/q50/q75 box with configurable whiskers."""
    x_min = float(stats["whisker_low"])
    x_q25 = float(stats["q25"])
    x_q50 = float(stats["q50"])
    x_q75 = float(stats["q75"])
    x_max = float(stats["whisker_high"])

    if not all(np.isfinite(v) for v in [x_min, x_q25, x_q50, x_q75, x_max]):
        return

    edgecolor = color if edgecolor is None else edgecolor
    facecolor = color if facecolor is None else facecolor
    cap_h = height * CAP_HEIGHT_FRACTION

    # Whisker line.
    ax.plot(
        [x_min, x_max],
        [y, y],
        color=edgecolor,
        linewidth=WHISKER_LINEWIDTH,
        linestyle=whisker_linestyle,
        alpha=WHISKER_ALPHA,
        zorder=2,
    )
    ax.plot([x_min, x_min], [y - cap_h / 2.0, y + cap_h / 2.0], color=edgecolor, linewidth=WHISKER_LINEWIDTH, linestyle=whisker_linestyle, zorder=2)
    ax.plot([x_max, x_max], [y - cap_h / 2.0, y + cap_h / 2.0], color=edgecolor, linewidth=WHISKER_LINEWIDTH, linestyle=whisker_linestyle, zorder=2)

    # q25-q75 box.
    box_left = min(x_q25, x_q75)
    box_width = abs(x_q75 - x_q25)
    if box_width <= 1e-12:
        ax.plot([x_q25, x_q75], [y, y], color=edgecolor, linewidth=BOX_LINEWIDTH + 2.0, alpha=box_alpha, zorder=3)
    else:
        rect = Rectangle(
            (box_left, y - height / 2.0),
            box_width,
            height,
            facecolor=facecolor,
            edgecolor=edgecolor,
            linewidth=BOX_LINEWIDTH,
            hatch=hatch,
            alpha=box_alpha,
            zorder=3,
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


def apply_metric_axis_limits(ax, metric: str) -> None:
    if metric == "npv":
        xmin = NPV_X_AXIS_MIN
        xmax = NPV_X_AXIS_MAX
    elif metric == "total_ci":
        xmin = CI_X_AXIS_MIN
        xmax = CI_X_AXIS_MAX
    else:
        xmin = None
        xmax = None

    current_xmin, current_xmax = ax.get_xlim()
    if xmin is None:
        xmin = current_xmin
    if xmax is None:
        xmax = current_xmax

    if metric == "total_ci" and SHOW_EU_LOW_CARBON_LIMIT:
        limit = float(EU_LOW_CARBON_LIMIT_KGCO2_PER_KGH2EQ)
        if np.isfinite(limit):
            # Keep the reference line visible when users leave CI_X_AXIS_MAX as automatic.
            if CI_X_AXIS_MIN is None:
                xmin = min(float(xmin), limit * 0.95)
            if CI_X_AXIS_MAX is None:
                xmax = max(float(xmax), limit * 1.08)

    if xmin >= xmax:
        raise ValueError(f"Invalid axis limits for {metric}: min {xmin} must be below max {xmax}.")
    ax.set_xlim(xmin, xmax)


def draw_eu_low_carbon_limit(ax) -> None:
    """Draw the EU low-carbon hydrogen CI threshold on a CI panel."""
    if not SHOW_EU_LOW_CARBON_LIMIT:
        return

    limit = float(EU_LOW_CARBON_LIMIT_KGCO2_PER_KGH2EQ)
    if not np.isfinite(limit):
        return

    ax.axvline(
        limit,
        color=EU_LOW_CARBON_LIMIT_COLOR,
        linestyle=EU_LOW_CARBON_LIMIT_LINESTYLE,
        linewidth=EU_LOW_CARBON_LIMIT_LINEWIDTH,
        alpha=EU_LOW_CARBON_LIMIT_ALPHA,
        zorder=5,
    )

    label = f"{EU_LOW_CARBON_LIMIT_LABEL}: {limit:.2f} kg CO₂/kg H₂-eq"
    ax.annotate(
        label,
        xy=(limit, 0.02),
        xycoords=("data", "axes fraction"),
        xytext=(4, 0),
        textcoords="offset points",
        rotation=90,
        ha="left",
        va="bottom",
        fontsize=EU_LOW_CARBON_LIMIT_LABEL_FONTSIZE,
        color=EU_LOW_CARBON_LIMIT_COLOR,
        bbox=dict(boxstyle="round,pad=0.15", facecolor="white", edgecolor="none", alpha=0.72),
        zorder=6,
    )


def country_display_labels(summary: pd.DataFrame, country_keys: List[Tuple[str, str]]) -> List[str]:
    labels = []
    for country, iso3 in country_keys:
        labels.append(country_label(country, iso3))
    return labels


def build_bottom_note_text() -> str:
    """Return the three-line note printed below the legend."""
    note_lines = [
        "Box = q25–q75; black line = q50; whiskers to q10 and q90",
        (
            f"Shipping CI from {SHIPPING_DECARB_SCENARIO.upper()} route summaries = "
            "ef_g_per_tnm_mid × distance_nm / 1e6, converted from kgCO₂/kgNH₃ "
            f"to kgCO₂/kgH₂-eq using {NH3_KG_PER_KG_H2_EQ:.3f} kg NH₃/kg H₂-eq"
        ),
    ]
    if SHOW_NPV_WITHOUT_CARBON_EFFECT:
        excluded = ", ".join(NPV_WITHOUT_CARBON_EFFECT_EXCLUDED_PARAMETERS)
        note_lines.append(
            f"Hatched Gray-ammonia NPV bars = {NPV_WITHOUT_CARBON_EFFECT_LABEL}; "
            f"summed from {NPV_PARAMETER_EFFECTS_SHEET} excluding {excluded}"
        )
    return "\n".join(note_lines)


def bottom_note_x_position(axes: np.ndarray) -> float:
    """Return the figure x-position for the bottom note."""
    if NOTE_ALIGN_WITH_NPV_LEFT_EDGE:
        try:
            return float(axes[0, 0].get_position().x0) + float(NOTE_X_OFFSET)
        except Exception:
            pass
    return float(NOTE_X_FIGURE) + float(NOTE_X_OFFSET)


def bottom_note_font_kwargs() -> Dict[str, object]:
    """Collect user-editable font settings for the bottom note."""
    kwargs: Dict[str, object] = {
        "fontsize": NOTE_FONTSIZE,
        "color": NOTE_COLOR,
        "fontstyle": NOTE_FONT_STYLE,
        "fontweight": NOTE_FONT_WEIGHT,
        "linespacing": NOTE_LINESPACING,
    }
    if NOTE_FONT_FAMILY:
        kwargs["fontfamily"] = NOTE_FONT_FAMILY
    return kwargs


def should_plot_npv_without_carbon_effect(pathway_key: str) -> bool:
    """True when the NPV-without-carbon-effect bars should be drawn for a pathway."""
    if not SHOW_NPV_WITHOUT_CARBON_EFFECT:
        return False

    keys = NPV_WITHOUT_CARBON_EFFECT_PATHWAY_KEYS_TO_PLOT
    if isinstance(keys, str):
        return keys.strip().upper() == "ALL" or str(pathway_key).strip().lower() == keys.strip().lower()

    normalized_keys = {str(key).strip().lower() for key in keys}
    return str(pathway_key).strip().lower() in normalized_keys


def centered_offsets(items: Sequence[object], preferred_spacing: float, max_total_span: float = 0.92) -> Dict[object, float]:
    """Return centered y-offsets, scaled to stay within one country row."""
    items = list(items)
    if not items:
        return {}
    if len(items) == 1:
        return {items[0]: 0.0}

    max_spacing = max(0.02, (float(max_total_span) - float(BAR_HEIGHT)) / float(len(items) - 1))
    spacing = min(float(preferred_spacing), max_spacing)
    return {item: (idx - (len(items) - 1) / 2.0) * spacing for idx, item in enumerate(items)}


def plot_items_for_npv_with_without_carbon() -> List[Tuple[int, str]]:
    """Order the four Gray-ammonia NPV bars: original years, then no-carbon years."""
    original_items = [(int(year), "original") for year in YEARS_TO_PLOT]
    no_carbon_items = [(int(year), "without_carbon") for year in YEARS_TO_PLOT]
    return original_items + no_carbon_items


def plot_combined_pathway_figure(summary: pd.DataFrame, output_png: Path) -> None:
    if summary.empty:
        raise ValueError("No country quantile summary could be plotted.")

    pathway_country_keys: Dict[str, List[Tuple[str, str]]] = {}
    pathway_summary_by_key: Dict[str, pd.DataFrame] = {}

    for pathway in PATHWAYS:
        pathway_key = pathway["key"]
        pathway_summary = summary[summary["pathway_key"].astype(str).eq(str(pathway_key))].copy()
        if APPLY_FEASIBILITY_SCREEN and "eligible_for_main_results" in pathway_summary.columns:
            pathway_summary = pathway_summary[pathway_summary["eligible_for_main_results"].astype(bool)].copy()

        pathway_summary_by_key[pathway_key] = pathway_summary
        pathway_country_keys[pathway_key] = ordered_country_keys(pathway_summary) if not pathway_summary.empty else []

    if not any(pathway_country_keys.values()):
        raise ValueError("No eligible countries available for plotting after the feasibility screen.")

    n_pathways = len(PATHWAYS)
    row_country_counts = [max(1, len(pathway_country_keys[p["key"]])) for p in PATHWAYS]
    fig_height = max(
        BASE_FIGURE_HEIGHT,
        2.0 + sum(max(2.2, HEIGHT_PER_COUNTRY_PER_PATHWAY * count) for count in row_country_counts),
    )

    fig, axes = plt.subplots(
        nrows=n_pathways,
        ncols=2,
        figsize=(FIGURE_WIDTH, fig_height),
        sharex="col",
        sharey=False,
        gridspec_kw={"height_ratios": row_country_counts},
    )

    axes = np.asarray(axes).reshape(n_pathways, 2)

    year_offsets: Dict[int, float] = {}
    if len(YEARS_TO_PLOT) == 1:
        year_offsets[int(YEARS_TO_PLOT[0])] = 0.0
    else:
        for i, year in enumerate(YEARS_TO_PLOT):
            centered = i - (len(YEARS_TO_PLOT) - 1) / 2.0
            year_offsets[int(year)] = centered * (2.0 * YEAR_OFFSET)

    npv_with_without_carbon_items = plot_items_for_npv_with_without_carbon()
    npv_with_without_carbon_offsets = centered_offsets(
        npv_with_without_carbon_items,
        NPV_WITHOUT_CARBON_EFFECT_OFFSET_SPACING,
    )

    metric_specs = [("npv", NPV_X_AXIS_LABEL), ("total_ci", CI_X_AXIS_LABEL)]

    for row_idx, pathway in enumerate(PATHWAYS):
        pathway_key = pathway["key"]
        pathway_label = pathway["label"]
        country_keys = pathway_country_keys[pathway_key]
        pathway_summary = pathway_summary_by_key[pathway_key]
        y_base = np.arange(len(country_keys), dtype=float)

        for col_idx, (metric, x_label) in enumerate(metric_specs):
            ax = axes[row_idx, col_idx]

            if country_keys:
                plot_no_carbon_npv_here = metric == "npv" and should_plot_npv_without_carbon_effect(pathway_key)

                for country_idx, (country, iso3) in enumerate(country_keys):
                    for year in YEARS_TO_PLOT:
                        year_int = int(year)
                        color = YEAR_COLORS.get(year_int, None)

                        stats = get_stat_row(pathway_summary, country, iso3, year_int, pathway_key, metric)
                        if stats is not None:
                            if plot_no_carbon_npv_here:
                                y_offset = npv_with_without_carbon_offsets.get((year_int, "original"), year_offsets[year_int])
                            else:
                                y_offset = year_offsets[year_int]
                            y = y_base[country_idx] + y_offset
                            draw_horizontal_quantile_bar(ax, y, stats, color=color, height=BAR_HEIGHT)

                        if plot_no_carbon_npv_here:
                            no_carbon_stats = get_stat_row(
                                pathway_summary,
                                country,
                                iso3,
                                year_int,
                                pathway_key,
                                NPV_WITHOUT_CARBON_EFFECT_METRIC,
                            )
                            if no_carbon_stats is not None:
                                y = y_base[country_idx] + npv_with_without_carbon_offsets.get((year_int, "without_carbon"), year_offsets[year_int])
                                draw_horizontal_quantile_bar(
                                    ax,
                                    y,
                                    no_carbon_stats,
                                    color=color,
                                    height=NPV_WITHOUT_CARBON_EFFECT_BAR_HEIGHT,
                                    facecolor=NPV_WITHOUT_CARBON_EFFECT_FACECOLOR,
                                    edgecolor=color,
                                    hatch=NPV_WITHOUT_CARBON_EFFECT_HATCH,
                                    whisker_linestyle=NPV_WITHOUT_CARBON_EFFECT_WHISKER_LINESTYLE,
                                    box_alpha=NPV_WITHOUT_CARBON_EFFECT_ALPHA,
                                )
            else:
                ax.text(
                    0.5,
                    0.5,
                    "No eligible countries",
                    ha="center",
                    va="center",
                    transform=ax.transAxes,
                    fontsize=10,
                    color="0.35",
                )

            metric_title = "NPV" if metric == "npv" else "Total carbon intensity"
            ax.set_title(f"{pathway_label} — {metric_title}", fontsize=10.5)
            ax.set_xlabel(x_label)
            ax.grid(True, axis="x", alpha=GRID_ALPHA)
            ax.set_axisbelow(True)
            apply_metric_axis_limits(ax, metric)
            if metric == "total_ci":
                draw_eu_low_carbon_limit(ax)

            if col_idx == 0:
                labels = country_display_labels(pathway_summary, country_keys)
                ax.set_yticks(y_base)
                ax.set_yticklabels(labels)
                ax.set_ylabel("Country")
            else:
                ax.set_yticks(y_base)
                ax.tick_params(axis="y", labelleft=False)

            if country_keys:
                ax.invert_yaxis()

    fig.suptitle(FIGURE_TITLE, y=0.995, fontsize=13)

    if SHOW_LEGEND:
        handles = []
        for year in YEARS_TO_PLOT:
            color = YEAR_COLORS.get(int(year), None)
            handles.append(Patch(facecolor=color, edgecolor=color, alpha=YEAR_ALPHA, label=str(year)))
        handles.append(Line2D([0], [0], color="black", lw=MEDIAN_LINEWIDTH, label="q50"))
        if SHOW_NPV_WITHOUT_CARBON_EFFECT:
            handles.append(Patch(
                facecolor=NPV_WITHOUT_CARBON_EFFECT_FACECOLOR,
                edgecolor="0.25",
                hatch=NPV_WITHOUT_CARBON_EFFECT_HATCH,
                label=NPV_WITHOUT_CARBON_EFFECT_LABEL,
            ))
        if SHOW_EU_LOW_CARBON_LIMIT:
            handles.append(Line2D(
                [0], [0],
                color=EU_LOW_CARBON_LIMIT_COLOR,
                linestyle=EU_LOW_CARBON_LIMIT_LINESTYLE,
                lw=EU_LOW_CARBON_LIMIT_LINEWIDTH,
                label=EU_LOW_CARBON_LIMIT_LABEL,
            ))
        fig.legend(
            handles=handles,
            loc=LEGEND_LOCATION,
            bbox_to_anchor=LEGEND_BBOX_TO_ANCHOR,
            ncol=min(LEGEND_NCOL, max(1, len(handles))),
            frameon=True,
            fontsize=9,
        )

    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, 0.035, 1, 0.975))

    if SHOW_BOTTOM_NOTE:
        fig.text(
            bottom_note_x_position(axes),
            NOTE_Y,
            build_bottom_note_text(),
            ha=NOTE_HORIZONTAL_ALIGNMENT,
            va=NOTE_VERTICAL_ALIGNMENT,
            **bottom_note_font_kwargs(),
        )

    fig.savefig(output_png, dpi=OUTPUT_DPI, bbox_inches="tight")
    plt.close(fig)


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    print("Project directory:", PROJECT_DIR)
    print("Results root:", RESULTS_ROOT)
    print("Output PNG:", OUTPUT_PNG)
    print("Years to plot:", YEARS_TO_PLOT)
    print("Year-to-workbook-time mapping:", YEAR_TO_TIME)
    print("Simulation range:", (SIMULATION_MIN, SIMULATION_MAX))
    print("Scenario filter:", SCENARIO_FILTER)
    print("Shipping scenario:", SHIPPING_DECARB_SCENARIO)
    print("NPV without carbon effect:", SHOW_NPV_WITHOUT_CARBON_EFFECT)
    if SHOW_NPV_WITHOUT_CARBON_EFFECT:
        print("NPV without carbon effect sheet:", NPV_PARAMETER_EFFECTS_SHEET)
        print("NPV without carbon effect excluded parameters:", NPV_WITHOUT_CARBON_EFFECT_EXCLUDED_PARAMETERS)
        print("NPV without carbon effect plotted pathways:", NPV_WITHOUT_CARBON_EFFECT_PATHWAY_KEYS_TO_PLOT)
    print(f"CI basis: kg CO2/kg H2-equivalent; NH3-to-H2eq conversion factor = {NH3_KG_PER_KG_H2_EQ:.6f} kg NH3/kg H2-eq")
    if SHOW_EU_LOW_CARBON_LIMIT:
        print(f"EU low-carbon reference line: {EU_LOW_CARBON_LIMIT_KGCO2_PER_KGH2EQ:.3f} kg CO2/kg H2-eq")

    print("Whisker mode:", WHISKER_MODE)
    print("NPV whisker manual min/max:", (NPV_WHISKER_MIN, NPV_WHISKER_MAX))
    print("CI whisker manual min/max:", (CI_WHISKER_MIN, CI_WHISKER_MAX))
    if str(WHISKER_MODE).strip().lower() == "percentile":
        print("NPV whisker quantiles:", (NPV_WHISKER_LOW_QUANTILE, NPV_WHISKER_HIGH_QUANTILE))
        print("CI whisker quantiles:", (CI_WHISKER_LOW_QUANTILE, CI_WHISKER_HIGH_QUANTILE))

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
        print("No country list found. Discovering country folders with npv results.")
        records = discover_country_records_from_results(RESULTS_ROOT)

    if not records:
        raise ValueError("No considered countries were found.")

    print(f"Considered countries: {len(records)}")
    print("Reading NPV workbooks from:")
    print(f"  {RESULTS_ROOT}\\<Country name>\\{NPV_RESULTS_FOLDER}\\{NPV_WORKBOOK_STEM}.xlsx")
    print("Reading shipping summaries from:")
    print(f"  {RESULTS_ROOT}\\<Country name>\\<selected_port>\\shipping_route_<selected_port>_{DESTINATION_PORT}_summary.xlsx/csv")

    frames: List[pd.DataFrame] = []
    warnings: List[str] = []

    for order, record in enumerate(records):
        label = country_label(record.get("country_name", ""), record.get("iso3", ""))
        try:
            country_values, country_warnings = load_run_level_values_for_country(record, country_order=order)
            warnings.extend(country_warnings)
            if not country_values.empty:
                frames.append(country_values)
            else:
                warnings.append(f"{label}: no valid run-level rows")
        except Exception as exc:
            warnings.append(f"{label}: {exc}")

    if warnings and PRINT_WARNINGS:
        print("\nWARNING: skipped/missing items:")
        for item in warnings[:300]:
            print("  - " + item)
        if len(warnings) > 300:
            print(f"  ... {len(warnings) - 300} more warning(s) not printed")

    if not frames:
        raise ValueError("No valid NPV/CI run-level values were loaded.")

    run_values = pd.concat(frames, ignore_index=True)
    if run_values.empty:
        raise ValueError("Run-level value table is empty after concatenation.")

    rows_before_screen = len(run_values)
    run_values = apply_feasibility_screen_to_run_values(run_values)
    if APPLY_FEASIBILITY_SCREEN:
        removed_rows = rows_before_screen - len(run_values)
        print(f"Feasibility screen applied: {removed_rows:,} run-level row(s) removed from headline results.")

    summary = build_country_quantile_summary(run_values)
    if summary.empty:
        raise ValueError("No country quantile summary could be built.")

    print("\nLoaded run-level pathway rows:", len(run_values))
    print("Summary rows:", len(summary))
    print("Countries with data:", summary[["country", "iso3"]].drop_duplicates().shape[0])

    print("\nSummary preview:")
    preview_cols = [
        "country", "iso3", "year", "pathway", "metric",
        "whisker_low", "q25", "q50", "q75", "whisker_high", "n_values",
    ]
    print(summary[preview_cols].head(60).to_string(index=False))

    if SAVE_RUN_VALUES_CSV:
        OUTPUT_RUN_VALUES_CSV.parent.mkdir(parents=True, exist_ok=True)
        run_values.to_csv(OUTPUT_RUN_VALUES_CSV, index=False)
        print(f"\nSaved run-level values CSV:\n{OUTPUT_RUN_VALUES_CSV}")

    if SAVE_SUMMARY_CSV:
        OUTPUT_SUMMARY_CSV.parent.mkdir(parents=True, exist_ok=True)
        summary.to_csv(OUTPUT_SUMMARY_CSV, index=False)
        print(f"\nSaved country quantile summary CSV:\n{OUTPUT_SUMMARY_CSV}")

    plot_combined_pathway_figure(summary, OUTPUT_PNG)
    print(f"\nSaved combined NPV/CI figure:\n{OUTPUT_PNG}")


if __name__ == "__main__":
    main()
