"""Grid carbon-intensity providers.

* Manual / static factors (annual or monthly) entered by the engineer with a cited source — e.g. India
  CEA CO₂ Baseline Database, US EPA eGRID subregion output rates, Ember country data. The platform does
  **not** ship default factors, because a wrong regional factor silently distorts every carbon result.
* Electricity Maps API (hourly history) — requires an API key (``SN_ELECTRICITY_MAPS_API_KEY``) and is
  subject to its licence. Values are *average* (consumption-based, lifecycle factors by default); they are
  never labeled marginal.
* Uploaded hourly CSV (columns: time, intensity in g or kg CO₂e/kWh).

If no hourly data exist, a clearly labeled annual or monthly factor is used; no hourly profile is invented.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from app.climate.http import CachedClient
from app.core.config import get_settings
from app.core.errors import ProviderError, ValidationFailure

EM_URL = "https://api.electricitymap.org/v3/carbon-intensity/past-range"


class ElectricityMapsProvider:
    name = "electricity_maps"

    def __init__(self, client: CachedClient | None = None, api_key: str | None = None, chunk_days: int = 10):
        self.client = client or CachedClient(self.name)
        key = api_key or (get_settings().electricity_maps_api_key.get_secret_value() if get_settings().electricity_maps_api_key else None)
        self.api_key = key
        self.chunk_days = chunk_days

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def fetch(self, zone: str, start: datetime, end: datetime) -> dict:
        if not self.api_key:
            raise ProviderError(self.name, "SN_ELECTRICITY_MAPS_API_KEY not configured")
        rows = []
        cur = start
        meta = None
        while cur < end:
            nxt = min(cur + timedelta(days=self.chunk_days), end)
            body, meta = self.client.get_json(EM_URL, {"zone": zone, "start": cur.isoformat(), "end": nxt.isoformat()},
                                              headers={"auth-token": self.api_key})
            for r in body.get("data", []):
                rows.append((r["datetime"], r.get("carbonIntensity"), r.get("emissionFactorType", "lifecycle"),
                             r.get("isEstimated")))
            cur = nxt
        if not rows:
            raise ProviderError(self.name, f"no carbon-intensity data returned for zone {zone}")
        df = pd.DataFrame(rows, columns=["time", "g_per_kwh", "factor_type", "estimated"])
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.drop_duplicates("time").set_index("time").sort_index()
        return {
            "series_kg_per_kwh": (df["g_per_kwh"].astype(float) / 1000.0),
            "provenance": {"provider": self.name, "zone": zone, "endpoint": EM_URL, "retrieved_at": (meta or {}).get("retrieved_at"),
                           "factor_kind": "average (consumption-based)", "emission_factor_type": df["factor_type"].iloc[0],
                           "estimated_fraction": float(df["estimated"].fillna(False).astype(bool).mean()),
                           "licence": "Electricity Maps terms of use; redistribution may be restricted"},
        }


def parse_intensity_csv(raw: bytes, unit: str = "g/kWh") -> pd.Series:
    try:
        df = pd.read_csv(io.BytesIO(raw))
    except Exception as e:  # noqa: BLE001
        raise ValidationFailure(f"could not parse CSV: {e}") from e
    tcol = next((c for c in df.columns if c.lower() in ("time", "datetime", "timestamp", "time_utc")), None)
    vcol = next((c for c in df.columns if c != tcol), None)
    if tcol is None or vcol is None:
        raise ValidationFailure("intensity CSV needs a time column and a value column")
    idx = pd.to_datetime(df[tcol], utc=True)
    vals = pd.to_numeric(df[vcol], errors="coerce").to_numpy(dtype=float)
    if unit.lower().startswith("g"):
        vals = vals / 1000.0
    s = pd.Series(vals, index=idx).sort_index()
    if (s < 0).any() or (s > 2.0).any():
        raise ValidationFailure("intensity values outside 0–2 kg CO₂e/kWh — check units")
    return s


def align_to(index: pd.DatetimeIndex, series: pd.Series, max_gap_h: int = 3) -> tuple[np.ndarray, dict]:
    """Align an intensity series to the simulation hours. Typical-year climate (nominal year) is matched by
    month-day-hour; unmatched hours stay NaN (reported), never invented."""
    s = series.copy()
    exact = s.reindex(index)
    if exact.notna().mean() > 0.5:
        out = exact.interpolate(limit=max_gap_h)
        how = "exact timestamps"
    else:
        key = s.groupby([s.index.month, s.index.day, s.index.hour]).mean()
        out = pd.Series([key.get((t.month, t.day, t.hour), np.nan) for t in index], index=index)
        how = "matched by month/day/hour (climate year differs from intensity year)"
    return out.to_numpy(dtype=float), {"alignment": how, "missing_hours": int(out.isna().sum())}


def now_utc() -> datetime:
    return datetime.now(UTC)
