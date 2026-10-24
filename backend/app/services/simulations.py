"""Simulation runs: job execution, persistence (Parquet + DB), retrieval and reproduction."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app import MODEL_VERSION
from app.climate import features
from app.core.config import get_settings
from app.core.errors import NotFound, ValidationFailure
from app.database.models import ScenarioResult, SimulationInput, SimulationRun
from app.database.session import session_scope
from app.recommendation.engine import StrategyEvaluation, evaluated_from_scenarios, recommend
from app.schemas.config import ProjectConfig, assumption_register
from app.services import climate as climate_svc
from app.services.climate import _jsonable
from app.services.projects import current_version, get_project
from app.simulation.scenarios import SCENARIO_META, hybrid_candidates, run_scenarios
from app.strategies.library import compatibility_issues

ALL = list(SCENARIO_META)


def create_run(s: Session, project_id: str, dataset_id: str | None, scenarios: list[str] | None, weights: dict | None) -> SimulationRun:
    p = get_project(s, project_id)
    dataset_id = dataset_id or p.default_dataset_id
    if not dataset_id:
        raise ValidationFailure("no climate dataset selected for this project — retrieve, upload or choose reference data first")
    rec = climate_svc.get_record(s, dataset_id)
    v = current_version(s, p)
    sc = scenarios or ALL
    bad = [x for x in sc if x not in SCENARIO_META]
    if bad:
        raise ValidationFailure(f"unknown scenarios {bad}; valid: {ALL}")
    if "S7" in sc and "S1" not in sc:
        sc = ["S1", *sc]
    run = SimulationRun(project_id=p.id, config_version_id=v.id, dataset_id=dataset_id, status="queued", model_version=MODEL_VERSION,
                        config_hash=v.config_hash, dataset_sha256=rec.sha256, scenarios=sc, weights=weights)
    s.add(run)
    s.flush()
    cfg = ProjectConfig(**v.config)
    s.add(SimulationInput(run_id=run.id, config=v.config, options={"scenarios": sc, "weights": weights, "dataset_id": dataset_id},
                          assumption_register=_jsonable(assumption_register(cfg))))
    return run


def execute(run_id: str, *, progress, check_cancel, job_id):
    with session_scope() as s:
        run = s.get(SimulationRun, run_id)
        run.status = "running"
        run.job_id = job_id
        inp = s.query(SimulationInput).filter_by(run_id=run_id).one()
        cfg = ProjectConfig(**inp.config)
        ds = climate_svc.load_dataset(s, run.dataset_id)
        scenarios, weights = list(run.scenarios), run.weights
    try:
        hourly_intensity = None
        if cfg.grid.hourly_dataset_id:
            hourly_intensity = _hourly_intensity(cfg.grid.hourly_dataset_id, ds)
        out = run_scenarios(cfg, ds, include=scenarios, weights=weights, cancel_cb=check_cancel,
                            progress_cb=lambda f, m: progress(0.05 + 0.80 * f, m), hourly_intensity=hourly_intensity)
        progress(0.88, "recommendation")
        clim = features.summarise_climate(ds, elevation_m=cfg.site.elevation_m, timezone=cfg.site.timezone)
        rec = None
        if "S7" in scenarios and out.get("candidate_runs"):
            ref, evals = evaluated_from_scenarios(out)
            for name, patch in hybrid_candidates(cfg, filter_water=False).items():
                if name not in evals:
                    from app.simulation.scenarios import derive

                    c = derive(out["s1_config"], patch)
                    evals[name] = StrategyEvaluation(name, {}, [], compatibility_issues(c) or
                                                     [{"level": "conflict", "message": "excluded by site water limit"}], c, {})
            rec = recommend(cfg, ds, clim, evaluated=(ref, evals))
        progress(0.93, "saving results")
        results_dir = get_settings().results_dir / run_id
        results_dir.mkdir(parents=True, exist_ok=True)
        with session_scope() as s:
            run = s.get(SimulationRun, run_id)
            for sid, r in out["runs"].items():
                path = results_dir / f"{sid}_hourly.parquet"
                r.result.hourly.to_parquet(path)
                sha = hashlib.sha256(path.read_bytes()).hexdigest()
                sm = r.result.summary
                s.add(ScenarioResult(run_id=run_id, scenario_id=sid, label=r.label, strategies=r.strategies, pue=sm["pue"]["annual"],
                                     facility_mwh=sm["energy_mwh"]["facility"], cooling_mwh=sm["energy_mwh"]["cooling"],
                                     water_withdrawal_m3=sm["water"]["withdrawal_m3"], co2e_t=sm["carbon"]["operational_t"],
                                     unmet_hours=sm["thermal"]["unmet_hours"], hourly_path=str(path), hourly_sha256=sha,
                                     summary=_jsonable(sm), diagnostics=_jsonable(_strip(r.result.diagnostics)),
                                     flags=_jsonable(r.result.flags), config=r.config.model_dump(mode="json"),
                                     inputs_hash=r.result.inputs_hash))
            run.comparison = _jsonable(out["comparison"])
            run.recommendation = _jsonable(rec) if rec else None
            run.hybrid_search = _jsonable(out["hybrid_search"])
            run.climate_summary = _jsonable(clim)
            run.weights = out["weights"]
            run.results_dir = str(results_dir)
            run.status = "succeeded"
            run.finished_at = datetime.now(UTC)
        return {"simulation_id": run_id}
    except Exception as e:
        with session_scope() as s:
            run = s.get(SimulationRun, run_id)
            run.status = "failed" if type(e).__name__ != "JobCancelled" else "cancelled"
            run.error = str(e)[:2000]
        raise


def _hourly_intensity(grid_dataset_id: str, ds) -> np.ndarray:
    """Align an uploaded/fetched hourly grid-intensity series to the climate hours (NaN where unmatched)."""
    from app.climate.grid import align_to
    from app.database.models import GridEmissionsDataset

    with session_scope() as s:
        rec = s.get(GridEmissionsDataset, grid_dataset_id)
        if rec is None:
            raise NotFound(f"grid emissions dataset {grid_dataset_id} not found")
        series = pd.read_parquet(rec.path)["kg_per_kwh"]
    arr, _info = align_to(ds.data.index, series)
    return arr


def _strip(d):
    """Drop bulky/non-serialisable diagnostics before storing as JSON."""
    if isinstance(d, dict):
        return {k: _strip(v) for k, v in d.items() if k not in ("final_theta", "t_ground_c")}
    if isinstance(d, np.ndarray):
        return None
    return d


def get_run(s: Session, run_id: str) -> SimulationRun:
    run = s.get(SimulationRun, run_id)
    if run is None:
        raise NotFound(f"simulation {run_id} not found")
    return run


def run_dict(s: Session, run: SimulationRun, full: bool = False) -> dict:
    d = {"id": run.id, "project_id": run.project_id, "dataset_id": run.dataset_id, "status": run.status, "job_id": run.job_id,
         "model_version": run.model_version, "config_hash": run.config_hash, "dataset_sha256": run.dataset_sha256,
         "scenarios": run.scenarios, "created_at": run.created_at.isoformat(), "error": run.error,
         "finished_at": run.finished_at.isoformat() if run.finished_at else None}
    if full and run.status == "succeeded":
        d["comparison"] = run.comparison
        d["recommendation"] = run.recommendation
        d["hybrid_search"] = run.hybrid_search
        d["weights"] = run.weights
        d["scenario_results"] = [{"scenario_id": r.scenario_id, "label": r.label, "strategies": r.strategies, "summary": r.summary,
                                  "diagnostics": r.diagnostics, "flags": r.flags, "inputs_hash": r.inputs_hash,
                                  "hourly_sha256": r.hourly_sha256} for r in sorted(run.results, key=lambda x: x.scenario_id)]
    return d


def scenario_result(s: Session, run_id: str, scenario_id: str) -> ScenarioResult:
    run = get_run(s, run_id)
    for r in run.results:
        if r.scenario_id == scenario_id:
            return r
    raise NotFound(f"scenario {scenario_id} not in simulation {run_id}")


def hourly(s: Session, run_id: str, scenario_id: str, columns: list[str] | None = None, start: str | None = None,
           end: str | None = None) -> pd.DataFrame:
    r = scenario_result(s, run_id, scenario_id)
    path = Path(r.hourly_path)
    if not path.exists():
        raise NotFound("hourly results file missing from the data directory")
    df = pd.read_parquet(path, columns=None)
    if start:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df.index <= pd.Timestamp(end, tz="UTC")]
    if columns:
        df = df[[c for c in columns if c in df.columns]]
    return df


def reproduce(s: Session, run_id: str, scenario_id: str = "S0") -> dict:
    """Re-run a stored scenario from its saved configuration and dataset; compare hashes and totals."""
    from app.simulation.engine import run_simulation

    r = scenario_result(s, run_id, scenario_id)
    run = get_run(s, run_id)
    ds = climate_svc.load_dataset(s, run.dataset_id)
    cfg = ProjectConfig(**r.config)
    res = run_simulation(cfg, ds, scenario=scenario_id)
    stored = pd.read_parquet(r.hourly_path)
    same = bool(np.allclose(stored["p_facility_w"].to_numpy(), res.hourly["p_facility_w"].to_numpy(), rtol=1e-9, atol=1e-6))
    return {"scenario_id": scenario_id, "inputs_hash_stored": r.inputs_hash, "inputs_hash_now": res.inputs_hash,
            "model_version_stored": run.model_version, "model_version_now": MODEL_VERSION,
            "dataset_sha256_stored": run.dataset_sha256, "dataset_sha256_now": ds.provenance.extra.get("sha256"),
            "facility_mwh_stored": r.facility_mwh, "facility_mwh_now": res.summary["energy_mwh"]["facility"],
            "hourly_identical": same, "reproduced": same and r.inputs_hash == res.inputs_hash}


def comparison_frame(run: SimulationRun) -> pd.DataFrame:
    rows = []
    for sid, row in (run.comparison or {}).items():
        rec = {"scenario": sid, "label": row["label"], "strategies": "+".join(row["strategies"]), "objective": row["objective"]}
        for k, v in row["metrics"].items():
            rec[k] = v
        for k, v in row["delta_pct"].items():
            rec[f"{k}_delta_pct"] = v
        rows.append(rec)
    return pd.DataFrame(rows)


def to_json_bytes(obj) -> bytes:
    return json.dumps(_jsonable(obj), indent=2, default=str).encode()
