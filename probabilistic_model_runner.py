#!/usr/bin/env python
# coding: utf-8

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import freeze_support

import pandas as pd
from pathlib import Path
from typing import Dict, Optional, Sequence

from numpy.random import uniform as uni
from numpy.random import seed as seed

from global_variables import *
from _instantiate_inputs import InstantiateInputs
from _GBM import brownian_motion
from _MI_OPEX import MI_OPEX
from _CI_Calculator import Carbon_Intensity_of_technology
from _DCF_Model import Stochastic_DCF
from cleaning_optimization_data import *
from PPA_model_testing import PPA_OUTPUT_FILE_NAME

from file_handling_funcs import *
from _path_dependent_data_handling import *

PPA_data = pd.read_excel(os.path.join(ROOT_DIR, PPA_OUTPUT_FILE_NAME))


def resolve_country_ng_emissions_factor(input_parameters: dict, carbon_intensity: dict) -> float:
    """Resolve the feedstock natural-gas supply-chain emissions factor.

    When the country-specific flag is enabled, this uses the value written by
    input_changer.py into the updated JSON. Otherwise it falls back to the
    legacy random 39-118 gCO2e/kWh range.
    """
    policy = input_parameters.get("carbon_policy", {}) if isinstance(input_parameters, dict) else {}
    ci_cfg = input_parameters.get("carbon_intensity", {}) if isinstance(input_parameters, dict) else {}
    use_country_value = bool(
        carbon_intensity.get("use_country_ng_emissions", False)
        or ci_cfg.get("use_country_ng_emissions", False)
        or policy.get("use_country_ng_emissions", False)
    )
    if use_country_value:
        raw_value = policy.get(
            "ng_emissions_kgco2e_per_kwh",
            carbon_intensity.get("natural gas", ci_cfg.get("natural gas", 0.043)),
        )
        return float(raw_value)
    return float(uni(39, 118) / 1000.0)


def resolve_process_carbon_price_for_year(input_parameters: dict, year: int) -> float:
    """Return the process carbon price used by the DCF model for the given year."""
    policy = input_parameters.get("carbon_policy", {}) if isinstance(input_parameters, dict) else {}

    def _interp(series: dict, year_value: int) -> float:
        if not isinstance(series, dict) or not series:
            return 0.0
        pts = []
        for key, value in series.items():
            try:
                pts.append((int(float(key)), float(value)))
            except Exception:
                continue
        if not pts:
            return 0.0
        pts.sort()
        years = [y for y, _ in pts]
        values = [v for _, v in pts]
        if year_value <= years[0]:
            return values[0]
        if year_value >= years[-1]:
            return values[-1]
        for idx in range(len(years) - 1):
            y0, y1 = years[idx], years[idx + 1]
            if y0 <= year_value <= y1:
                v0, v1 = values[idx], values[idx + 1]
                return v0 + (v1 - v0) * ((year_value - y0) / (y1 - y0))
        return values[-1]

    process_series = policy.get("process_carbon_prices_usd_per_tco2", {})
    if isinstance(process_series, dict) and process_series:
        return float(_interp(process_series, year))

    country_series = policy.get("country_carbon_prices_usd_per_tco2", {})
    eu12_series = policy.get("eu12_carbon_prices_usd_per_tco2", {})
    price = 0.0
    if bool(policy.get("enable_process_eu_50pct", True)):
        price += 0.5 * _interp(eu12_series, year)
    if bool(policy.get("enable_process_country_50pct", True)):
        price += 0.5 * _interp(country_series, year)
    return float(price)

# -----------------------------------------------------------------------------
# Site logistics loaded from input_changer.py outputs
# -----------------------------------------------------------------------------

SITE_META_CACHE = None


