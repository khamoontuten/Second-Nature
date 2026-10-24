"""Report generation jobs."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotFound, ValidationFailure
from app.database.models import Report
from app.database.session import session_scope
from app.reporting.bundle import csv_zip, json_bundle
from app.reporting.data import collect
from app.reporting.excel import build_workbook
from app.reporting.pdf import build_pdf
from app.services.simulations import get_run

FORMATS = {"pdf": "application/pdf", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
           "json": "application/json", "zip": "application/zip"}


def create_report(s: Session, simulation_id: str, fmt: str) -> Report:
    if fmt not in FORMATS:
        raise ValidationFailure(f"format must be one of {sorted(FORMATS)}")
    run = get_run(s, simulation_id)
    if run.status != "succeeded":
        raise ValidationFailure("simulation has not finished successfully")
    rep = Report(project_id=run.project_id, simulation_id=run.id, format=fmt, status="queued")
    s.add(rep)
    s.flush()
    return rep


def execute(report_id: str, *, progress, check_cancel, job_id):
    with session_scope() as s:
        rep = s.get(Report, report_id)
        rep.status, rep.job_id = "running", job_id
        progress(0.1, "collecting results")
        d = collect(s, rep.simulation_id)
        fmt = rep.format
        check_cancel()
        progress(0.3, f"building {fmt}")
        if fmt == "pdf":
            data = build_pdf(d)
        elif fmt == "xlsx":
            data = build_workbook(d)
        elif fmt == "json":
            data = json.dumps(json_bundle(d), indent=2, default=str).encode()
        else:
            data = csv_zip(d)
        out_dir = get_settings().reports_dir / rep.project_id
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{report_id}.{fmt}"
        path.write_bytes(data)
        rep.path, rep.sha256, rep.status = str(path), hashlib.sha256(data).hexdigest(), "succeeded"
    return {"report_id": report_id}


def get_report(s: Session, report_id: str) -> Report:
    r = s.get(Report, report_id)
    if r is None:
        raise NotFound(f"report {report_id} not found")
    return r
