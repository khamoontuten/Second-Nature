# Second Nature

Engineering decision support for biomimetic passive and low-energy data-center cooling. Second Nature
compares a conventional reference data center with designs that add termite-mound stack ventilation,
shell-like envelopes, fish-scale facades, prairie-dog-burrow earth-air heat exchangers, solar chimneys,
thermal storage, radiative and evaporative cooling. Comparisons use real hourly climate data, explicit
physics models, hard thermal constraints and consistent energy, water, carbon, cost and space accounting.

> **Status:** screening-level decision support. Results depend on stated assumptions. The platform has not
> been validated for construction and is not certified for regulatory compliance. Detailed design, CFD,
> geotechnical investigation and commissioning remain necessary.

## What it does

| Page | Purpose |
| --- | --- |
| 1 · Project setup | site, map, IT load, building, baseline plant, strategies, grid, water and economics; every input tagged *user* or *assumed* |
| 2 · Climate | provenance, quality control, hourly/monthly climate, psychrometrics, wind, solar, ground, grid and water context |
| 3 · Free cooling | eligibility vs delivered free cooling, operating modes, residual mechanical demand |
| 4 · Strategies | biomimetic strategy library, simulation-based ranking, user-defined design (S8) |
| **5 · Impact analysis** | each strategy alone vs one baseline, any combination simulated as a whole, CAPEX and space estimates, attribution and pairwise interactions ([docs/impact_analysis.md](docs/impact_analysis.md)) |
| 6 · Calculator | live psychrometric, airflow, stack, earth-air and envelope calculations with intermediate values |
| 7 · Comparison | scenarios S0–S8 on identical climate, load and redundancy |
| 8 · Optimisation | Pareto search, tornado, Monte Carlo, rank stability, recommendation |
| 9 · Report | PDF, Excel (14 sheets), CSV, JSON and ZIP exports; reproducibility check |

## Quick start (workstation, SQLite)

Requirements: Python ≥ 3.11 (tested with 3.13) and Node.js 22.

```bash
# backend
cd backend
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload            # API on http://127.0.0.1:8000, OpenAPI docs at /docs

# frontend (second terminal)
cd frontend
npm ci
npm run dev                                         # http://localhost:5173 (proxies /api to :8000)
```

Database migrations run automatically at start-up; to run them manually, use `cd backend && .venv/bin/alembic upgrade head`.
To serve everything from one port, build the frontend (`cd frontend && npm run build`). FastAPI then
serves `frontend/dist` at http://127.0.0.1:8000.

### Demonstration project

The demo uses real NREL TMY3 weather for Washington Dulles (Ashburn, Virginia) bundled in
`data/reference/weather`. Everything else is a labelled assumption: IT load, equipment, soil, grid
factor, tariffs and illustrative unit rates.

```bash
cd backend
.venv/bin/python -m app.demo                       # create the project and run scenarios S0–S8 (~1 min)
.venv/bin/python -m app.demo --site phoenix_az     # contrast sites: phoenix_az, miami_fl
.venv/bin/python -m app.demo --analysis            # also tornado + Monte Carlo
```

Alternatively, use the demo buttons on the project list. The API equivalent is
`POST /api/demo?site_id=ashburn_va` (add `&run=false` to only create the project).

## Configuration

Copy `.env.example` to `.env`. All variables use the `SN_` prefix:

* database URL (SQLite by default, PostgreSQL/PostGIS supported)
* data directory and contact e-mail for public APIs
* provider order
* `SN_CDS_API_KEY` (Copernicus ERA5) and `SN_ELECTRICITY_MAPS_API_KEY`
* rate limit and upload limits

Never commit `.env`. Secrets are never logged.

## Tests and checks

```bash
cd backend
.venv/bin/ruff check app tests
.venv/bin/pytest -q                                  # unit, benchmark, physical-sanity, integration, API and impact tests

cd ../frontend
npm run typecheck && npm run lint && npm test && npm run build
```

Browser end-to-end tests use Playwright with Chromium. Start a backend that serves the built frontend
with a throw-away data directory, then run them:

```bash
export SN_DATA_DIR=/tmp/sn-e2e SN_DATABASE_URL=sqlite:////tmp/sn-e2e/sn.db
mkdir -p $SN_DATA_DIR && (cd backend && .venv/bin/uvicorn app.main:app --port 8000 &)
cd frontend
node e2e/impacts.mjs     # strategy-impact acceptance workflow (baseline → single → second → combined → parameter change → export)
node e2e/workflow.mjs    # all pages on the demo project, incl. exports, reproducibility and dark mode
```

If Playwright is not installed, run `npm install --no-save playwright@1.63.0 && npx playwright install chromium`.
CI (`.github/workflows/ci.yml`) runs ruff, pytest, typecheck, lint, the build and both E2E scripts.

## Docker

```bash
cp .env.example .env            # set POSTGRES_PASSWORD and any API keys
docker compose up --build       # PostGIS + app on http://localhost:8000
docker compose --profile dev up # additionally a Vite dev server on :5173
```

The Docker files are provided but were **not executed** in the environment where this version was built
(no Docker daemon there). The PostGIS-aware migration was run against PostgreSQL 16 without PostGIS, and
against SQLite.

## Data sources

The climate adapters are:

* Open-Meteo historical archive
* NASA POWER
* Copernicus ERA5 / ERA5-Land (CDS key required)
* EPW and CSV/NetCDF uploads
* the bundled NREL TMY3 reference files

Each dataset records provider, product, coordinates, distance, elevation difference, time zone,
transformations, licence and checksum. See [docs/data_sources.md](docs/data_sources.md) for licences
and access. Network hosts that were blocked while this version was built are listed there.

## Documentation

* [docs/architecture.md](docs/architecture.md) — module map, data flow, conventions, accounting boundaries
* [docs/engineering_models.md](docs/engineering_models.md) — equations and model descriptions
* [docs/impact_analysis.md](docs/impact_analysis.md) — strategy impact engine, cost and space models, statuses
* [docs/data_sources.md](docs/data_sources.md) — providers, licences, keys, provenance
* [docs/validation.md](docs/validation.md) — benchmarks actually executed, with tolerances
* [docs/assumptions.md](docs/assumptions.md) — assumptions, limitations and future work

## Licence

Apache-2.0 (see [LICENSE](LICENSE)). The bundled weather files keep their own terms (see `data/reference/weather/catalog.json`).
