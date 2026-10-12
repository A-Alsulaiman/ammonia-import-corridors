import pandas as pd
from optimization import *

# Constants
AEO_PATH = ROOT_DIR + 'AEO22_AEO23_energy_mix_fraction.xlsx'
#OPTIMIZATION_FILE_PATH = ROOT_DIR + 'power_markets/solar_optimization_results.xlsx'
OPTIMIZATION_FILE_PATH = ROOT_DIR + 'power_markets/OPTIMIZATION_RESULTS_V2.xlsx'

# Script
aeo22_data = pd.read_excel(
    AEO_PATH, sheet_name='AEO22',
    index_col=0)
aeo23_data = pd.read_excel(
    AEO_PATH, sheet_name='AEO23',
    index_col=0)
wind_and_battery_excel = pd.read_excel(
    OPTIMIZATION_FILE_PATH, sheet_name='Sheet1')

det_INPUT_PARAMETERS_PATH = ROOT_DIR + "input_parameters_deterministic.json"
prob_INPUT_PARAMETERS_PATH = ROOT_DIR + "input_parameters.json"
