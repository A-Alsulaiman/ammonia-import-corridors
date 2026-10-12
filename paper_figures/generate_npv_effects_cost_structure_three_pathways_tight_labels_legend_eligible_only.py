#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Create a compounded 3-row x 2-column figure showing the percentage cost-structure
breakdown from the 'NPV_effects_average' sheet for the three main ammonia pathways:
    - AP SMR (Gray Ammonia)
    - AP CCS (Blue Ammonia)
    - AP AEC (Green Ammonia)

Layout:
    rows -> pathways (SMR, CCS, AEC)

Each pathway row is one combined graph containing both 2030 and 2040. For every
country, two horizontal 100% stacked bars are shown: one for 2030 and one for
2040. Only cost factors with negative mean_effect_on_NPV are kept. Positive and
zero effects are excluded from the cost structure. The stacked shares are
computed as:

    share_pct = abs(mean_effect_on_NPV) / sum(abs(all negative mean_effect_on_NPV)) * 100

Similar parameters are grouped with related colors and drawn next to each other. General tax and carbon-cost items are kept in separate color groups.

Expected country workbook path:
    C:/Users/user/Documents/Project/AP_IRA_Model/trade_version_results/<country name>/npv results/v14_DATASET_hourly_highgas.xlsx

This script follows the same country-list and project-path conventions used in the
other project scripts.
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
from matplotlib.patches import Patch


# =============================================================================
# USER SETTINGS
# =============================================================================

PROJECT_DIR = Path(os.environ.get("AP_IRA_PROJECT_DIR", str(Path(__file__).resolve().parent.parent))).resolve()
RESULTS_ROOT = PROJECT_DIR / "trade_version_results"
COUNTRY_LIST_PATH: Optional[Path] = PROJECT_DIR / "List of Countries"

NPV_RESULTS_FOLDER = "npv results"
WORKBOOK_STEM = "v14_DATASET_hourly_highgas"
EFFECTS_SHEET = "NPV_effects_average"

# Time mapping in the workbook.
DISPLAY_YEAR_TO_WORKBOOK_TIME = {
    2030: 2027,
    2040: 2040,
}

# Only these pathways are plotted and in this order.
TECHNOLOGY_TO_LABEL = {
    "AP SMR": "Gray ammonia",
    "AP CCS": "Blue ammonia",
    "AP AEC": "Green ammonia",
}
TECHNOLOGY_ORDER = ["AP SMR", "AP CCS", "AP AEC"]
DISPLAY_YEARS = [2030, 2040]

# =============================================================================
# LITERATURE-BASED FEASIBILITY SCREEN
# =============================================================================
# This screen controls what is shown in the headline result figures. Screened-out
# country-pathway combinations are removed from screened plots and screened output
# CSVs. The workbook input data are still read normally.
APPLY_FEASIBILITY_SCREEN = True
DROP_SCREENED_OUT_FROM_OUTPUTS = True

# Turkey is intentionally allowed for blue ammonia here, per the latest decision.
FEASIBILITY_SCREEN_ELIGIBLE_ISO3 = {
    "gray": {"AUS", "EGY", "NOR", "OMN", "SAU", "TUR", "ARE", "USA"},
    "blue": {"AUS", "EGY", "NOR", "OMN", "SAU", "TUR", "ARE", "USA"},
    "green": "ALL",
}

