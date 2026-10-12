#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pack the large caches and raw model outputs that are too big for GitHub into
zip archives for a Zenodo deposit.

Run this on the machine that holds the full project folder:

    python tools/make_zenodo_archive.py --project "C:/Users/user/Documents/Project/AP_IRA_Model" --out "C:/Users/user/Desktop/zenodo"

It writes, for the thirteen partner countries only:

    zenodo/
      01_siting_cache_cf_cache_cells_constraints_v11.zip   PVGIS / NASA POWER / cell caches used by step 1
      02_siting_cache_online_constraints.zip               OpenStreetMap (Overpass) constraint layers used by step 1
      03_shipping_ocean_cache.zip                          Natural Earth coastline and ocean-grid cache used by step 2
      04_dcf_outputs_<Country>.zip  (one per country)      raw DCF outputs (v14_DATASET_*.xlsx, mc_results, logs)
      05_paper_run_level_values.zip                        npv_ci_run_level_values_eligible_only.csv and other large CSVs
      MANIFEST.csv                                         file list, sizes and SHA-256 of every archive member

Nothing is deleted or modified in the project folder. Zenodo accepts files up to
50 GB each, so no archive needs splitting; pass --max-gb to split the per-country
output archives if a smaller size is wanted.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
import zipfile
from pathlib import Path

COUNTRIES = [
    "Australia", "Chile", "Egypt", "Morocco", "Namibia", "Norway", "Oman",
    "Saudi Arabia", "South Africa", "Turkey", "United Arab Emirates",
    "United Kingdom", "United States of America",
]

# Files in trade_version_results/<country>/ that are already in the GitHub repository
# and therefore left out of the output archives.
SMALL_RESULT_PATTERNS = [
    "optimal_locations/*",
    "chosen_ports_of_top_locations.xlsx",
    "*/shipping_route_*_summary.csv",
    "*/shipping_route_*_summary.xlsx",
    "*/shipping_route_*_routes.png",
    "*/shipping_route_*_plot_metadata.json",
    "*/shipping_route_config.json",
    "npv results/*.json",
    "npv results/AEO22_AEO23_energy_mix_fraction.xlsx",
    "npv results/AP_location_dataset_*_for_model.xlsx",
    "npv results/NG_price.xlsx",
    "npv results/OPTIMIZATION_RESULTS_V2.xlsx",
    "npv results/PPA_output.xlsx",
    "npv results/carbon_prices_for_model.xlsx",
    "npv results/projected_labour_costs.xlsx",
    "npv results/site_logistics_and_shipping.xlsx",
    "npv results/subsidy_value.xlsx",
]

ROOT_LARGE_FILES = [
    "npv_ci_run_level_values_eligible_only.csv",
]


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def add_tree(zf: zipfile.ZipFile, base: Path, arc_root: str, manifest: list, skip=None) -> int:
    n = 0
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(base)
        if skip and skip(rel):
            continue
        arcname = f"{arc_root}/{rel.as_posix()}"
        zf.write(p, arcname)
        manifest.append((zf.filename, arcname, p.stat().st_size, sha256(p)))
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="Full project folder (the one that contains trade_version_results)")
    ap.add_argument("--out", required=True, help="Folder to write the archives into")
    ap.add_argument("--skip-caches", action="store_true", help="Only archive the DCF outputs")
    args = ap.parse_args()

    project = Path(args.project).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    if not (project / "trade_version_results").is_dir():
        print(f"trade_version_results not found under {project}", file=sys.stderr)
        return 1

    manifest: list = []
    zopts = dict(compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True)

    if not args.skip_caches:
        caches = [
            ("01_siting_cache_cf_cache_cells_constraints_v11.zip", project / "cf_cache_cells_constraints_v11"),
            ("02_siting_cache_online_constraints.zip", project / ".online_constraints_cache"),
            ("03_shipping_ocean_cache.zip", project / "Trade_version" / ".ocean_cache"),
        ]
        for name, folder in caches:
            if not folder.is_dir():
                print(f"skip {name}: {folder} not found")
                continue
            print(f"writing {name} from {folder} ...")
            with zipfile.ZipFile(out / name, "w", **zopts) as zf:
                n = add_tree(zf, folder, folder.name, manifest)
            print(f"  {n} files")

    def is_small(rel: Path) -> bool:
        return any(rel.match(pat) for pat in SMALL_RESULT_PATTERNS)

    for country in COUNTRIES:
        folder = project / "trade_version_results" / country
        if not folder.is_dir():
            print(f"skip {country}: folder not found")
            continue
        name = f"04_dcf_outputs_{country.replace(' ', '_')}.zip"
        print(f"writing {name} ...")
        with zipfile.ZipFile(out / name, "w", **zopts) as zf:
            n = add_tree(zf, folder, f"trade_version_results/{country}", manifest, skip=is_small)
        print(f"  {n} files")

    name = "05_paper_run_level_values.zip"
    with zipfile.ZipFile(out / name, "w", **zopts) as zf:
        n = 0
        for f in ROOT_LARGE_FILES:
            p = project / f
            if p.is_file():
                zf.write(p, f)
                manifest.append((zf.filename, f, p.stat().st_size, sha256(p)))
                n += 1
    print(f"writing {name}: {n} files")

    with open(out / "MANIFEST.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["archive", "member", "bytes", "sha256"])
        for row in manifest:
            w.writerow([Path(row[0]).name, row[1], row[2], row[3]])
    print(f"done: {len(manifest)} files listed in {out / 'MANIFEST.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
