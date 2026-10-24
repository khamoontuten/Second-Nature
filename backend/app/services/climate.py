"""Climate dataset registration, retrieval and summaries (DB + Parquet)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.climate import features, quality, store
from app.climate.ingestion import fetch_climate, load_reference
from app.climate.providers.base import ClimateDataset, ClimateRequest
from app.core.errors import NotFound
from app.database.models import (
    ClimateDatasetRecord,
    ClimateObservationSummary,
    DataQualityReportRecord,
    SourceProvenance,
)
from app.database.models import _id as new_id

_cache: dict[str, ClimateDataset] = {}


def register(s: Session, ds: ClimateDataset, project_id: str | None = None, qc: quality.QualityReport | None = None) -> ClimateDatasetRecord:
    if qc is None:
        ds = quality.reindex_hourly(ds)
        qc = quality.assess(ds, ds.provenance.requested_lon)
    dataset_id = new_id()
    info = store.save(ds, dataset_id)
    p = ds.provenance
    rec = ClimateDatasetRecord(id=dataset_id, project_id=project_id, provider=p.provider, product=p.product, data_nature=p.data_nature,
                               start_utc=str(ds.data.index[0]), end_utc=str(ds.data.index[-1]), n_hours=len(ds.data),
                               requested_lat=p.requested_lat, requested_lon=p.requested_lon, actual_lat=p.actual_lat,
                               actual_lon=p.actual_lon, distance_km=p.distance_km, path=info["path"], sha256=info["sha256"],
                               provenance=_jsonable(p.to_dict()))
    s.add(rec)
    s.flush()
    s.add(DataQualityReportRecord(dataset_id=dataset_id, report=_jsonable(qc.to_dict())))
    for col in ds.data.columns:
        v = ds.data[col]
        s.add(ClimateObservationSummary(dataset_id=dataset_id, variable=col, unit=p.units.get(col, ""),
                                        completeness_pct=float(100 * v.notna().mean()),
                                        mean=_f(v.mean()), minimum=_f(v.min()), maximum=_f(v.max())))
    s.add(SourceProvenance(entity_type="climate_dataset", entity_id=dataset_id, provenance=_jsonable(p.to_dict())))
    s.flush()
    ds.provenance.extra["sha256"] = info["sha256"]
    _cache[dataset_id] = ds
    return rec


def _f(x):
    return None if x is None or not np.isfinite(x) else float(x)


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, float) and not np.isfinite(obj):
        return None
    return obj


def get_record(s: Session, dataset_id: str) -> ClimateDatasetRecord:
    rec = s.get(ClimateDatasetRecord, dataset_id)
    if rec is None:
        raise NotFound(f"climate dataset {dataset_id} not found")
    return rec


def load_dataset(s: Session, dataset_id: str) -> ClimateDataset:
    if dataset_id in _cache:
        return _cache[dataset_id]
    rec = get_record(s, dataset_id)
    if not store.exists(dataset_id):
        raise NotFound(f"climate dataset files for {dataset_id} are missing from the data directory")
    ds = store.load(dataset_id)
    _cache[dataset_id] = ds
    _ = rec
    return ds


def from_reference(s: Session, site_id: str, project_id: str | None, lat=None, lon=None, elevation=None) -> ClimateDatasetRecord:
    ds = load_reference(site_id, site_lat=lat, site_lon=lon, site_elevation_m=elevation)
    return register(s, ds, project_id)


def fetch_job(project_id: str | None, lat: float, lon: float, start: date, end: date, elevation_m: float | None, providers: list[str] | None,
              model: str | None, *, progress, check_cancel, job_id):
    from app.database.session import session_scope

    progress(0.05, "requesting climate data")
    check_cancel()
    out = fetch_climate(ClimateRequest(lat, lon, start, end, elevation_m=elevation_m, model=model), providers)
    progress(0.8, f"retrieved from {out.provider_used}; storing")
    with session_scope() as s:
        rec = register(s, out.dataset, project_id, out.quality)
        return {"dataset_id": rec.id, "provider": out.provider_used, "attempts": out.attempts}


def summary(s: Session, dataset_id: str, timezone: str = "UTC", elevation_m: float = 0.0) -> dict:
    ds = load_dataset(s, dataset_id)
    rec = get_record(s, dataset_id)
    out = features.summarise_climate(ds, elevation_m=elevation_m, timezone=timezone)
    qc = s.query(DataQualityReportRecord).filter_by(dataset_id=dataset_id).order_by(DataQualityReportRecord.created_at.desc()).first()
    return _jsonable({"dataset_id": dataset_id, "provider": rec.provider, "product": rec.product, "data_nature": rec.data_nature,
                      "summary": out, "quality": qc.report if qc else None, "provenance": rec.provenance})


def timeseries(s: Session, dataset_id: str, start: str | None = None, end: str | None = None, variables: list[str] | None = None,
               elevation_m: float = 0.0, step: int = 1) -> pd.DataFrame:
    ds = load_dataset(s, dataset_id)
    df = features.derived_frame(ds, elevation_m)
    if start:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df.index <= pd.Timestamp(end, tz="UTC")]
    if variables:
        df = df[[v for v in variables if v in df.columns]]
    if step > 1:
        df = df.iloc[::step]
    return df