TECHNOLOGY_TO_PATHWAY_KEY = {
    "AP SMR": "gray",
    "AP CCS": "blue",
    "AP AEC": "green",
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


# Workbook scenario filter. Set to None to keep all scenarios.
SCENARIO_FILTER: Optional[str] = "New"

# Only costs are shown -> keep strictly negative mean_effect_on_NPV.
NEGATIVE_THRESHOLD = 0.0

# Country order:
#   "country_list"  -> as loaded from List of Countries
#   "total_cost_2030_smr" -> ascending total negative cost for AP SMR in 2030
COUNTRY_SORT_MODE = "country_list"
COUNTRY_SORT_LOWEST_AT_BOTTOM = True

# Plot appearance.
# One graph per pathway; 2030 and 2040 are shown together inside each graph.
FIGURE_WIDTH = 17.5
BASE_FIGURE_HEIGHT = 8.5
HEIGHT_PER_COUNTRY = 0.42
OUTPUT_DPI = 600
SHOW_PERCENT_LABELS = False
MIN_PERCENT_LABEL_TO_SHOW = 5.0
COUNTRY_LABEL_FONTSIZE = 8.5
YEAR_LABEL_FONTSIZE = 7.5
SUBPLOT_TITLE_FONTSIZE = 11.5
LEGEND_FONTSIZE = 8.2
LEGEND_MAX_ROWS = 3
BAR_HEIGHT = 0.34
YEAR_BAR_GAP = 0.08

# Grouped y-axis label controls.
# The left side of each graph is drawn as:
#     Country name   {   2030
#                       2040
COUNTRY_YEAR_AXIS_HEADER_FONTSIZE = 8.0
COUNTRY_YEAR_BRACE_FONTSIZE = 20.0
COUNTRY_LABEL_X_AXES = -0.05
COUNTRY_YEAR_BRACE_X_AXES = -0.04
COUNTRY_YEAR_TEXT_X_AXES = -0.030
COUNTRY_YEAR_HEADER_Y_AXES = 1.015
LEFT_MARGIN_FOR_COUNTRY_YEAR_LABELS = 0.145

X_AXIS_MIN = 0
X_AXIS_MAX = 100
GRID_ALPHA = 0.22
SHOW_GROUP_HEADERS_IN_LEGEND = False

OUTPUT_PNG_DIR = PROJECT_DIR
OUTPUT_PNG_STEM = "npv_effects_cost_structure_breakdown_grouped_country_year_tight_legend_eligible_only"
OUTPUT_LONG_CSV = PROJECT_DIR / "npv_effects_cost_structure_country_breakdown_eligible_only.csv"
OUTPUT_WIDE_CSV = PROJECT_DIR / "npv_effects_cost_structure_country_breakdown_wide_eligible_only.csv"
OUTPUT_SUMMARY_CSV = PROJECT_DIR / "npv_effects_cost_structure_group_summary_eligible_only.csv"


# =============================================================================
# PARAMETER GROUPING AND COLORS
# =============================================================================

# The order below determines the stacking order in the bars.
PARAMETER_GROUPS: List[Tuple[str, List[str], List[str]]] = [
    (
        "Capital & finance",
        [
            "CAPEX",
            "Financing",
            "Replacement costs",
        ],
        ["#12355B", "#2A5D8F", "#5B8FB9"],
    ),
    (
        "Operations & maintenance",
        [
            "Plant-based OPEX",
            "Wind and solar O&M",
            "Battery fixed O&M",
            "Battery variable O&M",
            "Distribution and R&D markup",
        ],
        ["#6A3D9A", "#8E63B0", "#A87EC2", "#C09AD0", "#D6B7E0"],
    ),
    (
        "Energy & feedstocks",
        [
            "Feedstock price",
            "Electricity cost",
        ],
        ["#B15928", "#D08B52"],
    ),
    (
        "Logistics & shipping",
        [
            "Transport to port",
            "Shipping cost",
            "CCS transport and storage",
        ],
        ["#1B9E77", "#4DBB96", "#86D1B6"],
    ),
    (
        "General tax",
        [
            "Tax",
        ],
        ["#4D4D4D"],
    ),
    (
        "Carbon costs",
        [
            "Process carbon price",
            "Shipping carbon cost",
            "Embedded natural-gas carbon cost",
            "CBAM / border adjustment",
        ],
        ["#D95F02", "#E67E39", "#EE9B74", "#F4BCA8"],
    ),
    (
        "Policy",
        [
            "Tax credits and subsidies",
        ],
        ["#E7298A"],
    ),
    (
        "Other",
        [],
        ["#999999"],
    ),
]


# =============================================================================
# HELPERS
# =============================================================================


def normalize_header(value: object) -> str:
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


def technology_to_pathway_key(technology: object) -> str:
    return TECHNOLOGY_TO_PATHWAY_KEY.get(str(technology).strip(), normalize_header(technology))


def canonical_country_iso3(country: object, iso3: object = "") -> str:
    """Return a robust ISO3-style key for the feasibility screen."""
    iso_text = clean_text(iso3).upper()
    iso_text = COUNTRY_ALIAS_TO_ISO3.get(normalize_header(iso_text), iso_text)
    if len(iso_text) == 3 and iso_text not in {"ISO", "NAN", "NON"}:
        return iso_text

    country_key_value = normalize_header(clean_text(country))
    return COUNTRY_ALIAS_TO_ISO3.get(country_key_value, country_key_value.upper())


def is_pathway_eligible(country: object, iso3: object, pathway_key_or_technology: object) -> bool:
    """True when a country-pathway combination passes the feasibility screen."""
    if not APPLY_FEASIBILITY_SCREEN:
        return True

    raw_key = str(pathway_key_or_technology).strip()
    pathway = technology_to_pathway_key(raw_key) if raw_key in TECHNOLOGY_TO_PATHWAY_KEY else raw_key.lower()
    eligible = FEASIBILITY_SCREEN_ELIGIBLE_ISO3.get(pathway, "ALL")
    if isinstance(eligible, str) and eligible.upper() == "ALL":
        return True

    country_iso3 = canonical_country_iso3(country, iso3)
    return country_iso3 in set(eligible)


def screening_status(country: object, iso3: object, pathway_key_or_technology: object) -> str:
    return "eligible" if is_pathway_eligible(country, iso3, pathway_key_or_technology) else "screened_out"


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
        lambda row: is_pathway_eligible(row.get("country", ""), row.get("iso3", ""), row.get("technology", "")),
        axis=1,
    )
    out["screening_status"] = np.where(out["eligible_for_main_results"], "eligible", "screened_out")

    if DROP_SCREENED_OUT_FROM_OUTPUTS:
        out = out.loc[out["eligible_for_main_results"]].copy()

    return out


