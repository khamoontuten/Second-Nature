"""Optimisation and uncertainty jobs (persisted in optimization_runs / sensitivity_results)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app import MODEL_VERSION
from app.core.errors import NotFound, ValidationFailure
from app.database.models import OptimizationRun, SensitivityResult
from app.database.session import session_scope
from app.optimization.search import DEFAULT_VARIABLES, Variable, optimise
from app.schemas.config import ProjectConfig
from app.services import climate as climate_svc
from app.services.climate import _jsonable
from app.services.projects import current_config, get_project
from app.services.simulations import get_run, scenario_result
from app.simulation.scenarios import ALL_OFF, derive, improved_patch
from app.uncertainty.analysis import monte_carlo, rank_stability, tornado


def _design_config(s: Session, project_id: str, base_on: str, extra_patch: dict | None, simulation_id: str | None,
                   scenario_id: str | None) -> ProjectConfig:
    if simulation_id and scenario_id:
        return ProjectConfig(**scenario_result(s, simulation_id, scenario_id).config)
    cfg = current_config(s, get_project(s, project_id))
    if base_on == "S1":
        base = derive(cfg, {"strategies": ALL_OFF})
        cfg = derive(base, improved_patch(base))
    if extra_patch:
        cfg = derive(cfg, extra_patch)
    return cfg


def create_optimisation(s: Session, req: dict) -> OptimizationRun:
    p = get_project(s, req["project_id"])
    dataset_id = req.get("dataset_id") or p.default_dataset_id
    if not dataset_id:
        raise ValidationFailure("no climate dataset selected")
    variables = req.get("variables")
    if not variables:
        keys = req.get("strategies") or ["stack"]
        variables = [v.__dict__ for k in keys for v in DEFAULT_VARIABLES.get(k, [])]
        if not variables:
            raise ValidationFailure(f"no default variables for strategies {keys}")
    for v in variables:
        Variable(**v)  # validate early
    run = OptimizationRun(project_id=p.id, dataset_id=dataset_id, status="queued", request={**req, "variables": variables},
                          model_version=MODEL_VERSION)
    s.add(run)
    s.flush()
    return run


def execute_optimisation(opt_id: str, *, progress, check_cancel, job_id):
    with session_scope() as s:
        run = s.get(OptimizationRun, opt_id)
        run.status, run.job_id = "running", job_id
        req = dict(run.request)
        enable = {"strategies": {k: {"enabled": True} for k in (req.get("strategies") or []) if k != "economiser"}}
        if "economiser" in (req.get("strategies") or []):
            enable["strategies"]["economiser"] = {"enabled": True}
        cfg = _design_config(s, run.project_id, req.get("base_on", "S1"), _merge_patches(enable, req.get("patch")),
                             req.get("simulation_id"), req.get("scenario_id"))
        ds = climate_svc.load_dataset(s, run.dataset_id)
    try:
        res = optimise(cfg, ds, [Variable(**v) for v in req["variables"]], mode=req.get("mode", "constrained"),
                       objective=req.get("objective", "facility_mwh"), water_cap_m3=req.get("water_cap_m3"),
                       method=req.get("method", "auto"), budget=int(req.get("budget", 30)), cancel_cb=check_cancel,
                       progress_cb=progress)
        with session_scope() as s:
            run = s.get(OptimizationRun, opt_id)
            run.result = _jsonable(res.to_dict())
            run.status = "succeeded"
        return {"optimization_id": opt_id}
    except Exception:
        with session_scope() as s:
            s.get(OptimizationRun, opt_id).status = "failed"
        raise


def _merge_patches(a: dict, b: dict | None) -> dict:
    from app.simulation.scenarios import _merge

    return _merge(a, b or {})


def get_optimisation(s: Session, opt_id: str) -> OptimizationRun:
    r = s.get(OptimizationRun, opt_id)
    if r is None:
        raise NotFound(f"optimisation {opt_id} not found")
    return r


def create_sensitivity(s: Session, req: dict) -> SensitivityResult:
    if req.get("kind") not in ("tornado", "monte_carlo", "rank_stability"):
        raise ValidationFailure("kind must be tornado, monte_carlo or rank_stability")
    run = get_run(s, req["simulation_id"])
    if run.status != "succeeded":
        raise ValidationFailure("simulation has not finished successfully")
    r = SensitivityResult(project_id=run.project_id, simulation_id=run.id, kind=req["kind"], status="queued", request=req)
    s.add(r)
    s.flush()
    return r


def execute_sensitivity(sens_id: str, *, progress, check_cancel, job_id):
    with session_scope() as s:
        rec = s.get(SensitivityResult, sens_id)
        rec.status, rec.job_id = "running", job_id
        req = dict(rec.request)
        run = get_run(s, rec.simulation_id)
        ds = climate_svc.load_dataset(s, run.dataset_id)
        base = ProjectConfig(**scenario_result(s, run.id, req.get("baseline", "S0")).config)
        design_id = req.get("scenario_id") or ("S7" if any(r.scenario_id == "S7" for r in run.results) else "S1")
        design = ProjectConfig(**scenario_result(s, run.id, design_id).config)
        others = {r.scenario_id: ProjectConfig(**r.config) for r in run.results if r.scenario_id in (req.get("compare") or [])}
    try:
        if req["kind"] == "tornado":
            out = tornado(base, design, ds, metric=req.get("metric", "facility_mwh"), cancel_cb=check_cancel, progress_cb=progress)
        elif req["kind"] == "monte_carlo":
            out = monte_carlo(base, design, ds, n=int(req.get("n", 20)), n_category=int(req.get("n_category", 6)),
                              by_category=bool(req.get("by_category", True)), cancel_cb=check_cancel, progress_cb=progress)
        else:
            designs = others or {"S0": base, design_id: design}
            designs.setdefault(design_id, design)
            out = rank_stability(designs, ds, n=int(req.get("n", 10)), cancel_cb=check_cancel, progress_cb=progress)
        out["design_scenario"] = design_id
        with session_scope() as s:
            rec = s.get(SensitivityResult, sens_id)
            rec.result = _jsonable(out)
            rec.status = "succeeded"
        return {"sensitivity_id": sens_id}
    except Exception:
        with session_scope() as s:
            s.get(SensitivityResult, sens_id).status = "failed"
        raise


def get_sensitivity(s: Session, sens_id: str) -> SensitivityResult:
    r = s.get(SensitivityResult, sens_id)
    if r is None:
        raise NotFound(f"sensitivity analysis {sens_id} not found")
    return r
