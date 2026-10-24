"""Parquet store for climate datasets (data + flags + provenance), content-addressed by SHA-256."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from app.climate.providers.base import ClimateDataset, Provenance
from app.core.config import get_settings


def dataset_hash(ds: ClimateDataset) -> str:
    h = hashlib.sha256()
    h.update(pd.util.hash_pandas_object(ds.data, index=True).values.tobytes())
    h.update(json.dumps(ds.provenance.to_dict(), sort_keys=True, default=str).encode())
    return h.hexdigest()


def save(ds: ClimateDataset, dataset_id: str, directory: Path | None = None) -> dict:
    d = (directory or get_settings().datasets_dir) / dataset_id
    d.mkdir(parents=True, exist_ok=True)
    ds.data.to_parquet(d / "data.parquet")
    if ds.flags is not None:
        ds.flags.to_parquet(d / "flags.parquet")
    meta = {"provenance": ds.provenance.to_dict(), "soil_layers_m": ds.soil_layers_m,
            "monthly_ground_c": {str(k): v for k, v in ds.monthly_ground_c.items()}, "sha256": dataset_hash(ds)}
    (d / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
    return {"path": str(d), "sha256": meta["sha256"]}


def load(dataset_id: str, directory: Path | None = None) -> ClimateDataset:
    d = (directory or get_settings().datasets_dir) / dataset_id
    data = pd.read_parquet(d / "data.parquet")
    flags = pd.read_parquet(d / "flags.parquet") if (d / "flags.parquet").exists() else None
    meta = json.loads((d / "meta.json").read_text())
    prov = Provenance(**meta["provenance"])
    ds = ClimateDataset(data, prov, flags, soil_layers_m=meta.get("soil_layers_m", {}),
                        monthly_ground_c={float(k): v for k, v in meta.get("monthly_ground_c", {}).items()})
    ds.provenance.extra.setdefault("sha256", meta["sha256"])
    return ds


def exists(dataset_id: str, directory: Path | None = None) -> bool:
    return ((directory or get_settings().datasets_dir) / dataset_id / "data.parquet").exists()