def locate_country_list(project_dir: Path, results_root: Path, explicit_path: Optional[Path]) -> Optional[Path]:
    extensions = [".xlsx", ".xlsm", ".xls", ".csv"]
    candidates: List[Path] = []

    if explicit_path is not None:
        path = explicit_path if explicit_path.is_absolute() else project_dir / explicit_path
        path = path.resolve()
        if path.suffix:
            candidates.append(path)
        else:
            candidates.extend([path.with_suffix(ext) for ext in extensions])

    name_tokens = ["country_list", "country list", "countrylist", "list of countries", "listofcountries"]
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
            print(f"Loaded {len(records)} countries from {country_list_path} [{sheet_name}].")
            return records

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
            print(f"Loaded {len(records)} countries from Column A fallback in {country_list_path} [{sheet_name}].")
            return records
    raise ValueError(f"No countries could be loaded from {country_list_path}")


def discover_country_records_from_results(results_root: Path) -> List[Dict[str, str]]:
    records: List[Dict[str, str]] = []
    if not results_root.exists():
        return records
    for child in sorted(results_root.iterdir()):
        if not child.is_dir():
            continue
        results_dir = child / NPV_RESULTS_FOLDER
        if results_dir.exists():
            records.append({"country_name": child.name, "iso3": ""})
    return records


