# Ammonia exports to the EU from thirteen prospective trade partners: model code and data

Code, input data and country-level intermediate outputs for

> Alsulaiman, A. and Anice, O. *Geospatial siting, shipping and delivered economics of grey, blue and green ammonia exports to the EU and the competitiveness of thirteen prospective trade partner countries under EU climate regulation.* Submitted to Energy Strategy Reviews, 2026.

The pipeline sites renewable ammonia production inside each partner country, routes the product by sea to Rotterdam, prepares country-specific model inputs and then runs a stochastic discounted-cash-flow (DCF) model for grey, blue and green ammonia in 2030 and 2040. Thirteen countries are covered: Australia, Chile, Egypt, Morocco, Namibia, Norway, Oman, Saudi Arabia, South Africa, Turkey, the United Arab Emirates, the United Kingdom and the United States.

The DCF engine is the one released with the earlier Inflation Reduction Act study ([Nature Communications, 2025](https://www.nature.com/articles/s41467-025-56006-6); code at [Eduita/AP_IRA_Model](https://github.com/Eduita/AP_IRA_Model)); the trade layer around it (country siting, shipping, input preparation, batch wrapper) is new to this paper.

## Repository layout

```
.
├── trade_pipeline_spyder_wrapper_USA_precomputed_switch.py   entry point (batch over all countries)
├── country_siting_runner_..._v30_ports.py                    step 1: candidate-cell screening and ranking
├── country_siting_runner_..._v29_black_gap_overlay_cells.py  helper loaded by v30 (grid, constraints, scoring)
├── Shipping_route_model.py                                   step 2: sea routes, cost and emissions to Rotterdam
├── input_changer.py                                          step 3: country-specific inputs for the DCF model
├── probabilistic_model_runner.py                             step 4: stochastic DCF (NPV, carbon intensity)
├── optimization.py, PPA_model_testing.py                     renewable system optimisation and PPA price (called by step 3)
├── _DCF_Model.py, _CAPEX.py, _MI_OPEX.py, _CI_Calculator.py,
│   _TAX_CREDITS.py, _GBM.py, _instantiate_inputs.py, ...     DCF model internals
├── List of Countries.xlsx                                    the thirteen partner countries (name, official name, ISO3)
├── input_parameters.json                                     base DCF inputs (country fields are overwritten by step 3)
├── input_parameters_before_trade_input_changer.json          pristine copy of the base inputs
├── AEO22_AEO23_energy_mix_fraction.xlsx                      grid electricity mix fractions
├── Trade_version/                                            country-level input tables used by step 3, plus the
│                                                             shipping-lane network and World Port Index table
├── power_markets/capacity_factors/{solar,wind}/              renewables.ninja hourly capacity-factor cache for every
│                                                             shortlisted site (2027 and 2040 turbine/panel cases)
├── trade_version_results/<country>/                          per-country outputs of steps 1 to 3 (see below)
├── paper_outputs/                                            summary tables behind the paper's figures and tables
├── paper_figures/                                            scripts that produce the figures from the full outputs
├── docs/dev_notes/                                           development notes written while the pipeline was built
└── tools/make_zenodo_archive.py                              packs the large caches and raw outputs for Zenodo
```

What is in `trade_version_results/<country>/`:

| Folder or file | Produced by | Content |
|---|---|---|
| `optimal_locations/AP_location_dataset_<ISO3>_new.xlsx` | step 1 | top renewable-optimised production cells |
| `optimal_locations/ammonia_delivered_costs.csv` | step 1 | LCOE, production cost and transport proxy per candidate cell |
| `optimal_locations/pipeline_sensitivity.csv`, `ports_heatmap_plot_coordinates.csv` | step 1 | diagnostics |
| `chosen_ports_of_top_locations.xlsx` | wrapper | export port assigned to each top location |
| `<port>/shipping_route_<port>_Rotterdam_summary.csv/.xlsx` | step 2 | route distance, cost and emissions by scenario and year |
| `<port>/shipping_route_<port>_Rotterdam_routes.png` | step 2 | route map |
| `npv results/` | step 3 | the country-specific DCF inputs actually used (labour, gas and carbon prices, site logistics, renewable optimisation and PPA results, `input_parameters_country_specific.json`, manifest) |

The raw per-run DCF outputs (`v14_DATASET_*.xlsx`, `mc_results` CSVs, about 40 to 90 MB per port) and the two siting caches (`cf_cache_cells_constraints_v11/`, about 178,000 files, and `.online_constraints_cache/`, about 680 MB) exceed GitHub's file limits and are archived on Zenodo instead (DOI to be added on deposit). `tools/make_zenodo_archive.py` is the script used to build those archives.

## How to run

Requirements: Python 3.10 or later and the packages in `requirements.txt` (`pip install -r requirements.txt`). GeoPandas, Shapely and Rasterio are needed by the siting and shipping steps; the DCF step needs only pandas, NumPy, SciPy, PuLP and openpyxl. The pipeline was run on Windows from Spyder; it has no Windows-specific dependencies beyond the paths that were made relative for this release.

Open `trade_pipeline_spyder_wrapper_USA_precomputed_switch.py` and run it from the repository root, or:

```
python trade_pipeline_spyder_wrapper_USA_precomputed_switch.py
```

The wrapper reads `List of Countries.xlsx` and, for each country, runs the four steps in order and writes to `trade_version_results/<country>/`. Settings are plain constants at the top of the file:

* `PROCESS_ONLY_COUNTRIES = ["EGY"]` restricts the batch to one or more ISO3 codes.
* `REUSE_EXISTING_COUNTRY_SITING_OUTPUTS` and `REUSE_EXISTING_SHIPPING_OUTPUTS` are `True`, so with the released results folder in place steps 1 and 2 are skipped and the run goes straight to input preparation and the DCF model. This is the fast path for reproducing the paper's NPV and carbon-intensity results and it needs no network access and no API token.
* `PROBABILISTIC_MATCHING = "hourly"` and `PROBABILISTIC_MULT = 40` are the settings used in the paper; `PROBABILISTIC_WORKERS` controls parallelism.
* `USA_USE_PRECOMPUTED_AMMONIA_COSTS = True` builds the US top-ten workbook from the released `ammonia_delivered_costs.csv` filtered to Port Lavaca and Corpus Christi, as described in Section 4.3 of the paper.

Each step can also be run on its own; `python <script>.py --help` lists the arguments.

### Re-running the siting and shipping steps from scratch

Delete (or rename) a country's `optimal_locations/` or `<port>/` folder and the wrapper recomputes it. Step 1 downloads monthly resource data from PVGIS and NASA POWER and land-use constraints from OpenStreetMap (Overpass) and other open sources, and caches everything under `cf_cache_cells_constraints_v11/` and `.online_constraints_cache/`. A full recomputation for all thirteen countries takes days and depends on those services being available; the Zenodo archive contains the caches so the exact grids used in the paper can be restored instead. Step 2 downloads the Natural Earth coastline once into `Trade_version/.ocean_cache/`.

The hourly capacity factors used by the renewable optimisation in step 3 come from renewables.ninja. The release includes the cached hourly series for every site that appears in the paper, so the API is never called in a normal run. If a site is added, set a token before running:

```
set RENEWABLES_NINJA_TOKENS=<your token>            (Windows)
export RENEWABLES_NINJA_TOKENS=<your token>         (Linux, macOS)
```

Several comma-separated tokens are accepted and rotated when the hourly limit is reached. The variable `AP_IRA_MODEL_ROOT` can be set to point `optimization.py` at a project folder other than the one containing the file.

### Figures

`paper_figures/` holds the scripts that produced the figures and summary tables from the full outputs: the location maps (`generate_renewable_locations_shipping_map_v17.py`, `generate_country_locations_ports_grid_map_v12.py`), the LCOE-against-transport scatter and the top-ten location tables (`generate_best_locations_ovals_lcoe_vs_transport*.py`), the shipping cost and emissions bars (`generate_shipping_bau_cost_emissions_bars_usd_rate_1p0821.py`), the NPV and carbon-intensity box-whisker figure (`npv_ci_pathway_boxwhiskers_with_no_carbon_effect.py`), the cost-structure breakdown (`generate_npv_effects_cost_structure_three_pathways_tight_labels_legend_eligible_only.py`) and the per-country dashboard cards. They expect the repository root as the project folder (override with `AP_IRA_PROJECT_DIR`) and the DCF workbooks under `trade_version_results/<country>/npv results/`, which means the Zenodo archive or a fresh run of step 4. The summary tables they wrote for the submitted version are kept in `paper_outputs/`.

## Changes made for this release

The model logic is identical to the version the paper's results were produced with. Four portability edits were made so the code runs outside the original machine:

1. `optimization.py`: the renewables.ninja tokens were removed from the source and are read from `RENEWABLES_NINJA_TOKENS`; `ROOT_DIR` is resolved from the file location (or `AP_IRA_MODEL_ROOT`) instead of a fixed Windows path. A clear error is raised if a capacity-factor file is missing and no token is set.
2. `Shipping_route_model.py`: the default `--lanes` and `--ports` arguments point to `Trade_version/` relative to the script (the wrapper always passes these explicitly).
3. `trade_pipeline_spyder_wrapper_USA_precomputed_switch.py`: `USA_PRECOMPUTED_OPTIMAL_LOCATIONS_DIR` is empty, which resolves to the released `trade_version_results/United States of America/optimal_locations/`.
4. `paper_figures/*.py`: `PROJECT_DIR` is the repository root instead of a fixed path. `Trade_version/shipping_route_config.json` likewise uses relative paths.

Files written by earlier runs (`shipping_route_config.json` copies and `input_changer_manifest.json` inside the results folders) still record the absolute paths of the machine they were produced on; they are provenance records and are not read by the pipeline.

## Data sources and licences

| Dataset | Source | Terms |
|---|---|---|
| Hourly wind and solar capacity factors | [renewables.ninja](https://www.renewables.ninja) (Pfenninger and Staffell, 2016; Staffell and Pfenninger, 2016), MERRA-2 based | CC BY-NC 4.0; cached series redistributed for non-commercial research reproduction |
| Monthly solar resource | PVGIS 5.3, PVGIS-ERA5 database (European Commission JRC) | free reuse with attribution |
| Monthly wind resource | NASA POWER | public domain |
| Land-use and exclusion constraints | OpenStreetMap via Overpass, and other open layers listed in `docs/dev_notes/` | ODbL (OSM) |
| World Port Index (`Trade_version/UpdatedPub150.csv`) | US National Geospatial-Intelligence Agency, Pub. 150 | public domain |
| Global shipping lanes (`Trade_version/Shipping_Lanes_v1.geojson`) | P. Benden, Global Shipping Lanes, version 1.4, Zenodo, 2022, https://doi.org/10.5281/zenodo.6361813 (repository: github.com/newzealandpaul/Shipping-Lanes); lanes georeferenced from the CIA Map of the World's Oceans (October 2012) with later fixes, each lane typed Major, Middle or Minor. The file here is byte-identical to the upstream v1.4 GeoJSON. `Shipping_route_model.py` reads its LineString and MultiLineString geometries into a NetworkX graph with haversine edge weights | Creative Commons Attribution 4.0 with a custom clause excluding Statista (the repository LICENSE file; its README and Zenodo metadata label the same licence BY-SA and BY-NC respectively). Redistributed unchanged, with attribution |
| Coastline for the ocean grid (`Trade_version/.ocean_cache/`, downloaded on first run) | Natural Earth 1:110m / 1:10m land | public domain |
| Country gas, carbon, labour and grid-mix tables (`Trade_version/*.xlsx`) | compiled by the authors from the public sources cited in the Supplementary Information | CC BY 4.0 |
| US grid mix (`AEO22_AEO23_energy_mix_fraction.xlsx`) | US EIA Annual Energy Outlook 2022 and 2023 | public domain |

The code is released under the MIT licence (see `LICENSE`). Data compiled by the authors are released under CC BY 4.0. Third-party data keep their own terms as listed above.

## Citing

See `CITATION.cff`. Please cite the paper and, when the DCF engine is used on its own, the earlier Inflation Reduction Act study ([Nature Communications, 2025](https://www.nature.com/articles/s41467-025-56006-6)).

## Contact

Abdurahman Alsulaiman, EPFL (LEURE) and Oxford Institute for Energy Studies: abdurahman.alsulaiman@epfl.ch
