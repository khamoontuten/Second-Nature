from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_session, rate_limit, read_upload
from app.climate import grid as grid_mod
from app.climate import water as water_mod
from app.climate.ingestion import PROVIDER_INFO, PROVIDERS, reference_catalog
from app.climate.providers.csv_upload import parse_csv
from app.climate.providers.epw import parse_epw_bytes
from app.climate.providers.netcdf import parse_netcdf
from app.core.config import get_settings
from app.core.errors import ValidationFailure
from app.database.models import DataQualityReportRecord, GridEmissionsDataset, WaterResourceDataset
from app.jobs import get_jobs
from app.schemas.api import ClimateFetchRequest, ReferenceDatasetRequest
from app.services import climate as svc
from app.services.climate import _jsonable

router = APIRouter(prefix="/api", tags=["climate"])


@router.get("/climate/providers")
def providers():
    out = []
    for pid, info in PROVIDER_INFO.items():
        configured = True
        if pid in PROVIDERS:
            configured = PROVIDERS[pid]().is_configured()
        out.append({"id": pid, **info, "configured": configured})
    return out


@router.get("/climate/reference-sites")
def reference_sites():
    return reference_catalog()


@router.post("/climate/reference", status_code=201)
def attach_reference(body: ReferenceDatasetRequest, s: Session = Depends(get_session)):
    lat = lon = el = None
    if body.project_id:
        from app.services.projects import current_config, get_project

        p = get_project(s, body.project_id)
        cfg = current_config(s, p)
        lat, lon, el = cfg.site.latitude, cfg.site.longitude, cfg.site.elevation_m
    rec = svc.from_reference(s, body.site_id, body.project_id, lat, lon, el)
    if body.project_id:
        p.default_dataset_id = rec.id
    s.commit()
    return {"dataset_id": rec.id, "provenance": rec.provenance}


@router.post("/climate/fetch", status_code=202, dependencies=[Depends(rate_limit)])
def fetch(body: ClimateFetchRequest):
    job_id = get_jobs().submit("climate_fetch", svc.fetch_job, body.project_id, body.latitude, body.longitude, body.start, body.end,
                               body.elevation_m, body.providers, body.model)
    return {"job_id": job_id}


@router.post("/climate/upload", status_code=201, dependencies=[Depends(rate_limit)])
async def upload(file: UploadFile = File(...), latitude: float = Form(...), longitude: float = Form(...),
                 elevation_m: float | None = Form(None), project_id: str | None = Form(None), timezone: str = Form("UTC"),
                 mapping: str | None = Form(None, description="JSON {canonical: {column, unit}}"),
                 data_nature: str = Form("observed"), description: str = Form("user upload"),
                 accumulation: str = Form("hourly"), s: Session = Depends(get_session)):
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValidationFailure("latitude/longitude out of range")
    if data_nature not in ("observed", "reanalysis", "typical_year", "modeled", "manual", "forecast", "mixed"):
        raise ValidationFailure("invalid data_nature")
    raw, suffix = await read_upload(file)
    if suffix == ".epw":
        ds = parse_epw_bytes(raw, site_lat=latitude, site_lon=longitude, site_elevation_m=elevation_m, licence="as declared by uploader",
                             source_url=file.filename or "")
    elif suffix in (".nc", ".nc4"):
        ds = parse_netcdf(raw, latitude=latitude, longitude=longitude, elevation_m=elevation_m, accumulation=accumulation,
                          product=description, source_url=file.filename or "")
    else:
        try:
            mp = json.loads(mapping) if mapping else None
        except json.JSONDecodeError as e:
            raise ValidationFailure("mapping must be valid JSON") from e
        ds = parse_csv(raw, latitude=latitude, longitude=longitude, elevation_m=elevation_m, mapping=mp, timezone=timezone,
                       data_nature=data_nature, source_description=description)
    ds.provenance.notes.append(f"uploaded file: {file.filename}")
    rec = svc.register(s, ds, project_id)
    if project_id:
        from app.services.projects import get_project

        get_project(s, project_id).default_dataset_id = rec.id
    s.commit()
    qc = s.query(DataQualityReportRecord).filter_by(dataset_id=rec.id).first()
    return {"dataset_id": rec.id, "provenance": rec.provenance, "quality": qc.report if qc else None}


