# README — What Changed

This update connects the new trade wrapper to the legacy probabilistic ammonia model and replaces the old country-wrapper behaviour with a country-by-country workflow driven by `trade_pipeline_spyder_wrapper.py`.

## Files added

### `input_changer.py`

New country-specific input-preparation script. The new wrapper calls this after country siting and shipping, and before `probabilistic_model_runner.py`.

For each country, it prepares:

- `power_markets/AP_location_dataset.xlsx`
- `AEO22_AEO23_energy_mix_fraction.xlsx`
- `power_markets/site_logistics_and_shipping.xlsx`
- country-specific `input_parameters.json` values
- country-folder copies of the prepared inputs
- `power_markets/OPTIMIZATION_RESULTS_V2.xlsx` by running `optimization.py`
- `PPA_output.xlsx` by running `PPA_model_testing.py`

The script writes its audit trail to:

```text
trade_version_results/<country>/npv results/input_changer_manifest.json
```

## Files modified

### `trade_pipeline_spyder_wrapper.py`

The wrapper now performs the full sequence:

1. Loads the country list.
2. Runs country siting for the current country.
3. Builds `chosen_ports_of_top_locations.xlsx`.
4. Resolves every port to the canonical `Main Port Name` in `UpdatedPub150`.
5. Runs the shipping model once per unique export port.
6. Calls `input_changer.py`.
7. Runs the probabilistic ammonia model with outputs directly written to:

```text
trade_version_results/<country>/npv results
```

It also saves the canonical port name back into `chosen_ports_of_top_locations.xlsx` as:

```text
canonical_main_port
```

This lets the probabilistic model connect each selected AP site to the correct shipping result folder.

### `country_siting_runner_CELL_ADAPTIVE_CONSTRAINED_HEATMAP_OPT_C_v30_ports.py`

The AP location export now includes site-to-port metadata in addition to the original columns.

The exported AP workbook now contains:

```text
Country
City
lat
lon
nearest_port
distance_km
```

The legacy optimization still uses `Country`, `City`, `lat`, and `lon`, while the probabilistic model uses the added `nearest_port` and `distance_km`.

### `probabilistic_model_runner.py`

The probabilistic runner now:

- accepts command-line arguments:
  - `--country-name`
  - `--country-iso3`
  - `--output-dir`
  - `--matching`
  - `--mult`
  - `--cbam`
  - `--include-ci`
- writes output Excel files directly to `trade_version_results/<country>/npv results`
- loads site-level logistics from `power_markets/site_logistics_and_shipping.xlsx`
- attaches these fields to every randomly selected location:
  - `selected_port`
  - `distance_km`
  - `to_port_cost_usd_per_tnh3`
  - `shipping_cost_usd_per_tnh3`
  - `shipping_co2_t_per_tnh3`
  - `shipping_summary_path`
- records those fields in the `Locations` sheet for traceability
- uses country-specific natural-gas emissions from `input_parameters.json` instead of the old random 39–118 gCO2e/kWh draw when `use_country_ng_emissions` is true
- is now guarded by `if __name__ == "__main__":`, so importing it no longer automatically launches a full model run

### `_DCF_Model.py`

The DCF OPEX calculation now includes four new cost adders:

1. plant-to-port inland logistics cost
2. shipping cost from export port to Rotterdam
3. shipping carbon cost
4. embedded natural-gas carbon cost

The new helper methods are:

```text
_monthly_nh3_output_t()
_transport_to_port_cost_monthly()
_shipping_cost_monthly()
_shipping_carbon_cost_monthly(T)
_embedded_ng_carbon_cost_monthly(T)
_split_carbon_price(...)
_interpolate_carbon_price(...)
```

These costs are added inside `calculate_OPEX()` after the legacy market-dependent OPEX calculation.

### `input_parameters.json`

The base JSON now includes default keys for the new trade-cost logic:

```json
"MI_OPEX_inputs": {
    "to_port_pipeline_usd_per_tkm": 0.02,
    "to_port_port_infrastructure_usd_per_tnh3": 0.0
}
```

