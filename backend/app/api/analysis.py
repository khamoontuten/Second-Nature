from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_session, rate_limit
from app.core.errors import ValidationFailure
from app.jobs import get_jobs
from app.schemas.api import OptimisationRequest, ReportRequest, SensitivityRequest
from app.services import analysis as svc
from app.services import reports as rsvc

router = APIRouter(prefix="/api", tags=["optimisation, uncertainty, reports"])


@router.post("/optimizations", status_code=202, dependencies=[Depends(rate_limit)])
def create_optimisation(body: OptimisationRequest, s: Session = Depends(get_session)):
    req = body.model_dump()
    if req.get("variables"):
        req["variables"] = [{k: v for k, v in x.items()} for x in req["variables"]]
    run = svc.create_optimisation(s, req)
    s.commit()
    job_id = get_jobs().submit("optimisation", svc.execute_optimisation, run.id)
    run.job_id = job_id
    s.commit()
    return {"optimization_id": run.id, "job_id": job_id}


@router.get("/optimizations/{optimization_id}")
def get_optimisation(optimization_id: str, s: Session = Depends(get_session)):
    r = svc.get_optimisation(s, optimization_id)
    return {"id": r.id, "project_id": r.project_id, "status": r.status, "job_id": r.job_id, "request": r.request, "result": r.result,
            "model_version": r.model_version, "created_at": r.created_at.isoformat()}


@router.post("/uncertainty", status_code=202, dependencies=[Depends(rate_limit)])
def create_sensitivity(body: SensitivityRequest, s: Session = Depends(get_session)):
    rec = svc.create_sensitivity(s, body.model_dump())
    s.commit()
    job_id = get_jobs().submit("sensitivity", svc.execute_sensitivity, rec.id)
    rec.job_id = job_id
    s.commit()
    return {"sensitivity_id": rec.id, "job_id": job_id}


@router.get("/uncertainty/{sensitivity_id}")
def get_sensitivity(sensitivity_id: str, s: Session = Depends(get_session)):
    r = svc.get_sensitivity(s, sensitivity_id)
    return {"id": r.id, "simulation_id": r.simulation_id, "kind": r.kind, "status": r.status, "job_id": r.job_id, "request": r.request,
            "result": r.result}


@router.get("/simulations/{simulation_id}/uncertainty")
def list_sensitivity(simulation_id: str, s: Session = Depends(get_session)):
    from app.database.models import SensitivityResult

    rows = s.query(SensitivityResult).filter_by(simulation_id=simulation_id).order_by(SensitivityResult.created_at.desc()).all()
    return [{"id": r.id, "kind": r.kind, "status": r.status, "created_at": r.created_at.isoformat()} for r in rows]


@router.post("/reports", status_code=202, dependencies=[Depends(rate_limit)])
def create_report(body: ReportRequest, s: Session = Depends(get_session)):
    rep = rsvc.create_report(s, body.simulation_id, body.format)
    s.commit()
    job_id = get_jobs().submit("report", rsvc.execute, rep.id)
    rep.job_id = job_id
    s.commit()
    return {"report_id": rep.id, "job_id": job_id}


@router.get("/reports/{report_id}")
def get_report(report_id: str, s: Session = Depends(get_session)):
    r = rsvc.get_report(s, report_id)
    return {"id": r.id, "simulation_id": r.simulation_id, "format": r.format, "status": r.status, "job_id": r.job_id,
            "sha256": r.sha256, "download_url": f"/api/reports/{r.id}/download" if r.status == "succeeded" else None}


@router.get("/reports/{report_id}/download")
def download_report(report_id: str, s: Session = Depends(get_session)):
    r = rsvc.get_report(s, report_id)
    if r.status != "succeeded" or not r.path:
        raise ValidationFailure(f"report is {r.status}")
    return FileResponse(r.path, media_type=rsvc.FORMATS[r.format], filename=f"second-nature-{r.simulation_id[:8]}.{r.format}")
