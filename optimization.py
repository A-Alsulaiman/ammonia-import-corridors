# Imports
import json
import logging
import os
import time
from io import StringIO
import sys

import numpy as np
import pandas as pd
import requests
from pulp import LpProblem, LpMinimize, LpVariable, PULP_CBC_CMD, LpStatus
from tqdm import tqdm

# Constants
# Project root: the folder that holds this file. Override with AP_IRA_MODEL_ROOT if the
# data folders live elsewhere. Kept with a trailing slash because the legacy code
# builds paths by string concatenation.
ROOT_DIR = os.environ.get('AP_IRA_MODEL_ROOT') or (
    os.path.dirname(os.path.abspath(__file__)).replace('\\', '/') + '/'
)
if not ROOT_DIR.endswith('/'):
    ROOT_DIR = ROOT_DIR + '/'
API_BASE = 'https://www.renewables.ninja/api/'
# Renewables.ninja API tokens are read from the environment, never stored in the
# repository. Set RENEWABLES_NINJA_TOKENS to one token or a comma-separated list.
# They are only needed when a capacity-factor file is missing from
# power_markets/capacity_factors; the released cache covers every site used in
# the paper, so a normal reproduction run never calls the API.
API_KEYS = [k.strip() for k in os.environ.get('RENEWABLES_NINJA_TOKENS', '').split(',') if k.strip()]
AP_LOC_PATH = ROOT_DIR + 'power_markets/AP_location_dataset.xlsx'
API_CFDATA_OUTPUT_FOLDER = 'power_markets/capacity_factors'

MAX_HRLY_API_REQUESTS = 50
MAX_DAILY_API_REQUESTS = 500
MAX_INSTANT_REQUESTS = 6  # Number of instant requests before waiting
TIM_BETWEEN_REQUESTS = 1  # seconds

WIND_SPECS = {
    2027: {
        'date_from': '2019-01-01',
        'date_to': '2019-12-31',
        'capacity': 1.0,
        'height': 90.2,
        'turbine': 'Bonus B82 2300',
        'format': 'json'
    },
    2040: {
        'date_from': '2019-01-01',
        'date_to': '2019-12-31',
        'capacity': 1.0,
        'height': 120,
        'turbine': 'Gamesa G128 5000',
        'format': 'json'
    }
}
SOLAR_SPECS = {
    2027: {
        'date_from': '2019-01-01',
        'date_to': '2019-12-31',
        'dataset': 'merra2',
        'capacity': 1.0,
        'system_loss': 0.1,
        'tracking': 0,
        'tilt': 35,
        'azim': 180,
        'format': 'json'
    },
    2040: {
        'date_from': '2019-01-01',
        'date_to': '2019-12-31',
        'dataset': 'merra2',
        'capacity': 1.0,
        'system_loss': 0.1,
        'tracking': 0,
        'tilt': 35,
        'azim': 180,
        'format': 'json'
    }
}
DEMAND = {  # MW
    'AP AEC low': [913],
    'AP AEC high': [1007],
    'AP CCS': [127],
    'AP BH2S': [117]
}
CAPEX_SPECS = {
    2027: {
        'high': {
            'wind': 1400,
            'battery': 1500,
            'solar': 1220
        },
        'low': {
            'wind': 1200,
            'battery': 800,
            'solar': 600
        }},
    2040: {
        'high': {
            'wind': 1200,
            'battery': 1200,
            'solar': 700
        },
        'low': {
            'wind': 750,
            'battery': 450,
            'solar': 250
        }}
}

AVAILABILITY = 0.9
GEN_TYPES = ['solar', 'wind']
API_KEYS_INDEX = 0

CAPEX_CASES = ['high', 'low']
YEAR_CASES = [2027, 2040]
MATCHING_CASES = ['yearly', 'monthly', 'hourly']
TECHNOLOGY_CASES = ['AP CCS', 'AP BH2S', 'AP AEC low', 'AP AEC high']

OUTPUT_FILENAME = 'power_markets/OPTIMIZATION_RESULTS_V2.xlsx'

# Initialize logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s', datefmt='%Y-%m-%d %H:%M:%S')


# Functions
# API calls
def all_files_exist(directory, required_files):
    return all(os.path.isfile(os.path.join(directory, file)) for file in required_files)


def get_args_wind(year, lat, lon):
    # Add keys to this dictionary with the values of the WIND_SPECS dictionary
    api_params = {
        'lat': lat,
        'lon': lon,
    }
    for key, value in WIND_SPECS[year].items():
        api_params[key] = value
    return api_params