```json
"Market_inputs": {
    "site_logistics_path": "power_markets/site_logistics_and_shipping.xlsx",
    "to_port_cost_usd_per_tnh3": 0.0,
    "shipping_cost_usd_per_tnh3": 0.0,
    "shipping_co2_t_per_tnh3": 0.0,
    "enable_to_port_cost": true,
    "enable_shipping_cost": true,
    "enable_shipping_carbon_cost": true
}
```

```json
"carbon_policy": {
    "enable_embedded_ng_eu_50pct": true,
    "enable_embedded_ng_country_50pct": true,
    "enable_shipping_eu_50pct": true,
    "enable_shipping_country_50pct": true,
    "ng_emissions_kgco2e_per_kwh": 0.043,
    "country_carbon_prices_usd_per_tco2": {},
    "eu12_carbon_prices_usd_per_tco2": {}
}
```

`input_changer.py` overwrites these defaults country-by-country.

### `optimization.py`

The model root is now portable. Instead of a hard-coded Windows path, it uses:

```text
AP_IRA_MODEL_ROOT
```

if that environment variable is set, otherwise the directory where `optimization.py` is located.

The AP location input still resolves to:

```text
power_markets/AP_location_dataset.xlsx
```

The optimization output still resolves to:

```text
power_markets/OPTIMIZATION_RESULTS_V2.xlsx
```

### `_path_dependent_data_handling.py`

The model input paths are now built with `os.path.join()` from the portable `ROOT_DIR`.

### `PPA_model_testing.py`

The PPA output path now uses the portable `ROOT_DIR`.

## Country-folder outputs

For each country, the new workflow writes:

```text
trade_version_results/<country>/optimal_locations/
trade_version_results/<country>/<export port>/
trade_version_results/<country>/chosen_ports_of_top_locations.xlsx
trade_version_results/<country>/npv results/
```

The `npv results` folder contains:

```text
AEO22_AEO23_energy_mix_fraction.xlsx
AP_location_dataset_<ISO3>_for_model.xlsx
projected_labour_costs.xlsx
NG_price.xlsx
carbon_prices_for_model.xlsx
site_logistics_and_shipping.xlsx
input_parameters_country_specific.json
OPTIMIZATION_RESULTS_V2.xlsx
PPA_output.xlsx
input_changer_manifest.json
v14_DATASET_<matching>_highgas.xlsx
calibrate_electricity.xlsx
FINAL_sensitivities.xlsx
test.xlsx
```

## New controls in `trade_pipeline_spyder_wrapper.py`

The main controls are near the top of the wrapper:

```python
RUN_RENEWABLE_OPTIMIZATION = True
RUN_PPA_MODEL = True
PROBABILISTIC_MATCHING = "monthly"
PROBABILISTIC_MULT = 40
PROBABILISTIC_INCLUDE_CI = False
PROBABILISTIC_CBAM = False
```

Trade-cost settings:

```python
TO_PORT_PIPELINE_COST_USD_PER_TKM = 0.02
TO_PORT_PORT_INFRASTRUCTURE_USD_PER_TNH3 = 0.0
SHIPPING_ANALYSIS_YEAR = 2030
SHIPPING_DECARBONISATION_SCENARIO = "bau"
EUR_TO_USD_RATE_FOR_SHIPPING = 1.0821
ALLOW_MISSING_SHIPPING_FOR_PROBABILISTIC = False
```

## Required input workbooks

Place these in either the project root or `Trade_version`:

```text
electricity_grid_share_per_country.xlsx
Labour_cost_per_employee_by_country.xlsx
Global_Natural_gas_price_per_country.xlsx
Carbon_price_share_per_country.xlsx
Natural_gas_emissions.xlsx
```

The wrapper still also requires the shipping inputs:

```text
Trade_version/UpdatedPub150.csv or .xlsx
Trade_version/Shipping_Lanes_v1.geojson
```

## Notes

`input_changer.py` runs `optimization.py` and `PPA_model_testing.py` in subprocesses. This avoids stale imported data in Spyder when the wrapper loops from one country to the next.

The probabilistic model is also run in a subprocess by default for the same reason.
