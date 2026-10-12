# Carbon-price 2025 USD basis + wrapper port-infrastructure option

## What changed

### 1) Carbon prices now use a 2025 USD basis
The source workbook `Carbon_price_share_per_country.xlsx` reports carbon prices in `US$2010/t CO2`.
`input_changer.py` now converts those values to `US$2025/t CO2` before:
- writing `carbon_prices_for_model.xlsx`
- injecting the country/EU12 carbon-price series into `input_parameters.json`

The conversion stored in the code is:

- GDP deflator 2010 = `89.632`
- GDP deflator 2025 = `128.979`
- multiplier = `128.979 / 89.632 = 1.4389838450553374`

Method/source recorded in the code and output metadata:
- U.S. GDP implicit price deflator
- U.S. Bureau of Economic Analysis via FRED annual series `A191RD3A086NBEA`

`carbon_prices_for_model.xlsx` now includes:
- sheet `carbon_prices_2025usd`
- sheet `metadata`

The metadata sheet explicitly states that the original workbook used `US$2010/t CO2` and that the model output has been converted to a `US$2025/t CO2` basis.

### 2) Wrapper option for ammonia port infrastructure
The wrapper now exposes two settings:

```python
TO_PORT_PORT_INFRASTRUCTURE_USD_PER_TNH3 = 0.0
TO_PORT_PORT_INFRASTRUCTURE_USD_PER_TNH3_BY_COUNTRY = {
    # "ARE": 5.0,
    # "United Arab Emirates": 5.0,
}
```

Use the scalar when you want one value for the whole batch.
Use the dictionary when you want country-specific overrides.

The wrapper resolves the per-country value and passes it to `input_changer.py`, which then writes it into:
- `site_logistics_and_shipping.xlsx`
- `input_parameters.json`
- `input_parameters_country_specific.json`

## Files in this patch

- `input_changer.py`
- `trade_pipeline_spyder_wrapper.py`

Replace your local files with these two files.
