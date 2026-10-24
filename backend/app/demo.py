"""Reproducible demonstration project.

Real data: NREL TMY3 weather for Washington Dulles International Airport (Ashburn/Loudoun County, Virginia —
the largest data-center market), bundled under data/reference/weather with SHA-256 checksums.

Everything else is an explicitly labeled ASSUMPTION for demonstration (IT load, profile, equipment
performance, soil properties, grid emission factor, tariffs). Replace them with project data.

Usage:  python -m app.demo [--site ashburn_va] [--no-run] [--analysis]
"""

from __future__ import annotations

import argparse
import json

from app.core.logging import configure_logging
from app.database.session import init_db, session_scope
from app.schemas.config import ProjectConfig

DEMO_NOTES = {
    "data": "Weather: real NREL TMY3 typical-year data (bundled). All other inputs are labeled assumptions.",
    "it": "ASSUMPTION: 5 MW installed IT, 65 % mean utilisation with ±5 % diurnal swing (no measured profile).",
    "grid": "ASSUMPTION: 0.35 kg CO2e/kWh annual average placeholder. Replace with the EPA eGRID subregion output rate "
            "(SRVC/RFCE as applicable) or a utility-specific factor before using carbon results.",
    "economics": "ASSUMPTION: electricity 0.08 USD/kWh and water 2.0 USD/m³ placeholders; capital costs not supplied (data gap).",
    "soil": "ASSUMPTION: generic soil properties (Incropera Table A.3); no geotechnical data.",
}

SITES = {
    "ashburn_va": {"name": "Ashburn data-center campus (demo)", "country": "USA", "latitude": 38.98, "longitude": -77.47,
                   "elevation_m": 82.0, "timezone": "America/New_York", "address": "Loudoun County, Virginia (illustrative)"},
    "phoenix_az": {"name": "Phoenix data-center campus (demo)", "country": "USA", "latitude": 33.45, "longitude": -111.98,
                   "elevation_m": 337.0, "timezone": "America/Phoenix", "address": "Maricopa County, Arizona (illustrative)"},
    "miami_fl": {"name": "Miami data-center campus (demo)", "country": "USA", "latitude": 25.82, "longitude": -80.30,
                 "elevation_m": 11.0, "timezone": "America/New_York", "address": "Miami-Dade County, Florida (illustrative)"},
}


def demo_config(site_id: str = "ashburn_va") -> ProjectConfig:
    site = SITES[site_id]
    return ProjectConfig(
        name=f"Demo — {site['name']}",
        description="Reproducible demonstration: real TMY3 weather; all loads, equipment, soil, grid and cost inputs are assumptions.",
        site={**site, "land_available_for_buried_m2": 12000.0, "site_area_m2": 60000.0},
        it={"installed_capacity_mw": 5.0, "average_utilisation": 0.65},
        grid={"emission_factor_kg_per_kwh": 0.35, "source": "ASSUMPTION placeholder — replace with eGRID subregion factor",
              "factor_type": "average", "accounting_basis": "location"},
        economics={"electricity_price_per_kwh": 0.08, "water_price_per_m3": 2.0, "currency": "USD", "reference_year": 2026},
        notes=DEMO_NOTES,
        sources={"site.latitude": "retrieved", "site.longitude": "retrieved", "site.elevation_m": "retrieved"},
    )


def create_demo(site_id: str = "ashburn_va", run: bool = True, analysis: bool = False, wait: bool = True) -> dict:
    from app.jobs import get_jobs
    from app.services import climate as climate_svc
    from app.services import projects as project_svc
    from app.services import simulations as sim_svc

    init_db()
    cfg = demo_config(site_id)
    with session_scope() as s:
        p = project_svc.create_project(s, cfg, is_demo=True, geocode_source="reference station coordinates")
        rec = climate_svc.from_reference(s, site_id, p.id, cfg.site.latitude, cfg.site.longitude, cfg.site.elevation_m)
        p.default_dataset_id = rec.id
        project_id, dataset_id = p.id, rec.id
    out = {"project_id": project_id, "dataset_id": dataset_id}
    if not run:
        return out
    with session_scope() as s:
        r = sim_svc.create_run(s, project_id, dataset_id, None, None)
        run_id = r.id
    job = get_jobs().submit("simulation", sim_svc.execute, run_id)
    out.update({"simulation_id": run_id, "job_id": job})
    if wait:
        j = get_jobs().wait(job, timeout_s=1800)
        out["status"] = j.status
        if j.status != "succeeded":
            out["error"] = j.error or j.message
            return out
    if analysis and wait:
        from app.services import analysis as an

        for kind in ("tornado", "monte_carlo"):
            with session_scope() as s:
                rec = an.create_sensitivity(s, {"simulation_id": run_id, "kind": kind, "n": 12, "n_category": 4})
                sid = rec.id
            jid = get_jobs().submit("sensitivity", an.execute_sensitivity, sid)
            out[f"{kind}_status"] = get_jobs().wait(jid, timeout_s=3600).status
    return out


def main():
    ap = argparse.ArgumentParser(description="Create the Second Nature demonstration project")
    ap.add_argument("--site", default="ashburn_va", choices=sorted(SITES))
    ap.add_argument("--no-run", action="store_true")
    ap.add_argument("--analysis", action="store_true", help="also run tornado and Monte Carlo uncertainty (slower)")
    args = ap.parse_args()
    configure_logging("WARNING")
    print(json.dumps(create_demo(args.site, run=not args.no_run, analysis=args.analysis), indent=2))


if __name__ == "__main__":
    main()
