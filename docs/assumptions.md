# Assumptions, limitations and future work

Every configuration value is tagged *user*, *default-assumption*, *measured*, *quoted*, *derived* or
*retrieved* (`ProjectConfig.sources`). The UI marks default assumptions with ⚠ *assumed*; the report and
exports list them.

## Highest-impact assumptions

| Assumption | Where | Why it matters |
| --- | --- | --- |
| IT load profile (utilisation, diurnal/weekly swing) | `it.*` | Scales every energy, water and carbon result |
| Server ΔT and inlet set-point; ASHRAE class and recommended vs allowable envelope | `it.*` | Decide free-cooling eligibility and airflow |
| Chiller rated COP (or default by architecture) and Carnot-fraction model | `baseline.*` | Mechanical energy; manufacturer curves should replace defaults |
| Containment bypass and recirculation (full 5 %/0.5 K, partial 20 %/2 K) | `simulation.engine.CONTAINMENT` | Fan energy and inlet temperatures |
| Discharge and pressure coefficients, loss coefficients for stacks | `strategies.stack.*` | Natural-ventilation flow; requires CFD and wind-tunnel or literature verification |
| Soil conductivity, density, heat capacity, moisture | `strategies.eahe.soil_*` | EAHE capacity and long-term drift; requires a thermal response test |
| Grid emission factor (average, annual unless supplied hourly) | `grid.*` | Operational carbon; marginal factors can differ greatly |
| Tariffs | `economics.*` | Operating cost only |
| Unit rates and contingency | `economics.unit_rates`, `contingency_fraction` | CAPEX; the demo values are illustrative only |
| Layout ratios (white space per rack, M&E per kW, equipment per m³/s or kW) | `layout.*` | Space estimate (±30 % range shown) |
| Typical-year weather | climate dataset | Understates extremes; check design conditions separately |

## Modelling limitations

* **Lumped models:** the zonal airflow model cannot predict rack-level inlet temperatures or local
  recirculation.
* **Earth-air heat exchanger:** a single representative pipe with a shape-factor far field. No
  groundwater flow, soil moisture migration or freezing.
* **Controller:**
  * hourly resolution; transients within the hour are not represented
  * minimum run time can hold an outdoor-air mode for one hour after a humidity jump, so the coil
    dehumidifies outdoor air (visible as a higher peak coil load in some hybrids)
* **Plant sizing:** the same plant is sized for every scenario without passive credit, so passive
  strategies do not reduce chiller capacity or CAPEX unless the design-hour load itself changes.
* **Cost model:**
  * covers only the listed line items
  * excludes structural reinforcement, fire and smoke control for shafts, acoustic treatment, land,
    fees, financing and escalation
  * no learning or scale effects
* **Embodied carbon:** only added strategy materials, and only with user emission factors. The base
  building and plant are excluded, so lifecycle carbon is incomplete by design.
* **Space model:** planning ratios, not a layout. Wall build-up thickness and circulation outside the
  stated categories are not modelled.
* **Water:** operational site water only; water embedded in electricity generation is excluded.
* **Biomimetic analogies:** they are inspiration; performance claims rest only on the engineering
  models.

## Future work

* Calibration against metered facility data (energy, water, inlet temperatures), and model-form
  uncertainty from that comparison.
* CFD coupling or co-simulation for stack-ventilated halls; an airflow network with multiple hall zones.
* Multi-pipe EAHE fields with groundwater advection, and thermal-response-test import.
* Hourly marginal emission factors and market-based accounting with contract matching.
* Cost databases with regional indices (licensed data), and replacement and maintenance schedules per
  line item.
* Embodied carbon of the base building and plant (EPD import).
* Sub-hourly controller simulation for transient events (cooling failure ride-through).
* Optimisation over strategy combinations with the impact engine's cost and space objectives.
