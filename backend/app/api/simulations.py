from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_session, rate_limit
from app.jobs import get_jobs
from app.schemas.api import SimulationRequest
from app.services import simulations as svc
from app.services.climate import _jsonable

router = APIRouter(prefix="/api", tags=["simulations"])


@router.post("/simulations", status_code=202, dependencies=[Depends(rate_limit)])
def create(body: SimulationRequest, s: Session = Depends(get_session)):
    run = svc.create_run(s, body.project_id, body.dataset_id, body.scenarios, body.weights)
    s.commit()
    job_id = get_jobs().submit("simulation", svc.execute, run.id)
    run.job_id = job_id
    s.commit()
    return {"simulation_id": run.id, "job_id": job_id}


@router.get("/simulations/{simulation_id}")
def get(simulation_id: str, s: Session = Depends(get_session)):
    return svc.run_dict(s, svc.get_run(s, simulation_id))


@router.get("/simulations/{simulation_id}/results")
def results(simulation_id: str, s: Session = Depends(get_session)):
    run = svc.get_run(s, simulation_id)
    d = svc.run_dict(s, run, full=True)
    d["climate_summary"] = run.climate_summary
    return d


@router.get("/simulations/{simulation_id}/scenarios/{scenario_id}/hourly")
def hourly(simulation_id: str, scenario_id: str, columns: str | None = Query(None), start: str | None = None, end: str | None = None,
           format: str = "json", step: int = Query(1, ge=1, le=168), s: Session = Depends(get_session)):
    df = svc.hourly(s, simulation_id, scenario_id, columns.split(",") if columns else None, start, end)
    if step > 1:
        df = df.iloc[::step]
    if format == "csv":
        return Response(df.to_csv(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{simulation_id}_{scenario_id}_hourly.csv"'})
    out = {}
    for c in df.columns:
        col = df[c]
        if c == "time_local":
            out[c] = [str(x) for x in col]
        elif pd.api.types.is_bool_dtype(col):
            out[c] = col.astype(bool).tolist()
        elif pd.api.types.is_numeric_dtype(col):
            out[c] = _jsonable(col.astype(float).round(5).tolist())
        else:
            out[c] = col.astype(str).tolist()
    return {"index": [t.isoformat() for t in df.index], "columns": out}


@router.get("/simulations/{simulation_id}/comparison.csv")
def comparison_csv(simulation_id: str, s: Session = Depends(get_session)):
    df = svc.comparison_frame(svc.get_run(s, simulation_id))
    return Response(df.to_csv(index=False), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{simulation_id}_comparison.csv"'})


@router.post("/simulations/{simulation_id}/reproduce")
def reproduce(simulation_id: str, scenario_id: str = "S0", s: Session = Depends(get_session)):
    return _jsonable(svc.reproduce(s, simulation_id, scenario_id))
