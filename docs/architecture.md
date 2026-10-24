# Second Nature — Architecture

Second Nature is a decision-support platform for preliminary data-center cooling design.
It compares a conventional reference facility against hybrid designs that add
biomimetic passive and low-energy cooling strategies, using site climate data and
explicit physical models. It is a screening tool. It does not replace detailed
mechanical design, CFD, geotechnical investigation or commissioning.

## 1. Repository audit (Phase 1)

The repository was empty at the start of this work (no commits). No existing stack had
to be preserved. The design brief "Thermimic — Platform Design Document" supplied by the
user was used as an input. Its four organism-to-feature mappings (burrow → earth-air
tunnels, termite mound → stack chimney, fish scales → kinetic facade, mollusc shell →
layered envelope and spiral intakes) are all implemented, alongside the wider strategy
library A–I from the master build prompt.

## 2. Environment constraints discovered

| Resource | Status in the build environment | Consequence |
| --- | --- | --- |
| Open-Meteo archive and geocoding APIs | Blocked by the session's egress policy (HTTP 403 at proxy) | Adapter implemented and tested against mocked HTTP. Works on any network that allows the host. |
| NASA POWER | Blocked | Same as above |
| Copernicus CDS (ERA5 / ERA5-Land) | Blocked; also needs user credentials | Request builder + NetCDF parser implemented; NetCDF upload path tested with fixture files |
| Nominatim geocoding | Blocked | Adapter implemented with caching, mocked in tests; manual coordinate entry always available |
| raw.githubusercontent.com | Reachable | Real NREL TMY3 weather files from the EnergyPlus repository used for the demonstration projects |
| Docker daemon | Not running | `docker-compose.yml` is provided but was not executed in this environment |
| PostgreSQL 16 | Installed, no PostGIS | Migrations are PostGIS-aware: geography column + GiST index created only if the extension is available |

## 3. Subsystems and module map

```
backend/app/
  core/          config, logging, units (pint), constants, ASHRAE environmental classes, errors
  climate/
    providers/   base interface + EPW, Open-Meteo, NASA POWER, CDS ERA5/NetCDF, CSV upload
    ingestion.py provider orchestration, retries/backoff, caching, fallbacks with provenance
    quality.py   duplicates, gaps, impossible values, outliers, cross-source comparison
    features.py  psychrometric derivation, statistics, heatwaves, wind rose, free-cooling runs
    store.py     Parquet dataset store with checksums
    grid.py      grid-carbon providers (Electricity Maps adapter, manual/static factor)
    water.py     water-stress context (Aqueduct CSV import, manual entry)
  physics/       independent, unit-tested models (no web or database imports)
    psychrometrics.py  ASHRAE Fundamentals Ch.1 formulations
    airflow.py         orifice/duct/fan elements + Newton airflow-network solver
    ground.py          Kusuda–Achenbach periodic ground temperature, soil diffusivity
    earth_air.py       segmented, transient earth-air heat exchanger (radial FD + shape factor)
    envelope.py        multilayer 1-D implicit finite-difference conduction, RC thermal mass
    solar.py           solar position (pvlib) and surface irradiance, shading
    radiative.py       sky temperature, roof surface balance, radiative cooling panels
    evaporative.py     direct/indirect evaporative cooling, heat-and-mass balances
    thermal_balance.py energy balance closure utilities
  cooling/
    conventional.py    chillers (Carnot-fraction or biquadratic EIR), towers, dry coolers, DX, CRAH, pumps, UPS
    passive.py         strategy wrappers: stack/wind ventilation, EAHE, envelope, facade, storage
    controls.py        hysteresis/min-run-time supervisory logic
    hybrid.py          hourly mode evaluation + constraint enforcement
  strategies/    biomimetic strategy library (metadata A–I, compatibility matrix)
  simulation/    load profiles, scenario definitions 0–8, simulation engine, caching
  accounting/    PUE, WUE, carbon, heat rejection, lifecycle cost and embodied carbon
  optimization/  grid search, differential evolution, Pareto frontier
  uncertainty/   Monte Carlo, one-at-a-time tornado, rank-stability analysis
  recommendation/ transparent multi-criteria ranking + data-driven explanations
  reporting/     PDF (ReportLab + Matplotlib), Excel (openpyxl), CSV/JSON exports
  database/      SQLAlchemy models, session management
  api/           FastAPI routers (OpenAPI docs at /docs)
  jobs.py        background job manager (thread pool, progress, cancellation, DB-persisted status)
frontend/src/    React + TypeScript workbench (pages, charts, maps, forms, api, types)
```

The physics package has no dependency on FastAPI, the database or the frontend. The
simulation engine depends only on physics, cooling, climate DataFrames and Pydantic
configuration objects, so it can run from scripts, tests and the API alike.

