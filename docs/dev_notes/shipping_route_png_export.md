# Shipping route PNG export patch

## Files

- `Shipping_route_model.py`
- `trade_pipeline_spyder_wrapper.py` (optional but recommended)

## What changed

### Shipping_route_model.py

The route map is now saved as a PNG even when the interactive plot window is disabled.

The model now supports these config/CLI fields:

```json
{
  "plot": false,
  "save_plot": true,
  "plot_output_path": null,
  "plot_dpi": 250
}
```

When `save_plot` is `true` and `plot_output_path` is not set, the PNG is saved next to the other shipping outputs using:

```text
<output_prefix>_<origin>_<destination>_routes.png
```

Example:

```text
trade_version_results/United Arab Emirates/Jabal Az Zannah_ruways/
shipping_route_Jabal_Az_Zannah_ruways_Rotterdam_routes.png
```

If `plot` is `false`, the PNG is still saved but no Spyder plot window is shown.

### trade_pipeline_spyder_wrapper.py

The wrapper now passes/keeps `save_plot = true` for shipping runs.

If `REUSE_EXISTING_SHIPPING_OUTPUTS = True`, the wrapper will only reuse an old shipping run when both the summary CSV and a route PNG are present. If the summary exists but the PNG is missing, it reruns the shipping step to generate the PNG.

## How to use

Copy the two patched files into your project directory:

```text
C:\Users\user\Documents\Project\AP_IRA_Model
```

Replace the existing files:

```text
Shipping_route_model.py
trade_pipeline_spyder_wrapper.py
```

Then run the wrapper in Spyder as usual.

## Notes

- The route PNG is saved using the same safe port-name handling as the shipping CSV/XLSX outputs, so port names containing `/` are converted to safe filename tokens.
- To show the plot interactively when running `Shipping_route_model.py` directly, leave `plot` enabled.
- To save without showing, use `plot = false` and `save_plot = true`.