def _normalize_site_key(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip().upper()
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    return " ".join(text.split())


def _site_metadata_path() -> str:
    env_path = os.environ.get("TRADE_PIPELINE_SITE_LOGISTICS_PATH")
    if env_path:
        return env_path
    try:
        with open(prob_INPUT_PARAMETERS_PATH, "r", encoding="utf-8") as handle:
            cfg = json.load(handle)
        rel = cfg.get("Market_inputs", {}).get("site_logistics_path")
        if rel:
            return rel if os.path.isabs(rel) else os.path.join(ROOT_DIR, rel)
    except Exception:
        pass
    return os.path.join(ROOT_DIR, "power_markets", "site_logistics_and_shipping.xlsx")


def _load_site_metadata():
    global SITE_META_CACHE
    if SITE_META_CACHE is not None:
        return SITE_META_CACHE

    path = _site_metadata_path()
    if os.path.exists(path):
        df = pd.read_excel(path)
    else:
        df = pd.DataFrame()

    SITE_META_CACHE = df
    return SITE_META_CACHE


def get_site_logistics(country: object, city: object) -> Dict[str, object]:
    """Return logistics/shipping data for the selected optimization site."""
    df = _load_site_metadata()
    defaults = {
        "selected_port": None,
        "nearest_port": None,
        "distance_km": 0.0,
        "to_port_cost_usd_per_tnh3": 0.0,
        "shipping_cost_usd_per_tnh3": 0.0,
        "shipping_co2_t_per_tnh3": 0.0,
        "shipping_summary_path": "",
    }
    if df is None or df.empty:
        return defaults

    country_key = _normalize_site_key(country)
    city_key = _normalize_site_key(city)

    work = df.copy()
    if "Country" in work.columns:
        work["_country_key"] = work["Country"].apply(_normalize_site_key)
    else:
        work["_country_key"] = ""
    if "City" in work.columns:
        work["_city_key"] = work["City"].apply(_normalize_site_key)
    else:
        work["_city_key"] = ""

    hit = work[(work["_country_key"] == country_key) & (work["_city_key"] == city_key)]
    if hit.empty:
        hit = work[work["_city_key"] == city_key]
    if hit.empty:
        return defaults

    row = hit.iloc[0]

    def _float_col(name: str, default: float = 0.0) -> float:
        if name not in row or pd.isna(row[name]):
            return default
        try:
            return float(row[name])
        except Exception:
            return default

    selected_port = None
    for col in ["selected_port", "nearest_port"]:
        if col in row and not pd.isna(row[col]):
            selected_port = str(row[col])
            break

    return {
        "selected_port": selected_port,
        "nearest_port": None if "nearest_port" not in row or pd.isna(row["nearest_port"]) else str(row["nearest_port"]),
        "distance_km": _float_col("distance_km"),
        "to_port_cost_usd_per_tnh3": _float_col("to_port_cost_usd_per_tnh3"),
        "shipping_cost_usd_per_tnh3": _float_col("shipping_cost_usd_per_tnh3"),
        "shipping_co2_t_per_tnh3": _float_col("shipping_co2_t_per_tnh3"),
        "shipping_summary_path": "" if "shipping_summary_path" not in row or pd.isna(row["shipping_summary_path"]) else str(row["shipping_summary_path"]),
    }

def run_simulation(simulations, NPV=False, CAC=False, CI=False, Potential_TC=False, NP_NPV=False, CAPEX_OPEX=False,
                   absolute_support=False, ROI=False, El_market=False, CAPEX_component=False, Sensitivity=False, isCBAM=False,
                   quality_assurance=False, matching='hourly', CAC_quality = False, location_info = False,
                   NPV_parameter_effects: bool = False,
                   simulation_offset: int = 0, show_progress: bool = True):
    NPV_data = pd.DataFrame(
        columns=['time', 'scenario', 'simulation', 'AP_SMR_NPV', 'AP_CCS_NPV', 'AP_BH2S_NPV', 'AP_AEC_NPV'])
    NP_NPV_data = pd.DataFrame(
        columns=['time', 'scenario', 'simulation', 'AP_SMR_NPV', 'AP_CCS_NPV', 'AP_BH2S_NPV', 'AP_AEC_NPV'])
    ROI_data = pd.DataFrame(
        columns=['time', 'scenario', 'simulation', 'AP_SMR_ROI', 'AP_CCS_ROI', 'AP_BH4S_ROI', 'AP_AEC_ROI'])
    CAC_data = pd.DataFrame(columns=['time', 'scenario', 'simulation', 'AP_CCS_CAC', 'AP_BH2S_CAC', 'AP_AEC_CAC'])
    El_data = pd.DataFrame(columns=['time', 'scenario', 'simulation', 'Grid Electricity'])

    multiples_of_12_CI = [12 * i for i in range(28)]
    years_CI = [2027 + i for i in range(28)]
    CI_columns = ['time', 'scenario', 'simulation']
    for time in years_CI:
        CI_columns.append(str(time))
    CI_data = {
        'AP SMR': pd.DataFrame(columns=CI_columns),
        'AP CCS': pd.DataFrame(columns=CI_columns),
        'AP BH2S': pd.DataFrame(columns=CI_columns),
        'AP AEC': pd.DataFrame(columns=CI_columns)
    }

    Potential_and_AP_CE_TC = pd.DataFrame(
        columns=['time', 'scenario', 'simulation', 'AP_CCS_Potential', 'AP_BH2S_Potential', 'AP_AEC_Potential',
                 'AP_CCS_CE', 'AP_BH2S_CE', 'AP_AEC_CE', 'AP_CCS_CE_social', 'AP_BH2S_CE_social', 'AP_AEC_CE_social'])

    OPEX_and_CAPEX = pd.DataFrame(
        columns=['time', 'scenario', 'simulation', 'AP_SMR_CAPEX', 'AP_CCS_CAPEX', 'AP_BH2S_CAPEX', 'AP_AEC_CAPEX',
                 'AP_SMR_OPEX', 'AP_CCS_OPEX', 'AP_BH2S_OPEX', 'AP_AEC_OPEX'])

    absolute_support_data = pd.DataFrame(
        columns=['time', 'scenario', 'simulation', 'AP_CCS_support_45V', 'AP_BH2S_support_45V', 'AP_AEC_support_45V',
                 'AP_CCS_support_45Q', 'AP_BH2S_support_45Q', 'AP_AEC_support_45Q', 'AP_CCS_support_45Y',
                 'AP_BH2S_support_45Y', 'AP_AEC_support_45Y', 'AP_CCS_support_48C', 'AP_BH2S_support_48C',
                 'AP_AEC_support_48C', 'AP_CCS_support_48E', 'AP_BH2S_support_48E',
                 'AP_AEC_support_48E'])

    CAPEX_component_data = pd.DataFrame(columns=[
        'time', 'scenario', 'sim', 'AP_SMR_NPV', 'AP_CCS_NPV', 'AP_BH2S_NPV', 'AP_AEC_NPV'
    ])

    NPV_parameter_effects_records = []

    times =  [2027, 2040]

    column_names = ['time', 'scenario', 'simulation', 'CAPEX', 'Electricity Cost', 'Plant-based OPEX', 'Feedstock Price','Ammonia Price',
                    'Carbon Intensity', 'Battery Variable O&M', 'Battery Fixed O&M', 'Battery CAPEX', 'Wind Turbine CAPEX',
                    '48C Credits', 'Tax Credit Market Value', 'PPA price', 'Curtailment', 'AEC Stack CAPEX', 'AEC Efficiency', 'CBAM Certificates']

    sensitivity_data = {tech:pd.DataFrame(columns=column_names) for tech in technologies}

    location_info_df = pd.DataFrame(columns=[
        'time', 'scenario', 'simulation', 'city', 'country',
        'selected_port', 'distance_km', 'to_port_cost_usd_per_tnh3',
        'shipping_cost_usd_per_tnh3', 'shipping_co2_t_per_tnh3',
        'shipping_summary_path'
    ])

    if show_progress:
        print("Current time:", get_current_est_time())

    for start in starts:
        if start == 0:
            time = 2027
        elif start == 156:
            time = 2040

        storage_dictionary = {}

        for scenario in scenarios:
            for sim_local in range(simulations):

                sim = simulation_offset + sim_local

                global inter
                inter = int(sim)
                seed(inter)

                INPUT_PARAMETERS_PATH = prob_INPUT_PARAMETERS_PATH
                with open(INPUT_PARAMETERS_PATH, 'r') as json_file:
                    INPUT_PARAMETERS = json.load(json_file)
                INPUT_PARAMETERS_copy = INPUT_PARAMETERS.copy()
                #Instantiate inputs from JSON file
                instantiated_input = InstantiateInputs(inter)
                INSTANTIATED_MODEL_INPUTS = instantiated_input.randomness_from_JSON_inputs(INPUT_PARAMETERS_copy)

                # print(inter,  INSTANTIATED_MODEL_INPUTS)
                CAPEX_inputs = INSTANTIATED_MODEL_INPUTS['CAPEX_inputs']
                basic_equipment_costs = INSTANTIATED_MODEL_INPUTS['basic_equipment_costs']
                financial_inputs = INSTANTIATED_MODEL_INPUTS['financial_inputs']
                engineering_inputs = INSTANTIATED_MODEL_INPUTS['engineering_inputs']
                electricity_requirements = INSTANTIATED_MODEL_INPUTS['electricity_requirements']
                natural_gas_requirements = INSTANTIATED_MODEL_INPUTS['natural_gas_requirements']
                BFW_requirements = INSTANTIATED_MODEL_INPUTS['BFW_requirements']
                HP_steam_requirements = INSTANTIATED_MODEL_INPUTS['HP_steam_requirements']
                MI_OPEX_inputs = INSTANTIATED_MODEL_INPUTS['MI_OPEX_inputs']
                Market_inputs = INSTANTIATED_MODEL_INPUTS['Market_inputs']
                carbon_intensity = INSTANTIATED_MODEL_INPUTS['carbon_intensity']
                IRA_credits = INSTANTIATED_MODEL_INPUTS['IRA_credits']

                #Calculate additional inputs from datasets
                CAPEX_inputs['wind_capacity'] = {}
                CAPEX_inputs['battery_capacity'] = {}
                CAPEX_inputs['solar_capacity'] = {}

                RAND_AEC_CAPACITY = uni(0,1)
                LOCATION_RANDOMIZER = uni(0,1)

                def return_masked_data(time, matching, tech, data=CLEANED_DATA, randomizer = LOCATION_RANDOMIZER): #chooses a random LOCATION
                    mask = (data['year'] == time) & (data['matching'] == matching) & (data['technology'] == tech)
                    new_data = data[mask]
                    return new_data.iloc[int(randomizer*len(new_data))]

                def get_capacity_data(time, matching, tech):
                    return return_masked_data(time, matching, tech)

                selected_site_for_logistics = get_capacity_data(time, matching, 'AP CCS')
                site_logistics = get_site_logistics(
                    selected_site_for_logistics['country'],
                    selected_site_for_logistics['city']
                )
                Market_inputs['selected_port'] = site_logistics['selected_port']
                Market_inputs['distance_to_port_km'] = site_logistics['distance_km']
                Market_inputs['to_port_cost_usd_per_tnh3'] = site_logistics['to_port_cost_usd_per_tnh3']
                Market_inputs['shipping_cost_usd_per_tnh3'] = site_logistics['shipping_cost_usd_per_tnh3']
                Market_inputs['shipping_co2_t_per_tnh3'] = site_logistics['shipping_co2_t_per_tnh3']
                Market_inputs['shipping_summary_path'] = site_logistics['shipping_summary_path']

                def AEC_rand_dist(low_item, high_item, rand_num=RAND_AEC_CAPACITY): #ties the randomness of AEC power
                    if isinstance(low_item, float) and isinstance(high_item, float):
                        return low_item + (high_item - low_item) * rand_num
                    elif isinstance(low_item, list) and isinstance(high_item, list):
                        return [low_item[i] + (high_item[i] - low_item[i]) * rand_num for i in range(len(low_item))]

                for tech in technologies[1:]:
                    if tech != 'AP AEC':
                        OPTIMIZATION_ROW = get_capacity_data(time, matching, tech)
                        CAPEX_inputs['wind_capacity'][tech] = OPTIMIZATION_ROW['wind_capacity']
                        CAPEX_inputs['battery_capacity'][tech] = OPTIMIZATION_ROW['battery_capacity']
                        CAPEX_inputs['solar_capacity'][tech] = OPTIMIZATION_ROW['solar capacity']
                    else:
                        OPTIMIZATION_ROW_HIGH = get_capacity_data(time, matching, tech +' high')
                        OPTIMIZATION_ROW_LOW = get_capacity_data(time, matching, tech +' low')

                        CAPEX_inputs['wind_capacity'][tech] = AEC_rand_dist(OPTIMIZATION_ROW_LOW['wind_capacity'], OPTIMIZATION_ROW_HIGH['wind_capacity'])
                        CAPEX_inputs['battery_capacity'][tech] = AEC_rand_dist(OPTIMIZATION_ROW_LOW['battery_capacity'], OPTIMIZATION_ROW_HIGH['battery_capacity'])
                        CAPEX_inputs['solar_capacity'][tech] = AEC_rand_dist(OPTIMIZATION_ROW_LOW['solar capacity'], OPTIMIZATION_ROW_HIGH['solar capacity'])

                def back_calculate_depreciable_capital_factor(capex_inputs, excluded_factors):
                    keys = list(capex_inputs.keys())[1:12]
                    depreciable_capital_factor = 1
                    for key in keys:
                        if key not in excluded_factors:
                            depreciable_capital_factor += capex_inputs[key] * (1 / ((2717 / 100) ** 0.6))

                    capex_inputs['CAPEX_from_installed_cost'] = depreciable_capital_factor

                exclude_from_depreciation = ["Engineering and Supervision Cost", "Legal Expenses Cost",
                                             "Construction Expense and Contractors Fee Cost",
                                             "Working Capital", "Contingency Cost", "Land Cost"]

                back_calculate_depreciable_capital_factor(CAPEX_inputs, exclude_from_depreciation)

                financial_inputs['operating_hours_per_year'] = 365 * 24 * financial_inputs['availability']

                engineering_inputs['AP AEC H2_req'] = engineering_inputs['H2'] * engineering_inputs['H2 LHV'] / \
                        engineering_inputs['Eff_electrolysis'] / 24


                AP_AEC_curtailment_list = AEC_rand_dist(
                        get_capacity_data(time, matching, 'AP AEC low')['curtailment'],
                        get_capacity_data(time, matching, 'AP AEC high')['curtailment'])
                AP_AEC_discharge_list = AEC_rand_dist(
                        get_capacity_data(time, matching, 'AP AEC low')['demand'],
                        get_capacity_data(time, matching, 'AP AEC high')['demand'])
                AP_AEC_gen_list = AEC_rand_dist(
                        get_capacity_data(time, matching, 'AP AEC low')['total_gen'],
                        get_capacity_data(time, matching, 'AP AEC high')['total_gen'])


                electricity_requirements['AP AEC'] = (engineering_inputs['AP AEC H2_req'],
                        engineering_inputs['AP AEC H2_req'] + (408305790 + 3900560.592) / (
                        365 * 24 * financial_inputs['availability']) / 1000)  # MW

                electricity_requirements['curtailment'] = { #MWh/month
                        'AP CCS': get_capacity_data(time, matching, 'AP CCS')['curtailment'],
                        'AP BH2S': get_capacity_data(time, matching, 'AP BH2S')['curtailment'],
                        'AP AEC': AP_AEC_curtailment_list
                    }
                electricity_requirements['discharge'] = {  # MWh/month
                        'AP CCS': get_capacity_data(time, matching, 'AP CCS')['demand'],
                        'AP BH2S': get_capacity_data(time, matching, 'AP BH2S')['demand'],
                        'AP AEC': AP_AEC_discharge_list
                    }
                electricity_requirements['total_gen'] = {  # MWh/month
                        'AP CCS': get_capacity_data(time, matching, 'AP CCS')['total_gen'],
                        'AP BH2S': get_capacity_data(time, matching, 'AP BH2S')['total_gen'],
                        'AP AEC': AP_AEC_gen_list
                    }

                biomass_requirement = (engineering_inputs['H2']*1000/70.4) * (365 * financial_inputs['availability'])   # tonnes/year
                biomass_price = uni(81.23, 94.09)  # $/ton

                market_correlator = uni(0,1)
                def _corr(val1,val2,correlator=market_correlator):
                    return val1+(val2-val1)*correlator

                def get_PPA_data_row(time, matching, policy, randomizer = LOCATION_RANDOMIZER):
                    mask = (PPA_data['time'] == time) & (PPA_data['policy'] == policy) & (PPA_data['matching'] == matching)
                    return PPA_data[mask].iloc[int(randomizer*len(PPA_data[mask]))]


                Market_inputs['PPA_pricing'] = {time: {matching: {policy:
                                                                      get_PPA_data_row(time, matching, policy)['LCOE'] / 1000
                                                                  for policy in policies}
                                                       for matching in matching_type}
                                                for time in times}

                #Defining that the one of the prices for selling curtailment in scenario C are the yearly matching PPA prices
                Market_inputs['PPA_pricing_for_C'] = {
                    time: {policy: Market_inputs['PPA_pricing'][time]['yearly'][policy] for policy in policies} for time
                    in times}

                #Adjusting direct emissions of AP CCS with relation to AP SMR
                AP_SMR_lower_bound = 0.243*carbon_intensity['NGCC thermal efficiency']*13.4/0.28 # (Kg CO2/kWh_e)*(kWh_e/kWh_t_NG)*(kWh_t_NG/Kg NG)/(Kg H2/Kg NG) = Kg CO2/Kg H2
                AP_SMR_upper_bound = 0.527*carbon_intensity['NGCC thermal efficiency']*13.4/0.28 # (Kg CO2/kWh_e)*(kWh_e/kWh_t_NG)*(kWh_t_NG/Kg NG)/(Kg H2/Kg NG) = Kg CO2/Kg H2

                carbon_intensity['stack']['AP SMR'] = uni(AP_SMR_lower_bound, AP_SMR_upper_bound)
                capture_rate_CCS = 0.956
                carbon_intensity['stack']['AP CCS'] = carbon_intensity['stack']['AP SMR']*(1-capture_rate_CCS)

                carbon_intensity['natural gas'] = resolve_country_ng_emissions_factor(
                    INPUT_PARAMETERS,
                    carbon_intensity,
                )

                Policy45V_sensitivity_parameter = 1 if start == 0 else 1 #no units

                IRA_credits['45V'] = {float(key): value for key, value in IRA_credits['45V'].items()}

                def calculate_battery_and_turbine_cost(technology, time, CAPEX_inputs):
                    if technology == 'AP SMR':
                        return -1

                    # Constants
                    WIND_CAPACITY = CAPEX_inputs['wind_capacity'][technology]* 1000  # kW
                    BATTERY_CAPACITY = CAPEX_inputs['battery_capacity'][technology] * 1000  # kW
                    SOLAR_CAPACITY = CAPEX_inputs['solar_capacity'][technology] * 1000  # kW

                    # Calculations
                    wind_capex = CAPEX_inputs[f'Wind turbine CAPEX {time}'] * WIND_CAPACITY  # $/kW * kW = $
                    battery_capex = CAPEX_inputs[f'Battery Storage CAPEX {time}'] / 4 * BATTERY_CAPACITY  # ($/kW * kW/ 4 kWh)* kW = $ Note: kW/ 4 kWh is valid because the cost is based on a 4-hour battery
                    solar_capex = CAPEX_inputs[f'Solar PV CAPEX {time}'] * SOLAR_CAPACITY  # $/kW * kW = $

                    return wind_capex + battery_capex + solar_capex


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
#                        'UC': 0,  # Uninstalled cost
#                        'FCI': 0,  # Fixed Capital Investment
#                        'WC': 0,  # Working Capital
#                        'CAPEX': 0,  # Total CAPEX
#                        'installation': 0,
#                        'instrumentation_and_controls': 0,
#                        'piping': 0,
#                        'electrical': 0,
#                        'building_process_auxiliary': 0,
#                        'service_facilities_and_yard_improvements': 0,
#                        'land': 0,
#                        'engineering_supervision': 0,
#                        'legal_expenses': 0,
#                        'construction_expense_and_contractors_fee': 0,
#                        'contingency': 0                        
                    }

                    return capex_details

                final_CAPEX = {}
                battery_and_turbine_data_final = {}

                for technology in technologies:
                    # Calculate the cost of battery and turbine for the technology
                    battery_and_turbine = calculate_battery_and_turbine_cost(technology, time,
                                                                             CAPEX_inputs)
                    battery_and_turbine_data_final[technology] = battery_and_turbine

                    # Calculate the electrode cost if applicable
                    electrode_cost = 0 if technology != 'AP AEC' else calculate_electrode_cost(time,
                                                                                               electricity_requirements,
                                                                                               CAPEX_inputs)

                    # Calculate the final CAPEX for the technology
                    final_CAPEX[technology] = calculate_final_CAPEX(technology, scenario, battery_and_turbine,
                                                                    electrode_cost, basic_equipment_costs, CAPEX_inputs)

                final_MI_OPEX = {}

                for tecnology in technologies:
                    opex_calculator = MI_OPEX(MI_OPEX_inputs['processing_steps'][tecnology],
                                              MI_OPEX_inputs['operator_pay'], MI_OPEX_inputs['heuristics_factors'],
                                              tecnology,
                                              financial_inputs, MI_OPEX_inputs, final_CAPEX, HP_steam_requirements,
                                              BFW_requirements, biomass_price, biomass_requirement, CAPEX_inputs)
                    total_labor_costs = opex_calculator.calculate_labor_costs()
                    total_fixed_charges = opex_calculator.fixed_charges()
                    misc_costs = opex_calculator.misc_up_costs()
                    start_costs = opex_calculator.get_start_up_costs()
                    utilities = opex_calculator.utilities_costs()

                    if scenario == 'C' and tecnology != 'AP SMR':
                        electricity_requirement_for_wind = CAPEX_inputs['wind_capacity'][tecnology] * 1000  # kW
                        electricity_requirement_for_battery = CAPEX_inputs['battery_capacity'][tecnology] * 1000  # kW

                        battery_OPEX_fixed = (MI_OPEX_inputs['Battery OPEX']) * electricity_requirement_for_battery \
                                       / CAPEX_inputs['Battery roundtrip eff'] / 12 / 4 #VOM BATTERY COSTS ADDED IN THE market-dependent OPEX

                        wind_OPEX = (MI_OPEX_inputs['Wind OPEX']) * electricity_requirement_for_wind / 12  # ($/kW-year) * (kW) * (1 year/ 12 months) = $/month

                        final_MI_OPEX[tecnology] = {
                            'MI_OPEX': total_labor_costs + total_fixed_charges + misc_costs + utilities + wind_OPEX + battery_OPEX_fixed,
                            'MI_OPEX_start': start_costs}
                    else:
                        final_MI_OPEX[tecnology] = {
                            'MI_OPEX': total_labor_costs + total_fixed_charges + misc_costs + utilities,
                            'MI_OPEX_start': start_costs}

                if NPV or NPV_parameter_effects:
                    npv_by_technology = {}

                    for tech in technologies:
                        dcf_obj = Stochastic_DCF(
                            tech, start, L, sim, True, scenario, financial_inputs,
                            final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                            IRA_credits, carbon_intensity, natural_gas_requirements,
                            final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                            electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                            battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching
                        )

                        if NPV_parameter_effects:
                            parameter_effects = dcf_obj.calculate_NPV_parameter_effects()
                            npv_value = sum(parameter_effects.values())
                            absolute_effect_sum = sum(abs(value) for value in parameter_effects.values())
                            for parameter, effect_value in parameter_effects.items():
                                abs_normalized_effect = (
                                    abs(effect_value) / absolute_effect_sum if absolute_effect_sum else 0
                                )
                                signed_normalized_effect = (
                                    effect_value / absolute_effect_sum if absolute_effect_sum else 0
                                )
                                share_of_npv = effect_value / npv_value if npv_value else None
                                NPV_parameter_effects_records.append({
                                    'time': time,
                                    'scenario': scenario,
                                    'simulation': sim,
                                    'technology': tech,
                                    'NPV': npv_value,
                                    'parameter': parameter,
                                    'effect_on_NPV': effect_value,
                                    'abs_normalized_effect': abs_normalized_effect,
                                    'signed_normalized_effect': signed_normalized_effect,
                                    'share_of_NPV': share_of_npv,
                                })
                        else:
                            npv_value = dcf_obj.calculate_NPV()

                        npv_by_technology[tech] = npv_value

                    if NPV:
                        NPV_metrics = (
                            time, scenario, sim,
                            npv_by_technology['AP SMR'],
                            npv_by_technology['AP CCS'],
                            npv_by_technology['AP BH2S'],
                            npv_by_technology['AP AEC'],
                        )
                        NPV_data.loc[len(NPV_data)] = NPV_metrics

                    message = 'Calculating NPV...' + str(time) + ' ' + str(scenario) + ' ' + str(sim)
                    if show_progress:
                        sys.stdout.write('\r' + message)
                        sys.stdout.flush()

                if NP_NPV:
                    NP_NPV_metrics = (time, scenario, sim,
                                      Stochastic_DCF('AP SMR', start, L, sim, False, scenario, financial_inputs,
                                                     final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                     IRA_credits, carbon_intensity, natural_gas_requirements,
                                                     final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                     electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                     battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).calculate_NPV(),
                                      Stochastic_DCF('AP CCS', start, L, sim, False, scenario, financial_inputs,
                                                     final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                     IRA_credits, carbon_intensity, natural_gas_requirements,
                                                     final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                     electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                     battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).calculate_NPV(),
                                      Stochastic_DCF('AP BH2S', start, L, sim, False, scenario, financial_inputs,
                                                     final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                     IRA_credits, carbon_intensity, natural_gas_requirements,
                                                     final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                     electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                     battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).calculate_NPV(),
                                      Stochastic_DCF('AP AEC', start, L, sim, False, scenario, financial_inputs,
                                                     final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                     IRA_credits, carbon_intensity, natural_gas_requirements,
                                                     final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                     electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                     battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).calculate_NPV())
                    NP_NPV_data.loc[len(NP_NPV_data)] = NP_NPV_metrics

                    message = 'Calculating NP NPV...' + str(time) + ' ' + str(scenario) + ' ' + str(sim)
                    if show_progress:
                        sys.stdout.write('\r' + message)
                        sys.stdout.flush()

                if CAC:
                    CAC_metrics = [time, scenario, sim,
                                   Stochastic_DCF('AP CCS', start, L, sim, True, scenario, financial_inputs,
                                                  final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                  IRA_credits, carbon_intensity, natural_gas_requirements,
                                                  final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                  electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                  battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).CAC_updated(),
                                   Stochastic_DCF('AP BH2S', start, L, sim, True, scenario, financial_inputs,
                                                  final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                  IRA_credits, carbon_intensity, natural_gas_requirements,
                                                  final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                  electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                  battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).CAC_updated(),
                                   Stochastic_DCF('AP AEC', start, L, sim, True, scenario, financial_inputs,
                                                  final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                  IRA_credits, carbon_intensity, natural_gas_requirements,
                                                  final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                  electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                  battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).CAC_updated()]

                    CAC_data.loc[len(CAC_data)] = CAC_metrics


                    message = 'Calculating CAC...' + str(time) + ' ' + str(scenario) + ' ' + str(sim)
                    if show_progress:
                        sys.stdout.write('\r' + message)
                        sys.stdout.flush()

                if Potential_TC:
                    Potential_metrics = [time, scenario, sim,
                                         Stochastic_DCF('AP CCS', start, L, sim, True, scenario, financial_inputs,
                                                        final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                        IRA_credits, carbon_intensity, natural_gas_requirements,
                                                        final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                        electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                        battery_and_turbine_data_final,
                                                        isCBAM=isCBAM, matching=matching).total_support(),
                                         Stochastic_DCF('AP BH2S', start, L, sim, True, scenario, financial_inputs,
                                                        final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                        IRA_credits, carbon_intensity, natural_gas_requirements,
                                                        final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                        electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                        battery_and_turbine_data_final,
                                                        isCBAM=isCBAM, matching=matching).total_support(),
                                         Stochastic_DCF('AP AEC', start, L, sim, True, scenario, financial_inputs,
                                                        final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                        IRA_credits, carbon_intensity, natural_gas_requirements,
                                                        final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                        electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                        battery_and_turbine_data_final,
                                                        isCBAM=isCBAM, matching=matching).total_support(),
                                         Stochastic_DCF('AP CCS', start, L, sim, True, scenario, financial_inputs,
                                                        final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                        IRA_credits, carbon_intensity, natural_gas_requirements,
                                                        final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                        electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                        battery_and_turbine_data_final,
                                                        isCBAM=isCBAM, matching=matching).total_CE_support(),
                                         Stochastic_DCF('AP BH2S', start, L, sim, True, scenario, financial_inputs,
                                                        final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                        IRA_credits, carbon_intensity, natural_gas_requirements,
                                                        final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                        electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                        battery_and_turbine_data_final,
                                                        isCBAM=isCBAM, matching=matching).total_CE_support(),
                                         Stochastic_DCF('AP AEC', start, L, sim, True, scenario, financial_inputs,
                                                        final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                        IRA_credits, carbon_intensity, natural_gas_requirements,
                                                        final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                        electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                        battery_and_turbine_data_final,
                                                        isCBAM=isCBAM, matching=matching).total_CE_support(),
                                         Stochastic_DCF('AP CCS', start, L, sim, True, scenario, financial_inputs,
                                                        final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                        IRA_credits, carbon_intensity, natural_gas_requirements,
                                                        final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                        electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                        battery_and_turbine_data_final,
                                                        isCBAM=isCBAM, matching=matching).total_CE_support_social(),
                                         Stochastic_DCF('AP BH2S', start, L, sim, True, scenario, financial_inputs,
                                                        final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                        IRA_credits, carbon_intensity, natural_gas_requirements,
                                                        final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                        electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                        battery_and_turbine_data_final,
                                                        isCBAM=isCBAM, matching=matching).total_CE_support_social(),
                                         Stochastic_DCF('AP AEC', start, L, sim, True, scenario, financial_inputs,
                                                        final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                        IRA_credits, carbon_intensity, natural_gas_requirements,
                                                        final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                        electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                        battery_and_turbine_data_final,
                                                        isCBAM=isCBAM, matching=matching).total_CE_support_social()]


                    Potential_and_AP_CE_TC.loc[len(Potential_and_AP_CE_TC)] = Potential_metrics

                    message = 'Calculating Potential TC...' + str(time) + ' ' + str(scenario) + ' ' + str(sim)
                    if show_progress:
                        sys.stdout.write('\r' + message)
                        sys.stdout.flush()

                if CI:
                    CI_time_SMR = [time, scenario, sim]
                    CI_time_CCS = [time, scenario, sim]
                    CI_time_BH2S = [time, scenario, sim]
                    CI_time_AEC = [time, scenario, sim]

                    for months in multiples_of_12_CI:
                        scenario = scenario
                        CI_time_SMR_vals = Carbon_Intensity_of_technology('AP SMR', carbon_intensity, scenario,
                                                                          financial_inputs, engineering_inputs,
                                                                          biomass_requirement, natural_gas_requirements,
                                                                          electricity_requirements, aeo22_data,
                                                                          aeo23_data).total_emissions(
                            months)
                        CI_time_CCS_vals = Carbon_Intensity_of_technology('AP CCS', carbon_intensity, scenario,
                                                                          financial_inputs, engineering_inputs,
                                                                          biomass_requirement, natural_gas_requirements,
                                                                          electricity_requirements, aeo22_data,
                                                                          aeo23_data).total_emissions(
                            months)

                        CI_time_BH2S_vals = Carbon_Intensity_of_technology('AP BH2S', carbon_intensity, scenario,
                                                                           financial_inputs, engineering_inputs,
                                                                           biomass_requirement,
                                                                           natural_gas_requirements,
                                                                           electricity_requirements, aeo22_data,
                                                                           aeo23_data).total_emissions(months)
                        CI_time_AEC_vals = Carbon_Intensity_of_technology('AP AEC', carbon_intensity, scenario,
                                                                          financial_inputs, engineering_inputs,
                                                                          biomass_requirement, natural_gas_requirements,
                                                                          electricity_requirements, aeo22_data,
                                                                          aeo23_data).total_emissions(
                            months)
                        CI_time_SMR.append(CI_time_SMR_vals)
                        CI_time_CCS.append(CI_time_CCS_vals)
                        CI_time_BH2S.append(CI_time_BH2S_vals)
                        CI_time_AEC.append(CI_time_AEC_vals)

                    list_of_tech_CI = [CI_time_SMR, CI_time_CCS, CI_time_BH2S, CI_time_AEC]

                    for data, technology in zip(list_of_tech_CI, technologies):
                        CI_data[technology].loc[len(CI_data[technology])] = data

                    message = 'Calculating CI...' + str(time) + ' ' + str(scenario) + ' ' + str(sim)
                    if show_progress:
                        sys.stdout.write('\r' + message)
                        sys.stdout.flush()

                if CAPEX_OPEX:
                    OPEX_SMR = []
                    OPEX_CCS = []
                    OPEX_BH2S = []
                    OPEX_AEC = []

                    for T in range(601):
                        Point_SMR = Stochastic_DCF('AP SMR', start, L, sim, True, scenario, financial_inputs,
                                                   final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                   IRA_credits, carbon_intensity, natural_gas_requirements,
                                                   final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                   electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                   battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).calculate_OPEX(T) / (
                                            engineering_inputs['NH3'] * 365 / 12 * financial_inputs[
                                        'availability']),
                        Point_CCS = Stochastic_DCF('AP CCS', start, L, sim, True, scenario, financial_inputs,
                                                   final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                   IRA_credits, carbon_intensity, natural_gas_requirements,
                                                   final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                   electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                   battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).calculate_OPEX(T) / (
                                            engineering_inputs['NH3'] * 365 / 12 * financial_inputs[
                                        'availability']),
                        Point_BH2S = Stochastic_DCF('AP BH2S', start, L, sim, True, scenario, financial_inputs,
                                                    final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                    IRA_credits, carbon_intensity, natural_gas_requirements,
                                                    final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                    electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                    battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).calculate_OPEX(T) / (
                                             engineering_inputs['NH3'] * 365 / 12 * financial_inputs[
                                         'availability']),
                        Point_AEC = Stochastic_DCF('AP AEC', start, L, sim, True, scenario, financial_inputs,
                                                   final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                   IRA_credits, carbon_intensity, natural_gas_requirements,
                                                   final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                   electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                   battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).calculate_OPEX(T) / (
                                            engineering_inputs['NH3'] * 365 / 12 * financial_inputs['availability'])
                        OPEX_SMR.append(Point_SMR)
                        OPEX_CCS.append(Point_CCS)
                        OPEX_BH2S.append(Point_BH2S)
                        OPEX_AEC.append(Point_AEC)

                    CAPEX_OPEX_metric = [time, scenario, sim,
                                         final_CAPEX['AP SMR']['CAPEX'] / engineering_inputs['NH3'] / 1000,
                                         final_CAPEX['AP CCS']['CAPEX'] / engineering_inputs['NH3'] / 1000,
                                         final_CAPEX['AP BH2S']['CAPEX'] / engineering_inputs['NH3'] / 1000,
                                         final_CAPEX['AP AEC']['CAPEX'] / engineering_inputs['NH3'] / 1000,
                                         OPEX_SMR,
                                         OPEX_CCS,
                                         OPEX_BH2S,
                                         OPEX_AEC]

                    OPEX_and_CAPEX.loc[len(OPEX_and_CAPEX)] = CAPEX_OPEX_metric

                    message = 'Calculating CAPEX OPEX...' + str(time) + ' ' + str(scenario) + ' ' + str(sim)
                    if show_progress:
                        sys.stdout.write('\r' + message)
                        sys.stdout.flush()

                if absolute_support:
                    AP_CCS = Stochastic_DCF('AP CCS', start, L, sim, True, scenario, financial_inputs, final_CAPEX,
                                            CAPEX_inputs, engineering_inputs, Market_inputs, IRA_credits,
                                            carbon_intensity, natural_gas_requirements, final_MI_OPEX,
                                            electricity_requirements, MI_OPEX_inputs, electrode_cost,
                                            biomass_requirement, aeo22_data, aeo23_data,
                                            battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).separate_total_support()
                    AP_BH2S = Stochastic_DCF('AP BH2S', start, L, sim, True, scenario, financial_inputs, final_CAPEX,
                                             CAPEX_inputs, engineering_inputs, Market_inputs, IRA_credits,
                                             carbon_intensity, natural_gas_requirements, final_MI_OPEX,
                                             electricity_requirements, MI_OPEX_inputs, electrode_cost,
                                             biomass_requirement, aeo22_data, aeo23_data,
                                             battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).separate_total_support()
                    AP_AEC = Stochastic_DCF('AP AEC', start, L, sim, True, scenario, financial_inputs, final_CAPEX,
                                            CAPEX_inputs, engineering_inputs, Market_inputs, IRA_credits,
                                            carbon_intensity, natural_gas_requirements, final_MI_OPEX,
                                            electricity_requirements, MI_OPEX_inputs, electrode_cost,
                                            biomass_requirement, aeo22_data, aeo23_data,
                                            battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).separate_total_support()

                    absolute_support_metric = [time, scenario, sim,
                                               AP_CCS['45V'], AP_BH2S['45V'], AP_AEC['45V'],
                                               AP_CCS['45Q'], AP_BH2S['45Q'], AP_AEC['45Q'],
                                               AP_CCS['45Y'], AP_BH2S['45Y'], AP_AEC['45Y'],
                                               AP_CCS['45Y'], AP_BH2S['45Y'], AP_AEC['45Y'], #This is a placeholder for 48C. Ignore.
                                               AP_CCS['48E'], AP_BH2S['48E'], AP_AEC['48E']]

                    absolute_support_data.loc[len(absolute_support_data)] = absolute_support_metric

                    message = 'Calculating absolute support...' + str(time) + ' ' + str(scenario) + ' ' + str(sim)
                    if show_progress:
                        sys.stdout.write('\r' + message)
                        sys.stdout.flush()

                # for T in range(601):
                #     obj1 = Stochastic_DCF('AP BH2S', 0, L, 0, True, scenario,financial_inputs, final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs, IRA_credits, carbon_intensity, natural_gas_requirements, final_MI_OPEX, electricity_requirements, MI_OPEX_inputs, electrode_cost, biomass_requirement, aeo22_data, aeo23_data, battery_and_turbine_data_final)
                #     obj2 = Stochastic_DCF('AP BH2S', 0, L, 0, False, scenario,financial_inputs, final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs, IRA_credits, carbon_intensity, natural_gas_requirements, final_MI_OPEX, electricity_requirements, MI_OPEX_inputs, electrode_cost, biomass_requirement, aeo22_data, aeo23_data, battery_and_turbine_data_final)
                #     print(time,scenario,T, obj1.calculate_NPV(), obj2.calculate_NPV())

                if ROI:
                    ROI_metrics = (time, scenario, sim,
                                   Stochastic_DCF('AP SMR', start, L, sim, True, scenario, financial_inputs,
                                                  final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                  IRA_credits, carbon_intensity, natural_gas_requirements,
                                                  final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                  electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                  battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).calculate_NPV(
                                       ROI=True),
                                   Stochastic_DCF('AP CCS', start, L, sim, True, scenario, financial_inputs,
                                                  final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                  IRA_credits, carbon_intensity, natural_gas_requirements,
                                                  final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                  electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                  battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).calculate_NPV(
                                       ROI=True),
                                   Stochastic_DCF('AP BH2S', start, L, sim, True, scenario, financial_inputs,
                                                  final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                  IRA_credits, carbon_intensity, natural_gas_requirements,
                                                  final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                  electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                  battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).calculate_NPV(
                                       ROI=True),
                                   Stochastic_DCF('AP AEC', start, L, sim, True, scenario, financial_inputs,
                                                  final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                  IRA_credits, carbon_intensity, natural_gas_requirements,
                                                  final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                  electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                  battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).calculate_NPV(
                                       ROI=True))
                    ROI_data.loc[len(ROI_data)] = ROI_metrics

                    # print(Stochastic_DCF('AP CCS', start, L, sim, True, scenario,financial_inputs, final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs, IRA_credits, carbon_intensity, natural_gas_requirements, final_MI_OPEX, electricity_requirements, MI_OPEX_inputs, electrode_cost, biomass_requirement, aeo22_data, aeo23_data, battery_and_turbine_data_final).TCvalue,
                    #       Stochastic_DCF('AP CCS', start, L, sim, True, scenario,financial_inputs, final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs, IRA_credits, carbon_intensity, natural_gas_requirements, final_MI_OPEX, electricity_requirements, MI_OPEX_inputs, electrode_cost, biomass_requirement, aeo22_data, aeo23_data, battery_and_turbine_data_final).inflation_rate)

                    message = 'Calculating ROI...' + str(time) + ' ' + str(scenario) + ' ' + str(sim)
                    if show_progress:
                        sys.stdout.write('\r' + message)
                        sys.stdout.flush()

                if El_market and (scenario == 'A' or scenario == 'B'):
                    El_metric = [time, scenario, sim,
                                 Stochastic_DCF('AP SMR', start, L, sim, True, scenario, financial_inputs, final_CAPEX,
                                                CAPEX_inputs, engineering_inputs, Market_inputs, IRA_credits,
                                                carbon_intensity, natural_gas_requirements, final_MI_OPEX,
                                                electricity_requirements, MI_OPEX_inputs, electrode_cost,
                                                biomass_requirement, aeo22_data, aeo23_data,
                                                battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).El_market]

                    # print(scenario, El_metric[3])

                    El_data.loc[len(El_data)] = El_metric

                    message = 'Calculating Electricity Market...' + str(time) + ' ' + str(scenario) + ' ' + str(sim)
                    if show_progress:
                        sys.stdout.write('\r' + message)
                        sys.stdout.flush()

                if CAPEX_component:
                    capex_metrics = [
                        time, scenario, sim,
                        final_CAPEX['AP SMR'],
                        final_CAPEX['AP CCS'],
                        final_CAPEX['AP BH2S'],
                        final_CAPEX['AP AEC']
                    ]

                    CAPEX_component_data.loc[len(CAPEX_component_data)] = capex_metrics
                    message = 'Calculating CAPEX_component...' + str(time) + ' ' + str(scenario) + ' ' + str(sim)
                    if show_progress:
                        sys.stdout.write('\r' + message)
                        sys.stdout.flush()

                if Sensitivity:
                    A_elec_cost = np.mean(brownian_motion(drift=Market_inputs['El_drift'][1],
                                             std_dev=Market_inputs['El_STD'],
                                             n_steps=673,
                                             seed=sim).uncorrelated_GBM(Market_inputs['El_initial_price']))

                    B_elec_cost = np.mean(brownian_motion(drift=Market_inputs['El_drift'][0],
                                             std_dev=Market_inputs['El_STD'],
                                             n_steps=673,
                                             seed=sim).uncorrelated_GBM(Market_inputs['El_initial_price']))

                    electricityCost = 0
                    if scenario == 'A':
                        electricityCost = A_elec_cost
                    else:
                        electricityCost = B_elec_cost

                    NH3_average, NG_average = brownian_motion(drift=Market_inputs['NG_drift'],
                                                          std_dev=Market_inputs['NG_std'],
                                                          correlation=0.8,
                                                          n_steps=673,
                                                          seed=sim).correlated_GBM([Market_inputs['NH3_initial_price'], Market_inputs['NG_initial_price']])

                    NH3_average, NG_average = np.mean(NH3_average), np.mean(NG_average)

                    CI_time_SMR = []
                    CI_time_CCS = []
                    CI_time_BH2S = []
                    CI_time_AEC = []

                    for months in multiples_of_12_CI:
                        scenario = scenario
                        CI_time_SMR_vals = Carbon_Intensity_of_technology('AP SMR', carbon_intensity, scenario,
                                                                          financial_inputs, engineering_inputs,
                                                                          biomass_requirement, natural_gas_requirements,
                                                                          electricity_requirements, aeo22_data,
                                                                          aeo23_data).electricity_emissions(
                            months)
                        CI_time_CCS_vals = Carbon_Intensity_of_technology('AP CCS', carbon_intensity, scenario,
                                                                          financial_inputs, engineering_inputs,
                                                                          biomass_requirement, natural_gas_requirements,
                                                                          electricity_requirements, aeo22_data,
                                                                          aeo23_data).electricity_emissions(
                            months)
                        CI_time_BH2S_vals = Carbon_Intensity_of_technology('AP BH2S', carbon_intensity, scenario,
                                                                           financial_inputs, engineering_inputs,
                                                                           biomass_requirement,
                                                                           natural_gas_requirements,
                                                                           electricity_requirements, aeo22_data,
                                                                           aeo23_data).electricity_emissions(months)
                        CI_time_AEC_vals = Carbon_Intensity_of_technology('AP AEC', carbon_intensity, scenario,
                                                                          financial_inputs, engineering_inputs,
                                                                          biomass_requirement, natural_gas_requirements,
                                                                          electricity_requirements, aeo22_data,
                                                                          aeo23_data).electricity_emissions(
                            months)
                        CI_time_SMR.append(CI_time_SMR_vals)
                        CI_time_CCS.append(CI_time_CCS_vals)
                        CI_time_BH2S.append(CI_time_BH2S_vals)
                        CI_time_AEC.append(CI_time_AEC_vals)

                    CI_sens = {
                        'AP SMR': np.mean(CI_time_SMR),
                        'AP CCS': np.mean(CI_time_CCS),
                        'AP BH2S': np.mean(CI_time_BH2S),
                        'AP AEC': np.mean(CI_time_AEC)
                    }

                    CAPEX_sensitivity = {
                        'AP SMR': final_CAPEX['AP SMR']['CAPEX'],
                        'AP CCS': final_CAPEX['AP CCS']['CAPEX'],
                        'AP BH2S': final_CAPEX['AP BH2S']['CAPEX'],
                        'AP AEC': final_CAPEX['AP AEC']['CAPEX']
                    }

                    Feedstock_price = {
                        'AP SMR': NG_average,
                        'AP CCS': NG_average,
                        'AP BH2S': biomass_price,
                        'AP AEC': 0
                    }

                    MI_OPEX_sensitivity = {
                        'AP SMR': final_MI_OPEX['AP SMR']['MI_OPEX'],
                        'AP CCS': final_MI_OPEX['AP CCS']['MI_OPEX'],
                        'AP BH2S': final_MI_OPEX['AP BH2S']['MI_OPEX'],
                        'AP AEC': final_MI_OPEX['AP AEC']['MI_OPEX']
                    }

                    Credits_48C = IRA_credits['48E'] if scenario == 'C' else 0
                    IRA_Market_value =  np.mean([IRA_credits['TCvalue'][i] for i in IRA_credits['TCvalue'].keys()])
                    PPA_pricing = Market_inputs['PPA_pricing'][time][matching][True] if scenario == 'D' else 0
                    Curtailment = Market_inputs['PPA_pricing_for_C'][time][True] if scenario == 'C' else 0
                    Battery_CAPEX = CAPEX_inputs[f'Battery Storage CAPEX {time}'] if scenario == 'C' else 0
                    Wind_CAPEX = CAPEX_inputs[f'Wind turbine CAPEX {time}'] if scenario == 'C' else 0
                    Battery_var_OPEX = MI_OPEX_inputs['Battery Var OPEX'] if scenario == 'C' else 0
                    Battery_OPEX = MI_OPEX_inputs['Battery OPEX'] if scenario == 'C' else 0
                    stackCAPEX = CAPEX_inputs[f'Stack cost {time}']

                    for tech in technologies:
                        metric_Sense = [time, scenario, sim, CAPEX_sensitivity[tech], electricityCost, MI_OPEX_sensitivity[tech], Feedstock_price[tech], NH3_average,
                                  CI_sens[tech], Battery_var_OPEX, Battery_OPEX, Battery_CAPEX, Wind_CAPEX,
                                  Credits_48C, IRA_Market_value, PPA_pricing, Curtailment,
                                  stackCAPEX if tech == 'AP AEC' else 0,
                                  engineering_inputs['Eff_electrolysis'] if tech == 'AP AEC' else 0,
                                  resolve_process_carbon_price_for_year(
                                      INPUT_PARAMETERS,
                                      int(time + 3),
                                  )]

                        sensitivity_data[tech].loc[len(sensitivity_data[tech])] = metric_Sense


                    message = 'Calculating sensitivity...' + str(time) + ' ' + str(scenario) + ' ' + str(sim)
                    if show_progress:
                        sys.stdout.write('\r' + message)
                        sys.stdout.flush()

                if quality_assurance:
                    AP_SMR_CFs = Stochastic_DCF('AP SMR', start, L, sim, True, scenario, financial_inputs,
                                                  final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                  IRA_credits, carbon_intensity, natural_gas_requirements,
                                                  final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                  electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                  battery_and_turbine_data_final, isCBAM=isCBAM, matching=matching).quality_assure()
                    AP_CCS_CFs = Stochastic_DCF('AP CCS', start, L, sim, True, scenario, financial_inputs,
                                                final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                IRA_credits, carbon_intensity, natural_gas_requirements,
                                                final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                battery_and_turbine_data_final, isCBAM=isCBAM,
                                                matching=matching).quality_assure()
                    AP_BH2S_CFs = Stochastic_DCF('AP BH2S', start, L, sim, True, scenario, financial_inputs,
                                                final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                IRA_credits, carbon_intensity, natural_gas_requirements,
                                                final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                battery_and_turbine_data_final, isCBAM=isCBAM,
                                                matching=matching).quality_assure()
                    AP_AEC_CFs = Stochastic_DCF('AP AEC', start, L, sim, True, scenario, financial_inputs,
                                                final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                IRA_credits, carbon_intensity, natural_gas_requirements,
                                                final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                battery_and_turbine_data_final, isCBAM=isCBAM,
                                                matching=matching).quality_assure()
                    dfs = {
                        'AP SMR': AP_SMR_CFs,
                        'AP CCS': AP_CCS_CFs,
                        'AP BH2S': AP_BH2S_CFs,
                        'AP AEC': AP_AEC_CFs
                    }

                    dataframes_to_excel(dfs,f'quality_assurance/quality_{matching}_{time}_{scenario}_{sim}.xlsx')

                if CAC_quality:

                    AP_CCS_CFs = Stochastic_DCF('AP CCS', start, L, sim, True, scenario, financial_inputs,
                                                final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                IRA_credits, carbon_intensity, natural_gas_requirements,
                                                final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                battery_and_turbine_data_final, isCBAM=isCBAM,
                                                matching=matching).quality_assure_CAC()

                    AP_BH2S_CFs = Stochastic_DCF('AP BH2S', start, L, sim, True, scenario, financial_inputs,
                                                 final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                 IRA_credits, carbon_intensity, natural_gas_requirements,
                                                 final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                 electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                 battery_and_turbine_data_final, isCBAM=isCBAM,
                                                 matching=matching).quality_assure_CAC()

                    AP_AEC_CFs = Stochastic_DCF('AP AEC', start, L, sim, True, scenario, financial_inputs,
                                                final_CAPEX, CAPEX_inputs, engineering_inputs, Market_inputs,
                                                IRA_credits, carbon_intensity, natural_gas_requirements,
                                                final_MI_OPEX, electricity_requirements, MI_OPEX_inputs,
                                                electrode_cost, biomass_requirement, aeo22_data, aeo23_data,
                                                battery_and_turbine_data_final, isCBAM=isCBAM,
                                                matching=matching).quality_assure_CAC()

                if location_info:
                    metric = [
                        time, scenario, sim,
                        selected_site_for_logistics['city'],
                        selected_site_for_logistics['country'],
                        site_logistics['selected_port'],
                        site_logistics['distance_km'],
                        site_logistics['to_port_cost_usd_per_tnh3'],
                        site_logistics['shipping_cost_usd_per_tnh3'],
                        site_logistics['shipping_co2_t_per_tnh3'],
                        site_logistics['shipping_summary_path'],
                    ]
                    location_info_df.loc[len(location_info_df)] = metric




    if show_progress:
        print("Current time:", get_current_est_time())

    NPV_parameter_effects_data = pd.DataFrame(NPV_parameter_effects_records)

    if CAC:
        return CAC_data
    if NPV and NPV_parameter_effects:
        return {
            'NPV': NPV_data,
            'NPV_parameter_effects': NPV_parameter_effects_data,
        }
    if NPV:
        return NPV_data
    if NPV_parameter_effects:
        return NPV_parameter_effects_data
    if Potential_TC:
        return Potential_and_AP_CE_TC
    if CI:
        return CI_data
    if NP_NPV:
        return NP_NPV_data
    if CAPEX_OPEX:
        return OPEX_and_CAPEX
    if absolute_support:
        return absolute_support_data
    if ROI:
        return ROI_data
    if El_market:
        return El_data
    if CAPEX_component:
        return CAPEX_component_data
    if Sensitivity:
        return sensitivity_data
    if location_info:
        return location_info_df