def find_country_npv_workbook(results_root: Path, country_name: str, iso3: str = "") -> Path:
    country_folder = clean_text(country_name)
    iso3 = clean_text(iso3).upper()
    possible_country_dirs = [
        results_root / country_folder,
        results_root / iso3 if iso3 else None,
    ]

    country_dir = first_existing(possible_country_dirs)
    if country_dir is None and results_root.exists():
        target_country_key = normalize_header(country_folder)
        target_iso3_key = normalize_header(iso3)
        for child in sorted(results_root.iterdir()):
            if not child.is_dir():
                continue
            child_key = normalize_header(child.name)
            if child_key in {target_country_key, target_iso3_key}:
                country_dir = child
                break
        if country_dir is None:
            partial_matches: List[Path] = []
            for child in sorted(results_root.iterdir()):
                if not child.is_dir():
                    continue
                child_key = normalize_header(child.name)
                if target_country_key and (target_country_key in child_key or child_key in target_country_key):
                    partial_matches.append(child)
            if len(partial_matches) == 1:
                country_dir = partial_matches[0]

    if country_dir is None:
        raise FileNotFoundError(f"Country directory not found for {country_name} ({iso3}).")

    extensions = [".xlsx", ".xlsm", ".xls", ".csv"]
    workbook_dir = country_dir / NPV_RESULTS_FOLDER
    workbook_candidates = [workbook_dir / f"{WORKBOOK_STEM}{ext}" for ext in extensions]
    workbook = first_existing(workbook_candidates)
    if workbook is not None:
        return workbook

    if workbook_dir.exists():
        for path in workbook_dir.glob("*"):
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            if normalize_header(WORKBOOK_STEM) in normalize_header(path.stem):
                return path

    raise FileNotFoundError(f"Could not find workbook '{WORKBOOK_STEM}' in {workbook_dir}")


def load_effects_sheet(workbook_path: Path) -> pd.DataFrame:
    suffix = workbook_path.suffix.lower()
    if suffix == ".csv":
        raise ValueError(f"Workbook must be Excel for effects sheet, got CSV: {workbook_path}")
    df = pd.read_excel(workbook_path, sheet_name=EFFECTS_SHEET)
    return df


def build_parameter_color_tables() -> Tuple[Dict[str, str], Dict[str, str], List[str], Dict[str, int]]:
    param_to_group: Dict[str, str] = {}
    param_to_color: Dict[str, str] = {}
    ordered_params: List[str] = []
    group_order: Dict[str, int] = {}

    for group_idx, (group_name, parameters, colors) in enumerate(PARAMETER_GROUPS):
        group_order[group_name] = group_idx
        if not parameters:
            continue
        if len(colors) == 1 and len(parameters) > 1:
            colors = colors * len(parameters)
        for i, param in enumerate(parameters):
            param_to_group[param] = group_name
            color = colors[min(i, len(colors) - 1)]
            param_to_color[param] = color
            ordered_params.append(param)

    return param_to_group, param_to_color, ordered_params, group_order


PARAM_TO_GROUP, PARAM_TO_COLOR, ORDERED_PARAMS, GROUP_ORDER = build_parameter_color_tables()


def map_parameter_group(parameter: str) -> str:
    return PARAM_TO_GROUP.get(parameter, "Other")


def map_parameter_color(parameter: str) -> str:
    if parameter in PARAM_TO_COLOR:
        return PARAM_TO_COLOR[parameter]
    return PARAMETER_GROUPS[-1][2][0]


def load_country_effects(record: Dict[str, str], country_order: int) -> pd.DataFrame:
    country_name = clean_text(record.get("country_name", ""))
    iso3 = clean_text(record.get("iso3", "")).upper()
    workbook_path = find_country_npv_workbook(RESULTS_ROOT, country_name, iso3)
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
    out["country_order"] = country_order
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


