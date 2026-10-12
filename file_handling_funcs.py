import pandas as pd
import pytz
from datetime import datetime
import ast
from _CAPEX import CAPEX
from _path_dependent_data_handling import *

def dataframes_to_excel(dfs, file_name):
    """
    Convert multiple pandas DataFrames into one Excel file with multiple sheets.

    Parameters:
    - dfs (dict): Dictionary where keys are sheet names and values are corresponding DataFrames
    - file_name (str): Name of the Excel file

    Returns:
    None
    """
    try:
        # Open the Excel file for writing
        with pd.ExcelWriter(file_name) as writer:
            # Iterate over each sheet name and corresponding DataFrame in the dictionary
            for sheet_name, df in dfs.items():
                # Check if the value is another dictionary (specifically for 'CI')
                if isinstance(df, dict):
                    # If it is, iterate over each sub-sheet name and corresponding sub-DataFrame
                    for sub_sheet_name, sub_df in df.items():
                        # Use a combination of the main key and sub key as the sheet name
                        sub_sheet_name = f"{sheet_name}_{sub_sheet_name}"
                        # Write the sub-DataFrame to the Excel file
                        sub_df.to_excel(writer, sheet_name=sub_sheet_name, index=False)
                else:
                    # Write the DataFrame to the Excel file
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
        # Print a success message
        print(f'Successfully written to {file_name}')
    except Exception as e:
        # Print an error message if an exception occurs
        print(f'An error occurred: {e}')

def get_current_est_time():
    est_timezone = pytz.timezone('US/Eastern')

    current_time_est = datetime.now(est_timezone)

    return current_time_est.strftime('%Y-%m-%d %H:%M:%S')

def extract_values(time, matching, results_df=wind_and_battery_excel):
    # Filter for given time and matching, and capex_description 'low'
    mask_high = (results_df['time'] == time) & (results_df['matching'] == matching) & (
            results_df['capex_description'] == 'high') & (results_df['location'] == 'high')
    mask_low = (results_df['time'] == time) & (results_df['matching'] == matching) & (
            results_df['capex_description'] == 'low') & (results_df['location'] == 'low')

    # Extracting values for each technology
    extracted_values = {}
    for mask in [mask_high, mask_low]:
        filtered_df = results_df[mask]
        for _, row in filtered_df.iterrows():
            technology = row['technology']
            wind_capacity = row['wind_capacity']
            battery_capacity = row['battery_capacity']
            curtailment = ast.literal_eval(row['curtailment'])
            discharge = ast.literal_eval(row['discharge'])
            extracted_values[technology] = {
                'wind_capacity': wind_capacity,
                'battery_capacity': battery_capacity,
                'curtailment': curtailment,
                'discharge': discharge
            }

    return extracted_values


def calculate_battery_and_turbine_cost(technology, time, CAPEX_inputs):
    if technology == 'AP SMR':
        return 0

    electricity_requirement_for_wind = CAPEX_inputs['wind_capacity'][technology] * 1000  # kW
    electricity_requirement_for_battery = CAPEX_inputs['battery_capacity'][technology] * 1000  # kW

    return electricity_requirement_for_wind * CAPEX_inputs[f'Wind turbine CAPEX {time}'] + \
        electricity_requirement_for_battery * CAPEX_inputs[f'Battery Storage CAPEX {time}'] / CAPEX_inputs[
            'Battery roundtrip eff'] / 4  # $/kW * kW = kW


def calculate_electrode_cost(time, electricity_requirements, CAPEX_inputs):
    stack_cost_key = 'Stack cost 2027' if time == 2027 else 'Stack cost 2040'
    return electricity_requirements['AP AEC'][1] * CAPEX_inputs[stack_cost_key] * 1000


def calculate_final_CAPEX(technology, scenario, battery_and_turbine, electrode_cost,
                          basic_equipment_costs, CAPEX_inputs):
    capex_obj2 = CAPEX(CAPEX_inputs)
    capex_obj2.get_installed_and_uninstalled_cost(basic_equipment_costs, technology)
    capex_obj2.calculate_FCI_CAPEX_and_WC()

    # Calculating the final CAPEX based on the scenario and technology
    final_cost = 0
    if scenario == 'C' and technology != 'AP SMR':
        additional_cost = battery_and_turbine + (electrode_cost if technology == 'AP AEC' else 0)
        final_cost = capex_obj2.add_cost_outside_of_equipment_list(additional_cost)
    else:
        if technology == 'AP AEC':
            final_cost = capex_obj2.add_cost_outside_of_equipment_list(electrode_cost)
        else:
            final_cost = capex_obj2.calculate_FCI_CAPEX_and_WC()

    # Collecting all the required variables into a dictionary
    capex_details = {
        'UC': capex_obj2.UC,  # Assuming that UC is a property of capex_obj2
        'FCI': capex_obj2.FCI,  # Assuming that FCI is a property of capex_obj2
        'WC': capex_obj2.WC,  # Assuming that WC is a property of capex_obj2
        'CAPEX': capex_obj2.CAPEX,  # Assuming that CAPEX is a property of capex_obj2
        'installation': capex_obj2.installation,
        'instrumentation_and_controls': capex_obj2.instrumentation_and_controls,
        'piping': capex_obj2.piping,
        'electrical': capex_obj2.electrical,
        'building_process_auxiliary': capex_obj2.building_process_auxiliary,
        'service_facilities_and_yard_improvements': capex_obj2.service_facilities_and_yard_improvements,
        'land': capex_obj2.land,
        'engineering_supervision': capex_obj2.engineering_supervision,
        'legal_expenses': capex_obj2.legal_expenses,
        'construction_expense_and_contractors_fee': capex_obj2.construction_expense_and_contractors_fee,
        'contingency': capex_obj2.contingency
    }

    return capex_details


def back_calculate_depreciable_capital_factor(capex_inputs, excluded_factors):
    keys = list(capex_inputs.keys())[1:12]
    depreciable_capital_factor = 1
    for key in keys:
        if key not in excluded_factors:
            depreciable_capital_factor += capex_inputs[key] * (1 / ((2717 / 100) ** 0.6))

    capex_inputs['CAPEX_from_installed_cost'] = depreciable_capital_factor
