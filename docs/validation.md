# Verification and validation

This page lists only checks that are **executed by the automated test suite** (`backend/tests`,
`frontend/e2e`), with their tolerances. Passing them shows the models behave as implemented and agree
with the stated references. It does **not** show that predictions match a built data center: no model
here has been calibrated against measured facility data, and the platform is not validated for
construction or certified for compliance.

Run: `cd backend && .venv/bin/pytest -q` (all backend tests), `cd frontend && npm test` (UI unit tests) and the two
browser scripts in `frontend/e2e/`.

## Benchmarks against independent references

| Model | Reference | Tolerance | Test |
| --- | --- | --- | --- |
| Saturation vapour pressure | IAPWS reference values | relative 1.5 × 10⁻³ | `test_psychrometrics.py::test_saturation_pressure_vs_iapws` |
| Pressure vs altitude | ASHRAE standard atmosphere table | ±0.01 kPa | `test_pressure_with_altitude` |
| Humidity ratio, enthalpy, density | psychrolib (independent implementation), random states | relative 10⁻⁶ | `test_against_psychrolib_random_states` |
| Wet bulb, dew point | psychrolib | ±0.002 K | same |
| Stull approximation | thermodynamic wet bulb | within Stull's published band −1.0 … +0.7 K | `test_stull_approximation_close_to_thermodynamic` |
| Two-opening stack flow | analytical solution | relative 10⁻⁶ | `test_airflow.py::test_network_matches_analytical_two_opening_stack` |
| Stack pressure | `ρ g H ΔT / T_i` | relative 10⁻⁹ | `test_stack_pressure_formula_and_sign` |
| Swami–Chandra C_p | published normal-incidence value 0.6 | relative 2 % | `test_swami_chandra_coefficients` |
| Ground temperature | analytical damping `A e^{−z/δ}` | relative 10⁻⁹ | `test_ground_amplitude_decays_and_lags_with_depth` |
| Ground model fit | synthetic harmonic record (recover T_m, A, phase) | ±10⁻⁶ K, ±0.05 d | `test_harmonic_fit_recovers_parameters` |
| EAHE, constant-wall limit | analytical exponential `T_w + (T_in − T_w) e^{−NTU}` | ±0.05 K | `test_constant_wall_limit_matches_analytical_exponential` |
| EAHE, steady state | air + wall + shape-factor resistances in series | relative 5 % | `test_steady_state_limit_matches_shape_factor` |
| Envelope periodic response | transmission-matrix (ISO 13786-style) solution | transmittance 5 %, lag ±0.5 h | `test_fd_periodic_response_matches_transmission_matrix` |
| Envelope steady state | `U A ΔT` | relative 1 % | `test_steady_state_u_value` |
| Solar position | known solstice zenith | ±0.5° | `test_solar_position_and_facade_irradiance` |
| Sky temperature from IR | Stefan–Boltzmann inversion | exact | `test_sky_temperature_models` |
| Chiller model | rated COP at AHRI rating points | relative 10⁻⁹ | `test_carnot_model_reproduces_rated_cop_at_rating_point` |
| DEC, ε = 1 | reaches wet bulb, isenthalpic, water = ṁ ΔW | ±10⁻⁹ | `test_dec_effectiveness_one_reaches_wet_bulb_and_is_isenthalpic` |
| Cooling tower | latent-heat bound ≈ 1.47 kg/kWh; make-up = evaporation + blowdown + drift | 1.0–2.0 L/kWh; exact balance | `test_cooling_tower_water_balance` |
| Pumps, UPS | `V̇ Δp / η`; loss curve | relative 10⁻⁶ / 3 % | `test_pump_and_ups_models` |

## Physical-sanity and conservation checks

* **Energy balance:** the facility energy balance closes hourly for every scenario; the annual residual
  is below 0.1 % of facility energy (`test_engine_sanity.py::test_energy_balance_closes`).
* **Envelope energy:** conserved (stored + transmitted = absorbed, relative 10⁻⁶) while thermal mass
  shifts the timing.
* **No negatives:** no negative electricity or water.
* **Thermal limits:** inlet temperatures stay within the envelope, and unmet load is reported when the
  plant is undersized.
* **Fair comparison:** identical IT workload and capacity across scenarios; PUE is the ratio of
  energies, with the mean of hourly ratios reported separately.
* **Monotonic behaviour:**
  * lower COP gives more cooling energy
  * adding an economiser never increases cooling energy
  * a cold, dry site has more free cooling than a hot, humid one
  * evaporative water scales with effectiveness
  * a dry configuration uses zero water
* **Plausible magnitudes (not calibration):** baseline PUE between 1.15 and 1.8, and water-cooled WUE
  between 0.8 and 3 L/kWh.
* **No double counting:** cool roof and shell are not double counted; N+1 capacity is respected.
* **Determinism:** results are identical from saved inputs; reproduction from stored runs, including
  runs stored before the configuration schema gained fields.

## Strategy impact analysis (`test_impacts.py`)

1. Baseline compared with itself → zero change for every non-zero metric.
2. Each applicable strategy is simulated alone (only it and its declared dependencies switched on).
   Every record shares the baseline period (8 760 h) and units.
3. A combination is simulated as one configuration: the spy on `run_simulation` sees the combined
   configuration, its result equals a direct simulation, and it differs from the sum of individual
   changes.
4. Shared infrastructure is counted once:
   * the outdoor-air intake equals the largest single need, not the sum
   * one supervisory controller
   * one intake plenum in the space estimate
5. Invalid combinations (alternative economisers; storage on a DX plant; unknown ids; out-of-range
   parameters) give infeasible results or errors with reasons.
6. Missing grid factor, unit rates or tariff → *insufficient data*, never zero.
7. Zero baselines give no percentage; near-zero water baselines give no percentage, with the reason.
8. Operational CO₂e = facility energy × factor; doubling the factor doubles CO₂e.
9. The energy boundary has no double counting: components sum to facility energy, and IT + cooling +
   other = facility.
10. Cost and space percentages use the common baseline; ranges bracket the base value.
11. Repeated evaluation is identical.
12. A parameter change (EAHE length) affects only scenarios containing that strategy; doubling the
    length doubles the underground area.
13. Payback and NPV appear only when maintenance and rates are supplied.
14. API study lifecycle and CSV/XLSX/JSON exports.

## Browser end-to-end checks

`frontend/e2e/impacts.mjs` runs the 14-step acceptance workflow through the UI:

* baseline → one strategy → a second strategy → both → parameter change → export
* compares every displayed number with the stored study
* checks that every study used the identical baseline and period
* checks that there are no console or API errors

`frontend/e2e/workflow.mjs` visits all pages of a simulated demo project. It checks chart rendering,
table views, the calculator's reaction to input changes, report export, reproducibility and dark mode.

## Not validated

The following are verified only as described above, not against field data:

* whole-building results (PUE, water, carbon)
* natural-ventilation sufficiency (no CFD)
* EAHE performance in real soils (no ground-truth soil or groundwater data)
* kinetic facade behaviour
* radiative panel performance (material-specific)
* cost and space estimates (planning ratios and user-supplied rates)
