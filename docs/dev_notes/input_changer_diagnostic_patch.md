# Input changer diagnostic and preflight patch

This patch fixes the situation where the wrapper only shows:

```
input_changer.py returned non-zero exit status 1
```

without showing the actual error from inside `input_changer.py`.

## Files replaced

Copy these files into the model root folder, replacing the existing files:

- `trade_pipeline_spyder_wrapper.py`
- `input_changer.py`

## What changed

### `trade_pipeline_spyder_wrapper.py`

- Streams stdout/stderr from `input_changer.py` and `probabilistic_model_runner.py` line-by-line in Spyder.
- Sets `PYTHONUNBUFFERED=1` for subprocesses so tracebacks are printed immediately.
- Adds a preflight check for the input files required by `input_changer.py`:
  - `electricity_grid_share_per_country.xlsx`
  - `Labour_cost_per_employee_by_country.xlsx`
  - `Global_Natural_gas_price_per_country.xlsx`
  - `Carbon_price_share_per_country.xlsx`
  - `Natural_gas_emissions.xlsx`

If any of those files are missing, the wrapper now stops at the start with a clear message instead of waiting until after country siting and shipping.

### `input_changer.py`

- Prints the project folder, Trade_version folder, country folder, and NPV results folder before doing any input checks.
- Prints the resolved path for each country-level input workbook.
- Searches both the project root and `Trade_version`, with a recursive fallback.
- Streams errors from `optimization.py` and `PPA_model_testing.py` so missing packages such as `pulp`, API errors, or file-path errors are visible.

## Likely cause of the error you saw

The error happened at the `input_changer.py` stage. The wrapper log alone does not show the real child-process traceback. The most likely causes are:

1. one of the five required input workbooks is not copied into the model root or `Trade_version`; or
2. `optimization.py` failed inside `input_changer.py` because a package or data dependency is missing.

After this patch, the next run will show the exact underlying error.