def prepare_cost_structure(all_effects: pd.DataFrame) -> pd.DataFrame:
    df = all_effects.copy()

    if SCENARIO_FILTER is not None:
        df = df[df["scenario"].astype(str) == str(SCENARIO_FILTER)].copy()

    df = df[df["technology"].isin(TECHNOLOGY_ORDER)].copy()
    df = df[df["time"].isin(DISPLAY_YEAR_TO_WORKBOOK_TIME.values())].copy()

    # Keep only negative cost items.
    df = df[df["mean_effect_on_NPV"] < NEGATIVE_THRESHOLD].copy()
    df["abs_cost_component"] = df["mean_effect_on_NPV"].abs()

    # Map to display year and pathway label.
    time_to_display_year = {v: k for k, v in DISPLAY_YEAR_TO_WORKBOOK_TIME.items()}
    df["display_year"] = df["time"].map(time_to_display_year)
    df["pathway_label"] = df["technology"].map(TECHNOLOGY_TO_LABEL)

    # Remove exact zeros after abs.
    df = df[df["abs_cost_component"] > 0].copy()

    # Group similar parameters.
    df["parameter_group"] = df["parameter"].map(map_parameter_group)
    df["parameter_color"] = df["parameter"].map(map_parameter_color)

    # Aggregate in case the same parameter appears multiple times.
    group_cols = [
        "country", "iso3", "country_order", "technology", "pathway_label",
        "display_year", "time", "parameter", "parameter_group", "parameter_color",
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
        agg.groupby(["country", "iso3", "country_order", "technology", "pathway_label", "display_year", "time"], as_index=False)
        .agg(total_negative_cost=("abs_cost_component", "sum"))
    )
    agg = agg.merge(totals, on=["country", "iso3", "country_order", "technology", "pathway_label", "display_year", "time"], how="left")
    agg["share_pct_of_negative_cost"] = np.where(
        agg["total_negative_cost"] > 0,
        100.0 * agg["abs_cost_component"] / agg["total_negative_cost"],
        np.nan,
    )

    # Stacking order: grouped parameters first, then unknown parameters by rank/name.
    known_order_map = {param: i for i, param in enumerate(ORDERED_PARAMS)}
    base_order = len(ORDERED_PARAMS)
    agg["parameter_sort_key"] = agg.apply(
        lambda row: known_order_map.get(
            row["parameter"],
            base_order + GROUP_ORDER.get(row["parameter_group"], 999) * 1000 + (row["parameter_rank"] if pd.notna(row["parameter_rank"]) else 999),
        ),
        axis=1,
    )

    return agg


def ordered_country_keys(cost_df: pd.DataFrame) -> List[Tuple[str, str]]:
    countries = cost_df[["country", "iso3", "country_order"]].drop_duplicates().copy()
    mode = str(COUNTRY_SORT_MODE).strip().lower()

    if mode == "country_list":
        countries = countries.sort_values("country_order")
    elif mode == "total_cost_2030_smr":
        ranking = (
            cost_df[(cost_df["display_year"] == 2030) & (cost_df["technology"] == "AP SMR")]
            .groupby(["country", "iso3"], as_index=False)["total_negative_cost"]
            .max()
        )
        countries = countries.merge(ranking, on=["country", "iso3"], how="left")
        ascending = not bool(COUNTRY_SORT_LOWEST_AT_BOTTOM)
        countries = countries.sort_values(["total_negative_cost", "country_order"], ascending=[ascending, True], na_position="last")
    else:
        raise ValueError("COUNTRY_SORT_MODE must be 'country_list' or 'total_cost_2030_smr'.")

    return [(str(row["country"]), str(row["iso3"])) for _, row in countries.iterrows()]


def country_axis_labels(country_keys: List[Tuple[str, str]]) -> List[str]:
    return [str(country) for country, _iso3 in country_keys]


def build_parameter_legend_entries(cost_df: pd.DataFrame) -> List[Patch]:
    seen = []
    handles = []

    for group_name, parameters, _colors in PARAMETER_GROUPS:
        available_params = [p for p in parameters if p in set(cost_df["parameter"].astype(str).tolist())]

        # Add group heading only optionally.
        if SHOW_GROUP_HEADERS_IN_LEGEND and available_params:
            handles.append(Patch(facecolor="none", edgecolor="none", label=f"[{group_name}]"))

        for param in available_params:
            if param in seen:
                continue
            seen.append(param)
            handles.append(Patch(facecolor=map_parameter_color(param), edgecolor="white", label=param))

    # Unknown params, if any.
    unknown_params = [
        p for p in sorted(cost_df["parameter"].astype(str).unique().tolist())
        if p not in seen
    ]
    if unknown_params and SHOW_GROUP_HEADERS_IN_LEGEND:
        handles.append(Patch(facecolor="none", edgecolor="none", label="[Other]"))
    for param in unknown_params:
        handles.append(Patch(facecolor=map_parameter_color(param), edgecolor="white", label=param))

    return handles


# =============================================================================
# PLOTTING
# =============================================================================


