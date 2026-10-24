"""Water-stress context.

WRI Aqueduct 4.0 provides *basin-level* baseline water stress scores (0–5). It is distributed as a
downloadable dataset rather than a live API, so the platform imports a CSV extract (columns: name,
latitude, longitude, bws_score[, bws_label, basin_id]) or accepts a manual score with its source. A basin
score is context for weighting and red-flagging; it is not a site-specific withdrawal permit.
"""

from __future__ import annotations

import io

import pandas as pd

from app.climate.providers.base import haversine_km
from app.core.errors import ValidationFailure

LABELS = {0: "Low (<10%)", 1: "Low–medium (10–20%)", 2: "Medium–high (20–40%)", 3: "High (40–80%)", 4: "Extremely high (>80%)"}


def label_for(score: float | None) -> str | None:
    if score is None:
        return None
    return LABELS.get(min(int(score), 4))


def parse_aqueduct_extract(raw: bytes) -> pd.DataFrame:
    try:
        df = pd.read_csv(io.BytesIO(raw))
    except Exception as e:  # noqa: BLE001
        raise ValidationFailure(f"could not parse Aqueduct extract: {e}") from e
    need = {"latitude", "longitude", "bws_score"}
    if not need <= set(c.lower() for c in df.columns):
        raise ValidationFailure(f"Aqueduct extract needs columns {sorted(need)}")
    df.columns = [c.lower() for c in df.columns]
    if ((df["bws_score"] < 0) | (df["bws_score"] > 5)).any():
        raise ValidationFailure("bws_score must be within 0–5")
    return df


def nearest_score(df: pd.DataFrame, lat: float, lon: float, max_km: float = 50.0) -> dict:
    d = df.apply(lambda r: haversine_km(lat, lon, r["latitude"], r["longitude"]), axis=1)
    i = int(d.idxmin())
    row = df.loc[i]
    if d[i] > max_km:
        return {"found": False, "nearest_km": float(d[i]),
                "message": f"nearest extract point is {d[i]:.0f} km away (> {max_km} km); enter the basin score manually"}
    return {"found": True, "score": float(row["bws_score"]), "label": row.get("bws_label") or label_for(float(row["bws_score"])),
            "distance_km": float(d[i]), "basin_id": row.get("basin_id"), "source": "WRI Aqueduct 4.0 extract (basin level)"}
