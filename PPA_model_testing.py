# Imports
from scipy.optimize import root_scalar
from global_variables import *
from _instantiate_inputs import InstantiateInputs
from file_handling_funcs import *
from _path_dependent_data_handling import *
from cleaning_optimization_data import *
from tqdm import tqdm

# Constants
INPUT_PARAMETERS_PATH = prob_INPUT_PARAMETERS_PATH
with open(INPUT_PARAMETERS_PATH, 'r') as json_file:
    INPUT_PARAMETERS = json.load(json_file)
PPA_INPUTS = InstantiateInputs(0).average_values_from_JSON_inputs(INPUT_PARAMETERS)

EQUIP_COSTS = PPA_INPUTS['basic_equipment_costs']
FIN_INPUTS = PPA_INPUTS['financial_inputs']
ENG_INPUTS = PPA_INPUTS['engineering_inputs']
ELEC_INPUTS = PPA_INPUTS['electricity_requirements']
ELEC_INPUTS['fixed_demand'] = 1007 #MW
NG_INPUTS = PPA_INPUTS['natural_gas_requirements']
BFW_INPUTS = PPA_INPUTS['BFW_requirements']
STEAM_INPUTS = PPA_INPUTS['HP_steam_requirements']
MIOPEX_INPUTS = PPA_INPUTS['MI_OPEX_inputs']
MARKET_INPUTS = PPA_INPUTS['Market_inputs']
CI_INPUTS = PPA_INPUTS['carbon_intensity']
POLICY_INPUTS = PPA_INPUTS['IRA_credits']

SCENARIO_CASES = ['New']
TECHNOLOGY_CASES = 'AP AEC high'
CF_CASES = ['high','low']

PPA_OUTPUT_FILE_NAME = 'PPA_output.xlsx'
LOCATION_DATA = CLEANED_DATA.loc[CLEANED_DATA['technology'] == 'AP AEC high']

# Functions
def redefine_capex_heuristics(capex_inputs: dict) -> dict:
    values = list(capex_inputs.values())[:12]
    keys = list(capex_inputs.keys())[:12]
    sum_values = sum(values)    # = CAPEX in percentage form
    values = [i/sum_values for i in values]
    for key, value in zip(keys, values):
        capex_inputs[key] = value
def dataframes_to_excel(dfs, file_name):
    """
    Convert multiple pandas DataFrames into one Excel file with multiple sheets.

    Parameters:
    dfs (dict): Dictionary where keys are sheet names and values are corresponding DataFrames
    file_name (str): Name of the Excel file

    Returns:
    None
    """
    try:
        with pd.ExcelWriter(file_name) as writer:
            for sheet_name, df in dfs.items():
                if isinstance(df, dict):  # check if value is another dict (specifically for 'CI')
                    for sub_sheet_name, sub_df in df.items():
                        # use a combination of main key and sub key as the sheet name
                        sub_sheet_name = f"{sheet_name}_{sub_sheet_name}"
                        sub_df.to_excel(writer, sheet_name=sub_sheet_name, index=False)
                else:
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
        print(f'Successfully written to {file_name}')
    except Exception as e:
        print(f'An error occurred: {e}')
def CAPEX_electricity(time, CAPEX_inputs, CF_type):
    # Constants
    WIND_CAPACITY = CAPEX_inputs['wind_capacity']['AP AEC'] * 1000  # kW
    BATTERY_CAPACITY = CAPEX_inputs['battery_capacity']['AP AEC'] * 1000  # kW
    SOLAR_CAPACITY = CAPEX_inputs['solar_capacity']['AP AEC'] * 1000  # kW

    # Calculations
    wind_capex = CAPEX_inputs[f'Wind turbine CAPEX {time}'] * WIND_CAPACITY # $/kW * kW = $
    battery_capex = CAPEX_inputs[f'Battery Storage CAPEX {time}'] / 4 * BATTERY_CAPACITY # $/kW * kW = $
    solar_capex = CAPEX_inputs[f'Solar PV CAPEX {time}'] * SOLAR_CAPACITY # $/kW * kW = $

    return wind_capex + battery_capex + solar_capex

