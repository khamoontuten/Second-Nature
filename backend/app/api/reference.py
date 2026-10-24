from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import MODEL_VERSION, __version__
from app.api.deps import get_session
from app.core.config import get_settings
from app.core.environmental_classes import all_envelopes
from app.core.errors import NotFound
from app.database.models import Job
from app.jobs import get_jobs
from app.reporting.data import LIMITATIONS, REFERENCES
from app.strategies.library import COMPATIBILITY, STRATEGIES

router = APIRouter(prefix="/api", tags=["reference"])


@router.get("/health")
def health(s: Session = Depends(get_session)):
    db_ok = True
    try:
        s.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    st = get_settings()
    return {"status": "ok" if db_ok else "degraded", "database": db_ok, "app_version": __version__, "model_version": MODEL_VERSION,
            "data_dir_writable": st.data_dir.exists(), "environment": st.environment}


@router.get("/strategies")
def strategies():
    return {"strategies": STRATEGIES, "compatibility": [{"a": a, "b": b, "relation": r, "note": n} for a, b, r, n in COMPATIBILITY]}


@router.get("/environmental-classes")
def environmental_classes():
    return {"envelopes": all_envelopes(),
            "note": "ASHRAE TC 9.9 Thermal Guidelines (5th ed., 2021) values as configurable constraints; verify against the current edition."}


@router.get("/model-info")
def model_info():
    return {"model_version": MODEL_VERSION, "app_version": __version__,
            "modules": ["psychrometrics (ASHRAE 2017 Ch.1)", "airflow network (Newton–Raphson)", "earth-air HX (radial FD + shape factor)",
                        "envelope (implicit FD, transmission-matrix check)", "evaporative heat-and-mass balances",
                        "chillers (Carnot-fraction / biquadratic EIR)", "hourly supervisory controller"],
            "limitations": LIMITATIONS, "references": REFERENCES}


@router.get("/jobs/{job_id}")
def job(job_id: str, s: Session = Depends(get_session)):
    j = s.get(Job, job_id)
    if j is None:
        raise NotFound(f"job {job_id} not found")
    return {"id": j.id, "kind": j.kind, "status": j.status, "progress": j.progress, "message": j.message, "error": j.error,
            "result": j.result_ref, "created_at": j.created_at.isoformat(), "updated_at": j.updated_at.isoformat() if j.updated_at else None}


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str):
    return {"cancel_requested": get_jobs().cancel(job_id)}


@router.get("/jobs")
def jobs(limit: int = 50, s: Session = Depends(get_session)):
    rows = s.query(Job).order_by(Job.created_at.desc()).limit(limit).all()
    return [{"id": j.id, "kind": j.kind, "status": j.status, "progress": j.progress, "message": j.message} for j in rows]
