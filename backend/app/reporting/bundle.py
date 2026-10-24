"""CSV (zip) and JSON exports with the same values as the stored simulation."""

from __future__ import annotations

import io
import json
import zipfile

import pandas as pd

from app.reporting.data import LIMITATIONS, ReportData
from app.services.climate import _jsonable
from app.services.simulations import comparison_frame


def json_bundle(d: ReportData) -> dict:
    run = d.run
    return _jsonable({
        "schema": "second-nature/simulation-export/1",
        "project": {"id": d.project.id, "name": d.project.name},
        "model_version": run.model_version, "app_version": d.app_version,
        "configuration": d.inputs.config, "config_hash": run.config_hash, "options": d.inputs.options,
        "assumption_register": d.inputs.assumption_register,
        "climate_dataset": {"id": d.dataset.id, "sha256": d.dataset.sha256, "provenance": d.dataset.provenance},
        "simulation": {"id": run.id, "created_at": run.created_at.isoformat(), "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                       "weights": run.weights},
        "scenarios": {sid: {"label": r.label, "strategies": r.strategies, "summary": r.summary, "model_parameters": r.diagnostics.get("sizing"),
                            "diagnostics": r.diagnostics, "flags": r.flags, "inputs_hash": r.inputs_hash, "hourly_sha256": r.hourly_sha256,
                            "config": r.config} for sid, r in d.results.items()},
        "comparison": run.comparison, "recommendation": run.recommendation, "hybrid_search": run.hybrid_search,
        "optimisations": [{"id": o.id, "request": o.request, "result": o.result} for o in d.optimisations],
        "sensitivity": [{"id": s.id, "kind": s.kind, "request": s.request, "result": s.result} for s in d.sensitivity],
        "limitations": LIMITATIONS,
    })


def csv_zip(d: ReportData) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("scenario_comparison.csv", comparison_frame(d.run).to_csv(index=False))
        for sid in d.scenario_ids:
            h = d.hourly(sid)
            z.writestr(f"hourly_{sid}.csv", h.to_csv())
            cols = [c for c in h.columns if c in ("time_local", "mode", "partial", "t_oa_c", "t_supply_c", "t_inlet_c")]
            z.writestr(f"hourly_{sid}_cooling_mode_schedule.csv", h[cols].to_csv())
            z.writestr(f"hourly_{sid}_energy_kwh.csv", (h[[c for c in h.columns if c.startswith("p_")]] / 1000.0).to_csv())
            z.writestr(f"hourly_{sid}_water_l.csv", h[[c for c in h.columns if c.startswith("wl_")]].to_csv())
            z.writestr(f"hourly_{sid}_carbon_kg.csv", h[["carbon_intensity_kg_kwh", "co2e_kg"]].to_csv())
        rows = []
        for s in d.sensitivity:
            if s.kind == "tornado":
                rows += [{"kind": "tornado", **{k: v for k, v in r.items()}} for r in (s.result or {}).get("rows", [])]
            elif s.kind == "monte_carlo":
                for m, v in (s.result or {}).get("metrics", {}).items():
                    rows.append({"kind": "monte_carlo", "metric": m, **{f"saving_{k}": x for k, x in v.get("saving", {}).items()},
                                 "prob_design_better": v.get("prob_design_better")})
        z.writestr("sensitivity.csv", pd.DataFrame(rows).to_csv(index=False) if rows else "no sensitivity analysis stored\n")
        z.writestr("README.txt", "Second Nature export. Units: power in W (energy files in kWh per hour), water in litres per hour, "
                                 "temperatures in °C, timestamps in UTC (time_local column = site local time).\n"
                                 f"Simulation {d.run.id}, model {d.run.model_version}, config hash {d.run.config_hash}.\n")
        z.writestr("metadata.json", json.dumps(json_bundle(d)["simulation"] | {"config_hash": d.run.config_hash,
                                                                                  "dataset_sha256": d.dataset.sha256}, indent=2))
    return buf.getvalue()
