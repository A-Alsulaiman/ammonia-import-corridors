# README — Full Modelling Methodology

This document describes the updated end-to-end workflow from the new Spyder wrapper through the probabilistic ammonia model.

## 1. Country loop

The workflow starts from:

```text
List of Countries.xlsx
```

The wrapper reads the country name and ISO3 code. For each country, it creates:

```text
trade_version_results/<country>/
```

The same sequence is repeated until all countries in the workbook have been processed.

## 2. Country siting

For the current country, the wrapper injects:

```text
COUNTRY_NAME
COUNTRY_ISO3
OUTDIR
```

into the country-siting runner configuration and runs:

```text
country_siting_runner_CELL_ADAPTIVE_CONSTRAINED_HEATMAP_OPT_C_v30_ports.py
```

The output folder is:

```text
trade_version_results/<country>/optimal_locations/
```

The two key outputs are:

```text
AP_location_dataset_<ISO3>_new.xlsx
ammonia_delivered_costs.csv
```

The AP location workbook now contains:

```text
Country
City
lat
lon
nearest_port
distance_km
```

If a country-siting run produces an older AP workbook without `nearest_port` and `distance_km`, `input_changer.py` can recover those fields by matching lat/lon against `ammonia_delivered_costs.csv`.

## 3. Chosen export ports

The wrapper matches each top AP site to `ammonia_delivered_costs.csv` by rounded lat/lon and writes:

```text
trade_version_results/<country>/chosen_ports_of_top_locations.xlsx
```

The workbook contains:

```text
lat
lon
nearest_port
canonical_main_port
```

`nearest_port` is the name from the siting output.

`canonical_main_port` is the matched `Main Port Name` from `UpdatedPub150`. The wrapper checks the port name against:

```text
Main Port Name
Region Name
Alternate Port Name
```

If no match is found, the country run stops before shipping.

Duplicate canonical export ports are removed before shipping runs.

## 4. Shipping from selected export ports to Rotterdam

For each unique export port, the wrapper creates:

```text
trade_version_results/<country>/<export port>/
```

Then it writes a port-specific `shipping_route_config.json` and runs:

```text
Shipping_route_model.py
```

The route origin is the selected export port. The destination is Rotterdam unless the base shipping configuration is changed.

The shipping outputs remain in the export-port folder. The probabilistic model uses the shipping summary file to extract:

```text
cost_eur_per_t_mid
total_co2_t_mid
cargo_tonnes
```

The shipping cost used by the ammonia model is:

```text
shipping_cost_usd_per_tnh3 = cost_eur_per_t_mid × EUR_TO_USD_RATE_FOR_SHIPPING
```

The shipping emissions factor used by the ammonia model is:

```text
shipping_co2_t_per_tnh3 = total_co2_t_mid / cargo_tonnes
```

## 5. `input_changer.py`

After shipping is complete, the wrapper calls:

```text
input_changer.py
```

This is the new replacement for the old country wrapper's input-changing role.

It prepares inputs in the exact places expected by the legacy model, while also saving country-specific copies in:

```text
trade_version_results/<country>/npv results/
```

### 5.1 AP locations for optimization

The AP workbook from country siting is copied to:

```text
power_markets/AP_location_dataset.xlsx
```

That is the location workbook used by `optimization.py`.

### 5.2 Electricity mix

The old `electricity_mix_helper.py` generated `AEO22_AEO23_energy_mix_fraction.xlsx` from a European system-model workbook.

The new logic uses:

```text
electricity_grid_share_per_country.xlsx
```

For the current country, it extracts/interpolates the mix for:

```text
2030
2040
2050
```

The output columns are aligned to the legacy carbon-intensity model:

```text
oil
natural gas
coal
nuclear
renewables
```

The new workbook preserves the legacy shape:

```text
2030 at row 1
2040 at row 121
2050 at row 241
rows after 2050 extended to row 673
```

It writes identical sheets:

```text
AEO22
AEO23
```

and copies the workbook to:

```text
AEO22_AEO23_energy_mix_fraction.xlsx
```

in the model root.

### 5.3 Renewable optimization and PPA output