def back_calculate_depreciable_capital_factor(capex_inputs, excluded_factors):
    keys = list(capex_inputs.keys())[:12]
    depreciable_capital_factor = 0
    for key in keys:
        if key not in excluded_factors:
            depreciable_capital_factor += capex_inputs[key]

    capex_inputs['CAPEX_from_installed_cost'] = depreciable_capital_factor


# Objects
class CAPEX:
    #This function needs to infer the components of the CAPEX based on the heuristic provided by the literature
    def __init__(self, inputs):
        self.inputs = inputs

    def capex_components(self, CAPEX):
        CAPEX_final = {
            'CAPEX': CAPEX
        }
        for capex_component in list(self.inputs.keys())[:12]:
            CAPEX_final[capex_component] = CAPEX*self.inputs[capex_component]

        CAPEX_final['FCI'] = CAPEX_final['CAPEX'] - CAPEX_final['Working Capital']
        CAPEX_final['WC'] = CAPEX_final['Working Capital']
        CAPEX_final['UC'] = CAPEX_final['Purchased Equipment Cost']

        return CAPEX_final

class TaxCreditCalculator:
    def __init__(self, start_month, scenario, FIN_INPUTS,
                 POLICY_INPUTS, final_CAPEX, battery_and_turbine_data_final, ELEC_INPUTS, CAPEX_inputs, location):

        self.start_month = start_month
        self.scenario = scenario
        self.POLICY_INPUTS = POLICY_INPUTS
        self.time = 2027 if self.start_month == 0 else 2040
        self.battery_and_turbine_data_final = battery_and_turbine_data_final
        self.FIN_INPUTS = FIN_INPUTS
        self.final_CAPEX = final_CAPEX

        self.ELEC_INPUTS = ELEC_INPUTS
        self.CAPEX_inputs = CAPEX_inputs
        self.LOCATION = location


        # Pre-calculate constants
        self.start_operation_month = self.start_month + 36
        self.end_operation_month = self.start_operation_month + 12 * 40

    def _subsidies_disabled_for_scenario(self):
        # The New scenario is intended to run without tax credits or subsidies.
        return self.scenario == 'New'

    # Helper method to check if a month is within the operation period
    def _is_operating(self, month):
        start_operation_month = self.start_month + 36  # Operation starts after 3 years of construction
        end_operation_month = start_operation_month + 12 * 40  # Operation lasts for 30 years
        return start_operation_month <= month < end_operation_month

    # Helper method to check if a month is within the credit lifetime
    def _is_within_lifetime(self, month, credit_lifetime_years):
        start_credit_month = self.start_month + 36  # Credit starts when operation starts
        end_credit_month = start_credit_month + 12 * credit_lifetime_years
        return start_credit_month <= month < end_credit_month

    # Method to calculate 45Y tax credit
    def calculate_45Y(self, month):
        if self._subsidies_disabled_for_scenario():
            return 0

        if self._is_operating(month) and month < self.POLICY_INPUTS['45Y expiry']:

            # Variables
            TAU = month % 12 # In the SI: this is Tau
            ELECTRICITY_GENERATED = self.ELEC_INPUTS['total_generation']['AP AEC'][TAU]
            CREDITS_45Y = self.POLICY_INPUTS['45Y']

            # Calculations
            AWARDED_CREDITS = (ELECTRICITY_GENERATED * 1000) * (CREDITS_45Y / 100)  # (MWh/month * KWh/MWh) * (cents/KWh * dollars/cents) = $/month

            return AWARDED_CREDITS

        else:
            return 0

    def calculate_48E(self, month):
        if self._subsidies_disabled_for_scenario():
            return 0

        if not self._is_operating(month) or month >= self.POLICY_INPUTS['48E expiry']:
            return 0

        # Variables
        ELECTRICITY_CAPEX = CAPEX_electricity(self.time, self.CAPEX_inputs, self.CAPEX_inputs['description'])
        CREDITS_48E = self.POLICY_INPUTS['48E']

        # Calculations
        AWARDED_CREDITS = ELECTRICITY_CAPEX * (CREDITS_48E)  # $/month

        return AWARDED_CREDITS
    
    # Method to calculate European_subsidy tax credit
    def calculate_European_subsidy(self, month):
        if self._subsidies_disabled_for_scenario():
            return 0

        if self.scenario == 'New' and (self.technology == 'AP AEC' or self.technology == 'AP BH2S'):
            if self._is_operating(month) and self._is_within_lifetime(month, self.POLICY_INPUTS['European_subsidy_lifetime']):
                hydrogen_production_kg_monthly = self.hydrogen_production_monthly * 1000  # Convert tonnes/month to kg/month
                subsidy_rate = self.POLICY_INPUTS['European_subsidy_rate']  # $/kg of hydrogen
                total_subsidy = hydrogen_production_kg_monthly * subsidy_rate  # $/month
                return total_subsidy
            else:
                return 0
        else:
            return 0


