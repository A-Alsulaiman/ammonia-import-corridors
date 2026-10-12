import pysnooper
class Carbon_Intensity_of_technology:
    def __init__(self, technology, carbon_intensity, scenario, financial_inputs, engineering_inputs,
                 biomass_requirement, natural_gas_requirements, electricity_requirements, aeo22_data, aeo23_data):
        self.technology = technology
        self.aeo22_data = aeo22_data.values  # Converting to numpy array for efficiency
        self.aeo23_data = aeo23_data.values
        self.carbon_intensity = carbon_intensity
        self.electricity_requirements = electricity_requirements
        self.natural_gas_requirements = natural_gas_requirements
        self.biomass_requirement = biomass_requirement if technology == 'AP BH2S' else 0
        self.engineering_inputs = engineering_inputs
        self.financial_inputs = financial_inputs
        self.hydrogen_production_yearly = self.engineering_inputs['H2'] * 365 * self.financial_inputs['availability']
        self.scenario = scenario

        hydrogen_production_kg_yearly = self.hydrogen_production_yearly * 1000
        electricity_demand_kwh_yearly = self.electricity_requirements[self.technology][0] * 1000 * 24 * 365 * \
                                        self.financial_inputs['availability']
        ratio_h2_kwh = hydrogen_production_kg_yearly / electricity_demand_kwh_yearly
#        print(f"The ratio of hydrogen production to electricity demand is {ratio_h2_kwh:.2f} kg H₂ per kWh.")
        

        # Pre-compute values for natural gas and biomass emissions
        natural_gas_demand_mmbtu_yearly = self.natural_gas_requirements[self.technology]
        natural_gas_demand_kwh_yearly = natural_gas_demand_mmbtu_yearly * 293.071
#        print(f"Calculated natural_gas_demand_kwh_yearly: {natural_gas_demand_kwh_yearly}")

#        carbon_capture_rate_complement = (self.carbon_intensity['stack'][self.technology]/self.carbon_intensity['stack']['AP SMR']) if self.technology == 'AP CCS' else 1

        self.natural_gas_carbon_emissions = natural_gas_demand_kwh_yearly * self.carbon_intensity['natural gas'] #* carbon_capture_rate_complement
#        print(f"natural_gas_demand_kwh_yearly: {natural_gas_demand_kwh_yearly}")
#        print(f"carbon_intensity['natural gas']: {self.carbon_intensity['natural gas']}")
#        print(f"Calculated natural_gas_carbon_emissions: {self.natural_gas_carbon_emissions}")

        #print("Debugging Natural Gas Intensity per kg of Hydrogen:")
        #print(f"hydrogen_production_kg_yearly: {hydrogen_production_kg_yearly}")
        self.natural_gas_intensity_kg_h2 = self.natural_gas_carbon_emissions / hydrogen_production_kg_yearly
#        print(f"Calculated natural_gas_intensity_kg_h2: {self.natural_gas_intensity_kg_h2}")

        biomass_carbon_emissions = self.biomass_requirement * self.carbon_intensity['biomass'] * 1000 # (Tonne biomass/yr)*(Kg CO2e/Kg biomass)*(1000Kg biomass/tonne biomass) = kg Co2/yr
       # print(f"Biomass Carbon Emissions (kg CO2/yr): {biomass_carbon_emissions}")
        self.biomass_intensity_kg_h2 = biomass_carbon_emissions / hydrogen_production_kg_yearly #kg CO2/yr  /  kg h2/yr
       # print(f"Biomass Intensity per kg H2 (kg CO2/kg H2): {self.biomass_intensity_kg_h2}")
       # print(f"Biomass Requirement: {self.biomass_requirement}")
       # print(f"Carbon Intensity of Biomass: {self.carbon_intensity.get('biomass')}")
       # print(f"Hydrogen Production (kg): {hydrogen_production_kg_yearly}")

        self.ratio_h2_kwh = ratio_h2_kwh

    def _convert_electricity_intensity(self, electricity_carbon_intensity):
        return electricity_carbon_intensity / self.ratio_h2_kwh

    def electricity_emissions(self, T):
        if self.scenario == 'A':
            data = self.aeo22_data
        elif self.scenario == 'B':
            data = self.aeo23_data
        elif self.scenario in ['C', 'D', 'New']:
            return 0
        else:
            raise ValueError(f"Invalid scenario: {self.scenario}. Scenario should be one of ['A', 'B', 'C', 'D', 'New']")
        electricity_carbon_intensity = (data[T] * list(self.carbon_intensity['electricity'].values())).sum()
        return self._convert_electricity_intensity(electricity_carbon_intensity)

    def natural_gas_emissions(self):
        return self.natural_gas_intensity_kg_h2

    def biomass_emissions(self):
        return self.biomass_intensity_kg_h2

    def stack_emissions(self):
        return self.carbon_intensity['stack'][self.technology]


    def total_emissions(self, T):
        return self.electricity_emissions(T) + self.natural_gas_emissions() + self.biomass_emissions() + self.stack_emissions()