#CBAM = False # False or True
#matching = 'monthly' #options: hourly, monthly, and yearly
#mult = 1  #Multiplies the number of simulations
# run_simulation(1, quality_assurance=True, isCBAM=CBAM, matching=matching)  # checked all scenarios v5


# Simulations to do:
#Location_info = run_simulation(100*mult, location_info=True, isCBAM=CBAM, matching=matching)
#NPV_dataset = run_simulation(100*mult, NPV=True, isCBAM=CBAM, matching=matching)
#NP_NPV_dataset = run_simulation(100*mult, NP_NPV=True, isCBAM=CBAM, matching=matching)
#CAC_dataset = run_simulation(100*mult, CAC=True, isCBAM=CBAM, matching=matching)
#CI_dataset = run_simulation(100*mult, CI=True, isCBAM=CBAM, matching=matching)
#TC_dataset = run_simulation(100*mult, Potential_TC=True, isCBAM=CBAM, matching=matching)
#CAPEX_OPEX_dataset = run_simulation(100*mult, CAPEX_OPEX=True, isCBAM=CBAM, matching=matching)
#absolute_support_dataset = run_simulation(100*mult, absolute_support=True, isCBAM=CBAM, matching=matching)
#ROI_dataset = run_simulation(100*mult, ROI=True, isCBAM=CBAM, matching=matching)
#El_dataset = run_simulation(100*mult, El_market=True, isCBAM=CBAM, matching=matching)