class Stochastic_DCF:
    def __init__(self, start, L, policy, scenario, FIN_INPUTS, final_CAPEX, CAPEX_inputs, POLICY_INPUTS, ELEC_INPUTS, MIOPEX_INPUTS, battery_and_turbine_data_final, location, LCOE, matching = 'hourly'):

        self.LOCATION = location
        self.policy = policy
        self.start = start
        self.scenario = scenario
        self.matching = matching
        self.LCOE = LCOE

        self.is48E = True

        self.FIN_INPUTS = FIN_INPUTS
        self.final_CAPEX = final_CAPEX
        self.CAPEX_inputs = CAPEX_inputs
        self.POLICY_INPUTS = POLICY_INPUTS

        self.ELEC_INPUTS = ELEC_INPUTS
        self.MIOPEX_INPUTS = MIOPEX_INPUTS

        self.battery_and_turbine_data_final = battery_and_turbine_data_final

        self.construction = self.FIN_INPUTS['construction_time']
        self.L = L
        self.inflation_rate = self.FIN_INPUTS['inflation']
        self.Y = self.FIN_INPUTS['Y']
        self.FCI = self.final_CAPEX['FCI']
        self.land_cost = self.final_CAPEX['Land Cost']
        self.WC = self.final_CAPEX['WC']
        self.r = self.FIN_INPUTS['cost_of_debt']
        self.e = self.FIN_INPUTS['equity']

        self.F_A = self.FIN_INPUTS['availability']
        self.H_operating = self.FIN_INPUTS['operating_hours_per_year']
        self.phi_state = self.FIN_INPUTS['state_tax']
        self.phi_federal = self.FIN_INPUTS['federal_tax']
        self.L_loan = self.FIN_INPUTS['loan_lifetime']
        self.L_equipment = self.FIN_INPUTS['equipment_lifetime_depreciation']

        self.C_equipment = self.final_CAPEX['CAPEX']/self.CAPEX_inputs['CAPEX_from_installed_cost']

        self.discount_rate = self.e * self.FIN_INPUTS['return_on_equity'] + (1 - self.e) * self.r * (
                1 - (self.phi_federal + self.phi_state))


        if self.start == 0:
            self.time = 2027
        elif self.start == 156:
            self.time = 2040

        # Market costs definitions.
        self.TCvalue = self.POLICY_INPUTS['TCvalue']
        self.income_tax = 0
        self.tax_credit_calculator = TaxCreditCalculator(self.start, self.scenario, self.FIN_INPUTS,
                 self.POLICY_INPUTS, self.final_CAPEX, self.battery_and_turbine_data_final, self.ELEC_INPUTS, self.CAPEX_inputs
                                                         , self.LOCATION)

        self.discount_factor = ((1 + self.discount_rate / 12))
        self.inflation_correction = 1  # (1 + self.inflation_rate / 12) ** (self.start)

    def calculate_FCI(self, T):
        # Variables
        FCI_T = 0
        RELATIVE_T = T - self.start

        # Calculations
        if 0 < RELATIVE_T <= 12:
            FCI_T += -self.Y[0] * self.FCI / 12
        elif 12 < RELATIVE_T <= 24:
            FCI_T += -self.Y[1] * self.FCI / 12
        elif 24 < RELATIVE_T <= 36:
            FCI_T += -self.Y[2] * self.FCI / 12
        else:
            FCI_T += 0

        return FCI_T

    def calculate_land(self, T):
        if T == 0 + self.start:
            Land_T = -self.land_cost
        elif T == self.start + self.construction + self.L:
            Land_T = self.land_cost
        else:
            Land_T = 0
        return Land_T

    def calculate_WC(self, T):
        WC_T = 0
        if T == self.construction + self.start:
            WC_T += -self.WC
        elif T == self.start + self.construction + self.L:
            WC_T += self.WC
        else:
            WC_T += 0
        return WC_T

    def calculate_PMT(self, T):
        if 0 + self.start < T <= 12 + self.start:
            PMT_T = -(self.r / 12) * (T - self.start) * (1 - self.e) * self.Y[0] * self.FCI / 12
        elif 12 + self.start < T <= 24 + self.start:
            PMT_T = -(self.r / 12) * (1 - self.e) * (
                    (T - 12 - self.start) * self.Y[1] * self.FCI / 12 + self.Y[0] * self.FCI)
        elif 24 + self.start < T <= 36 + self.start:
            PMT_T = -(self.r / 12) * (1 - self.e) * (
                    (T - 24 - self.start) * self.Y[2] * self.FCI / 12 + (self.Y[0] + self.Y[1]) * self.FCI)
        elif 36 + self.start < T <= 36 + self.L_loan + self.start:
            PMT_T = -self.FCI * (1 - self.e) * self.r / 12 / (1 - (1 + self.r / 12) ** (-12 * self.L_loan))
        else:
            PMT_T = 0
        return PMT_T

    def electricity_demand(self, T):
        # Variables
        TAU = T % 12  # In the SI: this is Tau
        ELECTRICITY_GENERATED = self.ELEC_INPUTS['total_generation']['AP AEC'][TAU]

        if self.start + 36 < T <= self.start + self.construction + self.L:
            return ELECTRICITY_GENERATED
        else:
            return 0

    def calcOPEX(self, T):
        if not (self.start + 36 < T <= self.start + self.construction + self.L):
            return 0
        # Variables
        WIND_CAPACITY = self.CAPEX_inputs['wind_capacity']['AP AEC'] * 1000  # kW
        #print(f"WIND_CAPACITY: {WIND_CAPACITY} kW")
        BATTERY_CAPACITY = self.CAPEX_inputs['battery_capacity']['AP AEC'] * 1000 / 4  # kWh
        #print(f"BATTERY_CAPACITY: {BATTERY_CAPACITY} kW")
        SOLAR_CAPACITY = self.CAPEX_inputs['solar_capacity']['AP AEC'] * 1000  # kW
        #print(f"SOLAR_CAPACITY: {SOLAR_CAPACITY} kW")
        WIND_OPEX = (self.MIOPEX_INPUTS['Wind OPEX']) * WIND_CAPACITY / 12  # ($/kW-year) * (kW) * (1 year/ 12 months) = $/month
        TAU = T % 12  # In the SI: this is Tau
        BATTERY_DISCHARGE = self.ELEC_INPUTS['discharge']['AP AEC'][TAU]  # MWh

        # Calculations
        BATTERY_FIXED_OPEX = (self.MIOPEX_INPUTS[
            'Battery OPEX']) * BATTERY_CAPACITY / 12  # ($/kW-year) * (kW) * (1 year/ 12 months) = $/month
        BATTERY_VAR_OPEX = self.MIOPEX_INPUTS['Battery Var OPEX'] * BATTERY_DISCHARGE / 4
        SOLAR_OPEX = (self.MIOPEX_INPUTS[
            'Solar OPEX']) * SOLAR_CAPACITY / 12  # ($/kW-year) * (kW) * (1 year/ 12 months) = $/month

        OPEX = WIND_OPEX + BATTERY_FIXED_OPEX + BATTERY_VAR_OPEX + SOLAR_OPEX

        return -OPEX #remember cost basis is negative

    def calcDepreciation(self, T):
        if self.start + self.construction <= T <= 36 + self.L_equipment:
            return -(1 / self.L_equipment) * self.C_equipment
        return 0

    def calculate_replacement_cost(self, time_difference, lifetime, capex_key):
        if int(time_difference) % (lifetime * 12) == 0:

            # Variables
            WIND_CAPACITY = self.CAPEX_inputs['wind_capacity']['AP AEC'] * 1000  # kW
            BATTERY_CAPACITY = self.CAPEX_inputs['battery_capacity']['AP AEC'] * 1000 / 4  # kWh
            SOLAR_CAPACITY = self.CAPEX_inputs['solar_capacity']['AP AEC'] * 1000  # kW
            CAPEX_COST = self.CAPEX_inputs[capex_key + f' {self.time}']

            # Calculations
            TOTAL_COST = 0
            if capex_key == 'Battery Storage CAPEX':
                TOTAL_COST += BATTERY_CAPACITY * CAPEX_COST
            elif capex_key == 'Wind Turbine CAPEX':
                TOTAL_COST += WIND_CAPACITY * CAPEX_COST
            elif capex_key == 'Solar PV CAPEX':
                TOTAL_COST += SOLAR_CAPACITY * CAPEX_COST

            return TOTAL_COST

        return 0

    def stack_replacement_costs(self, T):
        # Variables
        COST_COUNTER = 0
        LIFETIME_COUNTER = T - self.start - self.construction

        # Calculations
        if LIFETIME_COUNTER > 0:
            # Battery replacement
            COST_COUNTER += self.calculate_replacement_cost(LIFETIME_COUNTER,
                                                            self.CAPEX_inputs['Battery lifetime'],
                                                            'Battery Storage CAPEX')

            # wind farm replacement
            COST_COUNTER += self.calculate_replacement_cost(LIFETIME_COUNTER,
                                                            self.CAPEX_inputs['Wind farm lifetime'],
                                                            'Wind turbine CAPEX')

            # solar farm replacement
            COST_COUNTER += self.calculate_replacement_cost(LIFETIME_COUNTER,
                                                            self.CAPEX_inputs['Wind farm lifetime'], #Assuming same lifetime as wind farm
                                                            'Solar PV CAPEX')

        return -COST_COUNTER

    def calculate_Tax(self, T, LCOE):
        self.income_tax = 0
        if self.start + self.construction < T < self.start + self.construction + self.L:
            Net_revenue_T = (
                    self.calcDepreciation(T)
                    + self.calcOPEX(T)
                    + self.electricity_demand(T)*LCOE
                    + self.calculate_PMT(T)
                    + self.stack_replacement_costs(T)
            )
            if Net_revenue_T > 0:
                self.income_tax = -Net_revenue_T * (self.phi_state + self.phi_federal)
            else:
                self.income_tax = 0

        return self.income_tax

    def __cashTransferability(self, T, credit_45Y=False):
        # Variables
        CONSTRUCTION_TIME = self.start + self.construction
        T_RELATIVE = T - CONSTRUCTION_TIME

        # Calculations
        if T_RELATIVE <= 0:
            return 0
        if T_RELATIVE <= 5 * 12:
            return self.TCvalue['Year 6'] if credit_45Y else self.TCvalue['Year 1-5']
        if T_RELATIVE <= 6 * 12:
            return self.TCvalue['Year 6']
        if T_RELATIVE <= 7 * 12:
            return self.TCvalue['Year 7']
        if T_RELATIVE <= 8 * 12:
            return self.TCvalue['Year 8']
        if T_RELATIVE <= 9 * 12:
            return self.TCvalue['Year 9']
        return self.TCvalue['Year 10 and after']

    def __cashEqConvert(self, TAX, TC, T, is45Y = False, set_value=False, value=None): #Let the income tax be positive
        TC_VALUE = value if set_value else self.__cashTransferability(T, credit_45Y=is45Y)

        if TC - TAX < 0:
            TAX = TAX - TC
            return TC, TAX
        elif TC - TAX >= 0:
            cash_equivalent_TC = TC + (TC-TAX)*TC_VALUE
            TAX = 0
            return cash_equivalent_TC, TAX

    def _choose_policy(self, LCOE, compare45Y_48E = False, set_value = False, value = None):
        counter_1 = 0
        counter_2 = 0

        # Iterates through the timepoints
        for T in range(self.start+self.construction+self.L):

            if compare45Y_48E: # compare45Y_48E is boolean. If true, performs the comparison of 45V and 45Q
                income_tax = abs(self.calculate_Tax(T, LCOE)) #Need positive IT for comparison with tax credits

                if T == self.start+self.construction+1: #Just calculates the one ITC timepoint
                    counter_1 += self.tax_credit_calculator.calculate_48E(T) / self.discount_factor ** (T - self.start)

                # Calculate cash-equivalent 45Y
                cash_equivalent_45Y, _ = self.__cashEqConvert(TAX=income_tax,
                                                                                TC=self.tax_credit_calculator.calculate_45Y(T),
                                                                                T=T,
                                                                                set_value=set_value,
                                                                                value=value)
                # Add CE PTCs to comparator 2
                counter_2 += cash_equivalent_45Y / self.discount_factor ** (T - self.start)

        if counter_1 > counter_2:
            return True
        else:
            return False
    
    def cash_equivalent_credits(self, T, LCOE):
        if not self.policy or self.scenario == 'New':
            return 0
    
        # Check which program is better. 45Y, 48E, or European subsidy
        if T == 0:
            self.is48E = self._choose_policy(LCOE, compare45Y_48E=True)
    
        if self.is48E:
            total_credits_48E = self.tax_credit_calculator.calculate_48E(T) if T == self.start + self.construction + 1 else 0
            total_credits_45Y = 0
            european_subsidy = self.tax_credit_calculator.calculate_European_subsidy(T)
        else:
            total_credits_48E = 0
            total_credits_45Y = self.tax_credit_calculator.calculate_45Y(T)
            european_subsidy = self.tax_credit_calculator.calculate_European_subsidy(T) if self.scenario == 'New' else 0
    
        income_tax = abs(self.calculate_Tax(T, LCOE))  # positive income tax
        cash_equivalent = 0  # initialize cash equivalent variable
    
        # Use 48E credits
        total_credits_48E, income_tax = self.__cashEqConvert(TAX=income_tax, TC=total_credits_48E, T=T)
    
        # Use 45Y credits
        total_credits_45Y, income_tax = self.__cashEqConvert(TAX=income_tax, TC=total_credits_45Y, is45Y=True, T=T)
    
        # Add European subsidy
        european_subsidy, income_tax = self.__cashEqConvert(TAX=income_tax, TC=european_subsidy, T=T)
    
        cash_equivalent += total_credits_45Y + total_credits_48E + european_subsidy
    
        return cash_equivalent

    def calculate_CF(self, T, LCOE):
        # Variables
        COSTS = [self.calculate_FCI(T), self.calculate_land(T), self.calculate_WC(T), self.calculate_PMT(T),
                 self.electricity_demand(T)*LCOE, self.calcOPEX(T), self.calculate_Tax(T, LCOE),
                 self.cash_equivalent_credits(T, LCOE), self.stack_replacement_costs(T)]

        # Calculations
        CF_T = np.sum(COSTS)

        return CF_T

    def calculate_NPV(self, LCOE):
        # Variables
        NPV = 0

        # Calculations
        for T in range(self.start + self.construction + self.L + 1):
            CF_T = self.calculate_CF(T, LCOE)
            NPV += CF_T / self.discount_factor ** (T - self.start)

        return NPV

    def quality_assure(self, LCOE):

        data = pd.DataFrame(columns=['time','FCI','Land','WC','PMT','Sales','OPEX','Tax','Credits','Stack_replacement','Cash Flow'])
        for T in range(self.start + self.construction + self.L + 1):
            terms = [
                T,
                self.calculate_FCI(T),
                self.calculate_land(T),
                self.calculate_WC(T),
                self.calculate_PMT(T),
                self.electricity_demand(T),
                self.calcOPEX(T),
                self.calculate_Tax(T, LCOE),
                self.cash_equivalent_credits(T, LCOE),
                self.stack_replacement_costs(T),
                self.calculate_CF(T, LCOE)
            ]
            data.loc[len(data)] = terms

        return data


