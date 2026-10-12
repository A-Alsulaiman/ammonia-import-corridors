# power_markets

`capacity_factors/solar/` and `capacity_factors/wind/` hold the hourly renewables.ninja
capacity-factor series (2019 weather year) for every shortlisted site in the thirteen
partner countries, one JSON file per `<year case>_<site letter>_<country>_<technology>`.
`optimization.py` reads them when it sizes the wind, solar and battery supply for each
site; with these files present it never calls the renewables.ninja API.

Three workbooks are written here during a run and are not part of the repository:
`AP_location_dataset.xlsx`, `OPTIMIZATION_RESULTS_V2.xlsx` and
`site_logistics_and_shipping.xlsx`. `input_changer.py` regenerates them for each
country, and the copy used for each country's DCF run is kept under
`trade_version_results/<country>/npv results/`.
