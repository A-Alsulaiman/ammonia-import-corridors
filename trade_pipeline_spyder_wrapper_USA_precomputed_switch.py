
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spyder-friendly batch wrapper for the trade workflow.

What it does, in order, for each country in "List of Countries.xlsx":
1) Reads the country list and ISO3 code.
2) Creates: trade_version_results/<country>/
3) Runs country_siting_runner... with COUNTRY_NAME / COUNTRY_ISO3 / OUTDIR injected
   so outputs land in:
       trade_version_results/<country>/optimal_locations/
4) Builds chosen_ports_of_top_locations.xlsx from:
       AP_location_dataset_<ISO3>_new.xlsx
       ammonia_delivered_costs.csv
5) Uses the same UpdatedPub150 table for siting and shipping, validates each
   chosen port against Main/Region/Alternate names, resolves it to one WPI row,
   and runs Shipping_route_model.py once per unique port into:
       trade_version_results/<country>/<port>/
6) Runs input_changer.py to prepare the country-specific ammonia-model inputs.
7) Runs probabilistic_model_runner.py and writes outputs to the country NPV folder.
8) Repeats for the next country.

This file is written to be run directly from Spyder.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import math
import os
import re
import runpy
import shutil
import subprocess
import sys
import traceback
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

try:
    import pycountry
except Exception:  # pragma: no cover
    pycountry = None


# =============================================================================
# USER SETTINGS
# =============================================================================

COUNTRY_LIST_FILENAME = "List of Countries.xlsx"

COUNTRY_SITING_SCRIPT = "country_siting_runner_CELL_ADAPTIVE_CONSTRAINED_HEATMAP_OPT_C_v30_ports.py"
SHIPPING_SCRIPT = "Shipping_route_model.py"
PROBABILISTIC_SCRIPT = "probabilistic_model_runner.py"
INPUT_CHANGER_SCRIPT = "input_changer.py"

TRADE_VERSION_DIRNAME = "Trade_version"
RESULTS_DIRNAME = "trade_version_results"
OPTIMAL_LOCATIONS_SUBDIR = "optimal_locations"
PROBABILISTIC_OUTPUTS_SUBDIR = "probabilistic_outputs"
NPV_RESULTS_SUBDIR = "npv results"

UPDATEDPUB150_BASENAME = "UpdatedPub150"
SHIPPING_CONFIG_FILENAME = "shipping_route_config.json"

CHOSEN_PORTS_FILENAME = "chosen_ports_of_top_locations.xlsx"
SHIPPING_OUTPUT_PREFIX_BASENAME = "shipping_route"
ROUTE_PLOT_STYLE_VERSION = "v2_unique_routes_port_legend"

# Matching precision for AP_location_dataset vs ammonia_delivered_costs
LATLON_MATCH_DECIMALS = 6

# Use the exact same UpdatedPub150 table for both the siting nearest-port
# calculation and the shipping route model. This avoids name/coordinate drift
# between online/alternate port sources and the shipping model's WPI table.
FORCE_COUNTRY_SITING_TO_USE_UPDATEDPUB150 = True

# If a port name is duplicated in UpdatedPub150, use port_lat/port_lon from
# ammonia_delivered_costs.csv to choose the WPI row nearest to the siting result.
PORT_COORDINATE_MATCH_TOLERANCE_KM = 50.0

# Batch behavior
SUPPRESS_PLOTS = True
FAIL_FAST = True
DEDUPE_COUNTRIES = True

# Country filter for testing.
# Use None to process every country in the workbook.
# Example: PROCESS_ONLY_COUNTRIES = ["AUS", "CHL"]
PROCESS_ONLY_COUNTRIES: Optional[List[str]] = None

# Country-siting outputs you specifically said you care about are the AP location
# workbook and the ammonia_delivered_costs CSV, so optional analytics are disabled
# here to make the batch lighter. Set to False if you want the extra outputs too.
DISABLE_OPTIONAL_COUNTRY_SITING_ANALYTICS = True

# Probabilistic ammonia model settings
RUN_INPUT_CHANGER_IN_SUBPROCESS = True
RUN_PROBABILISTIC_IN_SUBPROCESS = True
RUN_RENEWABLE_OPTIMIZATION = True
RUN_PPA_MODEL = True
PROBABILISTIC_MATCHING = "hourly"
PROBABILISTIC_MULT = 40
PROBABILISTIC_WORKERS = max(1, min(4, (os.cpu_count() or 1)))
PROBABILISTIC_INCLUDE_CI = False
PROBABILISTIC_CBAM = False

# Country-specific logistics settings passed into input_changer.py
TO_PORT_PIPELINE_COST_USD_PER_TKM = 0.02

# Default ammonia export-terminal infrastructure adder used by the
# probabilistic ammonia model input preparation. Change this single value when
# you want one setting for every country in the batch.
TO_PORT_PORT_INFRASTRUCTURE_USD_PER_TNH3 = 0.0

# Optional country-specific overrides for the ammonia export-terminal
# infrastructure adder. Keys can be either ISO3 codes or country names exactly
# as they appear in the country list workbook.
TO_PORT_PORT_INFRASTRUCTURE_USD_PER_TNH3_BY_COUNTRY: Dict[str, float] = {
    # "ARE": 5.0,
    # "United Arab Emirates": 5.0,
}

SHIPPING_ANALYSIS_YEAR = 2030
SHIPPING_DECARBONISATION_SCENARIO = "bau"
EUR_TO_USD_RATE_FOR_SHIPPING = 1.0821
ALLOW_MISSING_SHIPPING_FOR_PROBABILISTIC = False

# Required country-level data files used by input_changer.py. The wrapper checks
# these before doing a long siting/shipping run so missing files fail fast.
INPUT_CHANGER_REQUIRED_DATA_FILES = [
    "electricity_grid_share_per_country.xlsx",
    "Labour_cost_per_employee_by_country.xlsx",
    "Global_Natural_gas_price_per_country.xlsx",
    "Carbon_price_share_per_country.xlsx",
    "Natural_gas_emissions.xlsx",
]

# Helpful after an interrupted run: if the siting or shipping outputs already
# exist for a country/port, reuse them instead of re-running the expensive steps.
REUSE_EXISTING_COUNTRY_SITING_OUTPUTS = True
REUSE_EXISTING_SHIPPING_OUTPUTS = True

# USA shortcut / override
# -----------------------
# Set this to False to restore the original behavior for the USA, where the
# wrapper runs the country-siting method or reuses the normal AP workbook.
# Set it to True to skip country siting for the USA and build the top-10 AP
# workbook directly from a precomputed ammonia_delivered_costs.csv.
USA_USE_PRECOMPUTED_AMMONIA_COSTS = True

# Folder that already contains the USA ammonia_delivered_costs.csv to use.
# An empty string means trade_version_results/United States of America/optimal_locations
# under the project folder (the released layout). Set an absolute or relative
# path here to point somewhere else.
USA_PRECOMPUTED_OPTIMAL_LOCATIONS_DIR = ""
USA_PRECOMPUTED_AMMONIA_COSTS_FILENAME = "ammonia_delivered_costs.csv"

# Only rows whose port column matches one of these names are eligible. Matching
# is normalization-based and allows partial variants such as "Port of Corpus
# Christi" or simply "Lavaca".
USA_PRECOMPUTED_TARGET_PORTS = ["Port Lavaca", "Corpus Christi", "Deer Park"]

# Number of best distinct locations to keep after filtering by the target ports.
USA_PRECOMPUTED_TOP_N = 10

# Candidate cost columns, checked in order. If none are present, the wrapper
# falls back to a normalized "contains ammonia/NH3 + cost/delivered" search.
USA_PRECOMPUTED_COST_COLUMN_CANDIDATES = [
    "best_proxy_ammonia_cost",
    "proxy_ammonia_cost",
    "proxy_nh3_cost_usd_per_t",
    "nh3_delivered_usd_per_t",
    "delivered_nh3_usd_per_t",
    "ammonia_delivered_cost",
    "ammonia_cost_usd_per_t",
    "cost_usd_per_t",
    "score",
]

# Files written by the USA shortcut inside USA_PRECOMPUTED_OPTIMAL_LOCATIONS_DIR.
# The AP filename intentionally matches the normal siting output name so the
# rest of the wrapper can continue without special cases.
USA_PRECOMPUTED_SELECTED_AMMONIA_FILENAME = "ammonia_delivered_costs_USA_selected_top10.csv"
USA_PRECOMPUTED_RANKING_AUDIT_FILENAME = "usa_target_port_top10_ranking_audit.csv"


# =============================================================================
# SMALL UTILITIES
# =============================================================================