The model no longer uses a user-specified lat/lon workbook at the start.

Instead, the country-siting output provides the top AP locations. Those locations are copied into the expected AP workbook and then:

```text
optimization.py
```

is run to regenerate:

```text
power_markets/OPTIMIZATION_RESULTS_V2.xlsx
```

Then:

```text
PPA_model_testing.py
```

is run to regenerate:

```text
PPA_output.xlsx
```

Both outputs are copied into the country's `npv results` folder for traceability.

### 5.4 Labour cost

The input workbook is:

```text
Labour_cost_per_employee_by_country.xlsx
```

The script filters by ISO3 and tries activity codes in this priority order:

```text
2012
2011
201
20
```

The first activity code with at least two historical observations is linearly projected to 2030. If only one observation exists for a fallback code, the script uses that value as a constant fallback and records the method.

The projected annual pay is converted to an hourly operator wage using:

```text
operator_pay_usd_per_hour = projected_annual_pay_usd_per_employee / 2080
```

The output is:

```text
projected_labour_costs.xlsx
```

and the JSON field updated is:

```text
MI_OPEX_inputs.operator_pay
```

### 5.5 Natural gas and ammonia prices

The input workbook is:

```text
Global_Natural_gas_price_per_country.xlsx
```

For the producing country, `input_changer.py` extracts/interpolates the 2030 natural-gas price and converts it to the model's feedstock price:

```text
Market_inputs.NG_initial_price
```

The ammonia sales price is not based on the producing country's NG price. It is based on the EU27 average NG price, using the old `NG_and_NH3_prices_helper.py` conversion logic:

```text
EU27 average NG price
+ CO2 pass-through
→ USD/MMBtu
→ projected NH3 price
```

The JSON field updated is:

```text
Market_inputs.NH3_initial_price
```

The audit workbook is:

```text
NG_price.xlsx
```

### 5.6 Subsidy

The European subsidy is set to zero for now:

```text
IRA_credits.European_subsidy_rate = 0
```

An audit workbook is also written:

```text
subsidy_value.xlsx
```

### 5.7 Carbon prices and natural-gas emissions

The carbon-price input is:

```text
Carbon_price_share_per_country.xlsx
```

For each model year, `input_changer.py` builds two interpolated carbon-price paths:

```text
country_carbon_prices_usd_per_tco2
eu12_carbon_prices_usd_per_tco2
```

The EU side uses the average of rows labelled `EU-12` in the carbon workbook.

The natural-gas emissions input is:

```text
Natural_gas_emissions.xlsx
```

The country-specific value:

```text
CO2e (gCO2e/kWh)
```

is converted to:

```text
kgCO2e/kWh
```

and stored in the JSON.

The audit workbook is:

```text
carbon_prices_for_model.xlsx
```

### 5.8 Site logistics and shipping metadata

For every selected AP site, `input_changer.py` writes:

```text
power_markets/site_logistics_and_shipping.xlsx
```

and a country-folder copy:

```text
trade_version_results/<country>/npv results/site_logistics_and_shipping.xlsx
```

The key columns are:

```text
Country
City
lat
lon
nearest_port
selected_port
distance_km
to_port_cost_usd_per_tnh3
shipping_cost_usd_per_tnh3
shipping_co2_t_per_tnh3
shipping_summary_path
```

The plant-to-port cost is:

```text
to_port_cost_usd_per_tnh3
= TO_PORT_PIPELINE_COST_USD_PER_TKM × distance_km
+ TO_PORT_PORT_INFRASTRUCTURE_USD_PER_TNH3
```

The default wrapper values are:

```text
TO_PORT_PIPELINE_COST_USD_PER_TKM = 0.02
TO_PORT_PORT_INFRASTRUCTURE_USD_PER_TNH3 = 0.0
```

## 6. Probabilistic model

The wrapper then runs:

```text
probabilistic_model_runner.py
```

with arguments similar to:

```text
--country-name <country>
--country-iso3 <ISO3>
--output-dir trade_version_results/<country>/npv results
--matching monthly
--mult 40
```

The model writes outputs directly to:

```text
trade_version_results/<country>/npv results/
```

## 7. Random site selection inside the probabilistic model