@router.get("/climate/{dataset_id}/summary")
def summary(dataset_id: str, timezone: str = "UTC", elevation_m: float = 0.0, s: Session = Depends(get_session)):
    return svc.summary(s, dataset_id, timezone, elevation_m)


@router.get("/climate/{dataset_id}/provenance")
def provenance(dataset_id: str, s: Session = Depends(get_session)):
    rec = svc.get_record(s, dataset_id)
    qc = s.query(DataQualityReportRecord).filter_by(dataset_id=dataset_id).first()
    return {"dataset_id": rec.id, "sha256": rec.sha256, "provenance": rec.provenance, "quality": qc.report if qc else None}


@router.get("/climate/{dataset_id}/timeseries")
def timeseries(dataset_id: str, start: str | None = None, end: str | None = None, variables: str | None = Query(None),
               format: str = "json", step: int = Query(1, ge=1, le=168), elevation_m: float = 0.0, s: Session = Depends(get_session)):
    df = svc.timeseries(s, dataset_id, start, end, variables.split(",") if variables else None, elevation_m, step)
    if format == "csv":
        return Response(df.to_csv(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="climate_{dataset_id}.csv"'})
    if format == "raw_csv":
        ds = svc.load_dataset(s, dataset_id)
        return Response(ds.data.to_csv(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="climate_{dataset_id}_raw.csv"'})
    return {"index": [t.isoformat() for t in df.index], "columns": {c: _jsonable(df[c].round(4).tolist()) for c in df.columns}}


# ---------------------------------------------------------------------------------------------- grid / water
@router.post("/grid/upload", status_code=201, dependencies=[Depends(rate_limit)])
async def grid_upload(file: UploadFile = File(...), unit: str = Form("g/kWh"), project_id: str | None = Form(None),
                      factor_kind: str = Form("average"), source: str = Form("user upload"), s: Session = Depends(get_session)):
    raw, _ = await read_upload(file, {".csv", ".txt"})
    series = grid_mod.parse_intensity_csv(raw, unit)
    d = get_settings().data_dir / "processed" / "grid"
    d.mkdir(parents=True, exist_ok=True)
    rec = GridEmissionsDataset(project_id=project_id, provider="upload", factor_kind=factor_kind, path="", n_hours=len(series),
                               mean_kg_per_kwh=float(series.mean()), provenance={"source": source, "file": file.filename, "unit_in": unit})
    s.add(rec)
    s.flush()
    path = d / f"{rec.id}.parquet"
    series.rename("kg_per_kwh").to_frame().to_parquet(path)
    rec.path = str(path)
    s.commit()
    return {"grid_dataset_id": rec.id, "n_hours": len(series), "mean_kg_per_kwh": float(series.mean()), "factor_kind": factor_kind}


@router.post("/water/aqueduct-lookup", dependencies=[Depends(rate_limit)])
async def aqueduct_lookup(file: UploadFile = File(...), latitude: float = Form(...), longitude: float = Form(...),
                          project_id: str | None = Form(None), s: Session = Depends(get_session)):
    raw, _ = await read_upload(file, {".csv", ".txt"})
    df = water_mod.parse_aqueduct_extract(raw)
    res = water_mod.nearest_score(df, latitude, longitude)
    s.add(WaterResourceDataset(project_id=project_id, source="WRI Aqueduct extract (uploaded)", stress_score=res.get("score"),
                               label=res.get("label"), details=_jsonable(res)))
    s.commit()
    return res


@router.get("/water/labels")
def water_labels():
    return {"labels": water_mod.LABELS, "note": "Basin-level index; not a site-specific withdrawal permit."}