def get_args_solar(year, lat, lon):
    # Add keys to this dictionary with the values of the SOLAR_SPECS dictionary
    api_params = {
        'lat': lat,
        'lon': lon,
    }
    for key, value in SOLAR_SPECS[year].items():
        api_params[key] = value
    return api_params


def request_session(api_key):
    session = requests.Session()
    session.headers.update({'Authorization': f'Token {api_key}'})
    return session


def url_generator(API_BASE, args, wind_or_solar):
    if wind_or_solar == 'wind':
        url = API_BASE + 'data/wind'
    elif wind_or_solar == 'solar':
        url = API_BASE + 'data/pv'
    return url


def API_call(key, params, year, country, city, url, type, directory, API_KEYS_INDEX):
    s = request_session(key)
    max_retries = len(API_KEYS)  # Maximum retries based on the number of API keys
    retry_wait_time = 60  # seconds to wait before retrying
    retries = 0

    while retries < max_retries:
        try:
            r = s.get(url, params=params)

            # Log the response status code and text
            logging.info(f"API call to {url} with params {params} returned status code {r.status_code}")
            logging.info(f"Response text: {r.text}")

            # Check if the response status code indicates an error
            if r.status_code == 429:
                logging.error(f"Error response from API call: {r.status_code} {r.reason}")
                retries += 1
                API_KEYS_INDEX = (API_KEYS_INDEX + 1) % len(API_KEYS)
                key = API_KEYS[API_KEYS_INDEX]
                s = request_session(key)
                logging.info(f"Switching to next API key. Retrying in {retry_wait_time} seconds...")
                time.sleep(retry_wait_time)
                continue

            if r.status_code != 200:
                logging.error(f"Error response from API call: {r.status_code} {r.reason}")
                return None, API_KEYS_INDEX

            # Check if response is empty
            if not r.text.strip():
                logging.error(f"Empty response from API call to {url} with params {params}")
                return None, API_KEYS_INDEX

            try:
                parsed_response = json.loads(r.text)
            except json.JSONDecodeError as e:
                logging.error(f"JSONDecodeError: {e}")
                logging.error(f"Response that caused error: {r.text}")
                return None, API_KEYS_INDEX

            # Check if 'data' key is in parsed_response
            if 'data' not in parsed_response:
                logging.error(f"'data' key not found in the response: {parsed_response}")
                return None, API_KEYS_INDEX

            # Convert JSON data to a StringIO object and then read it with pandas
            json_str = json.dumps(parsed_response.get('data', {}))
            data_cf = pd.read_json(StringIO(json_str), orient='index')

            # Save the file in a generalized format
            file_path = os.path.join(directory, f'{year}_{city}_{country}_{type}_capacity_data.json')
            data_cf.to_json(file_path)
            logging.info(f"Data saved successfully: {file_path}")

            return parsed_response, API_KEYS_INDEX

        except requests.RequestException as e:
            logging.error(f"RequestException: {e}")
            return None, API_KEYS_INDEX
        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}")
            return None, API_KEYS_INDEX

    logging.error(f"Failed to get a valid response after {max_retries} retries.")
    return None, API_KEYS_INDEX


def get_args(type, year, lat, lon):
    if type == 'wind':
        return get_args_wind(year, lat, lon)
    elif type == 'solar':
        return get_args_solar(year, lat, lon)