# Creating a dictionary of dataframes
#dfs = {
#    'NPV': NPV_dataset,
#    'NP_NPV': NP_NPV_dataset,
#    'CAC': CAC_dataset,
#    'CI': CI_dataset,
#    'TC': TC_dataset,
#    'CAPEX_OPEX': CAPEX_OPEX_dataset,
#    'absolute_support': absolute_support_dataset,
#    'ROI': ROI_dataset,
#    'El': El_dataset,
#    'Locations': Location_info,
#    'CAPEX_component': CAPEX_component_dataset,
#    'sensitivity': sensitivity_dataset
#}

# Saving dataframes to Excel files
#dataframes_to_excel(dfs, f"v14_DATASET{'_CBAM' if CBAM else ''}_{matching}_highgas.xlsx")
#dataframes_to_excel(dfs, "calibrate_electricity.xlsx")
#dataframes_to_excel(df_sens, f"FINAL_sensitivities{'_CBAM' if CBAM else ''}_{matching}.xlsx")
#dataframes_to_excel(dfs, "test.xlsx")




def _merge_parallel_outputs(results):
    ordered_results = [result for _, result in sorted(results, key=lambda item: item[0])]
    if not ordered_results:
        return pd.DataFrame()

    first = ordered_results[0]
    if isinstance(first, dict):
        merged = {}
        all_keys = []
        for result in ordered_results:
            for key in result.keys():
                if key not in all_keys:
                    all_keys.append(key)
        for key in all_keys:
            frames = [result[key] for result in ordered_results if key in result]
            merged_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            if isinstance(merged_df, pd.DataFrame):
                sort_cols = [c for c in ['time', 'scenario', 'simulation'] if c in merged_df.columns]
                if sort_cols:
                    merged_df = merged_df.sort_values(sort_cols, kind='stable').reset_index(drop=True)
            merged[key] = merged_df
        return merged

    merged_df = pd.concat(ordered_results, ignore_index=True) if ordered_results else pd.DataFrame()
    if isinstance(merged_df, pd.DataFrame):
        sort_cols = [c for c in ['time', 'scenario', 'simulation'] if c in merged_df.columns]
        if sort_cols:
            merged_df = merged_df.sort_values(sort_cols, kind='stable').reset_index(drop=True)
    return merged_df


