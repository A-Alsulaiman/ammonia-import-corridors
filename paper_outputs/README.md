# paper_outputs

Summary tables written by the scripts in `paper_figures/` from the full DCF outputs,
as used for the submitted manuscript.

| File | Written by | Used for |
|---|---|---|
| `npv_ci_country_quantile_summary_eligible_only.csv` | `npv_ci_pathway_boxwhiskers_with_no_carbon_effect.py` | NPV and carbon-intensity quantiles by country, pathway and year (Fig. 5, Table 5) |
| `npv_effects_cost_structure_country_breakdown_eligible_only.csv` | `generate_npv_effects_cost_structure_three_pathways_tight_labels_legend_eligible_only.py` | long-format cost-structure shares by country |
| `npv_effects_cost_structure_country_breakdown_wide_eligible_only.csv` | same | wide-format version |
| `npv_effects_cost_structure_group_summary_eligible_only.csv` | same | cost-structure shares by pathway and year (Section 4.2.5) |
| `shipping_bau_port_level_values_usd_cost.csv` | `generate_shipping_bau_cost_emissions_bars_usd_rate_1p0821.py` | shipping cost and emissions per export port |
| `shipping_bau_country_quantile_summary_usd_cost.csv` | same | country-level shipping quantiles (Fig. 4) |
| `best_10_locations_lcoe_vs_transport_all_countries.csv` | `generate_best_locations_ovals_lcoe_vs_transport.py` | top-ten locations per country (Table 4) |
| `all_points_with_best10_flag.csv` | same | every candidate cell with a top-ten flag |

"Eligible only" means the gas-based pathways of countries classified red in the
traffic-light screen (Table 3) are excluded, as in the paper.