# Optimization Functions
def optimize_energy_system(COST_wind, COST_battery, COST_solar, EFF, D, num_time_periods, CF_t_wind, CF_t_solar,
                           withSolar=True):
    """
    Min-cost sizing of wind/solar/battery to meet firm demand D per period.
    Notes:
      * Hourly mode: CF_t_* are capacity factors (0..1) and D is MW. w_t, S_t, c_t, d_t are MW.
      * Monthly/yearly modes: CF_t_* are energy per MW in the period (MWh/MW).
        D is MWh per period; w_t, S_t, c_t, d_t are MWh per period.
      * Battery assumed 1C (charge/discharge power <= energy capacity).
      * Battery SoC is **cyclic** over the horizon: b_0 == b_{T-1} + η c_0 − d_0.
    """
    problem = LpProblem(str(np.random.uniform(0, 1)), LpMinimize)

    # Decision variables
    w = LpVariable("w", lowBound=0)  # wind capacity (MW)
    b = LpVariable("b", lowBound=0)  # battery energy capacity (MWh) or MW*h per period
    # If withSolar is False, completely disable solar by setting an upper bound of 0.
    S = LpVariable("S", lowBound=0, upBound=None if withSolar else 0.0)  # solar capacity (MW)
    w_t = [LpVariable(f"w_{t}", lowBound=0) for t in range(num_time_periods)]  # wind generation in period t
    c_t = [LpVariable(f"c_{t}", lowBound=0) for t in range(num_time_periods)]  # battery charge in period t
    d_t = [LpVariable(f"d_{t}", lowBound=0) for t in range(num_time_periods)]  # battery discharge in period t
    b_t = [LpVariable(f"b_{t}", lowBound=0) for t in range(num_time_periods)]  # battery state of charge
    S_t = [LpVariable(f"s_{t}", lowBound=0) for t in range(num_time_periods)]  # solar generation in period t

    # Objective function
    objective = COST_wind * w + COST_battery * b + COST_solar * S
    problem += objective

    # Power/energy balance and capacity limits
    for t in range(num_time_periods):
        # Balance: generation + discharge - charge == demand
        problem += w_t[t] + S_t[t] - c_t[t] + d_t[t] == D
        # Resource limits
        problem += w_t[t] <= CF_t_wind[t] * w
        problem += S_t[t] <= CF_t_solar[t] * S
        # Battery power limits (1C assumption)
        problem += c_t[t] <= b
        problem += d_t[t] <= b

    # --- Battery state-of-charge dynamics with cycle closure over the horizon ---
    # Allow energy to wrap across the boundary (end-of-horizon -> start)
    problem += b_t[0] == b_t[num_time_periods - 1] + EFF * c_t[0] - d_t[0]
    for tt in range(1, num_time_periods):
        problem += b_t[tt] == b_t[tt - 1] + EFF * c_t[tt] - d_t[tt]

    # SoC bounds at all timesteps (b_t already has lowBound=0)
    for tt in range(num_time_periods):
        problem += b_t[tt] <= b

    # Solve the optimization problem
    problem.solve(PULP_CBC_CMD(msg=False))

    # Checks the status of the LP problem
    status = LpStatus[problem.status]
    if LpStatus[problem.status] != 'Optimal':
        raise Exception("Non-optimal solution found. Optimization failed.")

    # Post-processing
    # Optimal Capacities
    wind_capacity = w.value()
    battery_capacity = b.value()
    solar_capacity = S.value()
    # Important generation profiles
    discharge_t = [d_t_var.value() for d_t_var in d_t]
    wind_total_gen = [w.value() * i for i in CF_t_wind]
    solar_total_gen = [S.value() * i for i in CF_t_solar]
    total_gen = [wind_total_gen[t] + solar_total_gen[t] for t in range(num_time_periods)]

    # Defining curtailment
    optimal_w_t = [w_t_var.value() for w_t_var in w_t]
    optimal_s_t = [S_t_var.value() for S_t_var in S_t]
    curtailment_t = [CF_t_wind[t] * w.value() + CF_t_solar[t] * S.value() - optimal_w_t[t] - optimal_s_t[t] for t in
                     range(num_time_periods)]

    obj_func_value = COST_wind * w.value() + COST_battery * b.value() + COST_solar * S.value()

    # returns (wind capacity, battery capacity, solar capacity, curtailment, demand, wind generation, solar generation,
    # optimization status)
    return (
        wind_capacity, battery_capacity, solar_capacity, curtailment_t, discharge_t, wind_total_gen, solar_total_gen,
        obj_func_value, total_gen, status)


def aggregate_to_monthly(CF):
    if len(CF) != 8760:
        raise ValueError("The input list must have a length of 8760.")

    # Define the number of hours in each month (non-leap year)
    hours_per_month = [744, 672, 744, 720, 744, 720, 744, 744, 720, 744, 720, 744]

    monthly_data = []
    start = 0
    for hours in hours_per_month:
        # Sum the values for each month
        monthly_sum = sum(CF[start:start + hours])
        monthly_data.append(monthly_sum)
        start += hours

    return monthly_data


def aggregate_to_yearly(CF):
    if len(CF) != 8760:
        raise ValueError("The input list must have a length of 8760.")
    return [sum(CF)]


