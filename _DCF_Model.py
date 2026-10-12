import os
import json

from _TAX_CREDITS import *
from _GBM import *
from file_handling_funcs import *

from _path_dependent_data_handling import prob_INPUT_PARAMETERS_PATH

from global_variables import *


class Stochastic_DCF:
    def __init__(self, technology, start, L, sim, policy, scenario, financial_inputs, final_CAPEX, CAPEX_inputs,
                 engineering_inputs,
                 Market_inputs, IRA_credits, carbon_intensity, natural_gas_requirements,
                 final_MI_OPEX, electricity_requirements, MI_OPEX_inputs, electrode_cost,
                 biomass_requirement, aeo22_data, aeo23_data, battery_and_turbine_data_final, isCBAM=False, matching='hourly', deterministic=False):
        
            # Load JSON configuration once in __init__ and store it in self.config
        with open(prob_INPUT_PARAMETERS_PATH, "r") as f:
              self.config = json.load(f)  # Cached data to avoid reloading each time
        
        self.technology = technology
        self.policy = policy
        self.start = start
        self.scenario = scenario
        self.matching = matching

        self.is45V = None
        self.is48E = None
        self.financial_inputs = financial_inputs
        self.final_CAPEX = final_CAPEX
        self.CAPEX_inputs = CAPEX_inputs
        self.engineering_inputs = engineering_inputs
        self.Market_inputs = Market_inputs
        self.IRA_credits = IRA_credits
        self.carbon_intensity = carbon_intensity
        self._apply_country_natural_gas_emissions_if_needed()

        self.final_MI_OPEX = final_MI_OPEX
        self.ELEC_INPUTS = electricity_requirements
        self.MIOPEX_INPUTS = MI_OPEX_inputs
        self.electrode_cost = electrode_cost
        self.natural_gas_requirements = natural_gas_requirements

        self.biomass_requirement = biomass_requirement
        self.aeo22_data = aeo22_data
        self.aeo23_data = aeo23_data
        self.battery_and_turbine_data_final = battery_and_turbine_data_final

        self.construction = self.financial_inputs['construction_time']
        self.L = L
        self.inflation_rate = self.financial_inputs['inflation']
        self.Y = self.financial_inputs['Y']
        self.FCI = self.final_CAPEX[self.technology]['FCI']
        self.land_cost = self.CAPEX_inputs['Land Cost']
        self.WC = self.final_CAPEX[self.technology]['WC']
        self.r = self.financial_inputs['cost_of_debt']
        self.e = self.financial_inputs['equity']
        self.M_NH3 = self.engineering_inputs['NH3']  # TPD
        self.F_A = self.financial_inputs['availability']
        self.H_operating = self.financial_inputs['operating_hours_per_year']
        self.phi_state = self.financial_inputs['state_tax']
        self.phi_federal = self.financial_inputs['federal_tax']
        self.L_loan = self.financial_inputs['loan_lifetime']
        self.L_equipment = self.financial_inputs['equipment_lifetime_depreciation']
        self.C_equipment = self.final_CAPEX[self.technology]['UC']
        self.discount_rate = self.e * self.financial_inputs['return_on_equity'] + (1 - self.e) * self.r * (
                1 - (self.phi_federal + self.phi_state))

        self.sim = sim

        if self.start == 0:
            self.time = 2027
        elif self.start == 156:
            self.time = 2040
            self.market_FCI = brownian_motion(drift=self.Market_inputs['SPY_drift'],
                                              std_dev=self.Market_inputs['SPY_std'],
                                              n_steps=self.L + self.start + self.construction + 1,
                                              seed=self.sim).uncorrelated_GBM(
                self.final_CAPEX[self.technology]['FCI'] * self.e)

        # Market costs definitions.
        if not deterministic:
            self.NH3_market, self.NG_market = brownian_motion(drift=self.Market_inputs['NG_drift'],
                                                              std_dev=self.Market_inputs['NG_std'],
                                                              correlation=0.8,
                                                              n_steps=self.L + self.start + self.construction + 1,
                                                              seed=self.sim).correlated_GBM(
                [self.Market_inputs['NH3_initial_price'], self.Market_inputs['NG_initial_price']])

        else:
            self.NH3_market = brownian_motion(drift=self.Market_inputs['NG_drift'],
                                              std_dev=self.Market_inputs['NG_std'],
                                              correlation=0.8,
                                              n_steps=self.L + self.start + self.construction + 1,
                                              seed=0).uncorrelated_GBM(self.Market_inputs['NH3_initial_price'])

            self.NG_market = brownian_motion(drift=self.Market_inputs['NG_drift'],
                                             std_dev=self.Market_inputs['NG_std'],
                                             correlation=0.8,
                                             n_steps=self.L + self.start + self.construction + 1,
                                             seed=0).uncorrelated_GBM(self.Market_inputs['NG_initial_price'])

        if self.scenario == 'A':
            self.El_market = brownian_motion(drift=self.Market_inputs['El_drift'][1],
                                             std_dev=self.Market_inputs['El_STD'],
                                             n_steps=self.L + self.start + self.construction + 1,
                                             seed=self.sim if not deterministic else 0).uncorrelated_GBM(self.Market_inputs['El_initial_price'])

        if self.scenario == 'B':
            self.El_market = brownian_motion(drift=self.Market_inputs['El_drift'][0],
                                             std_dev=self.Market_inputs['El_STD'],
                                             n_steps=self.L + self.start + self.construction + 1,
                                             seed=self.sim if not deterministic else 0).uncorrelated_GBM(self.Market_inputs['El_initial_price'])

        if self.scenario == 'C':
            self.El_PPA_market = 0
            self.El_market = brownian_motion(drift=self.Market_inputs['El_drift'][0],
                                             std_dev=self.Market_inputs['El_STD'],
                                             n_steps=self.L + self.start + self.construction + 1,
                                             seed=self.sim if not deterministic else 0).uncorrelated_GBM(self.Market_inputs['El_initial_price'])

        if self.scenario == 'D':
            self.El_PPA_market = self.Market_inputs['PPA_pricing'][self.time][self.matching][self.policy]

        if self.scenario == 'New':
            # Define the behavior for the 'New' scenario
            # For example, we use the same market inputs as scenario C.
            self.El_market = brownian_motion(drift=self.Market_inputs['El_drift'][0],  # Adjust drift for 'New' scenario if needed
                                             std_dev=self.Market_inputs['El_STD'],  # Adjust std_dev if necessary
                                             n_steps=self.L + self.start + self.construction + 1,
                                             seed=self.sim if not deterministic else 0).uncorrelated_GBM(self.Market_inputs['El_initial_price'])

        self.TCvalue = self.IRA_credits['TCvalue']
        self.income_tax = 0
        self.TaxCreditCalculator = TaxCreditCalculator(self.technology, self.start, self.scenario,
                                                       self.engineering_inputs, self.financial_inputs,
                                                       self.carbon_intensity, self.IRA_credits, self.final_CAPEX,
                                                       self.electrode_cost, self.battery_and_turbine_data_final,
                                                       self.biomass_requirement, self.natural_gas_requirements,
                                                       self.ELEC_INPUTS,
                                                       self.aeo22_data, self.aeo23_data, self.CAPEX_inputs)
        self.Emissions_of_technology = Carbon_Intensity_of_technology(self.technology, self.carbon_intensity,
                                                                      self.scenario, self.financial_inputs,
                                                                      self.engineering_inputs, self.biomass_requirement,
                                                                      self.natural_gas_requirements,
                                                                      self.ELEC_INPUTS, self.aeo22_data,
                                                                      self.aeo23_data)

        self.tax_credit_calculator = TaxCreditCalculator(self.technology, self.start, self.scenario, self.engineering_inputs,
                                                         self.financial_inputs, self.carbon_intensity, self.IRA_credits,
                                                         self.final_CAPEX, self.electrode_cost,
                                                         self.battery_and_turbine_data_final, self.biomass_requirement,
                                                         self.natural_gas_requirements, self.ELEC_INPUTS,
                                                         self.aeo22_data, self.aeo23_data, self.CAPEX_inputs)

        self.baseline_emissions = Carbon_Intensity_of_technology('AP SMR', self.carbon_intensity,
                                                                 self.scenario, self.financial_inputs,
                                                                 self.engineering_inputs, self.biomass_requirement,
                                                                 self.natural_gas_requirements,
                                                                 self.ELEC_INPUTS, self.aeo22_data,
                                                                 self.aeo23_data)

        self.remaining_48C = 0
        self.remaining_48C_v1 = 0
        self.discount_factor = ((1 + self.discount_rate / 12))
        self.discount_factor_CAC = ((1 + self.financial_inputs['CAC discount'] / 12))
        self.inflation_correction = 1  # (1 + self.inflation_rate / 12) ** (self.start)
        self.isCBAM = isCBAM

    def calculate_FCI(self, T):
        opportunity_cost_adjustment = 0 if self.start == 0 else (self.market_FCI[self.start] - self.FCI * self.e)
        FCI_T = 0

        if self.start == 156 and T == self.start:
            tax_rate = (1 - self.phi_federal - self.phi_state) if opportunity_cost_adjustment > 0 else 1
            FCI_T += opportunity_cost_adjustment * (tax_rate)

        T_adjusted = T - self.start
        if 0 < T_adjusted <= 12:
            FCI_T += -self.Y[0] * self.FCI / 12
        elif 12 < T_adjusted <= 24:
            FCI_T += -self.Y[1] * self.FCI / 12
        elif 24 < T_adjusted <= 36:
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
        if T == self.construction + self.start:
            WC_T = -self.WC
        elif T == self.start + self.construction + self.L:
            WC_T = self.WC
        else:
            WC_T = 0
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

    def calculate_Sales(self, T):
        if self.start + 36 < T <= self.start + self.construction + self.L:
            Sales_T = self.M_NH3 * (self.F_A * self.H_operating / 24 / 12) * self.NH3_market[T]
        else:
            Sales_T = 0
        return Sales_T

    def _TS_COST(self, T):
        TS_COST = 0
        if self.technology == 'AP CCS':
            baseline_emissions = Carbon_Intensity_of_technology('AP SMR', self.carbon_intensity, self.scenario,
                                                                self.financial_inputs, self.engineering_inputs,
                                                                self.biomass_requirement,
                                                                self.natural_gas_requirements,
                                                                self.ELEC_INPUTS, self.aeo22_data,
                                                                self.aeo23_data).total_emissions(T)

            TS_COST = abs(baseline_emissions - self.Emissions_of_technology.total_emissions(T)) * \
                      self.engineering_inputs['H2'] * 365 / 12 * self.financial_inputs['availability'] * \
                      self.MIOPEX_INPUTS['CCS T&S Cost']

        return TS_COST

    def _CBAM_COST(self, T):
        # CBAM CO2 Cost --
        CO2_TAX = 0

        if self.isCBAM:
            EU_CI_baseline = self.carbon_intensity['EU 2027 emissions base'] * (1 - self.carbon_intensity['EU_emissions_reduction']) ** (T / 12)

            CO2_TAX = (EU_CI_baseline - self.Emissions_of_technology.total_emissions(T)) * \
                      self.engineering_inputs['H2'] * 365 / 12 * self.financial_inputs['availability'] * \
                      self.IRA_credits['EU_CO2_price']

            # print(self.scenario, self.technology, T, EU_CI_baseline,self.Emissions_of_technology.total_emissions(T), CO2_TAX)

        return CO2_TAX

    def get_carbon_cost_for_year(self, year):
        """Return the process carbon price used by the DCF model for a given year.

        Priority order:
        1) carbon_policy.process_carbon_prices_usd_per_tco2
        2) 50/50 EU-12 + origin-country split from carbon_policy
        3) legacy top-level carbon_prices
        4) 0
        """
        policy = self._carbon_policy()
        process_series = policy.get('process_carbon_prices_usd_per_tco2', {})
        if isinstance(process_series, dict) and process_series:
            return float(self._interpolate_carbon_price(process_series, year))

        if isinstance(policy, dict) and policy:
            return float(
                self._split_carbon_price(
                    year,
                    'enable_process_eu_50pct',
                    'enable_process_country_50pct',
                )
            )

        return float(self._interpolate_carbon_price(self.config.get('carbon_prices', {}), year))

    def _monthly_nh3_output_t(self):
        return self.engineering_inputs['NH3'] * 365 / 12 * self.financial_inputs['availability']

    def _interpolate_carbon_price(self, series, year):
        if not isinstance(series, dict) or not series:
            return 0.0
        points = []
        for key, value in series.items():
            try:
                points.append((int(float(key)), float(value)))
            except Exception:
                continue
        if not points:
            return 0.0
        points = sorted(points)
        years = [p[0] for p in points]
        values = [p[1] for p in points]
        if year <= years[0]:
            return values[0]
        if year >= years[-1]:
            return values[-1]
        for idx in range(len(years) - 1):
            y0, y1 = years[idx], years[idx + 1]
            if y0 <= year <= y1:
                v0, v1 = values[idx], values[idx + 1]
                return v0 + (v1 - v0) * ((year - y0) / (y1 - y0))
        return values[-1]

    def _carbon_policy(self):
        return self.config.get('carbon_policy', {})

    def _apply_country_natural_gas_emissions_if_needed(self):
        policy = self._carbon_policy()
        use_country_value = bool(
            self.carbon_intensity.get('use_country_ng_emissions', False)
            or policy.get('use_country_ng_emissions', False)
        )
        if not use_country_value:
            return
        raw_value = policy.get(
            'ng_emissions_kgco2e_per_kwh',
            self.carbon_intensity.get('natural gas', 0.0),
        )
        try:
            resolved = float(raw_value)
        except Exception:
            return
        if resolved > 0:
            self.carbon_intensity['natural gas'] = resolved
            self.carbon_intensity['use_country_ng_emissions'] = True

    def _model_year_from_T(self, T):
        return int(self.time + max(0, T - self.start) // 12)

    def _split_carbon_price(self, year, enable_eu_flag, enable_country_flag):
        policy = self._carbon_policy()
        price = 0.0
        if bool(policy.get(enable_eu_flag, True)):
            price += 0.5 * self._interpolate_carbon_price(
                policy.get('eu12_carbon_prices_usd_per_tco2', {}), year
            )
        if bool(policy.get(enable_country_flag, True)):
            price += 0.5 * self._interpolate_carbon_price(
                policy.get('country_carbon_prices_usd_per_tco2', {}), year
            )
        return float(price)

    def _transport_to_port_cost_monthly(self):
        if not bool(self.Market_inputs.get('enable_to_port_cost', True)):
            return 0.0
        cost_per_t = float(self.Market_inputs.get('to_port_cost_usd_per_tnh3', 0.0) or 0.0)
        return self._monthly_nh3_output_t() * cost_per_t

    def _shipping_cost_monthly(self):
        if not bool(self.Market_inputs.get('enable_shipping_cost', True)):
            return 0.0
        cost_per_t = float(self.Market_inputs.get('shipping_cost_usd_per_tnh3', 0.0) or 0.0)
        return self._monthly_nh3_output_t() * cost_per_t

    def _shipping_carbon_cost_monthly(self, T):
        if not bool(self.Market_inputs.get('enable_shipping_carbon_cost', True)):
            return 0.0
        co2_t_per_tnh3 = float(self.Market_inputs.get('shipping_co2_t_per_tnh3', 0.0) or 0.0)
        if co2_t_per_tnh3 <= 0:
            return 0.0
        year = self._model_year_from_T(T)
        carbon_price = self._split_carbon_price(
            year,
            'enable_shipping_eu_50pct',
            'enable_shipping_country_50pct'
        )
        return self._monthly_nh3_output_t() * co2_t_per_tnh3 * carbon_price

    def _embedded_ng_carbon_cost_monthly(self, T):
        policy = self._carbon_policy()
        ng_req_mmbtu_per_year = float(self.natural_gas_requirements.get(self.technology, 0.0) or 0.0)
        if ng_req_mmbtu_per_year <= 0:
            return 0.0

        ng_emissions_kg_per_kwh = float(
            policy.get(
                'ng_emissions_kgco2e_per_kwh',
                self.carbon_intensity.get('natural gas', 0.0)
            ) or 0.0
        )
        if ng_emissions_kg_per_kwh <= 0:
            return 0.0

        year = self._model_year_from_T(T)
        carbon_price = self._split_carbon_price(
            year,
            'enable_embedded_ng_eu_50pct',
            'enable_embedded_ng_country_50pct'
        )
        ng_kwh_monthly = (ng_req_mmbtu_per_year / 12.0) * 293.071
        embedded_co2_t_monthly = ng_kwh_monthly * ng_emissions_kg_per_kwh / 1000.0
        return embedded_co2_t_monthly * carbon_price


    def calculate_OPEX(self, T):
        if not (self.start + 36 < T <= self.start + self.construction + self.L):
            return 0

        # Need to set a sign convention for this function

        TS_COST = self._TS_COST(T)
        CO2_TAX = self._CBAM_COST(T)
        CONSTANT_OPEX = self.final_MI_OPEX[self.technology]['MI_OPEX'] / 12

        MI_OPEX_COUNTER = -(CONSTANT_OPEX + TS_COST) + CO2_TAX

        if T == self.start + self.construction + 1:
            MI_OPEX_COUNTER += -self.final_MI_OPEX[self.technology]['MI_OPEX_start']

        NG_cost = (self.natural_gas_requirements[self.technology] * self.NG_market[T]) / 12
        elec_cost = 0

        if self.scenario == 'A' or self.scenario == 'B':
#        if self.scenario in ['A','B','New']:
            elec_cost = (self.ELEC_INPUTS[self.technology][1] * 1000 * self.H_operating / 12) * self.El_market[T]

        elif self.scenario == 'D':
            elec_cost = (self.ELEC_INPUTS[self.technology][1] * self.El_PPA_market * 1000 * self.H_operating / 12)

        elif self.scenario in ['C', 'New'] and self.technology != 'AP SMR':
#        elif self.scenario in ['C'] and self.technology != 'AP SMR':
            # Variables
            WIND_CAPACITY = self.CAPEX_inputs['wind_capacity'][self.technology] * 1000  # kW
            BATTERY_CAPACITY = self.CAPEX_inputs['battery_capacity'][self.technology] * 1000 / 4  # kWh
            SOLAR_CAPACITY = self.CAPEX_inputs['solar_capacity'][self.technology] * 1000  # kW
            WIND_OPEX = (self.MIOPEX_INPUTS['Wind OPEX']) * WIND_CAPACITY / 12  # ($/kW-year) * (kW) * (1 year/ 12 months) = $/month
            TAU = T % 12  # In the SI: this is Tau
            BATTERY_DISCHARGE = self.ELEC_INPUTS['discharge'][self.technology][TAU]  # MWh

            # Calculations
            BATTERY_FIXED_OPEX = (self.MIOPEX_INPUTS['Battery OPEX']) * BATTERY_CAPACITY / 12  # ($/kW-year) * (kW) * (1 year/ 12 months) = $/month
            BATTERY_VAR_OPEX = self.MIOPEX_INPUTS['Battery Var OPEX'] * BATTERY_DISCHARGE / 4
            SOLAR_OPEX = (self.MIOPEX_INPUTS['Solar OPEX']) * SOLAR_CAPACITY / 12  # ($/kW-year) * (kW) * (1 year/ 12 months) = $/month

            OPEX = WIND_OPEX + BATTERY_FIXED_OPEX + BATTERY_VAR_OPEX + SOLAR_OPEX

            ELECTRICITY_PPA_PRICE = self.Market_inputs['PPA_pricing_for_C'][self.time][self.policy]
            ELECTRICITY_SOLD = self.ELEC_INPUTS['curtailment'][self.technology][TAU] * max(self.El_market[T], ELECTRICITY_PPA_PRICE)

            # Electricity costs
            elec_cost = -ELECTRICITY_SOLD + OPEX

       # Adding carbon cost for the "New" scenario with SMR,CCS,BH2S technology

#        if self.technology in ['AP SMR', 'AP CCS', 'AP BH2S']:
            #print(f"Technology: {self.technology}")
            #print(f"Start Year: {self.start}")
            #print(f"Current Time Step (T): {T}")
            # Calculate the year based on start year and time step T
#            year = 2030 + (T - self.start) // 12
            #print(f"Calculated Year: {year}")
#            carbon_cost_per_ton = self.get_carbon_cost_for_year(year)
            #print(f"Carbon Cost per Ton for Year {year}: {carbon_cost_per_ton}")
#            emissions_per_technology = self.Emissions_of_technology.total_emissions(T)
            #print(f"Emissions per Technology for Time Step {T}: {emissions_per_technology}")
#            h2_production = self.engineering_inputs['H2'] * 365 / 12 * self.financial_inputs['availability']
            #print(f"H2 Production (Monthly): {h2_production}")
#            carbon_cost = emissions_per_technology * h2_production * carbon_cost_per_ton  # Monthly cost
            #print(f"Calculated Carbon Cost (Monthly): {carbon_cost}")
#            MI_OPEX_COUNTER -= carbon_cost  # Adding the carbon cost to MI_OPEX_COUNTER

        if self.technology in ['AP SMR', 'AP CCS', 'AP BH2S']:
#            print(f"Technology: {self.technology}")
            #print(f"Start Year: {self.start}")
            #print(f"Current Time Step (T): {T}")
            # Use the operating-year mapping already used by the shipping and
            # embedded-NG carbon adders so all carbon-cost paths stay aligned.
            year = self._model_year_from_T(T)
            #print(f"Calculated Year: {year}")
            carbon_cost_per_ton = self.get_carbon_cost_for_year(year)
            #print(f"Carbon Cost per Ton for Year {year}: {carbon_cost_per_ton}")
            
            # Calculate emissions intensities separately with debug statements
            stack_emissions = self.Emissions_of_technology.stack_emissions()  # Emissions from the process stack
#            print(f"[Debug] T={T}, Stack Emissions Intensity (kg CO2/kg H2): {stack_emissions}")
        
            natural_gas_emissions = self.Emissions_of_technology.natural_gas_emissions()  # Emissions from natural gas usage
            #print(f"[Debug] T={T}, Natural Gas Emissions Intensity (kg CO2/kg H2): {natural_gas_emissions}")
        
            biomass_emissions = self.Emissions_of_technology.biomass_emissions()  # Emissions from biomass usage
            #print(f"[Debug] T={T}, Biomass Emissions Intensity (kg CO2/kg H2): {biomass_emissions}")
        
            electricity_emissions = self.Emissions_of_technology.electricity_emissions(T)  # Optional: electricity emissions
            #print(f"[Debug] T={T}, Electricity Emissions Intensity (kg CO2/kg H2): {electricity_emissions}")
        
            # Sum up all emissions intensities
            total_emissions_intensity = stack_emissions + natural_gas_emissions + biomass_emissions + electricity_emissions
#            print(f"[Debug] T={T}, Total Emissions Intensity (kg CO2/kg H2): {total_emissions_intensity}")
        
            # Calculate hydrogen production (monthly)
            h2_production = self.engineering_inputs['H2'] * 365 / 12 * self.financial_inputs['availability']
            #print(f"[Debug] T={T}, Hydrogen Production (Monthly, kg): {h2_production}")
        
            # Calculate the monthly carbon cost
            carbon_cost = total_emissions_intensity * h2_production * carbon_cost_per_ton  # Monthly cost
            #print(f"[Debug] T={T}, Carbon Cost (Monthly, $): {carbon_cost}")
        
            # Update the MI_OPEX_COUNTER with the carbon cost
            MI_OPEX_COUNTER -= carbon_cost

          
        MD_OPEX = MI_OPEX_COUNTER - (NG_cost + elec_cost)

        # Country-specific trade logistics and carbon adders.
        # These are positive costs, so they reduce the model cash flow.
        MD_OPEX -= self._transport_to_port_cost_monthly()
        MD_OPEX -= self._shipping_cost_monthly()
        MD_OPEX -= self._shipping_carbon_cost_monthly(T)
        MD_OPEX -= self._embedded_ng_carbon_cost_monthly(T)

        distribution_and_marketing_costs = self.MIOPEX_INPUTS['distribution_and_marketing']
        MD_OPEX += MD_OPEX * (distribution_and_marketing_costs + distribution_and_marketing_costs * self.MIOPEX_INPUTS[
            'R&D costs'])

        return MD_OPEX

    def calculate_Depreciation(self, T):
        if self.start + self.construction <= T <= 36 + self.L_equipment:
            return -(1 / self.L_equipment) * self.C_equipment
        return 0

    def calculate_replacement_cost(self, time_difference, lifetime, capex_key):
        if time_difference % (lifetime * 12) == 0:
            # Variables
            WIND_CAPACITY = self.CAPEX_inputs['wind_capacity'][self.technology] * 1000  # kW
            BATTERY_CAPACITY = self.CAPEX_inputs['battery_capacity'][self.technology] * 1000 / 4  # kWh
            SOLAR_CAPACITY = self.CAPEX_inputs['solar_capacity'][self.technology] * 1000  # kW
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

        else:
            return 0

    def stack_replacement_costs(self, T):
        if self.scenario != 'C':
            # Variables
            COST_COUNTER = 0
            LIFETIME_COUNTER = T - self.start - self.construction

            if self.technology == 'AP AEC' and LIFETIME_COUNTER % (
                    self.CAPEX_inputs['AEC stack lifetime']) == 0 and T > self.start + self.construction:
                COST_COUNTER += self.electrode_cost * self.CAPEX_inputs['Stack and battery replacement'] / \
                                self.CAPEX_inputs['CAPEX_from_installed_cost']
            return -COST_COUNTER
        else:
            # Variables
            COST_COUNTER = 0
            LIFETIME_COUNTER = T - self.start - self.construction
            if LIFETIME_COUNTER > 0:
                # Battery replacement
                if self.technology != 'AP SMR':
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
                                                                    self.CAPEX_inputs['Wind farm lifetime'],
                                                                    # Assuming same lifetime as wind farm
                                                                    'Solar PV CAPEX')

                # electrode replacement
                if self.technology == 'AP AEC' and LIFETIME_COUNTER % (
                        self.CAPEX_inputs['AEC stack lifetime']) == 0 and T > self.start + self.construction:
                    COST_COUNTER += self.electrode_cost * self.CAPEX_inputs['Stack and battery replacement'] / self.CAPEX_inputs['CAPEX_from_installed_cost']

                return -COST_COUNTER

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
                                                                    self.CAPEX_inputs['Wind farm lifetime'],
                                                                    # Assuming same lifetime as wind farm
                                                                    'Solar PV CAPEX')

                return -COST_COUNTER
            else:
                return 0

    def calculate_Tax(self, T):
        self.income_tax = 0
        if self.start + self.construction < T < self.start + self.construction + self.L:
            Net_revenue_T = (
                    self.calculate_Depreciation(T)
                    + self.calculate_OPEX(T)
                    + self.calculate_Sales(T)
                    + self.calculate_PMT(T)
                    + self.stack_replacement_costs(T)
            )
            if Net_revenue_T > 0:
                self.income_tax = -Net_revenue_T * (self.phi_state + self.phi_federal)
            else:
                self.income_tax = 0

        return self.income_tax

    def _subsidies_disabled_for_scenario(self):
        # The New scenario is intended to run without tax credits or subsidies.
        return self.scenario == 'New'

    def _tax_credit_value_HELPER(self, T, credit_45Y=False):
        construction_end_time = self.start + self.construction
        relative_time = T - construction_end_time

        if relative_time <= 0:
            return 0
        if relative_time <= 5 * 12:
            return self.TCvalue['Year 6'] if credit_45Y else self.TCvalue['Year 1-5']
        if relative_time <= 6 * 12:
            return self.TCvalue['Year 6']
        if relative_time <= 7 * 12:
            return self.TCvalue['Year 7']
        if relative_time <= 8 * 12:
            return self.TCvalue['Year 8']
        if relative_time <= 9 * 12:
            return self.TCvalue['Year 9']
        return self.TCvalue['Year 10 and after']

    def _tax_credit_converter_for_program(self, income_tax, tax_credit, T, is45Y=False, set_value=False, value=None):  # Let the income tax be positive
        TC_market_value = value if set_value else self._tax_credit_value_HELPER(T, credit_45Y=is45Y)

        if tax_credit - income_tax < 0:
            income_tax = income_tax - tax_credit
            return tax_credit, income_tax
        elif tax_credit - income_tax >= 0:
            cash_equivalent_tax_credit = income_tax + (tax_credit - income_tax) * TC_market_value
            income_tax = 0
            return cash_equivalent_tax_credit, income_tax

    def _choose_policy(self, compare45V_45Q=False, compare45Y_48E=False, set_value=False, value=None):
        counter_1 = 0
        counter_2 = 0

        # Iterates through the timepoints
        for T in range(self.start + self.construction + self.L):

            if compare45V_45Q:  # compare45V_45Q is boolean. If true, performs the comparison of 45V and 45Q
                counter_1 += self.tax_credit_calculator.calculate_45V(T) / self.discount_factor ** (T - self.start)
                counter_2 += self.tax_credit_calculator.calculate_45Q(T) / self.discount_factor ** (T - self.start)

            elif compare45Y_48E:  # compare45Y_48E is boolean. If true, performs the comparison of 45V and 45Q
                income_tax = abs(self.calculate_Tax(T))  # Need positive IT for comparison with tax credits

                if T == self.start + self.construction + 1:  # Just calculates the one ITC timepoint
                    counter_1 += self.tax_credit_calculator.calculate_48E(T) / self.discount_factor ** (T - self.start)

                # Calculate cash-equivalent 45Y
                cash_equivalent_45Y, _ = self._tax_credit_converter_for_program(income_tax=income_tax,
                                                                                tax_credit=self.tax_credit_calculator.calculate_45Y(T),
                                                                                T=T,
                                                                                set_value=set_value,
                                                                                value=value)
                # Add CE PTCs to comparator 2
                counter_2 += cash_equivalent_45Y / self.discount_factor ** (T - self.start)

        if counter_1 > counter_2:
            return True  # TRUE means 45V > 45Q  OR  48E > 45Y
        else:
            return False  # FALSE means 45V < 45Q  OR  48E < 45Y

    def _find_abated_emissions(self):
        total_abated_emissions_across_lifetime = 0
        for T in range(self.start + self.construction, self.start + self.construction + self.L):
            emissions_difference_CI = (
                    self.baseline_emissions.total_emissions(T) - self.Emissions_of_technology.total_emissions(T))
            monthly_hydrogen_flowrate = self.engineering_inputs['H2'] * 365 / 12 * self.financial_inputs['availability']

            total_abated_emissions_across_lifetime += emissions_difference_CI * monthly_hydrogen_flowrate * (
                    1 / (self.discount_factor_CAC ** (T - self.start)))

        return total_abated_emissions_across_lifetime

    def _find_hydrogen_produced(self, discount_factor):
        # the discount factor in the form: (1+r/12)
        h2_TPD = self.engineering_inputs['H2']
        h2_kgPm = h2_TPD * self.F_A * 365 / 12 * 1000  # (Tonne/day)*(time/time)*(day/year)/(month/year)*(1000kg/1 tonne)

        total_h2_produced = 0
        for T in range(self.start + self.construction, self.start + self.construction + 10 * 12):
            total_h2_produced += h2_kgPm * (1 / (discount_factor ** (T - self.start)))

        # print("hydrogen produced", total_h2_produced/1000000)
        return total_h2_produced

    def cash_equivalent_credits(self, T):
        if not self.policy or self.technology == 'AP SMR' or self._subsidies_disabled_for_scenario():
            return 0

        # Checking which program is better for AP CCS and for wind farm

        if self.technology == 'AP CCS' and T == 0:
            self.is45V = self._choose_policy(compare45V_45Q=True)  # Compare the NPV of 45V and 45Q. Ignored transaction costs.

        if self.scenario == 'C' and T == 0:
            self.is48E = self._choose_policy(compare45Y_48E=True)  # Compare the NPV of 45Y and 48E. Don't ignore transaction costs.

        # Defining policy PTCs depending on the technology
        if self.technology in ['AP BH2S', 'AP AEC']:
            total_credits_others = self.tax_credit_calculator.calculate_45V(T)
        elif self.technology == 'AP CCS':
            total_credits_others = self.tax_credit_calculator.calculate_45V(T) if self.is45V else self.tax_credit_calculator.calculate_45Q(T)

        # 48C credits are set to 0
        total_credits_48C = 0
        
        # European subsidy integration
        if self.scenario == 'New':
            european_subsidy = self.tax_credit_calculator.calculate_European_subsidy(T)
        else:
            european_subsidy = 0

        # Defining policy for scenario C
        if self.is48E:
            total_credits_48E = self.tax_credit_calculator.calculate_48E(T) if T == self.start + self.construction + 1 else 0
            total_credits_45Y = 0
        else:
            total_credits_48E = 0
            total_credits_45Y = self.tax_credit_calculator.calculate_45Y(T)

        income_tax = abs(self.calculate_Tax(T))  # positive income tax
        cash_equivalent = 0  # initialize cash equivalent variable

        # print('income tax', income_tax)
        # Use 48C credits first
        total_credits_48C, income_tax = self._tax_credit_converter_for_program(income_tax=income_tax,
                                                                               tax_credit=total_credits_48C, T=T)

        # Use 48E credits
        total_credits_48E, income_tax = self._tax_credit_converter_for_program(income_tax=income_tax,
                                                                               tax_credit=total_credits_48E, T=T)
        # Use 45Y credits
        total_credits_45Y, income_tax = self._tax_credit_converter_for_program(income_tax=income_tax,
                                                                               tax_credit=total_credits_45Y,
                                                                               is45Y=True, T=T)  # Direct pay set false
        # Add European subsidy calculation
        european_subsidy, income_tax = self._tax_credit_converter_for_program(income_tax=income_tax,
                                                                                 tax_credit=european_subsidy, T=T)
        # Use 45V or 45Q credits
        total_credits_others, income_tax = self._tax_credit_converter_for_program(income_tax=income_tax,
                                                                                  tax_credit=total_credits_others, T=T)
        # print(self.is45V, self.technology, self.scenario, '45V or 45Q',total_credits_others,'48C',total_credits_48C,'45Y', total_credits_45Y,'48E', total_credits_48E)

        # Add all credits together
        cash_equivalent += total_credits_others + total_credits_45Y + total_credits_48E + total_credits_48C + european_subsidy

        # print(cash_equivalent)

        return cash_equivalent

    def _nominal_tax_credits(self, T, separate=False):
        if not self.policy or self.technology == 'AP SMR' or self._subsidies_disabled_for_scenario():
            if separate:
                return {'45V': 0, '45Q': 0, '48E': 0, '45Y': 0}
            return 0

        # Check which policy program is better with cash equivalency
        if self.technology == 'AP CCS' and T == 0:
            self.is45V = self._choose_policy(compare45V_45Q=True)  # Compare the NPV of 45V and 45Q. Ignored transaction costs.
        elif self.technology != 'AP CCS':
            self.is45V = True

        if self.scenario == 'C' and T == 0:
            self.is48E = self._choose_policy(compare45Y_48E=True)  # Compare the NPV of 45Y and 48E. Don't ignore transaction costs.

        individual_tax_credits = {
            '45V': self.tax_credit_calculator.calculate_45V(T) if self.is45V else 0,
            '45Q': self.tax_credit_calculator.calculate_45Q(T) if not self.is45V else 0,
            '48E': self.tax_credit_calculator.calculate_48E(T) if (T == self.start + self.construction + 1) and self.is48E else 0,
            '45Y': self.tax_credit_calculator.calculate_45Y(T) if not self.is48E else 0
        }

        if separate:
            return individual_tax_credits
        else:
            return sum(individual_tax_credits.values())

    def _convert_nominal_to_CE(self, T, separate=False):
        tax_credits = self._nominal_tax_credits(T, separate=True)
        income_tax = abs(self.calculate_Tax(T))

        CE_tax_credits = {}
        for key, val in tax_credits.items():
            if key == '45Y':
                CE_tax_credits[key], income_tax = self._tax_credit_converter_for_program(income_tax, val, T, is45Y=True)
            else:
                CE_tax_credits[key], income_tax = self._tax_credit_converter_for_program(income_tax, val, T)

        if separate:
            return CE_tax_credits
        else:
            return sum(CE_tax_credits.values())

    def CAC_updated(self):
        if not self.policy or self._subsidies_disabled_for_scenario():
            return 0

        carbon_abated = self._find_abated_emissions()
        CAC = 0
        for T in range(self.start + self.construction + self.L + 1):
            # Add European subsidy to the nominal tax credits if the scenario is 'New'
            nominal_credits = self._nominal_tax_credits(T)
            if self.scenario == 'New':
                european_subsidy = self.tax_credit_calculator.calculate_European_subsidy(T)
                nominal_credits += european_subsidy
    
            CAC += (nominal_credits / carbon_abated) / ((self.discount_factor_CAC) ** (T - self.start))
    
        return CAC
    
    def total_support(self):
        if not self.policy or self._subsidies_disabled_for_scenario():
            return 0

        total_support = 0
        hydrogen = self._find_hydrogen_produced(self.discount_factor_CAC)
        for T in range(self.start + self.construction + self.L + 1):
            nominal_credits = self._nominal_tax_credits(T)
            if self.scenario == 'New':
                european_subsidy = self.tax_credit_calculator.calculate_European_subsidy(T)
                nominal_credits += european_subsidy
    
            total_support += (nominal_credits / hydrogen) / ((self.discount_factor_CAC) ** (T - self.start))
    
        return total_support
    
    def total_CE_support(self):
        if not self.policy or self._subsidies_disabled_for_scenario():
            return 0

        total_CE_support = 0
        hydrogen = self._find_hydrogen_produced(self.discount_factor)
        for T in range(self.start + self.construction + self.L + 1):
            ce_credits = self._convert_nominal_to_CE(T)
            if self.scenario == 'New':
                european_subsidy = self.tax_credit_calculator.calculate_European_subsidy(T)
                ce_credits += european_subsidy  # Add European subsidy to CE credits
    
            total_CE_support += (ce_credits / hydrogen) / ((self.discount_factor) ** (T - self.start))
    
        return total_CE_support
    
    def total_CE_support_social(self):
        if not self.policy or self._subsidies_disabled_for_scenario():
            return 0

        total_CE_support = 0
        hydrogen = self._find_hydrogen_produced(self.discount_factor_CAC)
        for T in range(self.start + self.construction + self.L + 1):
            ce_credits = self._convert_nominal_to_CE(T)
            if self.scenario == 'New':
                european_subsidy = self.tax_credit_calculator.calculate_European_subsidy(T)
                ce_credits += european_subsidy  # Add European subsidy to CE credits
    
            total_CE_support += (ce_credits / hydrogen) / ((self.discount_factor_CAC ** (T - self.start)))
    
        return total_CE_support
    
    def separate_total_support(self):
        if not self.policy or self._subsidies_disabled_for_scenario():
            return {'45V': 0, '45Q': 0, '48E': 0, '45Y': 0, 'European_subsidy': 0}

        hydrogen = self._find_hydrogen_produced(self.discount_factor_CAC)
        total_support = {
            '45V': 0,
            '45Q': 0,
            '48E': 0,
            '45Y': 0,
            'European_subsidy': 0  # Adding a field for European subsidy
        }
        for T in range(self.start + self.construction + self.L + 1):
            total_support_separate = self._nominal_tax_credits(T, separate=True)
            
            # Add support for each credit type
            for key, val in total_support_separate.items():
                total_support[key] += (val / hydrogen) / ((self.discount_factor_CAC) ** (T - self.start))
            
            # Add European subsidy support if scenario is 'New'
            if self.scenario == 'New':
                european_subsidy = self.tax_credit_calculator.calculate_European_subsidy(T)
                total_support['European_subsidy'] += (european_subsidy / hydrogen) / ((self.discount_factor_CAC) ** (T - self.start))
    
        return total_support


    def carbon_abatement_cost(self, value=None, set_value=False, absolute=False, separate=False, quality_assure=False):
    
        def cash_equivalent_credits_if_no_market(T, set_value=set_value, TC_value=value, separate=separate):
            if not self.policy or self.technology == 'AP SMR' or self._subsidies_disabled_for_scenario():
                if separate:
                    return 0, 0, 0, 0, 0, 0
                return 0
    
            # Checking which program is better for AP CCS and for wind farm
            if self.technology == 'AP CCS' and T == 0:
                self.is45V = self._choose_policy(compare45V_45Q=True)
    
            if self.scenario == 'C' and T == 0:
                self.is48E = self._choose_policy(compare45Y_48E=True)
    
            # Defining policy PTCs depending on the technology
            if self.technology in ['AP BH2S', 'AP AEC']:
                total_credits_others = self.tax_credit_calculator.calculate_45V(T)
            elif self.technology == 'AP CCS':
                total_credits_others = self.tax_credit_calculator.calculate_45V(T) if self.is45V else self.tax_credit_calculator.calculate_45Q(T)
    
            # 48C credits are set to 0
            total_credits_48C = 0
    
            # Defining policy for scenario C
            if self.is48E:
                total_credits_48E = self.tax_credit_calculator.calculate_48E(T) if T == self.start + self.construction + 1 else 0
                total_credits_45Y = 0
            else:
                total_credits_48E = 0
                total_credits_45Y = self.tax_credit_calculator.calculate_45Y(T)
    
            # Adding European subsidy calculation for the 'New' scenario
            european_subsidy = 0  # Default to 0
            if self.scenario == 'New':  # Check if the scenario is 'New'
                european_subsidy = self.tax_credit_calculator.calculate_European_subsidy(T)
    
            income_tax = abs(self.calculate_Tax(T))  # positive income tax
            cash_equivalent = 0  # initialize cash equivalent variable
    
            # Use 48C credits first
            total_credits_48C, income_tax = self._tax_credit_converter_for_program(income_tax=income_tax,
                                                                                   tax_credit=total_credits_48C,
                                                                                   T=T,
                                                                                   set_value=set_value,
                                                                                   value=TC_value)
    
            # Use 48E credits
            total_credits_48E, income_tax = self._tax_credit_converter_for_program(income_tax=income_tax,
                                                                                   tax_credit=total_credits_48E,
                                                                                   T=T,
                                                                                   set_value=set_value,
                                                                                   value=TC_value)
    
            # Use 45Y credits
            total_credits_45Y, income_tax = self._tax_credit_converter_for_program(income_tax=income_tax,
                                                                                   tax_credit=total_credits_45Y,
                                                                                   is45Y=True,
                                                                                   T=T,
                                                                                   set_value=set_value,
                                                                                   value=TC_value)
    
            # Use 45V or 45Q credits
            total_credits_45V = total_credits_45Q = 0
            if self.is45V and self.technology == 'AP CCS':
                total_credits_45V, income_tax = self._tax_credit_converter_for_program(income_tax=income_tax,
                                                                                       tax_credit=total_credits_others,
                                                                                       T=T,
                                                                                       set_value=set_value,
                                                                                       value=TC_value)
            elif not self.is45V and self.technology == 'AP CCS':
                total_credits_45Q, income_tax = self._tax_credit_converter_for_program(income_tax=income_tax,
                                                                                       tax_credit=total_credits_others,
                                                                                       T=T,
                                                                                       set_value=set_value,
                                                                                       value=TC_value)
            else:
                total_credits_45V, income_tax = self._tax_credit_converter_for_program(income_tax=income_tax,
                                                                                       tax_credit=total_credits_others,
                                                                                       T=T,
                                                                                       set_value=set_value,
                                                                                       value=TC_value)
    
            # If not separating the credits, sum them all together
            if not separate:
                cash_equivalent += (total_credits_45Y +
                                    total_credits_48E +
                                    total_credits_48C +
                                    total_credits_45V +
                                    total_credits_45Q +
                                    european_subsidy)  # Add European subsidy here
    
                return cash_equivalent
            else:
                return total_credits_45V, total_credits_45Q, total_credits_45Y, total_credits_48C, total_credits_48E, european_subsidy  # Include subsidy when returning separate credits
    
        # INITIALIZE NPV-like variables
        if not separate:
            CAC = 0
    
        CAC_45V = CAC_45Q = CAC_45Y = CAC_48C = CAC_48E = CAC_subsidy = 0
    
        # Quality assurance storage of values
        store = {
            'T': [],
            '45V': [],
            '45Q': [],
            '45Y': [],
            '48C': [],
            '48E': [],
            'subsidy': []  # Add a column to store European subsidy values
        }
    
        # Loop to calculate the present value of carbon abatement cost
        for T in range(self.start + self.construction + self.L + 1):
            if separate:
                cash_equivalent_45V, cash_equivalent_45Q, cash_equivalent_45Y, cash_equivalent_48C, cash_equivalent_48E, cash_equivalent_subsidy = cash_equivalent_credits_if_no_market(
                    T, TC_value=value, separate=separate)
    
            else:
                all_cash = cash_equivalent_credits_if_no_market(T, TC_value=value, set_value=set_value, separate=separate) / (
                        (self.discount_factor_CAC) ** (T - self.start))
    
            # Choose if we want to normalize by the carbon abated
            if absolute:
                denominator = 1
            else:
                denominator = self._find_abated_emissions()
    
            if separate:
                store['T'].append(T)
                store['45V'].append(cash_equivalent_45V / denominator)
                store['45Q'].append(cash_equivalent_45Q / denominator)
                store['45Y'].append(cash_equivalent_45Y / denominator)
                store['48C'].append(cash_equivalent_48C / denominator)
                store['48E'].append(cash_equivalent_48E / denominator)
                store['subsidy'].append(cash_equivalent_subsidy / denominator)
    
                CAC_45V += (1 / self.discount_factor ** (T - self.start)) * cash_equivalent_45V / denominator
                CAC_45Q += (1 / self.discount_factor ** (T - self.start)) * cash_equivalent_45Q / denominator
                CAC_45Y += (1 / self.discount_factor ** (T - self.start)) * cash_equivalent_45Y / denominator
                CAC_48C += (1 / self.discount_factor ** (T - self.start)) * cash_equivalent_48C / denominator
                CAC_48E += (1 / self.discount_factor ** (T - self.start)) * cash_equivalent_48E / denominator
                CAC_subsidy += (1 / self.discount_factor ** (T - self.start)) * cash_equivalent_subsidy / denominator
            else:
                CAC += all_cash / denominator
    
        if separate and quality_assure:
            return pd.DataFrame(store)
        if separate:
            return CAC_45V, CAC_45Q, CAC_45Y, CAC_48C, CAC_48E, CAC_subsidy  # Return subsidy along with other credits
        else:
            CAC /= self.inflation_correction
            return CAC

    def calculate_CF(self, T):
        # Calculate Cash Flow (CF_T) for time step T
        CF_T = (
            self.calculate_FCI(T) +  # Fixed Capital Investment
            self.calculate_land(T) +  # Land cost
            self.calculate_WC(T) +  # Working Capital
            self.calculate_PMT(T) +  # Payment of loans or financing
            self.calculate_Sales(T) +  # Sales revenue
            self.calculate_OPEX(T) +  # Operational Expenses
            self.calculate_Tax(T) +  # Taxes
            self.cash_equivalent_credits(T) +  # Tax Credits and European subsidy (included here)
            self.stack_replacement_costs(T)  # Replacement costs for equipment
        )
        return CF_T

    def calculate_OPEX_parameter_effects(self, T):
        """Return a signed OPEX attribution for one model month.

        The values returned by this method use the same sign convention as the
        model cash flow: positive values increase NPV and negative values reduce
        NPV.  The sum of the returned values is designed to reconcile to
        ``calculate_OPEX(T)`` while splitting the operating cash flow into the
        major drivers used in the stochastic model.
        """
        effects = {
            'Plant-based OPEX': 0.0,
            'CCS transport and storage': 0.0,
            'CBAM / border adjustment': 0.0,
            'Feedstock price': 0.0,
            'Electricity cost': 0.0,
            'Battery fixed O&M': 0.0,
            'Battery variable O&M': 0.0,
            'Wind and solar O&M': 0.0,
            'Curtailment / power revenue': 0.0,
            'Process carbon price': 0.0,
            'Transport to port': 0.0,
            'Shipping cost': 0.0,
            'Shipping carbon cost': 0.0,
            'Embedded natural-gas carbon cost': 0.0,
            'Distribution and R&D markup': 0.0,
        }

        if not (self.start + 36 < T <= self.start + self.construction + self.L):
            return effects

        TS_COST = self._TS_COST(T)
        CO2_TAX = self._CBAM_COST(T)
        CONSTANT_OPEX = self.final_MI_OPEX[self.technology]['MI_OPEX'] / 12

        effects['Plant-based OPEX'] -= CONSTANT_OPEX
        effects['CCS transport and storage'] -= TS_COST
        effects['CBAM / border adjustment'] += CO2_TAX

        if T == self.start + self.construction + 1:
            effects['Plant-based OPEX'] -= self.final_MI_OPEX[self.technology]['MI_OPEX_start']

        NG_cost = (self.natural_gas_requirements[self.technology] * self.NG_market[T]) / 12
        effects['Feedstock price'] -= NG_cost

        if self.scenario == 'A' or self.scenario == 'B':
            elec_cost = (self.ELEC_INPUTS[self.technology][1] * 1000 * self.H_operating / 12) * self.El_market[T]
            effects['Electricity cost'] -= elec_cost

        elif self.scenario == 'D':
            elec_cost = (self.ELEC_INPUTS[self.technology][1] * self.El_PPA_market * 1000 * self.H_operating / 12)
            effects['Electricity cost'] -= elec_cost

        elif self.scenario in ['C', 'New'] and self.technology != 'AP SMR':
            WIND_CAPACITY = self.CAPEX_inputs['wind_capacity'][self.technology] * 1000  # kW
            BATTERY_CAPACITY = self.CAPEX_inputs['battery_capacity'][self.technology] * 1000 / 4  # kWh
            SOLAR_CAPACITY = self.CAPEX_inputs['solar_capacity'][self.technology] * 1000  # kW
            WIND_OPEX = (self.MIOPEX_INPUTS['Wind OPEX']) * WIND_CAPACITY / 12
            TAU = T % 12
            BATTERY_DISCHARGE = self.ELEC_INPUTS['discharge'][self.technology][TAU]
            BATTERY_FIXED_OPEX = (self.MIOPEX_INPUTS['Battery OPEX']) * BATTERY_CAPACITY / 12
            BATTERY_VAR_OPEX = self.MIOPEX_INPUTS['Battery Var OPEX'] * BATTERY_DISCHARGE / 4
            SOLAR_OPEX = (self.MIOPEX_INPUTS['Solar OPEX']) * SOLAR_CAPACITY / 12

            ELECTRICITY_PPA_PRICE = self.Market_inputs['PPA_pricing_for_C'][self.time][self.policy]
            ELECTRICITY_SOLD = self.ELEC_INPUTS['curtailment'][self.technology][TAU] * max(
                self.El_market[T], ELECTRICITY_PPA_PRICE
            )

            effects['Battery fixed O&M'] -= BATTERY_FIXED_OPEX
            effects['Battery variable O&M'] -= BATTERY_VAR_OPEX
            effects['Wind and solar O&M'] -= (WIND_OPEX + SOLAR_OPEX)
            effects['Curtailment / power revenue'] += ELECTRICITY_SOLD

        if self.technology in ['AP SMR', 'AP CCS', 'AP BH2S']:
            year = self._model_year_from_T(T)
            carbon_cost_per_ton = self.get_carbon_cost_for_year(year)
            stack_emissions = self.Emissions_of_technology.stack_emissions()
            natural_gas_emissions = self.Emissions_of_technology.natural_gas_emissions()
            biomass_emissions = self.Emissions_of_technology.biomass_emissions()
            electricity_emissions = self.Emissions_of_technology.electricity_emissions(T)
            total_emissions_intensity = (
                stack_emissions + natural_gas_emissions + biomass_emissions + electricity_emissions
            )
            h2_production = self.engineering_inputs['H2'] * 365 / 12 * self.financial_inputs['availability']
            carbon_cost = total_emissions_intensity * h2_production * carbon_cost_per_ton
            effects['Process carbon price'] -= carbon_cost

        effects['Transport to port'] -= self._transport_to_port_cost_monthly()
        effects['Shipping cost'] -= self._shipping_cost_monthly()
        effects['Shipping carbon cost'] -= self._shipping_carbon_cost_monthly(T)
        effects['Embedded natural-gas carbon cost'] -= self._embedded_ng_carbon_cost_monthly(T)

        base_opex = sum(effects.values())
        distribution_and_marketing_costs = self.MIOPEX_INPUTS['distribution_and_marketing']
        effects['Distribution and R&D markup'] = base_opex * (
            distribution_and_marketing_costs
            + distribution_and_marketing_costs * self.MIOPEX_INPUTS['R&D costs']
        )

        return effects

    def calculate_NPV_parameter_effects(self):
        """Return signed, discounted NPV effects by major model driver.

        The returned dictionary is in the same units as ``calculate_NPV()``.
        It is normalized by lifetime ammonia production in exactly the same way
        as the model NPV, so the sum of the returned effects reconciles to the
        run-level NPV.
        """
        effects = {
            'CAPEX': 0.0,
            'Financing': 0.0,
            'Ammonia price / revenue': 0.0,
            'Plant-based OPEX': 0.0,
            'CCS transport and storage': 0.0,
            'CBAM / border adjustment': 0.0,
            'Feedstock price': 0.0,
            'Electricity cost': 0.0,
            'Battery fixed O&M': 0.0,
            'Battery variable O&M': 0.0,
            'Wind and solar O&M': 0.0,
            'Curtailment / power revenue': 0.0,
            'Process carbon price': 0.0,
            'Transport to port': 0.0,
            'Shipping cost': 0.0,
            'Shipping carbon cost': 0.0,
            'Embedded natural-gas carbon cost': 0.0,
            'Distribution and R&D markup': 0.0,
            'Tax': 0.0,
            'Tax credits and subsidies': 0.0,
            'Replacement costs': 0.0,
        }

        for T in range(self.start + self.construction + self.L + 1):
            discount = self.discount_factor ** (T - self.start)
            effects['CAPEX'] += (
                self.calculate_FCI(T) + self.calculate_land(T) + self.calculate_WC(T)
            ) / discount
            effects['Financing'] += self.calculate_PMT(T) / discount
            effects['Ammonia price / revenue'] += self.calculate_Sales(T) / discount

            opex_effects = self.calculate_OPEX_parameter_effects(T)
            for key, value in opex_effects.items():
                effects[key] += value / discount

            effects['Tax'] += self.calculate_Tax(T) / discount
            effects['Tax credits and subsidies'] += self.cash_equivalent_credits(T) / discount
            effects['Replacement costs'] += self.stack_replacement_costs(T) / discount

        npv_denominator = self.M_NH3 * 365 * self.L / 12 * self.F_A
        for key in list(effects.keys()):
            effects[key] /= npv_denominator
            effects[key] /= self.inflation_correction

        return effects

    def calculate_NPV(self, ROI=False):
        NPV = 0
        ROI_value = 0
        for T in range(self.start + self.construction + self.L + 1):
            CF_T = self.calculate_CF(T)
            CAPEX = self.calculate_FCI(T) + self.calculate_land(T) + self.calculate_WC(T)
            if not ROI:
                NPV += CF_T / self.discount_factor ** (T - self.start)
            if ROI:
                ROI_value += (CF_T - (CAPEX)) / self.discount_factor ** (T - self.start)

        # Correct for inflation to bring back dollars to 2027.

        NPV /= (self.M_NH3 * 365 * L / 12 * self.F_A)
        NPV /= self.inflation_correction

        ROI_value /= (self.final_CAPEX[self.technology]['CAPEX'])
        ROI_value /= self.inflation_correction
        ROI_value = 0
        if not ROI:
            return NPV
        else:
            return ROI_value

    def quality_assure(self):
        data = pd.DataFrame(
            columns=['time', 'FCI', 'Land', 'WC', 'PMT', 'Sales', 'OPEX', 'Tax', 'Credits', 'Stack_replacement',
                     'Cash Flow'])
        for T in range(self.start + self.construction + self.L + 1):
            terms = [
                T,
                self.calculate_FCI(T),
                self.calculate_land(T),
                self.calculate_WC(T),
                self.calculate_PMT(T),
                self.calculate_Sales(T),
                self.calculate_OPEX(T),
                self.calculate_Tax(T),
                self.cash_equivalent_credits(T),
                self.stack_replacement_costs(T),
                self.calculate_CF(T)
            ]
            data.loc[len(data)] = terms

        return data

    def quality_assure_CAC(self):
        df = {
            'CAC': self.carbon_abatement_cost(separate=True, quality_assure=True, absolute=True)
        }
        return dataframes_to_excel(df, f'CAC_quality_asssure_{self.technology}_{self.time}_{self.scenario}.xlsx')


