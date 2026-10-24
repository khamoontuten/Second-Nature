"""Impact studies: creation, background execution and retrieval (persisted in impact_studies)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import MODEL_VERSION
from app.core.errors import NotFound, ValidationFailure
from app.database.models import ClimateDatasetRecord, ImpactStudy, ProjectConfigVersion
from app.database.session import session_scope
from app.impacts import catalogue
from app.impacts.engine import run_study
from app.schemas.config import ProjectConfig, assumption_register
from app.services import climate as climate_svc
from app.services.climate import _jsonable
from app.services.projects import current_config, current_version, get_project
from app.simulation.scenarios import baseline_config

KINDS = ("baseline", "individual", "combination", "pairwise")
MAX_PAIRWISE = 10


def create_study(s: Session, req: dict) -> ImpactStudy:
    p = get_project(s, req["project_id"])
    dataset_id = req.get("dataset_id") or p.default_dataset_id
    if not dataset_id:
        raise ValidationFailure("no climate dataset selected for this project")
    rec = s.get(ClimateDatasetRecord, dataset_id)
    if rec is None:
        raise NotFound(f"climate dataset {dataset_id} not found")
    kind = req.get("kind", "individual")
    if kind not in KINDS:
        raise ValidationFailure(f"kind must be one of {KINDS}")
    strategies = [] if kind == "baseline" else req.get("strategies")
    if kind in ("combination", "pairwise") and not strategies:
        raise ValidationFailure(f"a {kind} study needs a list of strategies")
    if kind == "combination" and len(strategies) < 1:
        raise ValidationFailure("select at least one strategy")
    if kind == "pairwise" and not (2 <= len(strategies) <= MAX_PAIRWISE):
        raise ValidationFailure(f"pairwise analysis needs 2–{MAX_PAIRWISE} strategies")
    try:
        if strategies:
            catalogue.select(strategies, req.get("parameters"))
    except catalogue.SelectionError as e:
        raise ValidationFailure(str(e)) from e
    v = current_version(s, p)
    st = ImpactStudy(project_id=p.id, dataset_id=dataset_id, kind=kind, status="queued",
                     request={"kind": kind, "strategies": strategies, "parameters": req.get("parameters") or {}},
                     config_hash=v.config_hash, config_version_no=v.version_no, dataset_sha256=rec.sha256, model_version=MODEL_VERSION)
    s.add(st)
    s.flush()
    return st


def _config_version(s: Session, project_id: str, version_no: int) -> ProjectConfig:
    v = s.scalar(select(ProjectConfigVersion).where(ProjectConfigVersion.project_id == project_id,
                                                    ProjectConfigVersion.version_no == version_no))
    if v is None:
        raise NotFound(f"configuration version {version_no} not found")
    return ProjectConfig(**v.config)


def execute(study_id: str, *, progress, check_cancel, job_id):
    from app.services.simulations import _hourly_intensity

    with session_scope() as s:
        st = s.get(ImpactStudy, study_id)
        st.status, st.job_id = "running", job_id
        cfg = _config_version(s, st.project_id, st.config_version_no)
        ds = climate_svc.load_dataset(s, st.dataset_id)
        req = dict(st.request)
        dataset_id = st.dataset_id
    try:
        hi = _hourly_intensity(cfg.grid.hourly_dataset_id, ds) if cfg.grid.hourly_dataset_id else None
        out = run_study(cfg, ds, req["kind"], req.get("strategies"), req.get("parameters"), hourly_intensity=hi,
                        progress_cb=progress, cancel_cb=check_cancel)
        out["dataset"] = {"id": dataset_id, "provider": ds.provenance.provider,
                          "product": ds.provenance.product, "data_nature": ds.provenance.data_nature,
                          "sha256": ds.provenance.extra.get("sha256")}
        with session_scope() as s:
            st = s.get(ImpactStudy, study_id)
            st.result = _jsonable(out)
            st.status = "succeeded"
        return {"impact_study_id": study_id}
    except Exception as e:
        with session_scope() as s:
            st = s.get(ImpactStudy, study_id)
            st.status, st.error = "failed", str(e)[:2000]
        raise


def get_study(s: Session, study_id: str) -> ImpactStudy:
    st = s.get(ImpactStudy, study_id)
    if st is None:
        raise NotFound(f"impact study {study_id} not found")
    return st


def study_dict(st: ImpactStudy, with_result: bool = True) -> dict:
    d = {"id": st.id, "project_id": st.project_id, "dataset_id": st.dataset_id, "kind": st.kind, "status": st.status, "job_id": st.job_id,
         "request": st.request, "config_hash": st.config_hash, "config_version_no": st.config_version_no,
         "dataset_sha256": st.dataset_sha256, "model_version": st.model_version, "error": st.error, "created_at": st.created_at.isoformat()}
    if with_result:
        d["result"] = st.result
    return d


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


BASELINE_PATHS = ("site.", "it.", "electrical.", "building.", "baseline.", "grid.", "economics.", "layout.", "simulation.")


def baseline_view(s: Session, project_id: str) -> dict:
    """View A: the formalised baseline every comparison uses, with value provenance."""
    p = get_project(s, project_id)
    cfg = current_config(s, p)
    base = baseline_config(cfg)
    reg = [r for r in assumption_register(base) if r["path"].startswith(BASELINE_PATHS)]
    ds = None
    if p.default_dataset_id:
        rec = s.get(ClimateDatasetRecord, p.default_dataset_id)
        if rec:
            ds = {"id": rec.id, "provider": rec.provider, "product": rec.product, "data_nature": rec.data_nature,
                  "start_utc": _iso(rec.start_utc), "end_utc": _iso(rec.end_utc),
                  "n_hours": rec.n_hours, "sha256": rec.sha256}
    b, it, g = base.baseline, base.it, base.grid
    return {
        "project_id": p.id, "config_version_no": p.current_version_no, "config_hash": cfg.config_hash(),
        "description": "Conventional data center: the project configuration with every additional strategy switched off (scenario S0).",
        "site": base.site.model_dump(), "dataset": ds,
        "it": {"installed_capacity_mw": it.installed_capacity_mw, "average_utilisation": it.average_utilisation,
               "mean_it_load_mw": it.installed_capacity_mw * it.average_utilisation, "utilisation_scenario": it.utilisation_scenario,
               "server_delta_t_k": it.server_delta_t_k, "inlet_setpoint_c": it.inlet_setpoint_c, "ashrae_class": it.ashrae_class,
               "envelope_mode": it.envelope_mode, "containment": it.containment, "rack_count": it.rack_count,
               "schedule": "continuous 8 760 h/yr with diurnal/weekly modulation"},
        "cooling": {"architecture": b.architecture, "rated_cop": b.rated_cop, "chiller_model": b.chiller_model,
                    "chw_supply_c": b.chw_supply_c, "redundancy": b.cooling_redundancy, "design_margin": b.design_margin,
                    "humidification": b.humidification, "waterside_economiser": b.waterside_economiser},
        "grid": {"emission_factor_kg_per_kwh": g.emission_factor_kg_per_kwh, "factor_type": g.factor_type, "source": g.source,
                 "hourly_dataset_id": g.hourly_dataset_id},
        "economics": {"currency": cfg.economics.currency, "electricity_price_per_kwh": cfg.economics.electricity_price_per_kwh,
                      "water_price_per_m3": cfg.economics.water_price_per_m3, "unit_rates_supplied": len(cfg.economics.unit_rates),
                      "contingency_fraction": cfg.economics.contingency_fraction},
        "building": {"length_m": base.building.length_m, "width_m": base.building.width_m, "height_m": base.building.height_m,
                     "floors": base.building.floors, "site_area_m2": base.site.site_area_m2},
        "assumptions": reg,
        "assumed_count": sum(1 for r in reg if r["source"] == "default-assumption"),
    }