def normalize_header(value: object) -> str:
    """Normalize a column/header name for fuzzy-safe matching."""
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def normalize_text(value: object) -> str:
    """Normalize free text such as country names or port names."""
    if value is None:
        return ""
    if pd.isna(value):
        return ""
    text = str(value)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.strip().upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def sanitize_filename(name: str) -> str:
    """Make a Windows-safe file/folder name while keeping it human-readable."""
    cleaned = str(name).strip()
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.rstrip(". ")
    return cleaned or "unnamed"


def shipping_output_token(name: str) -> str:
    """Match Shipping_route_model.py's safe token for generated output names."""
    cleaned = str(name).strip()
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned)
    cleaned = cleaned.strip("._ ")
    return cleaned or "unnamed"


def expected_shipping_route_png(output_prefix: Path, origin_name: str, destination_name: str) -> Path:
    """Expected PNG name produced by Shipping_route_model.py for a route map."""
    return Path(
        f"{output_prefix.resolve()}_"
        f"{shipping_output_token(origin_name)}_"
        f"{shipping_output_token(destination_name)}_routes.png"
    )


def expected_shipping_route_plot_metadata(output_prefix: Path, origin_name: str, destination_name: str) -> Path:
    """Expected metadata sidecar written by Shipping_route_model.py for the route map."""
    png_path = expected_shipping_route_png(output_prefix, origin_name, destination_name)
    return png_path.with_name(f"{png_path.stem}_plot_metadata.json")


def route_plot_style_is_current(plot_path: Path) -> bool:
    """Return True if the existing route plot was made with the current visual style."""
    metadata_path = plot_path.with_name(f"{plot_path.stem}_plot_metadata.json")
    if not plot_path.exists() or not metadata_path.exists():
        return False
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return str(payload.get("plot_style_version", "")) == ROUTE_PLOT_STYLE_VERSION



