"""Strategy impact analysis: catalogue, baseline view, individual / combination / pairwise studies, exports."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_session, rate_limit
from app.core.errors import ValidationFailure
from app.database.models import ImpactStudy
from app.impacts import catalogue, export
from app.impacts.costing import COST_LINES
from app.impacts.metrics import METRICS, STATUSES
from app.jobs import get_jobs
from app.services import impacts as svc
from app.services.projects import current_config, get_project
from app.simulation.scenarios import baseline_config

router = APIRouter(prefix="/api", tags=["strategy impacts"])


class ImpactStudyRequest(BaseModel):
    project_id: str
    dataset_id: str | None = None
    kind: Literal["baseline", "individual", "combination", "pairwise"] = "individual"
    strategies: list[str] | None = Field(None, description="Catalogue ids; default for 'individual' = every applicable strategy")
    parameters: dict[str, float] | None = Field(None, description="Strategy parameter overrides by config path")


@router.get("/impacts/catalogue")
def impact_catalogue(project_id: str | None = None, s: Session = Depends(get_session)):
    cfg = baseline_config(current_config(s, get_project(s, project_id))) if project_id else None
    return {
        "strategies": [catalogue.describe(m, cfg) for m in catalogue.MEASURES],
        "applicability": catalogue.applicability(cfg) if cfg else None,
        "controller_note": catalogue.CONTROLLER_NOTE,
        "metrics": [{"key": m.key, "label": m.label, "group": m.group, "unit": m.unit, "lower_is_better": m.lower_is_better} for m in METRICS],
        "statuses": STATUSES,
        "cost_lines": [asdict(c) for c in COST_LINES],
    }


@router.get("/projects/{project_id}/baseline")
def baseline(project_id: str, s: Session = Depends(get_session)):
    return svc.baseline_view(s, project_id)


@router.post("/impact-studies", status_code=202, dependencies=[Depends(rate_limit)])
def create_impact_study(body: ImpactStudyRequest, s: Session = Depends(get_session)):
    st = svc.create_study(s, body.model_dump())
    s.commit()
    job_id = get_jobs().submit("impact_study", svc.execute, st.id)
    st.job_id = job_id
    s.commit()
    return {"impact_study_id": st.id, "job_id": job_id}


@router.get("/impact-studies/{study_id}")
def get_impact_study(study_id: str, s: Session = Depends(get_session)):
    return svc.study_dict(svc.get_study(s, study_id))


@router.get("/projects/{project_id}/impact-studies")
def list_impact_studies(project_id: str, s: Session = Depends(get_session)):
    rows = s.query(ImpactStudy).filter_by(project_id=project_id).order_by(ImpactStudy.created_at.desc()).all()
    return [svc.study_dict(r, with_result=False) for r in rows]


@router.get("/impact-studies/{study_id}/export")
def export_impact_study(study_id: str, format: str = Query("json", pattern="^(json|csv|xlsx)$"), s: Session = Depends(get_session)):
    st = svc.get_study(s, study_id)
    if st.status != "succeeded" or not st.result:
        raise ValidationFailure(f"impact study is {st.status}")
    meta = svc.study_dict(st, with_result=False)
    name = f"impact-study-{st.kind}-{st.id[:8]}"
    if format == "csv":
        return Response(export.to_csv(st.result), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    if format == "xlsx":
        return Response(export.to_xlsx(st.result, meta), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})
    body = json.dumps({**meta, "limitations": export.LIMITATIONS, "result": st.result}, indent=1, default=str)
    return Response(body, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