def _run_simulation_worker(kwargs):
    return run_simulation(show_progress=False, **kwargs)


def run_simulation_parallel(simulations: int, workers: int = 1, **kwargs):
    workers = max(1, int(workers or 1))
    simulations = int(simulations)

    if workers <= 1 or simulations <= 1:
        return run_simulation(simulations, **kwargs)

    workers = min(workers, simulations)

    base_chunk, remainder = divmod(simulations, workers)
    chunks = []
    offset = 0
    for idx in range(workers):
        count = base_chunk + (1 if idx < remainder else 0)
        if count <= 0:
            continue
        chunks.append((offset, count))
        offset += count

    results = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        submitted = []
        for offset, count in chunks:
            task_kwargs = dict(kwargs)
            task_kwargs['simulations'] = count
            task_kwargs['simulation_offset'] = offset
            submitted.append((offset, executor.submit(_run_simulation_worker, task_kwargs)))

        for offset, future in submitted:
            results.append((offset, future.result()))

    return _merge_parallel_outputs(results)


def build_ci_same_year_summary(ci_dataset):
    """Collapse full CI trajectories into same-year pathway CI values.

    For each row (time/scenario/simulation) in the technology-specific CI outputs,
    this extracts the CI value from the column that matches that row's `time`.
    The result aligns directly with the NPV output rows.
    """
    key_cols = ["time", "scenario", "simulation"]
    if not isinstance(ci_dataset, dict) or not ci_dataset:
        return pd.DataFrame(columns=key_cols)

    summary_frames = []
    for tech_name, df in ci_dataset.items():
        ci_col = f"{tech_name}_CI"
        if not isinstance(df, pd.DataFrame) or df.empty:
            summary_frames.append(pd.DataFrame(columns=key_cols + [ci_col]))
            continue

        rows = []
        for _, row in df.iterrows():
            time_value = row.get("time")
            try:
                year_col = str(int(float(time_value)))
            except Exception:
                year_col = str(time_value)
            ci_value = row.get(year_col, None)
            rows.append({
                "time": row.get("time"),
                "scenario": row.get("scenario"),
                "simulation": row.get("simulation"),
                ci_col: ci_value,
            })
        summary_frames.append(pd.DataFrame(rows))

    merged = None
    for frame in summary_frames:
        if merged is None:
            merged = frame
        else:
            merged = merged.merge(frame, on=key_cols, how="outer")

    if merged is None:
        merged = pd.DataFrame(columns=key_cols)

    sort_cols = [c for c in key_cols if c in merged.columns]
    if sort_cols:
        merged = merged.sort_values(sort_cols, kind="stable").reset_index(drop=True)
    return merged