def ordered_unique(values: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        key = normalize_text(value)
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(str(value))
    return out


def resolve_country_specific_float(
    default_value: float,
    overrides: Dict[str, float],
    country_name: str,
    iso3: str,
) -> float:
    """Resolve a global default with optional country/ISO3-specific overrides."""
    if not overrides:
        return float(default_value)

    normalized_overrides: Dict[str, float] = {}
    for key, value in overrides.items():
        try:
            normalized_overrides[normalize_text(key)] = float(value)
        except Exception:
            continue

    for key in [iso3, country_name]:
        norm_key = normalize_text(key)
        if norm_key and norm_key in normalized_overrides:
            return float(normalized_overrides[norm_key])

    return float(default_value)


def country_name_candidates(country_name: Optional[str], iso3: Optional[str] = None) -> List[str]:
    """Return normalized country-name variants for matching UpdatedPub150."""
    candidates: List[str] = []
    for value in [country_name, iso3]:
        if value:
            candidates.append(str(value))

    if pycountry is not None and iso3:
        try:
            rec = pycountry.countries.get(alpha_3=str(iso3).upper())
            if rec is not None:
                for attr in ["name", "official_name", "common_name"]:
                    value = getattr(rec, attr, None)
                    if value:
                        candidates.append(str(value))
        except Exception:
            pass

    if country_name and pycountry is not None:
        try:
            rec = pycountry.countries.lookup(str(country_name))
            for attr in ["alpha_3", "name", "official_name", "common_name"]:
                value = getattr(rec, attr, None)
                if value:
                    candidates.append(str(value))
        except Exception:
            pass

    # Practical aliases seen often in country lists versus WPI tables.
    alias_map = {
        "UNITED STATES OF AMERICA": ["United States", "USA", "US"],
        "UNITED STATES": ["United States of America", "USA", "US"],
        "UNITED KINGDOM": ["UK", "Great Britain"],
        "RUSSIAN FEDERATION": ["Russia"],
        "KOREA REPUBLIC OF": ["South Korea", "Korea, South"],
        "VIET NAM": ["Vietnam"],
        "COTE D IVOIRE": ["Ivory Coast", "Cote d'Ivoire"],
    }
    expanded = list(candidates)
    for candidate in candidates:
        expanded.extend(alias_map.get(normalize_text(candidate), []))

    out: List[str] = []
    seen = set()
    for candidate in expanded:
        norm = normalize_text(candidate)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(candidate)
    return out


def haversine_km(lat1: object, lon1: object, lat2: object, lon2: object) -> float:
    """Great-circle distance in kilometres, tolerant of non-numeric inputs."""
    try:
        lat1_f = float(lat1)
        lon1_f = float(lon1)
        lat2_f = float(lat2)
        lon2_f = float(lon2)
    except Exception:
        return float("inf")

    if any(pd.isna(v) for v in [lat1_f, lon1_f, lat2_f, lon2_f]):
        return float("inf")

    r_km = 6371.0088
    phi1 = math.radians(lat1_f)
    phi2 = math.radians(lat2_f)
    dphi = math.radians(lat2_f - lat1_f)
    dlambda = math.radians(lon2_f - lon1_f)

    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * r_km * math.asin(math.sqrt(min(1.0, a)))


def find_first_file(
    search_roots: Sequence[Path],
    patterns: Sequence[str],
    required: bool = True,
) -> Optional[Path]:
    for root in search_roots:
        if root is None or not root.exists():
            continue
        for pattern in patterns:
            matches = sorted(root.glob(pattern))
            if matches:
                return matches[0]
    if required:
        joined = ", ".join(patterns)
        roots = ", ".join(str(p) for p in search_roots if p is not None)
        raise FileNotFoundError(f"Could not find any of [{joined}] under: {roots}")
    return None


def find_input_changer_data_file(project_dir: Path, trade_version_dir: Path, filename: str) -> Optional[Path]:
    """Find one input_changer data workbook in root/Trade_version, with a recursive fallback."""
    direct_candidates = [
        project_dir / filename,
        trade_version_dir / filename,
        project_dir / TRADE_VERSION_DIRNAME / filename,
    ]
    for candidate in direct_candidates:
        if candidate.exists():
            return candidate

    roots = []
    for root in [trade_version_dir, project_dir]:
        if root.exists():
            resolved = root.resolve()
            if resolved not in [r.resolve() for r in roots]:
                roots.append(root)

    matches: List[Path] = []
    for root in roots:
        try:
            for path in root.rglob(filename):
                # Do not let prior country outputs satisfy source-data requirements.
                if RESULTS_DIRNAME in path.parts:
                    continue
                if path.is_file():
                    matches.append(path)
        except Exception:
            continue

    if not matches:
        return None
    matches = sorted(matches, key=lambda p: (len(str(p)), str(p)))
    return matches[0]


def preflight_input_changer_data_files(project_dir: Path, trade_version_dir: Path) -> Dict[str, Path]:
    """Fail early with a clear message if the country-level input workbooks are missing."""
    resolved: Dict[str, Path] = {}
    missing: List[str] = []

    for filename in INPUT_CHANGER_REQUIRED_DATA_FILES:
        path = find_input_changer_data_file(project_dir, trade_version_dir, filename)
        if path is None:
            missing.append(filename)
        else:
            resolved[filename] = path

    if missing:
        locations = [
            str(project_dir),
            str(trade_version_dir),
            str(project_dir / TRADE_VERSION_DIRNAME),
        ]
        raise FileNotFoundError(
            "input_changer.py cannot run because the following required data files are missing:\n"
            + "\n".join(f"  - {name}" for name in missing)
            + "\n\nCopy those files into either the project root or the Trade_version folder.\n"
            + "Checked locations:\n"
            + "\n".join(f"  - {loc}" for loc in locations)
        )

    return resolved


def find_named_directory(project_dir: Path, preferred_name: str) -> Path:
    direct = project_dir / preferred_name
    if direct.exists() and direct.is_dir():
        return direct

    preferred_norm = normalize_text(preferred_name)
    for child in project_dir.iterdir():
        if child.is_dir() and normalize_text(child.name) == preferred_norm:
            return child

    # If it does not exist, return the direct path so the caller can create it.
    return direct


def pick_column(df: pd.DataFrame, candidates: Sequence[str], required: bool = True) -> Optional[str]:
    normalized_map = {normalize_header(col): col for col in df.columns}
    for candidate in candidates:
        col = normalized_map.get(normalize_header(candidate))
        if col is not None:
            return col

    # Prefix fallback: useful for headers such as "Alternate Port Name 1"
    candidate_norms = [normalize_header(c) for c in candidates]
    for norm_col, original_col in normalized_map.items():
        if any(norm_col.startswith(cn) for cn in candidate_norms if cn):
            return original_col

    if required:
        raise KeyError(f"Could not find required column. Tried: {list(candidates)}")
    return None


def resolve_relative_path(value: Optional[str], base_dir: Path) -> Optional[str]:
    if value in (None, ""):
        return value
    path = Path(str(value))
    if path.is_absolute():
        return str(path)
    return str((base_dir / path).resolve())


@contextmanager
def working_directory(path: Path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


@contextmanager
def suppressed_matplotlib_show(enabled: bool = True):
    if not enabled:
        yield
        return

    try:
        import matplotlib.pyplot as plt
    except Exception:
        yield
        return

    original_show = plt.show
    try:
        plt.show = lambda *args, **kwargs: None
        yield
    finally:
        plt.show = original_show
        try:
            plt.close("all")
        except Exception:
            pass


def run_script_as_main(script_path: Path, extra_argv: Optional[List[str]] = None) -> None:
    """Run a Python file as if it were executed directly."""
    extra_argv = extra_argv or []
    old_argv = sys.argv[:]
    try:
        sys.argv = [str(script_path)] + list(extra_argv)
        runpy.run_path(str(script_path), run_name="__main__")
    finally:
        sys.argv = old_argv


def run_script_subprocess(script_path: Path, extra_argv: Optional[List[str]] = None, cwd: Optional[Path] = None) -> None:
    """Run a Python file in a fresh interpreter and stream the child traceback in Spyder."""
    extra_argv = extra_argv or []
    command = [sys.executable, str(script_path)] + list(extra_argv)
    run_cwd = Path(cwd or script_path.parent)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("AP_IRA_MODEL_ROOT", str(run_cwd.resolve()))

    print("Running subprocess:", " ".join(command))
    print("Subprocess working directory:", run_cwd)

    captured_lines: List[str] = []
    process = subprocess.Popen(
        command,
        cwd=str(run_cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )

    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")
        captured_lines.append(line)
        if len(captured_lines) > 400:
            captured_lines = captured_lines[-400:]

    return_code = process.wait()
    if return_code != 0:
        tail = "".join(captured_lines[-120:])
        raise subprocess.CalledProcessError(
            return_code,
            command,
            output=tail,
        )


def load_module_from_file(module_name: str, script_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(script_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not import module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# =============================================================================
# COUNTRY LIST
# =============================================================================

COUNTRY_NAME_ALIASES = {
    "COTE D IVOIRE": "CIV",
    "IVORY COAST": "CIV",
    "SOUTH KOREA": "KOR",
    "NORTH KOREA": "PRK",
    "RUSSIA": "RUS",
    "VIETNAM": "VNM",
    "BOLIVIA": "BOL",
    "MOLDOVA": "MDA",
    "LAOS": "LAO",
    "TANZANIA": "TZA",
    "SYRIA": "SYR",
    "IRAN": "IRN",
    "VENEZUELA": "VEN",
}


def iso3_from_country_name(country_name: str) -> str:
    normalized = normalize_text(country_name)
    if normalized in COUNTRY_NAME_ALIASES:
        return COUNTRY_NAME_ALIASES[normalized]

    if pycountry is None:
        raise ValueError(
            f"Could not derive ISO3 for '{country_name}' because pycountry is unavailable."
        )

    try:
        return pycountry.countries.lookup(country_name).alpha_3
    except Exception as exc:
        raise ValueError(f"Could not derive ISO3 code for '{country_name}'.") from exc


def load_country_list(country_list_path: Path) -> List[Dict[str, str]]:
    df = pd.read_excel(country_list_path)

    common_col = pick_column(
        df,
        ["Country (common)", "Country", "Country Name", "Common Name", "Name"],
        required=False,
    )
    official_col = pick_column(
        df,
        ["Official Full Name", "Official Name", "Formal Name"],
        required=False,
    )
    iso3_col = pick_column(
        df,
        ["ISO3", "ISO 3", "ISO_3", "ISO"],
        required=False,
    )

    if common_col is None and official_col is None and iso3_col is None:
        raise ValueError(
            "The country list workbook must contain at least a country-name column or an ISO3 column."
        )

    rows: List[Dict[str, str]] = []
    seen_iso3 = set()

    for _, row in df.iterrows():
        common_name = ""
        official_name = ""

        if common_col is not None and not pd.isna(row[common_col]):
            common_name = str(row[common_col]).strip()
        if official_col is not None and not pd.isna(row[official_col]):
            official_name = str(row[official_col]).strip()

        country_name = common_name or official_name
        if not country_name and iso3_col is None:
            continue

        if iso3_col is not None and not pd.isna(row[iso3_col]) and str(row[iso3_col]).strip():
            iso3 = str(row[iso3_col]).strip().upper()
        else:
            iso3 = iso3_from_country_name(country_name)

        record = {
            "country_name": country_name or iso3,
            "official_name": official_name or country_name or iso3,
            "iso3": iso3,
        }

        if PROCESS_ONLY_COUNTRIES:
            filters = {normalize_text(x) for x in PROCESS_ONLY_COUNTRIES}
            if (
                normalize_text(record["iso3"]) not in filters
                and normalize_text(record["country_name"]) not in filters
                and normalize_text(record["official_name"]) not in filters
            ):
                continue

        if DEDUPE_COUNTRIES:
            if record["iso3"] in seen_iso3:
                continue
            seen_iso3.add(record["iso3"])

        rows.append(record)

    if not rows:
        raise ValueError("No countries were loaded from the country list workbook after filtering.")

    return rows


# =============================================================================
# COUNTRY SITING
# =============================================================================

def configure_country_siting(
    country_module,
    country_name: str,
    iso3: str,
    outdir: Path,
    shipping_ready_wpi_csv: Optional[Path] = None,
) -> Dict:
    cfg = copy.deepcopy(country_module.CONFIG)
    cfg["COUNTRY_NAME"] = country_name
    cfg["COUNTRY_ISO3"] = iso3
    cfg["OUTDIR"] = str(outdir)

    if FORCE_COUNTRY_SITING_TO_USE_UPDATEDPUB150:
        if shipping_ready_wpi_csv is None:
            raise ValueError("shipping_ready_wpi_csv is required when FORCE_COUNTRY_SITING_TO_USE_UPDATEDPUB150=True")
        cfg.setdefault("PORTS", {})
        cfg["PORTS"]["SOURCE"] = "WPI_CSV"
        cfg["PORTS"]["PATH"] = str(shipping_ready_wpi_csv)
        cfg["PORTS"]["COUNTRY_NAME"] = country_name
        cfg["PORTS"]["COUNTRY_ISO3"] = iso3

    if DISABLE_OPTIONAL_COUNTRY_SITING_ANALYTICS:
        try:
            cfg["VIZ"]["MONTE_CARLO"]["ENABLE"] = False
        except Exception:
            pass
        try:
            cfg["VIZ"]["PARETO"]["ENABLE"] = False
        except Exception:
            pass

    return cfg


def find_country_siting_outputs(optimal_dir: Path, iso3: str) -> Tuple[Path, Path]:
    ap_candidates = [
        optimal_dir / f"AP_location_dataset_{iso3}_new.xlsx",
        *sorted(optimal_dir.glob("AP_location_dataset*_new.xlsx")),
    ]
    ap_path = next((p for p in ap_candidates if p.exists()), None)
    if ap_path is None:
        raise FileNotFoundError(
            f"Could not find the AP location workbook in {optimal_dir}"
        )

    ammonia_path = optimal_dir / "ammonia_delivered_costs.csv"
    if not ammonia_path.exists():
        raise FileNotFoundError(
            f"Could not find ammonia_delivered_costs.csv in {optimal_dir}"
        )

    return ap_path, ammonia_path


def is_usa_country(country_name: str, iso3: str) -> bool:
    """Return True when the current country-list row is the USA case."""
    iso_norm = normalize_text(iso3)
    name_norm = normalize_text(country_name)
    return iso_norm == "USA" or name_norm in {
        "UNITED STATES OF AMERICA",
        "UNITED STATES",
        "USA",
        "US",
    }


def use_precomputed_usa_ammonia_costs(country_name: str, iso3: str) -> bool:
    """High-level on/off switch for the USA precomputed-cost shortcut."""
    return bool(USA_USE_PRECOMPUTED_AMMONIA_COSTS) and is_usa_country(country_name, iso3)


def resolve_precomputed_usa_optimal_dir(project_dir: Path, results_root: Path) -> Path:
    """Resolve the configured USA optimal_locations folder."""
    raw = str(USA_PRECOMPUTED_OPTIMAL_LOCATIONS_DIR or "").strip()
    if raw:
        configured = Path(raw)
        # Windows absolute paths such as C:\... may not look absolute when this
        # file is inspected on a non-Windows machine, so detect drive prefixes too.
        if configured.is_absolute() or re.match(r"^[A-Za-z]:[\\/]", raw):
            return configured
        return (project_dir / configured).resolve()

    return results_root / "United States of America" / OPTIMAL_LOCATIONS_SUBDIR


def _port_name_matches_any_target(port_name: object, target_norms: Sequence[str]) -> bool:
    """Loose normalized match for configured USA target port names."""
    port_norm = normalize_text(port_name)
    if not port_norm:
        return False
    return any(target in port_norm or port_norm in target for target in target_norms if target)


def _find_usa_port_column(df: pd.DataFrame) -> str:
    col = pick_column(
        df,
        [
            "nearest_port",
            "nearest port",
            "destination_port",
            "destination port",
            "dest_port",
            "port_name",
            "main_port_name",
            "canonical_main_port",
            "port",
        ],
        required=False,
    )
    if col is None:
        raise KeyError(
            "Could not find a port column in the USA ammonia CSV. Tried nearest_port, "
            "destination_port, port_name, main_port_name, canonical_main_port, and port."
        )
    return col


def _find_usa_cost_column(df: pd.DataFrame) -> str:
    direct = pick_column(df, USA_PRECOMPUTED_COST_COLUMN_CANDIDATES, required=False)
    if direct is not None:
        return direct

    numeric_cols: List[str] = []
    for col in df.columns:
        numeric = pd.to_numeric(df[col], errors="coerce")
        if numeric.notna().any():
            numeric_cols.append(col)

    pattern_groups = [
        ("proxy", "ammonia", "cost"),
        ("proxy", "nh3", "cost"),
        ("nh3", "delivered", "usd", "t"),
        ("ammonia", "delivered", "cost"),
        ("delivered", "cost"),
        ("ammonia", "cost"),
        ("nh3", "cost"),
    ]
    for parts in pattern_groups:
        for col in numeric_cols:
            norm = normalize_header(col)
            if all(part in norm for part in parts):
                return col

    raise KeyError(
        "Could not identify the USA proxy ammonia cost column. Add its exact name to "
        "USA_PRECOMPUTED_COST_COLUMN_CANDIDATES near the top of this script."
    )


def _location_code(index: int) -> str:
    """A, B, ..., Z, AA, AB, ... labels for generated AP-location rows."""
    n = int(index)
    if n < 0:
        raise ValueError("index must be non-negative")
    letters: List[str] = []
    while True:
        n, rem = divmod(n, 26)
        letters.append(chr(ord("A") + rem))
        if n == 0:
            break
        n -= 1
    return "".join(reversed(letters))


def build_precomputed_usa_top_locations(
    *,
    country_name: str,
    iso3: str,
    optimal_dir: Path,
) -> Tuple[Path, Path]:
    """
    For the USA only, skip siting and derive the top-N AP workbook from an
    existing ammonia_delivered_costs.csv filtered to configured target ports.

    Returns
    -------
    (ap_path, selected_ammonia_path)
        Paths that are API-compatible with the normal country-siting outputs.
    """
    optimal_dir.mkdir(parents=True, exist_ok=True)
    source_ammonia_path = optimal_dir / USA_PRECOMPUTED_AMMONIA_COSTS_FILENAME
    if not source_ammonia_path.exists():
        raise FileNotFoundError(
            "USA shortcut is enabled but the configured ammonia CSV was not found:\n"
            f"  {source_ammonia_path}\n"
            "Either put the file there or set USA_USE_PRECOMPUTED_AMMONIA_COSTS=False."
        )

    df = pd.read_csv(source_ammonia_path)
    lat_col = pick_column(df, ["lat", "latitude"])
    lon_col = pick_column(df, ["lon", "longitude"])
    port_col = _find_usa_port_column(df)
    cost_col = _find_usa_cost_column(df)

    work = df.copy()
    work[lat_col] = pd.to_numeric(work[lat_col], errors="coerce")
    work[lon_col] = pd.to_numeric(work[lon_col], errors="coerce")
    work["_usa_proxy_cost_numeric"] = pd.to_numeric(work[cost_col], errors="coerce")
    work = work.dropna(subset=[lat_col, lon_col, "_usa_proxy_cost_numeric"]).copy()

    target_norms = [normalize_text(p) for p in USA_PRECOMPUTED_TARGET_PORTS]
    matched = work[work[port_col].apply(lambda value: _port_name_matches_any_target(value, target_norms))].copy()
    if matched.empty:
        raise ValueError(
            "USA shortcut found no ammonia-cost rows for target ports: "
            f"{USA_PRECOMPUTED_TARGET_PORTS}. Port column used: {port_col!r}."
        )

    matched = matched.sort_values("_usa_proxy_cost_numeric", ascending=True).reset_index(drop=True)

    # Keep one row per physical location, using the cheapest matching target-port
    # row when the CSV has multiple port alternatives for the same lat/lon.
    matched["_lat_key"] = matched[lat_col].round(LATLON_MATCH_DECIMALS)
    matched["_lon_key"] = matched[lon_col].round(LATLON_MATCH_DECIMALS)
    ranked_locations = (
        matched
        .drop_duplicates(subset=["_lat_key", "_lon_key"], keep="first")
        .head(int(USA_PRECOMPUTED_TOP_N))
        .copy()
        .reset_index(drop=True)
    )

    if len(ranked_locations) < int(USA_PRECOMPUTED_TOP_N):
        print(
            f"[USA shortcut] WARNING: only {len(ranked_locations)} distinct matching location(s) "
            f"were found for target ports {USA_PRECOMPUTED_TARGET_PORTS}; continuing with available rows."
        )

    ranked_locations.insert(0, "usa_rank", range(1, len(ranked_locations) + 1))
    ranked_locations["usa_rank_cost_column"] = cost_col
    ranked_locations["usa_rank_cost"] = ranked_locations["_usa_proxy_cost_numeric"]
    ranked_locations["usa_rank_matched_port"] = ranked_locations[port_col].astype(str).str.strip()

    # Normalize columns expected by the downstream wrapper functions. Keep the
    # original columns too, so the audit CSV remains traceable.
    ranked_locations["lat"] = ranked_locations[lat_col].astype(float)
    ranked_locations["lon"] = ranked_locations[lon_col].astype(float)
    ranked_locations["nearest_port"] = ranked_locations[port_col].astype(str).str.strip()

    drop_internal_cols = ["_lat_key", "_lon_key", "_usa_proxy_cost_numeric"]
    selected_for_csv = ranked_locations.drop(columns=[c for c in drop_internal_cols if c in ranked_locations.columns])

    selected_ammonia_path = optimal_dir / USA_PRECOMPUTED_SELECTED_AMMONIA_FILENAME
    selected_for_csv.to_csv(selected_ammonia_path, index=False)

    audit_path = optimal_dir / USA_PRECOMPUTED_RANKING_AUDIT_FILENAME
    audit_cols = [
        c for c in [
            "usa_rank", "lat", "lon", "nearest_port", "usa_rank_cost",
            "usa_rank_cost_column", "distance_km", "port_lat", "port_lon",
        ]
        if c in selected_for_csv.columns
    ]
    selected_for_csv[audit_cols].to_csv(audit_path, index=False)

    ap_path = optimal_dir / f"AP_location_dataset_{iso3}_new.xlsx"
    ap_df = pd.DataFrame({
        "Country": [country_name] * len(ranked_locations),
        "City": [_location_code(i) for i in range(len(ranked_locations))],
        "lat": ranked_locations["lat"].astype(float).round(6),
        "lon": ranked_locations["lon"].astype(float).round(6),
        "nearest_port": ranked_locations["nearest_port"].astype(str),
        "best_proxy_ammonia_cost": ranked_locations["usa_rank_cost"].astype(float),
        "source_cost_column": [cost_col] * len(ranked_locations),
        "usa_rank": ranked_locations["usa_rank"].astype(int),
    })
    if "distance_km" in ranked_locations.columns:
        ap_df["distance_km"] = pd.to_numeric(ranked_locations["distance_km"], errors="coerce")

    ap_df.to_excel(ap_path, index=False)

    print(
        "[USA shortcut] Skipped country siting and built top locations from precomputed ammonia costs:\n"
        f"  Source ammonia CSV:   {source_ammonia_path}\n"
        f"  Selected ammonia CSV: {selected_ammonia_path}\n"
        f"  AP workbook:          {ap_path}\n"
        f"  Ranking audit CSV:    {audit_path}\n"
        f"  Port column:          {port_col}\n"
        f"  Cost column:          {cost_col}\n"
        f"  Rows kept:            {len(ranked_locations)}"
    )

    return ap_path, selected_ammonia_path


# =============================================================================
# AP LOCATION + AMMONIA -> CHOSEN PORTS
# =============================================================================

def load_ap_locations(ap_path: Path) -> pd.DataFrame:
    df = pd.read_excel(ap_path)
    lat_col = pick_column(df, ["lat", "latitude"])
    lon_col = pick_column(df, ["lon", "longitude"])
    out = df[[lat_col, lon_col]].copy()
    out.columns = ["lat", "lon"]
    out["lat"] = pd.to_numeric(out["lat"], errors="coerce")
    out["lon"] = pd.to_numeric(out["lon"], errors="coerce")
    out = out.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    return out


def load_ammonia_delivered_costs(ammonia_path: Path) -> pd.DataFrame:
    df = pd.read_csv(ammonia_path)
    lat_col = pick_column(df, ["lat", "latitude"])
    lon_col = pick_column(df, ["lon", "longitude"])
    nearest_port_col = pick_column(df, ["nearest_port", "nearest port"])

    optional_map = {
        "nearest_port_country": ["nearest_port_country", "port_country", "country_code"],
        "distance_km": ["distance_km", "distance to port km", "distance_to_port_km"],
        "port_lat": ["port_lat", "nearest_port_lat", "port latitude", "port_latitude"],
        "port_lon": ["port_lon", "nearest_port_lon", "port longitude", "port_longitude"],
    }

    selected = {lat_col: "lat", lon_col: "lon", nearest_port_col: "nearest_port"}
    for standard_name, candidates in optional_map.items():
        col = pick_column(df, candidates, required=False)
        if col is not None:
            selected[col] = standard_name

    out = df[list(selected.keys())].copy().rename(columns=selected)
    out["lat"] = pd.to_numeric(out["lat"], errors="coerce")
    out["lon"] = pd.to_numeric(out["lon"], errors="coerce")
    out["nearest_port"] = out["nearest_port"].astype(str).str.strip()

    for numeric_col in ["distance_km", "port_lat", "port_lon"]:
        if numeric_col in out.columns:
            out[numeric_col] = pd.to_numeric(out[numeric_col], errors="coerce")

    if "nearest_port_country" in out.columns:
        out["nearest_port_country"] = (
            out["nearest_port_country"]
            .where(~out["nearest_port_country"].isna(), "")
            .astype(str)
            .str.strip()
        )

    out = out.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    return out


def build_chosen_ports_excel(ap_path: Path, ammonia_path: Path, output_path: Path) -> pd.DataFrame:
    ap_df = load_ap_locations(ap_path)
    ammonia_df = load_ammonia_delivered_costs(ammonia_path)

    ap_df["lat_key"] = ap_df["lat"].round(LATLON_MATCH_DECIMALS)
    ap_df["lon_key"] = ap_df["lon"].round(LATLON_MATCH_DECIMALS)

    ammonia_df["lat_key"] = ammonia_df["lat"].round(LATLON_MATCH_DECIMALS)
    ammonia_df["lon_key"] = ammonia_df["lon"].round(LATLON_MATCH_DECIMALS)

    ambiguous = (
        ammonia_df.groupby(["lat_key", "lon_key"])["nearest_port"]
        .nunique(dropna=True)
        .reset_index(name="n_ports")
    )
    ambiguous = ambiguous[ambiguous["n_ports"] > 1]
    if not ambiguous.empty:
        raise ValueError(
            "ammonia_delivered_costs.csv has ambiguous nearest_port values for the same rounded lat/lon pair."
        )

    keep_cols = [
        "lat_key",
        "lon_key",
        "nearest_port",
        "nearest_port_country",
        "distance_km",
        "port_lat",
        "port_lon",
    ]
    keep_cols = [col for col in keep_cols if col in ammonia_df.columns]

    ammonia_keys = (
        ammonia_df[keep_cols]
        .drop_duplicates(subset=["lat_key", "lon_key"])
        .reset_index(drop=True)
    )

    merged = ap_df.merge(
        ammonia_keys,
        on=["lat_key", "lon_key"],
        how="left",
    )

    missing = merged[merged["nearest_port"].isna()]
    if not missing.empty:
        missing_pairs = missing[["lat", "lon"]].to_dict(orient="records")
        raise ValueError(
            "Could not find nearest_port matches for some AP locations in ammonia_delivered_costs.csv: "
            f"{missing_pairs}"
        )

    result_cols = [
        "lat",
        "lon",
        "nearest_port",
        "nearest_port_country",
        "distance_km",
        "port_lat",
        "port_lon",
    ]
    result_cols = [col for col in result_cols if col in merged.columns]
    result = merged[result_cols].copy()
    result.to_excel(output_path, index=False)
    return result


# =============================================================================
# UPDATEDPUB150 / WPI HANDLING
# =============================================================================

def load_tabular_file(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        return pd.read_excel(path)
    raise ValueError(f"Unsupported table format: {path}")


def find_updatedpub150_source(search_roots: Sequence[Path]) -> Path:
    patterns = [
        f"{UPDATEDPUB150_BASENAME}.csv",
        f"{UPDATEDPUB150_BASENAME}.xlsx",
        f"{UPDATEDPUB150_BASENAME}.xls",
        f"{UPDATEDPUB150_BASENAME}*.csv",
        f"{UPDATEDPUB150_BASENAME}*.xlsx",
        f"{UPDATEDPUB150_BASENAME}*.xls",
    ]
    return find_first_file(search_roots, patterns, required=True)


def standardize_wpi_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    main_col = pick_column(df, ["Main Port Name", "MainPortName"])
    lat_col = pick_column(df, ["Latitude", "Lat"])
    lon_col = pick_column(df, ["Longitude", "Lon", "Long"])
    region_col = pick_column(df, ["Region Name", "Region"], required=False)
    alternate_col = pick_column(df, ["Alternate Port Name", "AlternatePortName"], required=False)

    rename_map = {
        main_col: "Main Port Name",
        lat_col: "Latitude",
        lon_col: "Longitude",
    }
    if region_col is not None:
        rename_map[region_col] = "Region Name"
    if alternate_col is not None:
        rename_map[alternate_col] = "Alternate Port Name"

    df = df.rename(columns=rename_map)

    # Strip whitespace from all headers to make downstream usage cleaner.
    df.columns = [str(c).strip() for c in df.columns]

    # Make sure the three key columns exist exactly as the shipping script expects.
    for required_col in ["Main Port Name", "Latitude", "Longitude"]:
        if required_col not in df.columns:
            raise ValueError(f"UpdatedPub150 is missing required column '{required_col}'")

    return df


def ensure_shipping_ready_wpi_csv(updatedpub150_source: Path, output_dir: Path) -> Tuple[Path, pd.DataFrame]:
    raw_df = load_tabular_file(updatedpub150_source)
    wpi_df = standardize_wpi_dataframe(raw_df)

    csv_path = output_dir / f"{UPDATEDPUB150_BASENAME}_wrapper_ready.csv"
    wpi_df.to_csv(csv_path, index=False)
    return csv_path, wpi_df


def _row_country_code(row: pd.Series) -> str:
    return str(row.get("Country Code", "")).strip()


def _port_record_from_row(
    row: pd.Series,
    raw_port_name: str,
    match_type: str,
    coordinate_distance_km: Optional[float] = None,
) -> Dict[str, object]:
    return {
        "raw_port_name": raw_port_name,
        "main_port_name": str(row["Main Port Name"]).strip(),
        "country_code": _row_country_code(row),
        "wpi_lat": float(row["Latitude"]) if not pd.isna(row.get("Latitude")) else None,
        "wpi_lon": float(row["Longitude"]) if not pd.isna(row.get("Longitude")) else None,
        "match_type": match_type,
        "coordinate_distance_km": coordinate_distance_km,
    }


def resolve_port_to_wpi_record(
    raw_port_name: str,
    wpi_df: pd.DataFrame,
    *,
    port_lat: Optional[object] = None,
    port_lon: Optional[object] = None,
    country_name: Optional[str] = None,
    country_iso3: Optional[str] = None,
    allow_partial: bool = True,
) -> Dict[str, object]:
    """
    Resolve a siting/export port to a single UpdatedPub150 row.

    Matching order:
      1) exact Main/Region/Alternate name;
      2) country filter if it reduces the candidate set;
      3) nearest WPI latitude/longitude if ammonia_delivered_costs.csv supplied
         port_lat/port_lon;
      4) partial name match as a fallback.

    The returned country_code is the exact value from UpdatedPub150, so the
    shipping script can filter on Country Code and avoid duplicate names.
    """
    raw_norm = normalize_text(raw_port_name)
    if not raw_norm:
        raise ValueError("Encountered a blank export port name.")

    candidate_columns = ["Main Port Name"]
    if "Region Name" in wpi_df.columns:
        candidate_columns.append("Region Name")
    if "Alternate Port Name" in wpi_df.columns:
        candidate_columns.append("Alternate Port Name")

    exact_rows: List[Tuple[pd.Series, str]] = []
    partial_rows: List[Tuple[pd.Series, str]] = []

    for _, row in wpi_df.iterrows():
        for col in candidate_columns:
            cell_norm = normalize_text(row.get(col, ""))
            if not cell_norm:
                continue
            if raw_norm == cell_norm:
                exact_rows.append((row, f"exact:{col}"))
            elif allow_partial and (raw_norm in cell_norm or cell_norm in raw_norm):
                partial_rows.append((row, f"partial:{col}"))

    candidates = exact_rows if exact_rows else partial_rows
    if not candidates:
        raise ValueError(
            f"Port '{raw_port_name}' was not found in UpdatedPub150 under Main Port Name, Region Name, or Alternate Port Name."
        )

    # If possible, use the country name/ISO3 to reduce duplicate global port names.
    country_norms = {normalize_text(x) for x in country_name_candidates(country_name, country_iso3)}
    if country_norms and "Country Code" in wpi_df.columns:
        country_filtered = [
            item for item in candidates
            if normalize_text(_row_country_code(item[0])) in country_norms
        ]
        if country_filtered:
            candidates = country_filtered

    # De-duplicate exact same WPI rows.
    seen_rows = set()
    unique_candidates: List[Tuple[pd.Series, str]] = []
    for row, match_type in candidates:
        key = (
            normalize_text(row.get("Main Port Name", "")),
            normalize_text(row.get("Country Code", "")),
            str(row.get("World Port Index Number", "")),
            str(row.get("Latitude", "")),
            str(row.get("Longitude", "")),
        )
        if key not in seen_rows:
            seen_rows.add(key)
            unique_candidates.append((row, match_type))
    candidates = unique_candidates

    if len(candidates) == 1:
        return _port_record_from_row(candidates[0][0], raw_port_name, candidates[0][1], None)

    # If the siting output supplied port coordinates, choose the nearest WPI row.
    scored: List[Tuple[float, pd.Series, str]] = []
    for row, match_type in candidates:
        dist = haversine_km(port_lat, port_lon, row.get("Latitude"), row.get("Longitude"))
        scored.append((dist, row, match_type))

    scored = sorted(scored, key=lambda x: x[0])
    if scored and math.isfinite(scored[0][0]):
        best_distance = scored[0][0]
        if best_distance <= PORT_COORDINATE_MATCH_TOLERANCE_KM or (
            len(scored) > 1 and best_distance < scored[1][0] * 0.25
        ):
            return _port_record_from_row(
                scored[0][1],
                raw_port_name,
                scored[0][2] + ":coordinate_disambiguated",
                best_distance,
            )

    candidate_labels = ordered_unique(
        f"{str(row['Main Port Name']).strip()} [{_row_country_code(row)}]"
        for row, _ in candidates
    )
    raise ValueError(
        f"Port '{raw_port_name}' matched multiple UpdatedPub150 rows: {candidate_labels}. "
        "The wrapper could not disambiguate them by country or port_lat/port_lon."
    )


def resolve_port_to_main_name(raw_port_name: str, wpi_df: pd.DataFrame) -> str:
    """Backward-compatible name-only resolver."""
    return str(resolve_port_to_wpi_record(raw_port_name, wpi_df)["main_port_name"])


# =============================================================================
# SHIPPING CONFIG
# =============================================================================

def load_json_file(path: Path) -> Dict:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def write_json_file(path: Path, payload: Dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def locate_base_shipping_config(
    project_dir: Path,
    shipping_script_path: Path,
    trade_version_dir: Path,
    lanes_path: Optional[Path],
) -> Optional[Path]:
    candidate_dirs = []
    if shipping_script_path.parent.exists():
        candidate_dirs.append(shipping_script_path.parent)
    if trade_version_dir.exists():
        candidate_dirs.append(trade_version_dir)
    if project_dir.exists():
        candidate_dirs.append(project_dir)
    if lanes_path is not None and lanes_path.parent.exists():
        candidate_dirs.append(lanes_path.parent)

    seen = set()
    for candidate_dir in candidate_dirs:
        key = str(candidate_dir.resolve())
        if key in seen:
            continue
        seen.add(key)
        candidate = candidate_dir / SHIPPING_CONFIG_FILENAME
        if candidate.exists():
            return candidate
    return None


def build_base_shipping_config(
    project_dir: Path,
    shipping_script_path: Path,
    trade_version_dir: Path,
    lanes_path: Path,
    ports_csv_path: Path,
) -> Dict:
    base_config_path = locate_base_shipping_config(project_dir, shipping_script_path, trade_version_dir, lanes_path)
    cfg: Dict

    if base_config_path is not None:
        cfg = load_json_file(base_config_path)
        base_dir = base_config_path.parent
        for path_key in ["lanes", "ports", "land_shapefile_path", "ocean_cache_dir"]:
            if path_key in cfg:
                cfg[path_key] = resolve_relative_path(cfg[path_key], base_dir)
    else:
        cfg = {}

    # Fill defaults using the shipping script defaults where possible.
    cfg.setdefault("lanes", str(lanes_path))
    cfg.setdefault("ports", str(ports_csv_path))
    cfg.setdefault("origin", "San Antonio")
    cfg.setdefault("origin_country", None)
    cfg.setdefault("destination", "Rotterdam")
    cfg.setdefault("destination_country", None)
    cfg.setdefault("cargo_tonnes", 50000.0)
    cfg.setdefault("year", 2030)
    cfg.setdefault("decarbonisation_scenario", "bau")
    cfg.setdefault("suez_toll_eur", 300000.0)
    cfg.setdefault("panama_toll_eur", 250000.0)
    cfg.setdefault("carbon_price_eur_per_tco2", None)
    cfg.setdefault("mc_iters", 1000)
    cfg.setdefault("output_prefix", str((project_dir / SHIPPING_OUTPUT_PREFIX_BASENAME).resolve()))
    cfg.setdefault("random_seed", 42)
    cfg.setdefault("allow_open_ocean_bridge", True)
    cfg.setdefault("max_bridge_nm", 2500.0)
    cfg.setdefault("min_bridge_nm", 50.0)
    cfg.setdefault("plot", False if SUPPRESS_PLOTS else True)
    cfg.setdefault("save_plot", True)
    cfg.setdefault("plot_dpi", 250)
    cfg.setdefault("ocean_grid_step_deg", 1.0)
    cfg.setdefault("ocean_grid_lat_min", -75.0)
    cfg.setdefault("ocean_grid_lat_max", 75.0)

    # Always use the wrapper-generated ports CSV and the detected lanes file.
    cfg["lanes"] = str(lanes_path)
    cfg["ports"] = str(ports_csv_path)

    return cfg


def prepare_shipping_config_for_port(
    base_cfg: Dict,
    export_port_main_name: str,
    destination_port_name: str,
    output_prefix: Path,
    origin_country_code: Optional[str] = None,
    destination_country_code: Optional[str] = None,
) -> Dict:
    cfg = copy.deepcopy(base_cfg)
    cfg["origin"] = export_port_main_name
    cfg["origin_country"] = origin_country_code
    cfg["destination"] = destination_port_name
    cfg["destination_country"] = destination_country_code
    cfg["output_prefix"] = str(output_prefix.resolve())
    cfg["save_plot"] = True
    if SUPPRESS_PLOTS:
        cfg["plot"] = False
    return cfg


# =============================================================================
# PROBABILISTIC OUTPUT CAPTURE
# =============================================================================

def snapshot_project_outputs(project_dir: Path, exclude_dirs: Optional[Sequence[Path]] = None) -> Dict[str, Tuple[float, int]]:
    exclude_dirs = exclude_dirs or []
    exclude_resolved = {str(p.resolve()) for p in exclude_dirs}

    snapshot: Dict[str, Tuple[float, int]] = {}
    for path in project_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".xlsx", ".csv"}:
            continue

        skip = False
        for excluded in exclude_resolved:
            try:
                if str(path.resolve()).startswith(excluded + os.sep) or str(path.resolve()) == excluded:
                    skip = True
                    break
            except Exception:
                continue
        if skip:
            continue

        stat = path.stat()
        snapshot[str(path.resolve())] = (stat.st_mtime, stat.st_size)
    return snapshot


def copy_new_or_updated_outputs(
    before: Dict[str, Tuple[float, int]],
    after: Dict[str, Tuple[float, int]],
    destination_dir: Path,
) -> List[Path]:
    destination_dir.mkdir(parents=True, exist_ok=True)
    copied: List[Path] = []

    for path_str, after_sig in after.items():
        before_sig = before.get(path_str)
        if before_sig == after_sig:
            continue

        src = Path(path_str)
        dst = destination_dir / src.name
        shutil.copy2(src, dst)
        copied.append(dst)

    return copied


# =============================================================================
# MAIN PIPELINE
# =============================================================================

def validate_required_scripts(project_dir: Path) -> Tuple[Path, Path, Path, Path]:
    country_siting_path = project_dir / COUNTRY_SITING_SCRIPT
    shipping_script_path = project_dir / SHIPPING_SCRIPT
    probabilistic_script_path = project_dir / PROBABILISTIC_SCRIPT
    input_changer_path = project_dir / INPUT_CHANGER_SCRIPT

    required_paths = [country_siting_path, shipping_script_path, probabilistic_script_path, input_changer_path]
    missing = [str(p.name) for p in required_paths if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Missing required script(s) in project directory: {missing}")

    return country_siting_path, shipping_script_path, probabilistic_script_path, input_changer_path


def canonicalize_destination_record(base_shipping_cfg: Dict, wpi_df: pd.DataFrame) -> Dict[str, object]:
    destination = str(base_shipping_cfg.get("destination", "Rotterdam")).strip()
    destination_country = base_shipping_cfg.get("destination_country")
    try:
        return resolve_port_to_wpi_record(
            destination,
            wpi_df,
            country_name=str(destination_country) if destination_country else None,
            allow_partial=True,
        )
    except Exception:
        # Keep the existing destination if it is already what the user wants and
        # not present in the validation columns exactly.
        return {
            "raw_port_name": destination,
            "main_port_name": destination,
            "country_code": destination_country,
            "wpi_lat": None,
            "wpi_lon": None,
            "match_type": "unresolved_destination_kept",
            "coordinate_distance_km": None,
        }


def process_one_country(
    index: int,
    total: int,
    country: Dict[str, str],
    project_dir: Path,
    results_root: Path,
    country_module,
    shipping_script_path: Path,
    probabilistic_script_path: Path,
    input_changer_path: Path,
    base_shipping_cfg: Dict,
    wpi_df: pd.DataFrame,
    shipping_ready_wpi_csv: Path,
) -> None:
    country_name = country["country_name"]
    iso3 = country["iso3"]

    print("=" * 100)
    print(f"[{index}/{total}] Processing country: {country_name} ({iso3})")

    country_folder = results_root / sanitize_filename(country_name)
    optimal_dir = country_folder / OPTIMAL_LOCATIONS_SUBDIR

    usa_precomputed_mode = use_precomputed_usa_ammonia_costs(country_name, iso3)
    if usa_precomputed_mode:
        optimal_dir = resolve_precomputed_usa_optimal_dir(project_dir, results_root)
        country_folder = optimal_dir.parent
        print(
            "[USA shortcut] Enabled: using precomputed ammonia-delivered costs instead of country siting.\n"
            f"[USA shortcut] Output country folder: {country_folder}\n"
            f"[USA shortcut] Optimal locations folder: {optimal_dir}"
        )

    optimal_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # 1) Country siting
    # -------------------------------------------------------------------------
    ap_path = None
    ammonia_path = None

    if usa_precomputed_mode:
        ap_path, ammonia_path = build_precomputed_usa_top_locations(
            country_name=country_name,
            iso3=iso3,
            optimal_dir=optimal_dir,
        )
    else:
        if REUSE_EXISTING_COUNTRY_SITING_OUTPUTS:
            try:
                ap_path, ammonia_path = find_country_siting_outputs(optimal_dir, iso3)
                print(
                    "Reusing existing country siting outputs instead of rerunning:\n"
                    f"  - {ap_path}\n"
                    f"  - {ammonia_path}"
                )
            except FileNotFoundError:
                ap_path = None
                ammonia_path = None

        if ap_path is None or ammonia_path is None:
            print(f"Running country siting for {country_name} ({iso3}) ...")
            cfg = configure_country_siting(
                country_module,
                country_name=country_name,
                iso3=iso3,
                outdir=optimal_dir,
                shipping_ready_wpi_csv=shipping_ready_wpi_csv,
            )

            with working_directory(project_dir), suppressed_matplotlib_show(SUPPRESS_PLOTS):
                country_module.run_with_ports(cfg)

            ap_path, ammonia_path = find_country_siting_outputs(optimal_dir, iso3)
            print(f"Country siting outputs found:\n  - {ap_path}\n  - {ammonia_path}")

    # -------------------------------------------------------------------------
    # 2) Build chosen_ports_of_top_locations.xlsx
    # -------------------------------------------------------------------------
    chosen_ports_path = country_folder / CHOSEN_PORTS_FILENAME
    chosen_ports_df = build_chosen_ports_excel(ap_path, ammonia_path, chosen_ports_path)
    print(f"Chosen ports workbook written to: {chosen_ports_path}")

    raw_ports = [
        str(value).strip()
        for value in chosen_ports_df["nearest_port"].tolist()
        if str(value).strip()
    ]
    if not raw_ports:
        raise ValueError(f"No nearest_port values were found for {country_name} ({iso3}).")

    # Resolve to a single UpdatedPub150 row and deduplicate.
    destination_record = canonicalize_destination_record(base_shipping_cfg, wpi_df)
    destination_name = str(destination_record["main_port_name"])
    destination_country_code = destination_record.get("country_code")

    canonical_port_plan: List[Dict[str, object]] = []
    seen_canonical = set()

    # Save the canonical WPI main-port name back into the chosen-ports workbook.
    # input_changer.py uses this to connect AP sites to the correct shipping
    # result folders even when the siting nearest_port matched a Region or
    # Alternate Port Name.
    chosen_ports_df["canonical_main_port"] = ""
    chosen_ports_df["canonical_port_country"] = ""
    chosen_ports_df["canonical_port_lat"] = pd.NA
    chosen_ports_df["canonical_port_lon"] = pd.NA
    chosen_ports_df["port_match_type"] = ""
    chosen_ports_df["port_match_distance_km"] = pd.NA

    for row_index, row in chosen_ports_df.iterrows():
        raw_port = str(row.get("nearest_port", "")).strip()
        if not raw_port:
            continue

        record = resolve_port_to_wpi_record(
            raw_port,
            wpi_df,
            port_lat=row.get("port_lat") if "port_lat" in chosen_ports_df.columns else None,
            port_lon=row.get("port_lon") if "port_lon" in chosen_ports_df.columns else None,
            country_name=country_name,
            country_iso3=iso3,
        )

        chosen_ports_df.at[row_index, "canonical_main_port"] = record["main_port_name"]
        chosen_ports_df.at[row_index, "canonical_port_country"] = record.get("country_code", "")
        chosen_ports_df.at[row_index, "canonical_port_lat"] = record.get("wpi_lat")
        chosen_ports_df.at[row_index, "canonical_port_lon"] = record.get("wpi_lon")
        chosen_ports_df.at[row_index, "port_match_type"] = record.get("match_type", "")
        chosen_ports_df.at[row_index, "port_match_distance_km"] = record.get("coordinate_distance_km")

        canonical_key = (
            normalize_text(record["main_port_name"]),
            normalize_text(record.get("country_code", "")),
        )
        if canonical_key in seen_canonical:
            continue
        seen_canonical.add(canonical_key)
        canonical_port_plan.append(record)

    chosen_ports_df.to_excel(chosen_ports_path, index=False)

    print("Unique export ports to run through Shipping_route_model.py:")
    for record in canonical_port_plan:
        raw_port = str(record.get("raw_port_name", "")).strip()
        canonical_main = str(record["main_port_name"]).strip()
        country_code = str(record.get("country_code", "") or "").strip()
        suffix = f" [{country_code}]" if country_code else ""
        if normalize_text(raw_port) == normalize_text(canonical_main):
            print(f"  - {canonical_main}{suffix}")
        else:
            print(f"  - {raw_port} -> {canonical_main}{suffix}")

    # -------------------------------------------------------------------------
    # 3) Shipping routes (once per unique canonical export port)
    # -------------------------------------------------------------------------
    for run_number, record in enumerate(canonical_port_plan, start=1):
        canonical_main = str(record["main_port_name"]).strip()
        origin_country_code = str(record.get("country_code", "") or "").strip() or None
        port_folder = country_folder / sanitize_filename(canonical_main)
        port_folder.mkdir(parents=True, exist_ok=True)

        port_cfg = prepare_shipping_config_for_port(
            base_cfg=base_shipping_cfg,
            export_port_main_name=canonical_main,
            destination_port_name=destination_name,
            output_prefix=port_folder / SHIPPING_OUTPUT_PREFIX_BASENAME,
            origin_country_code=origin_country_code,
            destination_country_code=str(destination_country_code) if destination_country_code else None,
        )
        port_cfg["ports"] = str(shipping_ready_wpi_csv)

        port_cfg_path = port_folder / SHIPPING_CONFIG_FILENAME
        write_json_file(port_cfg_path, port_cfg)

        existing_summaries = sorted(port_folder.glob("*_summary.csv"))
        expected_plot_path = expected_shipping_route_png(
            output_prefix=port_folder / SHIPPING_OUTPUT_PREFIX_BASENAME,
            origin_name=canonical_main,
            destination_name=destination_name,
        )
        plot_required = bool(port_cfg.get("save_plot", True))
        plot_current = (not plot_required) or route_plot_style_is_current(expected_plot_path)

        can_reuse_shipping = (
            REUSE_EXISTING_SHIPPING_OUTPUTS
            and existing_summaries
            and plot_current
        )

        if can_reuse_shipping:
            print(
                f"[{country_name}] Reusing existing shipping output for {canonical_main}: "
                f"{existing_summaries[0]}"
            )
            if expected_plot_path.exists():
                print(f"[{country_name}] Existing route plot: {expected_plot_path}")
        else:
            if REUSE_EXISTING_SHIPPING_OUTPUTS and existing_summaries and plot_required:
                if not expected_plot_path.exists():
                    print(
                        f"[{country_name}] Existing shipping summary found, but route PNG is missing; "
                        "rerunning shipping to save the plot."
                    )
                elif not route_plot_style_is_current(expected_plot_path):
                    print(
                        f"[{country_name}] Existing route PNG uses an older visual style; "
                        "rerunning shipping to regenerate the plot."
                    )
            print(f"[{country_name}] Shipping run {run_number}/{len(canonical_port_plan)}: {canonical_main}")
            with working_directory(project_dir), suppressed_matplotlib_show(SUPPRESS_PLOTS):
                run_script_as_main(shipping_script_path, ["--config", str(port_cfg_path)])

    # -------------------------------------------------------------------------
    # 4) Prepare inputs for the probabilistic ammonia model
    # -------------------------------------------------------------------------
    npv_results_dir = country_folder / NPV_RESULTS_SUBDIR
    npv_results_dir.mkdir(parents=True, exist_ok=True)

    port_infrastructure_usd_per_tnh3 = resolve_country_specific_float(
        default_value=TO_PORT_PORT_INFRASTRUCTURE_USD_PER_TNH3,
        overrides=TO_PORT_PORT_INFRASTRUCTURE_USD_PER_TNH3_BY_COUNTRY,
        country_name=country_name,
        iso3=iso3,
    )
    print(
        f"Using ammonia export-port infrastructure adder for {country_name} ({iso3}): "
        f"{port_infrastructure_usd_per_tnh3:.2f} USD/tNH3"
    )

    input_changer_args = [
        "--country-name", country_name,
        "--country-iso3", iso3,
        "--country-folder", str(country_folder),
        "--npv-results-dir", str(npv_results_dir),
        "--project-dir", str(project_dir),
        "--trade-version-dir", str(find_named_directory(project_dir, TRADE_VERSION_DIRNAME)),
        "--ap-location", str(ap_path),
        "--ammonia-costs", str(ammonia_path),
        "--chosen-ports", str(chosen_ports_path),
        "--pipeline-cost-usd-per-tkm", str(TO_PORT_PIPELINE_COST_USD_PER_TKM),
        "--port-infrastructure-usd-per-tnh3", str(port_infrastructure_usd_per_tnh3),
        "--shipping-year", str(SHIPPING_ANALYSIS_YEAR),
        "--shipping-decarbonisation-scenario", str(SHIPPING_DECARBONISATION_SCENARIO),
        "--eur-to-usd-rate", str(EUR_TO_USD_RATE_FOR_SHIPPING),
    ]
    if not RUN_RENEWABLE_OPTIMIZATION:
        input_changer_args.append("--skip-renewable-optimization")
    if not RUN_PPA_MODEL:
        input_changer_args.append("--skip-ppa-model")
    if ALLOW_MISSING_SHIPPING_FOR_PROBABILISTIC:
        input_changer_args.append("--allow-missing-shipping")

    print(f"Preparing probabilistic inputs for {country_name} ({iso3}) ...")
    if RUN_INPUT_CHANGER_IN_SUBPROCESS:
        run_script_subprocess(input_changer_path, input_changer_args, cwd=project_dir)
    else:
        with working_directory(project_dir), suppressed_matplotlib_show(SUPPRESS_PLOTS):
            run_script_as_main(input_changer_path, input_changer_args)

    # -------------------------------------------------------------------------
    # 5) Probabilistic model
    # -------------------------------------------------------------------------
    print(f"Running probabilistic_model_runner.py for {country_name} ({iso3}) ...")
    probabilistic_args = [
        "--country-name", country_name,
        "--country-iso3", iso3,
        "--output-dir", str(npv_results_dir),
        "--matching", PROBABILISTIC_MATCHING,
        "--mult", str(PROBABILISTIC_MULT),
        "--workers", str(PROBABILISTIC_WORKERS),
    ]
    if PROBABILISTIC_CBAM:
        probabilistic_args.append("--cbam")
    if PROBABILISTIC_INCLUDE_CI:
        probabilistic_args.append("--include-ci")

    if RUN_PROBABILISTIC_IN_SUBPROCESS:
        run_script_subprocess(probabilistic_script_path, probabilistic_args, cwd=project_dir)
    else:
        with working_directory(project_dir), suppressed_matplotlib_show(SUPPRESS_PLOTS):
            run_script_as_main(probabilistic_script_path, probabilistic_args)

    print(f"Finished country: {country_name} ({iso3})")


def main() -> None:
    project_dir = Path(__file__).resolve().parent
    results_root = find_named_directory(project_dir, RESULTS_DIRNAME)
    results_root.mkdir(parents=True, exist_ok=True)

    # Make sure local imports used by the target scripts keep working.
    if str(project_dir) not in sys.path:
        sys.path.insert(0, str(project_dir))

    country_siting_path, shipping_script_path, probabilistic_script_path, input_changer_path = validate_required_scripts(project_dir)
    trade_version_dir = find_named_directory(project_dir, TRADE_VERSION_DIRNAME)

    country_list_path = project_dir / COUNTRY_LIST_FILENAME
    if not country_list_path.exists():
        raise FileNotFoundError(f"Could not find country list workbook: {country_list_path}")

    search_roots = [trade_version_dir, project_dir]
    lanes_path = find_first_file(search_roots, ["Shipping_Lanes_v1.geojson"], required=True)
    updatedpub150_source = find_updatedpub150_source(search_roots)
    shipping_ready_wpi_csv, wpi_df = ensure_shipping_ready_wpi_csv(updatedpub150_source, results_root)

    input_changer_data_files = preflight_input_changer_data_files(project_dir, trade_version_dir)

    base_shipping_cfg = build_base_shipping_config(
        project_dir=project_dir,
        shipping_script_path=shipping_script_path,
        trade_version_dir=trade_version_dir,
        lanes_path=lanes_path,
        ports_csv_path=shipping_ready_wpi_csv,
    )

    countries = load_country_list(country_list_path)
    total = len(countries)

    print("=" * 100)
    print("Batch wrapper starting")
    print(f"Project directory:          {project_dir}")
    print(f"Country list workbook:      {country_list_path}")
    print(f"Trade_version directory:    {trade_version_dir}")
    print(f"Shipping lanes file:        {lanes_path}")
    print(f"UpdatedPub150 source:       {updatedpub150_source}")
    print(f"Shipping-ready WPI CSV:     {shipping_ready_wpi_csv}")
    print(f"Results root:               {results_root}")
    print("Input changer data files:")
    for _name, _path in input_changer_data_files.items():
        print(f"  - {_name}: {_path}")
    print(f"Countries to process:       {total}")
    print("=" * 100)

    country_module = load_module_from_file("country_siting_batch_module", country_siting_path)

    errors: List[str] = []

    for index, country in enumerate(countries, start=1):
        try:
            process_one_country(
                index=index,
                total=total,
                country=country,
                project_dir=project_dir,
                results_root=results_root,
                country_module=country_module,
                shipping_script_path=shipping_script_path,
                probabilistic_script_path=probabilistic_script_path,
                input_changer_path=input_changer_path,
                base_shipping_cfg=base_shipping_cfg,
                wpi_df=wpi_df,
                shipping_ready_wpi_csv=shipping_ready_wpi_csv,
            )
        except Exception as exc:
            message = f"[{country['country_name']} | {country['iso3']}] {exc}"
            errors.append(message)
            print("\n" + "!" * 100)
            print("ERROR")
            print(message)
            print(traceback.format_exc())
            print("!" * 100 + "\n")

            if FAIL_FAST:
                raise

    if errors:
        print("=" * 100)
        print("Batch completed with errors:")
        for error in errors:
            print(f" - {error}")
        print("=" * 100)
    else:
        print("=" * 100)
        print("Batch completed successfully.")
        print("=" * 100)


if __name__ == "__main__":
    main()