The legacy model randomly selects a row from `OPTIMIZATION_RESULTS_V2.xlsx`.

The updated model uses the selected `AP CCS` row as the canonical site for each simulation because the same random location is already shared across the technology-specific optimization rows.

For that selected `Country` and `City`, the model looks up site logistics from:

```text
power_markets/site_logistics_and_shipping.xlsx
```

It then injects these values into `Market_inputs`:

```text
selected_port
distance_to_port_km
to_port_cost_usd_per_tnh3
shipping_cost_usd_per_tnh3
shipping_co2_t_per_tnh3
shipping_summary_path
```

The `Locations` output sheet now records these values for auditability.

## 8. New OPEX adders in the DCF model

Inside `_DCF_Model.py`, the new costs are applied in `calculate_OPEX()`.

### 8.1 Plant-to-port logistics

Monthly ammonia output is:

```text
monthly_nh3_output_t
= NH3_tpd × 365 / 12 × availability
```

The inland logistics cost is:

```text
monthly_to_port_cost
= monthly_nh3_output_t × to_port_cost_usd_per_tnh3
```

### 8.2 Shipping cost

The shipping route model provides:

```text
shipping_cost_usd_per_tnh3
```

The monthly shipping cost is:

```text
monthly_shipping_cost
= monthly_nh3_output_t × shipping_cost_usd_per_tnh3
```

### 8.3 Shipping carbon cost

The shipping route model provides:

```text
shipping_co2_t_per_tnh3
```

The monthly shipping emissions are:

```text
monthly_shipping_co2_t
= monthly_nh3_output_t × shipping_co2_t_per_tnh3
```

The default carbon-price split is:

```text
50% × EU-12 carbon price
+
50% × origin-country carbon price
```

So:

```text
monthly_shipping_carbon_cost
= monthly_shipping_co2_t × split_carbon_price
```

The split can be controlled in `input_parameters.json`:

```json
"enable_shipping_eu_50pct": true,
"enable_shipping_country_50pct": true
```

### 8.4 Embedded natural-gas carbon cost

For technologies with natural-gas use, the model calculates:

```text
monthly_ng_kwh
= monthly_ng_mmbtu × 293.071
```

Then:

```text
monthly_embedded_ng_co2_t
= monthly_ng_kwh × country_ng_emissions_kgco2e_per_kwh / 1000
```

The carbon-price split is again:

```text
50% × EU-12 carbon price
+
50% × origin-country carbon price
```

So:

```text
monthly_embedded_ng_carbon_cost
= monthly_embedded_ng_co2_t × split_carbon_price
```

The split can be controlled in `input_parameters.json`:

```json
"enable_embedded_ng_eu_50pct": true,
"enable_embedded_ng_country_50pct": true
```

## 9. Carbon toggles

The new carbon-cost toggles are in:

```text
input_parameters.json → carbon_policy
```

Default values:

```json
{
    "enable_embedded_ng_eu_50pct": true,
    "enable_embedded_ng_country_50pct": true,
    "enable_shipping_eu_50pct": true,
    "enable_shipping_country_50pct": true
}
```

To disable the EU half of embedded NG carbon cost:

```json
"enable_embedded_ng_eu_50pct": false
```

To disable the origin-country half of shipping carbon cost:

```json
"enable_shipping_country_50pct": false
```

To disable a full cost stream:

```json
"Market_inputs": {
    "enable_to_port_cost": false,
    "enable_shipping_cost": false,
    "enable_shipping_carbon_cost": false
}
```

## 10. Final country outputs

The probabilistic outputs are now country-specific and are not overwritten by later countries.

Expected output location:

```text
trade_version_results/<country>/npv results/
```

Expected output files:

```text
v14_DATASET_monthly_highgas.xlsx
calibrate_electricity.xlsx
FINAL_sensitivities.xlsx
test.xlsx
```

The most important audit sheet is:

```text
Locations
```

It now links every simulation to:

```text
city
country
selected_port
distance_km
to_port_cost_usd_per_tnh3
shipping_cost_usd_per_tnh3
shipping_co2_t_per_tnh3
shipping_summary_path
```
