# Carbon-price and natural-gas-emissions model fix

Replace these files in your project root:

- `input_changer.py`
- `probabilistic_model_runner.py`
- `_DCF_Model.py`

with the patched versions in this bundle.

## What changed

### Natural gas supply-chain emissions

The probabilistic runner now resolves the feedstock natural-gas emissions factor from the updated JSON when the country-specific flag is enabled:

- `carbon_intensity.use_country_ng_emissions = true`
- `carbon_policy.use_country_ng_emissions = true`
- `carbon_policy.ng_emissions_kgco2e_per_kwh = <country value>`

If those are present, the model uses that fixed country value instead of the old random `uni(39, 118) / 1000` draw.

`_DCF_Model.py` also applies the same override inside `Stochastic_DCF.__init__`, so the DCF path uses the country-specific value even if an older caller misses the override.

### Carbon prices

`input_changer.py` now writes three aligned carbon-price structures into the JSON:

- `carbon_policy.country_carbon_prices_usd_per_tco2`
- `carbon_policy.eu12_carbon_prices_usd_per_tco2`
- `carbon_policy.process_carbon_prices_usd_per_tco2` = 50% EU-12 + 50% origin-country

It also synchronizes the legacy top-level `carbon_prices` block to the same process-carbon series for compatibility.

`_DCF_Model.py` now makes `get_carbon_cost_for_year(year)` return:

1. `carbon_policy.process_carbon_prices_usd_per_tco2` when available
2. otherwise the 50/50 EU-12 + origin-country split from `carbon_policy`
3. otherwise the legacy top-level `carbon_prices`
4. otherwise `0`

The general process-carbon OPEX in `calculate_OPEX()` now uses `self._model_year_from_T(T)` so it lines up with the model's operating year mapping instead of the old hard-coded `2030 + ...` expression.

## Files in this bundle

- `input_changer.py`
- `probabilistic_model_runner.py`
- `_DCF_Model.py`
- this README