def _year_offsets() -> Dict[int, float]:
    """Return y-offsets so the two year bars sit around each country tick."""
    years = [int(y) for y in DISPLAY_YEARS]
    if len(years) == 1:
        return {years[0]: 0.0}

    spacing = BAR_HEIGHT + YEAR_BAR_GAP
    center = (len(years) - 1) / 2.0
    return {
        year: (idx - center) * spacing
        for idx, year in enumerate(years)
    }


def draw_grouped_country_year_axis(
    ax,
    country_keys: List[Tuple[str, str]],
    y_base: np.ndarray,
    year_offsets: Dict[int, float],
) -> None:
    """
    Draw grouped y-axis labels in this structure:

        Country name   {   2030
                          2040

    The country sits at the centre of the pair of year bars. The year labels are
    aligned to their corresponding horizontal bars.
    """
    # Hide the normal y tick labels; the grouped labels are manually placed.
    ax.set_yticks(y_base)
    ax.set_yticklabels([""] * len(country_keys))
    ax.tick_params(axis="y", length=0)

    y_axis_transform = ax.get_yaxis_transform()

    for country_idx, (country, _iso3) in enumerate(country_keys):
        y_center = float(y_base[country_idx])

        # Country name.
        ax.text(
            COUNTRY_LABEL_X_AXES,
            y_center,
            str(country),
            transform=y_axis_transform,
            ha="right",
            va="center",
            fontsize=COUNTRY_LABEL_FONTSIZE,
            color="black",
            clip_on=False,
        )

        # Curly brace between country name and year labels.
        ax.text(
            COUNTRY_YEAR_BRACE_X_AXES,
            y_center,
            "{",
            transform=y_axis_transform,
            ha="center",
            va="center",
            fontsize=COUNTRY_YEAR_BRACE_FONTSIZE,
            color="0.25",
            clip_on=False,
        )

        # Year labels aligned with each bar.
        for display_year in DISPLAY_YEARS:
            y = y_center + year_offsets[int(display_year)]
            ax.text(
                COUNTRY_YEAR_TEXT_X_AXES,
                y,
                str(display_year),
                transform=y_axis_transform,
                ha="left",
                va="center",
                fontsize=YEAR_LABEL_FONTSIZE,
                color="0.25",
                clip_on=False,
            )

    # Small headers above the grouped y-axis label columns.
    ax.text(
        COUNTRY_LABEL_X_AXES,
        COUNTRY_YEAR_HEADER_Y_AXES,
        "Country",
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        fontsize=COUNTRY_YEAR_AXIS_HEADER_FONTSIZE,
        fontweight="bold",
        color="0.25",
        clip_on=False,
    )
    ax.text(
        COUNTRY_YEAR_TEXT_X_AXES,
        COUNTRY_YEAR_HEADER_Y_AXES,
        "Year",
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=COUNTRY_YEAR_AXIS_HEADER_FONTSIZE,
        fontweight="bold",
        color="0.25",
        clip_on=False,
    )