def build_npv_with_ci_dataset(npv_dataset, ci_dataset):
    """Merge the standard NPV output with same-year CI values per pathway."""
    if not isinstance(npv_dataset, pd.DataFrame):
        return pd.DataFrame()
    ci_summary = build_ci_same_year_summary(ci_dataset)
    if ci_summary.empty:
        return npv_dataset.copy()
    merged = npv_dataset.merge(ci_summary, on=["time", "scenario", "simulation"], how="left")
    sort_cols = [c for c in ["time", "scenario", "simulation"] if c in merged.columns]
    if sort_cols:
        merged = merged.sort_values(sort_cols, kind="stable").reset_index(drop=True)
    return merged


def build_npv_parameter_effects_average_dataset(parameter_effects_dataset):
    """Average normalized NPV effects by year, scenario, technology, and parameter."""
    required_cols = [
        "time", "scenario", "technology", "parameter", "effect_on_NPV",
        "abs_normalized_effect", "signed_normalized_effect", "share_of_NPV",
    ]
    if not isinstance(parameter_effects_dataset, pd.DataFrame) or parameter_effects_dataset.empty:
        return pd.DataFrame(columns=required_cols + ["runs", "parameter_rank"])

    missing_cols = [col for col in required_cols if col not in parameter_effects_dataset.columns]
    if missing_cols:
        return pd.DataFrame(columns=required_cols + ["runs", "parameter_rank"])

    group_cols = ["time", "scenario", "technology", "parameter"]
    average = (
        parameter_effects_dataset
        .groupby(group_cols, dropna=False)
        .agg(
            runs=("simulation", "nunique"),
            mean_effect_on_NPV=("effect_on_NPV", "mean"),
            mean_abs_normalized_effect=("abs_normalized_effect", "mean"),
            mean_signed_normalized_effect=("signed_normalized_effect", "mean"),
            mean_share_of_NPV=("share_of_NPV", "mean"),
        )
        .reset_index()
    )
    average = average.sort_values(
        ["time", "scenario", "technology", "mean_abs_normalized_effect"],
        ascending=[True, True, True, False],
        kind="stable",
    ).reset_index(drop=True)
    average["parameter_rank"] = (
        average.groupby(["time", "scenario", "technology"])["mean_abs_normalized_effect"]
        .rank(method="first", ascending=False)
        .astype(int)
    )
    return average

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the probabilistic ammonia model.")
    parser.add_argument("--country-name", default=os.environ.get("TRADE_PIPELINE_COUNTRY_NAME", ""))
    parser.add_argument("--country-iso3", default=os.environ.get("TRADE_PIPELINE_COUNTRY_ISO3", ""))
    parser.add_argument("--output-dir", default=os.environ.get("TRADE_PIPELINE_NPV_OUTPUT_DIR", "."))
    parser.add_argument("--matching", default="monthly", choices=["hourly", "monthly", "yearly"])
    parser.add_argument("--mult", type=int, default=40, help="Number of 100-simulation blocks.")
    parser.add_argument("--cbam", action="store_true", help="Enable the legacy CBAM flag.")
    parser.add_argument(
        "--include-ci",
        dest="include_ci",
        action="store_true",
        default=True,
        help="Also calculate carbon-intensity outputs alongside NPV (default: on).",
    )
    parser.add_argument(
        "--skip-ci",
        dest="include_ci",
        action="store_false",
        help="Skip carbon-intensity outputs if you want a faster run.",
    )
    parser.add_argument(
        "--include-npv-effects",
        dest="include_npv_effects",
        action="store_true",
        default=True,
        help="Also write a run-level NPV parameter-effect attribution sheet (default: on).",
    )
    parser.add_argument(
        "--skip-npv-effects",
        dest="include_npv_effects",
        action="store_false",
        help="Skip the NPV parameter-effect attribution sheets.",
    )
    parser.add_argument("--workers", type=int, default=int(os.environ.get("TRADE_PIPELINE_PROB_WORKERS", "1")),
                        help="Number of worker processes for simulation chunks.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    os.environ["TRADE_PIPELINE_COUNTRY_NAME"] = args.country_name or ""
    os.environ["TRADE_PIPELINE_COUNTRY_ISO3"] = args.country_iso3 or ""
    os.environ["TRADE_PIPELINE_NPV_OUTPUT_DIR"] = str(output_dir)

    CBAM = bool(args.cbam)
    matching = args.matching
    mult = int(args.mult)
    workers = max(1, int(args.workers or 1))

    if workers > 1:
        for env_var in ["OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"]:
            os.environ.setdefault(env_var, "1")

    print("=" * 100)
    print(f"Running probabilistic model for {args.country_name} ({args.country_iso3})")
    print(f"Output directory: {output_dir}")
    print(
        f"matching={matching}; simulations={100 * mult}; CBAM={CBAM}; workers={workers}; "
        f"include_ci={args.include_ci}; include_npv_effects={args.include_npv_effects}"
    )
    print("=" * 100)

    Location_info = run_simulation_parallel(100 * mult, workers=workers, location_info=True, isCBAM=CBAM, matching=matching)
    NPV_outputs = run_simulation_parallel(
        100 * mult,
        workers=workers,
        NPV=True,
        NPV_parameter_effects=args.include_npv_effects,
        isCBAM=CBAM,
        matching=matching,
    )

    if isinstance(NPV_outputs, dict):
        NPV_dataset = NPV_outputs.get("NPV", pd.DataFrame())
        NPV_parameter_effects_dataset = NPV_outputs.get("NPV_parameter_effects", pd.DataFrame())
    else:
        NPV_dataset = NPV_outputs
        NPV_parameter_effects_dataset = pd.DataFrame()

    dfs = {
        "NPV": NPV_dataset,
        "Locations": Location_info,
    }

    if args.include_npv_effects:
        dfs["NPV_parameter_effects"] = NPV_parameter_effects_dataset
        dfs["NPV_effects_average"] = build_npv_parameter_effects_average_dataset(NPV_parameter_effects_dataset)

    if args.include_ci:
        CI_dataset = run_simulation_parallel(100 * mult, workers=workers, CI=True, isCBAM=CBAM, matching=matching)
        dfs["CI"] = CI_dataset
        dfs["CI_same_year"] = build_ci_same_year_summary(CI_dataset)
        dfs["NPV_with_CI"] = build_npv_with_ci_dataset(NPV_dataset, CI_dataset)

    dataframes_to_excel(dfs, str(output_dir / f"v14_DATASET{'_CBAM' if CBAM else ''}_{matching}_highgas.xlsx"))
    dataframes_to_excel(dfs, str(output_dir / "calibrate_electricity.xlsx"))
    dataframes_to_excel(dfs, str(output_dir / "FINAL_sensitivities.xlsx"))
    dataframes_to_excel(dfs, str(output_dir / "test.xlsx"))

    print(f"Probabilistic model outputs written to: {output_dir}")


if __name__ == "__main__":
    freeze_support()
    main()
