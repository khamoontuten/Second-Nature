from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_session, rate_limit
from app.climate.geocoding import geocode
from app.core.errors import ValidationFailure
from app.database.models import ClimateDatasetRecord, Project, SimulationRun
from app.schemas.api import GeocodeRequest, ProjectCreate, ProjectPatch
from app.schemas.config import ProjectConfig, assumption_register
from app.services import climate as climate_svc
from app.services import projects as svc
from app.strategies.library import compatibility_issues

router = APIRouter(prefix="/api", tags=["projects"])


def _config(data: dict) -> ProjectConfig:
    try:
        return ProjectConfig(**data)
    except ValidationError as e:
        raise ValidationFailure("invalid project configuration", details={"errors": e.errors(include_url=False)}) from e


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, s: Session = Depends(get_session)):
    cfg = _config(body.config)
    p = svc.create_project(s, cfg, geocode_source=body.geocode_source)
    if body.reference_site_id:
        rec = climate_svc.from_reference(s, body.reference_site_id, p.id, cfg.site.latitude, cfg.site.longitude, cfg.site.elevation_m)
        p.default_dataset_id = rec.id
    s.commit()
    return svc.project_dict(s, p)


@router.get("/projects")
def list_projects(s: Session = Depends(get_session)):
    ps = s.scalars(select(Project).order_by(Project.updated_at.desc())).all()
    return [svc.project_dict(s, p, include_config=False) for p in ps]


@router.get("/projects/{project_id}")
def get_project(project_id: str, s: Session = Depends(get_session)):
    p = svc.get_project(s, project_id)
    d = svc.project_dict(s, p)
    d["versions"] = [{"version_no": v.version_no, "config_hash": v.config_hash, "note": v.note, "created_at": v.created_at.isoformat()}
                     for v in p.versions]
    d["datasets"] = [{"id": r.id, "provider": r.provider, "product": r.product, "data_nature": r.data_nature, "n_hours": r.n_hours,
                      "start_utc": r.start_utc, "end_utc": r.end_utc, "distance_km": r.distance_km}
                     for r in s.scalars(select(ClimateDatasetRecord).where(ClimateDatasetRecord.project_id == p.id)).all()]
    d["simulations"] = [{"id": r.id, "status": r.status, "created_at": r.created_at.isoformat(), "scenarios": r.scenarios,
                         "model_version": r.model_version}
                        for r in s.scalars(select(SimulationRun).where(SimulationRun.project_id == p.id)
                                           .order_by(SimulationRun.created_at.desc())).all()]
    d["compatibility"] = compatibility_issues(ProjectConfig(**d["config"]))
    return d


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, body: ProjectPatch, s: Session = Depends(get_session)):
    p = svc.get_project(s, project_id)
    try:
        svc.update_project(s, p, body.patch, body.note)
    except ValidationError as e:
        raise ValidationFailure("invalid configuration update", details={"errors": e.errors(include_url=False)}) from e
    if body.default_dataset_id:
        climate_svc.get_record(s, body.default_dataset_id)
        p.default_dataset_id = body.default_dataset_id
    s.commit()
    return svc.project_dict(s, p)


@router.get("/projects/{project_id}/versions/{version_no}")
def get_version(project_id: str, version_no: int, s: Session = Depends(get_session)):
    p = svc.get_project(s, project_id)
    v = next((v for v in p.versions if v.version_no == version_no), None)
    if v is None:
        from app.core.errors import NotFound

        raise NotFound(f"version {version_no} not found")
    return {"version_no": v.version_no, "config": v.config, "config_hash": v.config_hash, "note": v.note,
            "assumptions": assumption_register(ProjectConfig(**v.config))}


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, s: Session = Depends(get_session)):
    p = svc.get_project(s, project_id)
    s.delete(p)
    s.commit()


@router.post("/projects/validate")
def validate_config(body: dict):
    cfg = _config(body)
    return {"valid": True, "config_hash": cfg.config_hash(), "assumptions": assumption_register(cfg),
            "compatibility": compatibility_issues(cfg)}


@router.post("/demo", status_code=202, dependencies=[Depends(rate_limit)])
def create_demo(site_id: str = "ashburn_va"):
    """Create the reproducible demonstration project (real TMY3 weather, labeled assumptions) and start its simulation."""
    from app.demo import SITES
    from app.demo import create_demo as make

    if site_id not in SITES:
        raise ValidationFailure(f"site_id must be one of {sorted(SITES)}")
    return make(site_id, run=True, wait=False)


@router.post("/sites/geocode", dependencies=[Depends(rate_limit)])
def geocode_site(body: GeocodeRequest):
    return geocode(body.query, body.provider)