def plot_stacked_breakdown_combined_years(
    ax,
    sub_df: pd.DataFrame,
    country_keys: List[Tuple[str, str]],
    technology: str,
) -> None:
    """
    Draw one pathway panel where 2030 and 2040 appear together.

    Each country has one y-tick label at the centre, and separate bars for each
    display year. Small year labels are drawn just to the left of the bars.
    """
    y_base = np.arange(len(country_keys), dtype=float)
    y_map = {(country, iso3): y for y, (country, iso3) in enumerate(country_keys)}
    year_offsets = _year_offsets()

    for country, iso3 in country_keys:
        for display_year in DISPLAY_YEARS:
            y = y_map[(country, iso3)] + year_offsets[int(display_year)]

            row_df = sub_df[
                (sub_df["country"] == country)
                & (sub_df["iso3"] == iso3)
                & (sub_df["display_year"] == int(display_year))
            ].copy()

            if row_df.empty:
                continue

            row_df = row_df.sort_values(
                ["parameter_sort_key", "parameter_rank", "parameter"],
                ascending=[True, True, True],
            )

            left = 0.0

            for _, row in row_df.iterrows():
                width = float(row["share_pct_of_negative_cost"])
                if not np.isfinite(width) or width <= 0:
                    continue

                color = str(row["parameter_color"])
                ax.barh(
                    y,
                    width,
                    left=left,
                    height=BAR_HEIGHT,
                    color=color,
                    edgecolor="white",
                    linewidth=0.6,
                    zorder=3,
                )

                if SHOW_PERCENT_LABELS and width >= MIN_PERCENT_LABEL_TO_SHOW:
                    ax.text(
                        left + width / 2.0,
                        y,
                        f"{width:.0f}%",
                        ha="center",
                        va="center",
                        fontsize=7.0,
                        color="white",
                        zorder=4,
                    )

                left += width

    ax.set_title(f"{TECHNOLOGY_TO_LABEL[technology]} | 2030 and 2040", fontsize=SUBPLOT_TITLE_FONTSIZE)
    ax.set_xlim(X_AXIS_MIN, X_AXIS_MAX)
    ax.set_xlabel("Share of the different cost items (%)")
    draw_grouped_country_year_axis(ax, country_keys, y_base, year_offsets)
    ax.grid(axis="x", alpha=GRID_ALPHA, zorder=0)
    ax.set_axisbelow(True)

    # Add enough padding so the upper/lower year bars are not clipped.
    if len(country_keys) > 0:
        max_offset = max(abs(v) for v in year_offsets.values()) if year_offsets else 0.0
        padding = max_offset + BAR_HEIGHT
        ax.set_ylim(-padding, len(country_keys) - 1 + padding)

    if COUNTRY_SORT_LOWEST_AT_BOTTOM:
        ax.invert_yaxis()


def technology_output_png(technology: str) -> Path:
    """Return the output path for one pathway-specific PNG."""
    tech_slug_map = {
        "AP SMR": "gray_ammonia",
        "AP CCS": "blue_ammonia",
        "AP AEC": "green_ammonia",
    }
    slug = tech_slug_map.get(technology, normalize_header(technology))
    return OUTPUT_PNG_DIR / f"{OUTPUT_PNG_STEM}_{slug}_2030_2040_600dpi.png"


def plot_single_pathway_cost_structure(
    cost_df: pd.DataFrame,
    technology: str,
    output_png: Path,
) -> None:
    """
    Create one standalone graph for a single pathway, with 2030 and 2040 shown
    together for each country.
    """
    sub_df = cost_df[cost_df["technology"] == technology].copy()
    if sub_df.empty:
        print(f"No rows found for {technology}; skipping figure.")
        return

    country_keys = ordered_country_keys(sub_df)
    n_countries = len(country_keys)
    fig_height = max(BASE_FIGURE_HEIGHT, 2.4 + HEIGHT_PER_COUNTRY * n_countries)

    fig, ax = plt.subplots(
        nrows=1,
        ncols=1,
        figsize=(FIGURE_WIDTH, fig_height),
        sharex=True,
        sharey=True,
    )

    plot_stacked_breakdown_combined_years(ax, sub_df, country_keys, technology)

    handles = build_parameter_legend_entries(sub_df)
    if handles:
        legend_cols = max(1, int(np.ceil(len(handles) / max(int(LEGEND_MAX_ROWS), 1))))
        fig.legend(
            handles=handles,
            loc="lower center",
            bbox_to_anchor=(0.64, 0.035),
            ncol=legend_cols,
            frameon=False,
            fontsize=LEGEND_FONTSIZE,
            columnspacing=1.2,
            handlelength=1.4,
            handletextpad=0.45,
            borderaxespad=0.0,
        )

    pathway_name = TECHNOLOGY_TO_LABEL.get(technology, technology)
    fig.suptitle(
        f"NPV cost structure breakdown: {pathway_name}\n"
        "2030/2040 grouped by country; ineligible countries are removed by the feasibility screen",
        fontsize=13,
        y=0.995,
    )

    fig.tight_layout(rect=[LEFT_MARGIN_FOR_COUNTRY_YEAR_LABELS, 0.085, 0.995, 0.955])
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=OUTPUT_DPI, bbox_inches="tight")
    plt.close(fig)