def run_simulation():
    #Define counters
    LCOE_i = 0
    n=0
    LCOE_data = pd.DataFrame(columns=['time', 'matching', 'cost_type','city','country', 'policy', 'LCOE', 'CF sum'])
    TOTAL_ITERATIONS = len(policies) * len(LOCATION_DATA.index)


    #Begin calculating SCENARIO_CASES
    with tqdm(total=TOTAL_ITERATIONS) as pbar:
        for policy in policies:
           for index,location in LOCATION_DATA.iterrows():
                #Define inputs
                matching = location['matching']
                time = location['year']


                start = 0 if time == 2027 else 156
                CAPEX_inputs['description'] = location['capex_type']

                CAPEX_inputs['wind_capacity'] = {
                    'AP AEC': location['wind_capacity']
                }
                CAPEX_inputs['battery_capacity'] = {
                    'AP AEC': location['battery_capacity']
                }
                CAPEX_inputs['solar_capacity'] = {
                    'AP AEC': location['solar capacity']
                }
                exclude_from_depreciation = ["Engineering and Supervision Cost", "Legal Expenses Cost", "Construction Expense and Contractor\'s Fee Cost",

                                             "Working Capital", "Contingency Cost", "Land Cost"]
                back_calculate_depreciable_capital_factor(CAPEX_inputs, exclude_from_depreciation)

                FIN_INPUTS['operating_hours_per_year'] = 365 * 24 * FIN_INPUTS['availability']

                ELEC_INPUTS['curtailment'] = { #MWh/month
                        'AP AEC': location['curtailment']
                    }
                ELEC_INPUTS['total_generation']= {
                        'AP AEC': location['total_gen']
                    }
                ELEC_INPUTS['discharge']= {  # MWh/month
                        'AP AEC': location['demand']
                    }


                battery_and_turbine_data_final = {}

                final_CAPEX = CAPEX(CAPEX_inputs).capex_components(CAPEX_electricity(time, CAPEX_inputs, location['capex_type']))

                NPV_object = Stochastic_DCF(start, L, policy, 'New' if location['technology'] in ['AP AEC', 'AP BH2S'] else 'D', 
                            FIN_INPUTS, final_CAPEX, CAPEX_inputs, POLICY_INPUTS, ELEC_INPUTS, MIOPEX_INPUTS, 
                            battery_and_turbine_data_final, location['capex_type'], LCOE_i, matching=matching)
                def NPV(LCOE):
                    return NPV_object.calculate_NPV(LCOE)
                result = root_scalar(NPV, bracket=[0,2000])

                metric = [time, matching, location['capex_type'], policy, result.root,
                          get_closest_quantile_data(time, matching, 'AP AEC high')[0]['city'],
                          get_closest_quantile_data(time, matching, 'AP AEC high')[1]['city'],]

                metric = [time, matching, location['capex_type'], location['city'], location['country'], policy, result.root, location['CF sum']]

                LCOE_data.loc[len(LCOE_data)] = metric

                pbar.update(1)
                logging.info(f"Added row for {location['city']}, {location['country']}")

    return LCOE_data

CAPEX_inputs = PPA_INPUTS['CAPEX_inputs']
CAPEX_inputs['Land Cost'] = 0.04
redefine_capex_heuristics(CAPEX_inputs)



if __name__ == "__main__":
    LCOE_dataset = run_simulation()
    LCOE_dataset.to_excel(ROOT_DIR + PPA_OUTPUT_FILE_NAME, index=False)

    print(LCOE_dataset)