if __name__ == '__main__':
    from numpy.random import uniform as uni
    from numpy.random import seed as seed

    from global_variables import *
    from _instantiate_inputs import InstantiateInputs
    from _GBM import brownian_motion
    from _MI_OPEX import MI_OPEX
    from _CI_Calculator import Carbon_Intensity_of_technology
    from cleaning_optimization_data import *
    from PPA_model_testing import PPA_OUTPUT_FILE_NAME

    from file_handling_funcs import *
    from _path_dependent_data_handling import *

    PPA_data = pd.read_excel(ROOT_DIR + PPA_OUTPUT_FILE_NAME)
    inter = 1
    start = 0
    scenario = 'D'
    matching = 'hourly'

    INPUT_PARAMETERS_PATH = prob_INPUT_PARAMETERS_PATH
    with open(INPUT_PARAMETERS_PATH, 'r') as json_file:
        INPUT_PARAMETERS = json.load(json_file)
    INPUT_PARAMETERS_copy = INPUT_PARAMETERS.copy()
    # Instantiate inputs from JSON file
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

    # Calculate additional inputs from datasets
    CAPEX_inputs['wind_capacity'] = {}
    CAPEX_inputs['battery_capacity'] = {}
    CAPEX_inputs['solar_capacity'] = {}

    RAND_AEC_CAPACITY = uni(0, 1)
    LOCATION_RANDOMIZER = uni(0, 1)


    def return_masked_data(time, matching, tech, data=CLEANED_DATA, randomizer=LOCATION_RANDOMIZER):  # chooses a random LOCATION
        mask = (data['year'] == time) & (data['matching'] == matching) & (data['technology'] == tech)
        new_data = data[mask]
        return new_data.iloc[int(randomizer * len(new_data))]


    def get_capacity_data(time, matching, tech):
        return return_masked_data(time, matching, tech)


    def AEC_rand_dist(low_item, high_item, rand_num=RAND_AEC_CAPACITY):  # ties the randomness of AEC power
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
            OPTIMIZATION_ROW_HIGH = get_capacity_data(time, matching, tech + ' high')
            OPTIMIZATION_ROW_LOW = get_capacity_data(time, matching, tech + ' low')

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

    electricity_requirements['curtailment'] = {  # MWh/month
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

    biomass_requirement = (engineering_inputs['H2'] * 1000 / 70.4) * (365 * financial_inputs['availability'])  # tonnes/year
    biomass_price = uni(81.23, 94.09)  # $/ton

    market_correlator = uni(0, 1)


    def _corr(val1, val2, correlator=market_correlator):
        return val1 + (val2 - val1) * correlator


    def get_PPA_data_row(time, matching, policy, randomizer=LOCATION_RANDOMIZER):
        mask = (PPA_data['time'] == time) & (PPA_data['policy'] == policy) & (PPA_data['matching'] == matching)
        return PPA_data[mask].iloc[int(randomizer * len(PPA_data[mask]))]


    Market_inputs['PPA_pricing'] = {time: {matching: {policy:
                                                          get_PPA_data_row(time, matching, policy)['LCOE'] / 1000
                                                      for policy in policies}
                                           for matching in matching_type}
                                    for time in times}

    # Defining that the one of the prices for selling curtailment in scenario C are the yearly matching PPA prices
    Market_inputs['PPA_pricing_for_C'] = {
        time: {policy: Market_inputs['PPA_pricing'][time]['yearly'][policy] for policy in policies} for time
        in times}

    # Adjusting direct emissions of AP CCS with relation to AP SMR
    AP_SMR_lower_bound = 0.243 * carbon_intensity[
        'NGCC thermal efficiency'] * 13.4 / 0.28  # (Kg CO2/kWh_e)*(kWh_e/kWh_t_NG)*(kWh_t_NG/Kg NG)/(Kg H2/Kg NG) = Kg CO2/Kg H2
    AP_SMR_upper_bound = 0.527 * carbon_intensity[
        'NGCC thermal efficiency'] * 13.4 / 0.28  # (Kg CO2/kWh_e)*(kWh_e/kWh_t_NG)*(kWh_t_NG/Kg NG)/(Kg H2/Kg NG) = Kg CO2/Kg H2

    carbon_intensity['stack']['AP SMR'] = uni(AP_SMR_lower_bound, AP_SMR_upper_bound)
    capture_rate_CCS = 0.956
    carbon_intensity['stack']['AP CCS'] = carbon_intensity['stack']['AP SMR'] * (1 - capture_rate_CCS)

#    carbon_intensity['natural gas'] = (uni(0.01, 7.9) / 1000) * carbon_intensity[
#        'NGCC thermal efficiency']  # ((g CO2 / kWh_e) / (1000 g CO2 / kg CO2)) * (kWh_e/kWh_t)

    if carbon_intensity.get('use_country_ng_emissions', False):
        carbon_intensity['natural gas'] = float(carbon_intensity.get('natural gas', 0.043))
    else:
        carbon_intensity['natural gas'] = (uni(39, 118) / 1000)  # ((g CO2 / kWh_t) / (1000 g CO2 / kg CO2))

    Policy45V_sensitivity_parameter = 1 if start == 0 else 1  # no units

    IRA_credits['45V'] = {float(key): value for key, value in IRA_credits['45V'].items()}


    def calculate_battery_and_turbine_cost(technology, time, CAPEX_inputs):
        if technology == 'AP SMR':
            return -1

        # Constants
        WIND_CAPACITY = CAPEX_inputs['wind_capacity'][technology] * 1000  # kW
        BATTERY_CAPACITY = CAPEX_inputs['battery_capacity'][technology] * 1000  # kW
        SOLAR_CAPACITY = CAPEX_inputs['solar_capacity'][technology] * 1000  # kW

        # Calculations
        wind_capex = CAPEX_inputs[f'Wind turbine CAPEX {time}'] * WIND_CAPACITY  # $/kW * kW = $
        battery_capex = CAPEX_inputs[
                            f'Battery Storage CAPEX {time}'] / 4 * BATTERY_CAPACITY  # ($/kW * kW/ 4 kWh)* kW = $ Note: kW/ 4 kWh is valid because the cost is based on a 4-hour battery
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
#            'UC': 0,  # Uninstalled cost
#            'FCI': 0,  # Fixed Capital Investment
#            'WC': 0,  # Working Capital
#            'CAPEX': 0,  # Total CAPEX
#            'installation': 0,
#            'instrumentation_and_controls': 0,
#            'piping': 0,
#            'electrical': 0,
#            'building_process_auxiliary': 0,
#            'service_facilities_and_yard_improvements': 0,
#            'land': 0,
#            'engineering_supervision': 0,
#            'legal_expenses': 0,
#            'construction_expense_and_contractors_fee': 0,
#            'contingency': 0
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
                                 / CAPEX_inputs['Battery roundtrip eff'] / 12 / 4  # VOM BATTERY COSTS ADDED IN THE market-dependent OPEX

            wind_OPEX = (MI_OPEX_inputs['Wind OPEX']) * electricity_requirement_for_wind / 12  # ($/kW-year) * (kW) * (1 year/ 12 months) = $/month

            final_MI_OPEX[tecnology] = {
                'MI_OPEX': total_labor_costs + total_fixed_charges + misc_costs + utilities + wind_OPEX + battery_OPEX_fixed,
                'MI_OPEX_start': start_costs}
        else:
            final_MI_OPEX[tecnology] = {
                'MI_OPEX': total_labor_costs + total_fixed_charges + misc_costs + utilities,
                'MI_OPEX_start': start_costs}