def plot_all_pathway_figures(cost_df: pd.DataFrame) -> List[Path]:
    """Generate one separate graph per pathway and return the saved PNG paths."""
    saved_paths: List[Path] = []
    for technology in TECHNOLOGY_ORDER:
        output_png = technology_output_png(technology)
        plot_single_pathway_cost_structure(cost_df, technology, output_png)
        saved_paths.append(output_png)
    return saved_paths


# =============================================================================
# MAIN
# =============================================================================


def main() -> None:
    print("Project directory:", PROJECT_DIR)
    print("Results root:", RESULTS_ROOT)
    print("Output PNG directory:", OUTPUT_PNG_DIR)
    print("Scenario filter:", SCENARIO_FILTER)
    print("Country sort mode:", COUNTRY_SORT_MODE)

    if not RESULTS_ROOT.exists():
        raise FileNotFoundError(f"RESULTS_ROOT does not exist: {RESULTS_ROOT}")

    country_list_path = locate_country_list(PROJECT_DIR, RESULTS_ROOT, COUNTRY_LIST_PATH)
    if country_list_path is not None:
        print(f"Using country list: {country_list_path}")
        records = load_country_list(country_list_path)
    else:
        print("No country list found. Discovering country folders under trade_version_results.")
        records = discover_country_records_from_results(RESULTS_ROOT)

    if not records:
        raise ValueError("No countries found.")

    frames: List[pd.DataFrame] = []
    skipped: List[str] = []

    for order, record in enumerate(records):
        label = country_label(record.get("country_name", ""), record.get("iso3", ""))
        try:
            df = load_country_effects(record, country_order=order)
            frames.append(df)
            print(f"  Loaded effects for {label}: {len(df)} rows")
        except Exception as exc:
            skipped.append(f"{label}: {exc}")

    if skipped:
        print("\nWARNING: skipped country/countries:")
        for item in skipped:
            print("  -", item)

    if not frames:
        raise ValueError("No NPV_effects_average data could be loaded.")

    all_effects = pd.concat(frames, ignore_index=True)
    cost_df = prepare_cost_structure(all_effects)
    rows_before_screen = len(cost_df)
    cost_df = apply_feasibility_screen_to_cost_df(cost_df)
    if APPLY_FEASIBILITY_SCREEN:
        removed_rows = rows_before_screen - len(cost_df)
        print(f"Feasibility screen applied: {removed_rows:,} cost-structure row(s) removed from plots and CSVs.")

    if cost_df.empty:
        raise ValueError("No negative NPV-effect components were found after filtering.")

    # Summary tables.
    summary = (
        cost_df.groupby(["country", "iso3", "display_year", "technology", "pathway_label", "eligible_for_main_results", "screening_status", "parameter_group"], as_index=False)
        .agg(
            group_abs_cost=("abs_cost_component", "sum"),
            group_share_pct=("share_pct_of_negative_cost", "sum"),
        )
    )

    wide = (
        cost_df.pivot_table(
            index=["country", "iso3", "display_year", "technology", "pathway_label", "eligible_for_main_results", "screening_status"],
            columns="parameter",
            values="share_pct_of_negative_cost",
            aggfunc="sum",
            fill_value=0.0,
        )
        .reset_index()
    )
    wide.columns.name = None

    cost_df = cost_df.sort_values(["country_order", "display_year", "technology", "parameter_sort_key", "parameter_rank", "parameter"])
    summary = summary.sort_values(["country", "display_year", "technology", "parameter_group"])
    wide = wide.sort_values(["country", "display_year", "technology"])

    cost_df.to_csv(OUTPUT_LONG_CSV, index=False)
    wide.to_csv(OUTPUT_WIDE_CSV, index=False)
    summary.to_csv(OUTPUT_SUMMARY_CSV, index=False)

    print(f"Saved long-form breakdown CSV:\n{OUTPUT_LONG_CSV}")
    print(f"Saved wide breakdown CSV:\n{OUTPUT_WIDE_CSV}")
    print(f"Saved group summary CSV:\n{OUTPUT_SUMMARY_CSV}")

    saved_pngs = plot_all_pathway_figures(cost_df)
    print("Saved pathway figures:")
    for png in saved_pngs:
        print(png)


if __name__ == "__main__":
    main()
