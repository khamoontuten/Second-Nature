# Engineering models

All models are in SI units and run on an hourly time step, with sub-steps where noted. Each section
gives the governing relations, the implementation location and the main limitations. Validation
evidence is in [validation.md](validation.md); assumptions are in [assumptions.md](assumptions.md).

## Psychrometrics — `app/physics/psychrometrics.py`

ASHRAE Handbook—Fundamentals (2017) Ch. 1. The relations used are:

* saturation pressure: Hyland–Wexler
* humidity ratio: `W = 0.621945 p_w / (p − p_w)`
* enthalpy: `h = 1006 t + W (2 501 000 + 1860 t)` J/kg dry air
* specific volume: `v = 287.042 (t + 273.15)(1 + 1.607858 W) / p`
* station pressure from elevation
* wet bulb by iterative solution of the psychrometric equation, plus dew point and adiabatic mixing

Stull (2011) is available only as a labelled approximation, with errors −1 to +0.65 °C.

## Airflow network and stack ventilation — `app/physics/airflow.py`, `app/cooling/passive.py`

* Zones with temperature-dependent density.
* Links are orifices, `ṁ = C_d A √(2 ρ |Δp|)` (expressed as `K = 1/C_d² + ΣK`), with sign handling, or ducts.
* Stack pressure: `Δp = g H (ρ_o − ρ_i)`.
* Wind pressure: `½ ρ U_H² C_p`, using the ASHRAE power-law wind profile and Swami–Chandra wall C_p.
* Solution: batched Newton–Raphson on zone mass balances, verified against the analytical two-opening
  solution.
* The data-hall network has facade inlets, a contained hall with leakage, and chimneys with optional
  solar boost and fan assist.
* Natural flow is compared hourly with server airflow `ṁ = Q / (c_p ΔT)`. Where it falls short, fan
  assist is counted, or the mode is unavailable.
* **Limitation:** a lumped zonal model. It cannot resolve rack-level recirculation, so CFD is required.

## Ground temperature — `app/physics/ground.py`

Kusuda–Achenbach periodic model:
`T(z,t) = T_m − A_s e^{−z/δ} cos(ω(t − t₀) − z/δ)`, with damping depth `δ = √(2α/ω)`.

* Parameters are fitted to the air-temperature record, or to measured soil layers when supplied.
* Soil properties: Incropera Table A.3 values when no site data exist (labelled assumptions).

## Earth-air heat exchanger — `app/physics/earth_air.py`

* **Air side:** segmented along the pipe; each segment uses
  `T_out = T_w + (T_in − T_w) e^{−UA/(ṁ c_p)}`, with the Gnielinski convection coefficient.
* **Soil:** implicit radial finite differences in disturbance form around each pipe. The far field uses
  the Incropera row-of-cylinders shape factor, which represents interference between pipes.
* **Multi-year operation:** the year is repeated to show soil saturation and drift.
* **Condensation:** occurs when the wall is below the dew point.
* **Hydraulics:** Haaland friction factor, bends and filter losses; fan power `P = V̇ Δp / η`. Fan heat
  is added to the air.
* **Coupling:** wind/stack assist and the controller's operating hours are coupled through a
  fixed-point iteration with the hourly simulation.

## Envelope — `app/physics/envelope.py`, `app/simulation/envelope_model.py`

* Multilayer 1-D implicit finite-difference conduction for roof, walls and slab.
* Exterior boundary: sol-air with wind-dependent convection and long-wave exchange to the sky.
* Interior: coupled to the hall.
* The periodic response (decrement factor, time lag, periodic transmittance) is checked against an
  ISO 13786-style transmission matrix.
* Solar and roof fluxes are computed in one place, so cool-roof and shell effects are not double counted.

## Solar, shading and radiative cooling — `app/physics/solar.py`, `app/physics/radiative.py`

* Solar position: pvlib (NREL SPA). Facade irradiance: isotropic sky plus ground reflection.
* Fixed overhangs and fins, and kinetic louvres, which close above a beam-irradiance threshold, lock
  out in high wind, and consume actuator energy.
* Sky temperature: from the EPW horizontal infrared, or Berdahl–Martin with a cloud correction.
* Radiative panels: net flux against fluid temperature, applied to chilled-water return pre-cooling.

## Evaporative cooling — `app/physics/evaporative.py`

* **Direct evaporative cooling:** isenthalpic; `T_out = T_in − ε (T_in − T_wb)`; water = `ṁ ΔW` plus
  blowdown.
* **Indirect evaporative cooling:** product air is not humidified. The secondary air leaves at
  `T_p,in − 3 K` (counter-flow approach), clamped to saturation; heat and mass balance on the
  secondary side.
* **Cooling tower:** air-side enthalpy balance with saturated exhaust; evaporation, blowdown
  (cycles of concentration) and drift.

## Conventional plant — `app/cooling/conventional.py`, `app/cooling/plant.py`

* **Chillers:** Carnot-fraction model calibrated to rated COP at AHRI conditions, or user biquadratic
  EIR curves. Minimum condensing temperature 25 °C (air-cooled) / 20 °C (water-cooled); COP capped
  at 10. Part-load curve, staging, and N+1 sizing for the design hour plus margin.
* **Fans:** affinity law with part-load exponent 2.7; redundant units share flow.
* **Pumps:** `P = V̇ Δp / η`. **UPS:** loss curve.

The same sizing rule applies to every scenario, so no scenario gains capacity or loses redundancy.

## Supervisory controller — `app/cooling/hybrid.py`, `app/cooling/controls.py`

Every hour, each available mode is evaluated with its electricity and water use:

* mechanical
* mechanical with evaporative pre-cooling
* direct or natural (stack) economiser
* earth-air pre-cooled intake
* indirect economiser
* indirect evaporative
* direct evaporative

Modes that violate the server-inlet envelope are excluded. The envelope is ASHRAE TC 9.9 class
recommended or allowable; for example A1 recommended is 18–27 °C, dew point −9 to 15 °C, RH ≤ 70 %.
Modes that exceed airflow, capacity or water limits are also excluded.

The chosen mode minimises a weighted objective, subject to hysteresis (0.5 K deadband) and a minimum
run time of 2 h. An emergency fallback reports unmet hours. The hall air balance uses bypass and
recirculation by containment level (full 5 % / 0.5 K, partial 20 % / 2 K, none 40 % / 4 K).

## Accounting — `app/accounting/`

* **PUE:** ratio of annual energies (the mean of hourly ratios is also reported).
* **WUE:** site water (withdrawal or consumption) ÷ IT energy.
* **Operational carbon:** facility electricity × grid factor (time-matched when an hourly series is
  supplied).
* **Heat-rejection paths and energy-balance closure:** checked hourly.
* **Lifecycle:** NPV and payback only with supplied costs.

## Strategy impact analysis — `app/impacts/`

Quantity take-off, unit-rate CAPEX, OPEX, the space estimate, status labels and the non-additivity
diagnostic are described in [impact_analysis.md](impact_analysis.md).

## Optimisation, uncertainty, recommendation — `app/optimization/`, `app/uncertainty/`, `app/recommendation/`

* **Optimisation:** grid or Latin-hypercube search and SciPy differential evolution with hard
  constraints; non-dominated sorting over energy, water, carbon, land, complexity and cost (when
  supplied).
* **Uncertainty:** one-at-a-time tornado; paired Monte Carlo with common random numbers and separated
  uncertainty sources; rank stability.
* **Recommendation:** each strategy is simulated on the improved-conventional design (S1) and ranked
  by min–max normalised criteria with stated weights. Explanations are generated from the computed
  numbers.