## 4. Data flow

```
site + config ──► climate ingestion ──► QC + feature engineering ──► canonical hourly table (UTC)
                         │                                              │
                   provenance record                                    ▼
                                      ┌──────── IT load profile ─► heat-load model
                                      ▼
            strategy pre-models (EAHE, envelope, facade, stack network, storage)
                                      ▼
            hourly supervisory controller (mode selection under hard constraints)
                                      ▼
            equipment models (chiller/tower/fans/pumps/UPS)  ─► hourly ledger
                                      ▼
            accounting (PUE, WUE, CO2e, heat balance, cost)  ─► scenario summaries
                                      ▼
     scenario comparison ─► optimization / uncertainty ─► recommendation ─► reports
```

## 5. Conventions

* **Units.** SI internally: temperature in °C for state variables (K wherever an absolute
  temperature appears in an equation), pressure in Pa, power in W, time step in s,
  airflow in m³/s and kg/s, humidity ratio in kg/kg dry air. Energy in the hourly ledger
  is stored in kWh as `power_W × Δt_s / 3.6e6`, which is the only non-SI accounting unit;
  water in litres (1 L = 1e-3 m³). Unit conversion happens only during ingestion
  (`core/units.py`, pint-based) and display.
* **Time.** All hourly indices are timezone-aware UTC. Local time is derived for
  reporting using the site's IANA time zone or the dataset's fixed UTC offset.
  EPW/TMY timestamps (local standard time, hour-ending) are shifted to hour-beginning
  UTC; the transformation is recorded in provenance.
* **Column naming.** Canonical climate columns carry unit suffixes
  (`t_db_c`, `t_dp_c`, `rh_pct`, `p_pa`, `wind_ms`, `wind_dir_deg`, `ghi_wm2`,
  `dni_wm2`, `dhi_wm2`, `ir_horiz_wm2`, `cloud_pct`, `precip_mm`, `t_soil_<depth>_c`).
* **Provenance.** Every dataset carries a `Provenance` record (provider, product,
  data nature: observed/reanalysis/typical-year/modeled/manual, coordinates requested vs
  actual, distance and elevation difference, resolution, licence, transformations).
  Every configuration value carries a `source` tag (`user`, `default-assumption`,
  `measured`, `quoted`, `derived`), surfaced in the UI and in reports.
* **Model versioning.** `app/__init__.py:MODEL_VERSION` is stored with every simulation
  together with the SHA-256 of the canonical configuration JSON and of the climate dataset,
  which makes each result reproducible.

## 6. Accounting boundaries

* IT boundary: electrical power delivered to IT equipment (servers, storage, network),
  measured at the PDU output.
* Facility boundary: IT + cooling (compressors, fans, pumps, towers, controls,
  actuators, humidifiers) + UPS losses + distribution losses + lighting + other loads.
* Cooling load (thermal): IT heat + the configured fraction of electrical losses and
  lighting released inside conditioned space + fan heat added to the air stream +
  envelope gains (signed) − heat removed directly by passive paths.
* Heat rejected to outdoors = cooling load handled by the plant + plant compressor/fan
  work + air-side economizer exhaust enthalpy; heat to ground and to storage are tracked
  separately. The hourly balance is checked to a documented tolerance.
* PUE = Σ E_facility / Σ E_IT over the same period (annual ratio of energies; the mean of
  hourly PUE is reported separately and labeled as such).
* WUE = site water use (L) / IT energy (kWh). Withdrawal and consumption are kept apart.

## 7. Persistence and jobs

* Relational metadata in SQLAlchemy models (PostgreSQL in production, SQLite for local
  development and tests). Migrations live in `backend/migrations` (Alembic).
* Hourly climate and simulation results are stored as Parquet files under
  `DATA_DIR`, referenced by path + checksum from the database.
* Long-running work (climate downloads, simulations, optimizations, Monte Carlo, reports)
  runs through `app/jobs.py`: an in-process thread pool with persisted status, progress
  and cooperative cancellation. This is enough for a single-node workstation deployment;
  a multi-worker deployment would swap in a Redis-backed queue behind the same interface.

## 8. Highest-risk engineering assumptions (tracked in docs/assumptions.md)

1. Natural ventilation in high-density halls: the airflow-network model is a lumped
   zonal model. It cannot resolve recirculation or hot spots; CFD is required before design.
2. Earth-air heat exchanger soil response depends strongly on soil conductivity and
   moisture, which are rarely known at concept stage; defaults are assumptions.
3. Chiller performance without manufacturer curves uses a Carnot-fraction model
   calibrated to the user's rated COP.
4. Grid carbon and water stress are often only available as annual/basin-level values.
5. TMY weather represents a typical year and understates extremes; design-day capacity
   checks use the dataset maximum and user-entered design conditions.