def aggregate_to_monthly_post_processing(data, matching):
    if matching == 'yearly':
        # If the data is yearly, divide by 12 and create a list of length 12
        monthly_data = [data[0] / 12] * 12
    elif matching == 'monthly':
        # If the data is already monthly, no change is needed
        monthly_data = data
    elif matching == 'hourly':
        # If the data is hourly, aggregate to monthly
        # Assuming `data` is a list of length 8760 (hours in a year, non-leap)
        monthly_data = []
        hours_per_month = [31 * 24, 28 * 24, 31 * 24, 30 * 24, 31 * 24, 30 * 24, 31 * 24, 31 * 24, 30 * 24, 31 * 24,
                           30 * 24, 31 * 24]
        start = 0
        for hours in hours_per_month:
            end = start + hours
            monthly_sum = sum(data[start:end])
            monthly_data.append(monthly_sum)
            start = end
    return monthly_data


# Run the script
if '__main__' == __name__:

    # Set up a log file
    # log_file = open('log_file.txt', 'w')
    # sys.stdout = log_file
    # sys.stderr = log_file

    # Set up CF directories
    solar_dir = os.path.join(API_CFDATA_OUTPUT_FOLDER, 'solar')
    wind_dir = os.path.join(API_CFDATA_OUTPUT_FOLDER, 'wind')

    # Create directories if they don't exist
    os.makedirs(solar_dir, exist_ok=True)
    os.makedirs(wind_dir, exist_ok=True)

    # Read the US AP Plants sheet from the Excel file
    us_ap_plants_df = pd.read_excel(AP_LOC_PATH)

    requests_count = 0
    for year in YEAR_CASES:
        for type in GEN_TYPES:
            directory = os.path.join(API_CFDATA_OUTPUT_FOLDER, type)
            for index, row in us_ap_plants_df.iterrows():
                if index >= 0:  # Assuming you always want to start from the first row
                    lat = row['lat']
                    lon = row['lon']
                    city = row['City']
                    country = row['Country']

                    # Check if file already exists
                    filename = f"{year}_{city}_{country}_{type}_capacity_data.json"
                    file_path = os.path.join(directory, filename)
                    if not os.path.isfile(file_path):
                        args = get_args(type, year, lat, lon)
                        url = url_generator(API_BASE, args, type)
                        if not API_KEYS:
                            raise RuntimeError(
                                f'Capacity-factor file {file_path} is missing and no renewables.ninja token is set. '
                                'Export RENEWABLES_NINJA_TOKENS=<token> to fetch it, or restore the cache file.'
                            )
                        api_key = API_KEYS[API_KEYS_INDEX]

                        # Call API
                        response, API_KEYS_INDEX = API_call(api_key, args, year, country, city, url, type, directory, API_KEYS_INDEX)

                        # Handle API throttling
                        requests_count += 1
                        if requests_count % MAX_INSTANT_REQUESTS == 0:
                            time.sleep(TIM_BETWEEN_REQUESTS)
                        if requests_count >= MAX_DAILY_API_REQUESTS:
                            requests_count = 0
                            logging.info(f"Daily limit reached. Pausing for 24 hours...")
                            time.sleep(86400)  # Sleep for 24 hours

    logging.info("API Call Script execution completed.")
    # logging.info(f"successfully saved {year}_{town}_{state}_{type}_capacity_data.json")

    # obtain the list of values of the capacity_factor json files and save them in a dictionary with the tuple being the year, town, state, type
    capacity_factors = {}
    for year in YEAR_CASES:
        for type in GEN_TYPES:
            for index, row in us_ap_plants_df.iterrows():
                if index >= 0:  # Assuming you always want to start from the first row
                    city = row['City']
                    country = row['Country']
                    filename = f"{year}_{city}_{country}_{type}_capacity_data.json"
                    file_path = os.path.join(API_CFDATA_OUTPUT_FOLDER, type, filename)
                    if os.path.isfile(file_path):
                        with open(file_path, 'r') as f:
                            data = json.load(f)
                            # Store a flat list (not list-of-list)
                            cf_flat = list(data['electricity'].values())
                            if city == 'Pryor':
                                cf_flat = [max(0.01, v) for v in cf_flat]
                            capacity_factors[(year, city, country, type)] = cf_flat

    # Build a one-column object table where each cell contains the full hourly CF list.
    # Using DataFrame(capacity_factors).T expands the 8760-hour list into 8760 columns
    # in newer pandas versions, which breaks the legacy column rename to ['CFs'].
    capacity_factor_df = pd.Series(capacity_factors, name='CFs').to_frame()
    if not isinstance(capacity_factor_df.index, pd.MultiIndex):
        capacity_factor_df.index = pd.MultiIndex.from_tuples(
            capacity_factor_df.index,
            names=['year', 'city', 'country', 'type'],
        )
    else:
        capacity_factor_df.index = capacity_factor_df.index.set_names(['year', 'city', 'country', 'type'])
    capacity_factor_df.sort_index(inplace=True)

    total_iterations = len(YEAR_CASES) * len(MATCHING_CASES) * len(us_ap_plants_df) * len(CAPEX_CASES) * len(
        TECHNOLOGY_CASES)
    optimization_df = pd.DataFrame(
        columns=['year', 'matching', 'country', 'city', 'capex_type', 'technology', 'wind_capacity', 'battery_capacity',
                 'solar capacity', 'curtailment', 'demand', 'wind_gen', 'solar_gen', 'cost', 'total_gen', 'CF sum', 'status'])

    with tqdm(total=total_iterations) as pbar:
        for year in YEAR_CASES:
            for matching in MATCHING_CASES:
                for country, city in zip(list(us_ap_plants_df['Country']), list(us_ap_plants_df['City'])):

                    logging.info(f"Processing {year, matching, country, city}")

                    # get the rows that match the town and the state
                    rows = capacity_factor_df.loc[(year, city, country)]
                    CF_wind = rows.loc['wind']['CFs']
                    CF_solar = rows.loc['solar']['CFs']
                    if matching == 'yearly':
                        CF_solar = aggregate_to_yearly(CF_solar)
                        CF_wind = aggregate_to_yearly(CF_wind)
                    elif matching == 'monthly':
                        CF_solar = aggregate_to_monthly(CF_solar)
                        CF_wind = aggregate_to_monthly(CF_wind)

                    for capex_type in CAPEX_CASES:
                        for technology in TECHNOLOGY_CASES:

                            # Optimization inputs
                            COST_wind = CAPEX_SPECS[year][capex_type]['wind'] * 1000  # $/MW
                            COST_battery = CAPEX_SPECS[year][capex_type]['battery'] * 1000 / 4  # $/MW (1C assumption)
                            COST_solar = CAPEX_SPECS[year][capex_type]['solar'] * 1000  # $/MW
                            EFF = 0.85
                            num_time_periods = len(CF_wind)

                            # Conditional electricity demand
                            if matching == 'hourly':
                                D = DEMAND[technology][0]  # MW each hour
                            elif matching == 'monthly':
                                D = DEMAND[technology][0] * 365 * 24 / 12 * AVAILABILITY  # MWh per month
                            elif matching == 'yearly':
                                D = DEMAND[technology][0] * 365 * 24 * AVAILABILITY  # MWh per year (single period)

                            total_CF = sum(CF_wind) + sum(CF_solar)

                            # optimize the energy system
                            W, B, S, curtailment, demand, w_gen, s_gen, obj_func, total_gen, status = optimize_energy_system(
                                COST_wind, COST_battery,
                                COST_solar,
                                EFF, D, num_time_periods,
                                CF_wind, CF_solar
                                , withSolar=True)

                            # process curtailment, demand, and generation to have len(12). Case 1) yearly: divide by 12 and have list len 12.
                            # Case 2) monthly: do nothing. #Case 3) Take the list of len(8760) and aggregate to monthly
                            curtailment_monthly = aggregate_to_monthly_post_processing(curtailment, matching)
                            demand_monthly = aggregate_to_monthly_post_processing(demand, matching)
                            w_gen_monthly = aggregate_to_monthly_post_processing(w_gen, matching)
                            s_gen_monthly = aggregate_to_monthly_post_processing(s_gen, matching)
                            total_gen_monthly = aggregate_to_monthly_post_processing(total_gen, matching)

                            # add row to optimization_df with the values
                            optimization_row = [year, matching, country, city, capex_type, technology,
                                                W, B, S, curtailment_monthly, demand_monthly, w_gen_monthly,
                                                s_gen_monthly, obj_func, total_gen_monthly, total_CF, status]
                            optimization_df.loc[len(optimization_df)] = optimization_row

                            # update the progress bar
                            pbar.update(1)

                    logging.info(f"Added rows for {city}, {country}")

    logging.info("Optimization complete.")

    # export the optimization_df to an Excel file
    optimization_df.to_excel(OUTPUT_FILENAME, index=False)
