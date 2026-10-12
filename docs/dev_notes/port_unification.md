# Port-source unification patch

## Why this patch exists

The country-siting step and the shipping-route step can disagree if they use different port sources or slightly different port coordinates/names. The shipping model uses `UpdatedPub150.csv`, while the siting script previously defaulted to its own `AUTO`/online port loader. That can create mismatches such as:

- a siting nearest-port name that is not a `Main Port Name` in `UpdatedPub150.csv`;
- a duplicate port name in another country;
- a port-name match that works by text but points to the wrong WPI coordinate;
- a rounding mismatch between the siting output and the shipping model input.

## Files changed

This patch replaces two files:

```text
trade_pipeline_spyder_wrapper.py
country_siting_runner_CELL_ADAPTIVE_CONSTRAINED_HEATMAP_OPT_C_v30_ports.py
```

No change is required in `Shipping_route_model.py`.

## What changed

### 1. The wrapper now forces the siting step to use the same WPI table as shipping

The wrapper creates the standardized shipping-ready WPI CSV as before, then injects it into the siting config:

```python
cfg["PORTS"]["SOURCE"] = "WPI_CSV"
cfg["PORTS"]["PATH"] = str(shipping_ready_wpi_csv)
cfg["PORTS"]["COUNTRY_NAME"] = country_name
cfg["PORTS"]["COUNTRY_ISO3"] = iso3
```

This means `ammonia_delivered_costs.csv` should now write `nearest_port` using the same `Main Port Name` values that `Shipping_route_model.py` expects.

To turn this off, set this in the wrapper:

```python
FORCE_COUNTRY_SITING_TO_USE_UPDATEDPUB150 = False
```

### 2. The siting script now has an `UpdatedPub150`/WPI CSV loader

The new `WPI_CSV` loader reads:

```text
Main Port Name
Latitude
Longitude
Country Code
Region Name
Alternate Port Name
```

It constructs a GeoDataFrame with:

```text
port_name = Main Port Name
lat = Latitude
lon = Longitude
```

The `port_name` field is the important part: it makes the siting output write the canonical WPI `Main Port Name` rather than an online/alternate label.

### 3. The chosen-ports workbook now keeps more audit columns

`chosen_ports_of_top_locations.xlsx` still has the original site latitude/longitude and selected port, but it now also keeps the siting-side port metadata when available:

```text
lat
lon
nearest_port
nearest_port_country
distance_km
port_lat
port_lon
canonical_main_port
canonical_port_country
canonical_port_lat
canonical_port_lon
port_match_type
port_match_distance_km
```

These extra fields make the port matching traceable.

### 4. Duplicate names are resolved using country and coordinates

The wrapper now resolves each selected export port to one specific `UpdatedPub150` row. It checks:

1. exact `Main Port Name`, `Region Name`, or `Alternate Port Name`;
2. matching country name / ISO3 where possible;
3. nearest WPI latitude/longitude using `port_lat` and `port_lon` from `ammonia_delivered_costs.csv`;
4. partial name match only as a fallback.

If a port still cannot be resolved to one WPI row, the run stops with a clear error rather than silently routing from the wrong port.

### 5. Shipping configs now include the WPI country field

For each shipping run, the wrapper writes:

```json
"origin": "<canonical Main Port Name>",
"origin_country": "<exact Country Code value from UpdatedPub150>"
```

This lets `Shipping_route_model.py` filter the WPI table before matching the port name, which avoids duplicate-name errors.

## How to install

Copy these two files into your model directory, replacing the existing files with the same names:

```text
trade_pipeline_spyder_wrapper.py
country_siting_runner_CELL_ADAPTIVE_CONSTRAINED_HEATMAP_OPT_C_v30_ports.py
```

Keep `UpdatedPub150.csv` in:

```text
Trade_version/UpdatedPub150.csv
```

Then run:

```text
trade_pipeline_spyder_wrapper.py
```

from Spyder.

## Expected result

For each country, the nearest ports selected by the optimal-location step and the ports used by the shipping model now come from the same WPI source table. The wrapper also writes the resolved canonical WPI row details into `chosen_ports_of_top_locations.xlsx`.
