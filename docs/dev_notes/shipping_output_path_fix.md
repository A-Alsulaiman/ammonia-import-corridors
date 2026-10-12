# Shipping output path fix

## Problem

The wrapper was working through country siting and port matching, but `Shipping_route_model.py`
failed when saving the shipping summary for ports whose WPI name contains a slash, for example:

`Jabal Az Zannah/ruways`

The shipping script used the resolved port names directly when building output filenames and
only replaced spaces. The slash was interpreted as a folder separator, so Pandas tried to save
into a directory such as:

`.../Jabal Az Zannah_ruways/shipping_route_Jabal_Az_Zannah/`

That directory did not exist, causing:

`OSError: Cannot save file into a non-existent directory`

## Files changed

### Shipping_route_model.py

The patched version adds two small helpers:

- `safe_output_name_part(...)` sanitizes generated output filename pieces.
- `ensure_parent_dir(...)` creates the parent folder before writing generated CSV/XLSX files.

The multi-year summary and Monte Carlo output names now use sanitized origin/destination tokens.
For the example above, the output stays inside the intended port folder and uses a filename like:

`shipping_route_Jabal_Az_Zannah_ruways_Rotterdam_summary.csv`

instead of accidentally creating a nested path.

### trade_pipeline_spyder_wrapper.py

The patched wrapper also includes two resume flags:

```python
REUSE_EXISTING_COUNTRY_SITING_OUTPUTS = True
REUSE_EXISTING_SHIPPING_OUTPUTS = True
```

This lets you rerun the wrapper after an interruption without repeating the expensive siting step
when the AP-location workbook and ammonia-cost CSV are already present.

## How to install

Copy these files into your project folder, replacing the files with the same names:

- `Shipping_route_model.py`
- `trade_pipeline_spyder_wrapper.py`

Then rerun `trade_pipeline_spyder_wrapper.py` in Spyder.

If the previous failed run created an empty/intermediate folder named something like
`shipping_route_Jabal_Az_Zannah`, it can be deleted, but it is not required.
