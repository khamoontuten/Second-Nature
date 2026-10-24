"""EnergyPlus Weather (EPW) reader — used for TMY/TMY3 files and user uploads.

Format: EnergyPlus Auxiliary Programs documentation, "Weather Converter Program / EPW CSV format".
* Timestamps are local *standard* time; hour 1 covers 00:00–01:00 (hour-ending). They are
  converted to hour-beginning UTC using the LOCATION time-zone offset.
* Missing-value codes (per field) become NaN and are flagged as missing.
* Typical-year files (TMY3) splice months from different calendar years; the source years per
  month are recorded, and the data are placed on a nominal non-leap year for simulation.
* The GROUND TEMPERATURES header contains monthly values produced by the weather converter with
  a default soil diffusivity — they are **modeled**, not measured, and labeled as such.
"""

from __future__ import annotations

import gzip
import hashlib
import io
from pathlib import Path

import numpy as np
import pandas as pd

from app.climate.providers.base import (
    ClimateDataset,
    ClimateProvider,
    ClimateRequest,
    Provenance,
    haversine_km,
    now_iso,
)

# (column index in EPW data row, canonical name, missing code)
EPW_FIELDS = [
    (6, "t_db_c", 99.9),
    (7, "t_dp_c", 99.9),
    (8, "rh_pct", 999.0),
    (9, "p_pa", 999999.0),
    (12, "ir_horiz_wm2", 9999.0),
    (13, "ghi_wm2", 9999.0),
    (14, "dni_wm2", 9999.0),
    (15, "dhi_wm2", 9999.0),
    (20, "wind_dir_deg", 999.0),
    (21, "wind_ms", 999.0),
    (22, "cloud_pct", 99.0),   # total sky cover, tenths → %
    (33, "precip_mm", 999.0),  # liquid precipitation depth (often missing in TMY3)
]

NOMINAL_YEAR = 2001  # non-leap


def _open_text(path: Path) -> str:
    raw = path.read_bytes()
    if path.suffix == ".gz" or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("latin-1")


def parse_epw(text: str, *, source_name: str = "uploaded.epw", site_lat: float | None = None,
              site_lon: float | None = None, site_elevation_m: float | None = None,
              source_url: str = "", licence: str = "see source", data_nature: str = "typical_year",
              product: str | None = None, citation: str = "") -> ClimateDataset:
    lines = text.splitlines()
    if not lines or not lines[0].startswith("LOCATION"):
        raise ValueError("Not an EPW file: first line must start with LOCATION")
    loc = lines[0].split(",")
    city, state, country, src, wmo = loc[1], loc[2], loc[3], loc[4], loc[5]
    lat, lon, tz_off, elev = float(loc[6]), float(loc[7]), float(loc[8]), float(loc[9])
    header_end = next(i for i, ln in enumerate(lines) if ln.startswith("DATA PERIODS")) + 1
    ground: dict[float, list[float]] = {}
    comments = []
    for ln in lines[:header_end]:
        if ln.startswith("GROUND TEMPERATURES"):
            parts = ln.split(",")
            n = int(parts[1])
            pos = 2
            for _ in range(n):
                depth = float(parts[pos])
                vals = [float(v) for v in parts[pos + 4: pos + 16]]
                ground[depth] = vals
                pos += 16
        if ln.startswith("COMMENTS"):
            comments.append(ln.split(",", 1)[1].strip())
    rows = [r.split(",") for r in lines[header_end:] if r.strip()]
    arr = rows
    years = np.array([int(r[0]) for r in arr])
    months = np.array([int(r[1]) for r in arr])
    days = np.array([int(r[2]) for r in arr])
    hours = np.array([int(r[3]) for r in arr])
    data = {}
    missing_mask = {}
    for col, name, code in EPW_FIELDS:
        vals = np.array([float(r[col]) if len(r) > col and r[col] not in ("", "?") else np.nan for r in arr])
        miss = ~np.isfinite(vals) | (vals >= code)
        if name == "cloud_pct":
            vals = vals * 10.0
        vals[miss] = np.nan
        data[name] = vals
        missing_mask[name] = miss
    # Wind speed of 0 with direction 0 is calm, not missing.
    is_typical = len(set(years.tolist())) > 1 or "TMY" in src.upper()
    yr = np.full(len(arr), NOMINAL_YEAR) if is_typical else years
    # Feb 29 rows in a typical year mapped onto a non-leap year are dropped.
    keep = ~((months == 2) & (days == 29)) if is_typical else np.ones(len(arr), bool)
    local_std = pd.to_datetime({"year": yr[keep], "month": months[keep], "day": days[keep]}) + pd.to_timedelta(hours[keep] - 1, unit="h")
    utc = (local_std - pd.to_timedelta(tz_off, unit="h")).dt.tz_localize("UTC")
    df = pd.DataFrame({k: v[keep] for k, v in data.items()}, index=pd.DatetimeIndex(utc, name="time_utc"))
    df = df.sort_index()
    flags = pd.DataFrame({k: missing_mask[k][keep].astype("int16") for k in data}, index=df.index).sort_index()
    month_years = {}
    for m in range(1, 13):
        ys = sorted(set(years[months == m].tolist()))
        month_years[m] = ys
    transformations = [
        f"EPW local standard time (UTC{tz_off:+g}), hour-ending → hour-beginning UTC",
        "missing-value codes → NaN (flag bit 1)",
        "total sky cover tenths → percent",
    ]
    if is_typical:
        transformations.append(f"typical-year months re-labelled onto nominal year {NOMINAL_YEAR}; Feb 29 dropped if present")
    dist = haversine_km(site_lat, site_lon, lat, lon) if site_lat is not None else None
    prov = Provenance(
        provider="epw_file",
        product=product or f"EPW ({src})",
        source_url=source_url,
        endpoint=None,
        retrieved_at=now_iso(),
        requested_lat=site_lat if site_lat is not None else lat,
        requested_lon=site_lon if site_lon is not None else lon,
        actual_lat=lat,
        actual_lon=lon,
        actual_elevation_m=elev,
        site_elevation_m=site_elevation_m,
        distance_km=dist,
        elevation_difference_m=(site_elevation_m - elev) if site_elevation_m is not None else None,
        spatial_resolution=f"point (station WMO {wmo}: {city}, {state}, {country})",
        temporal_resolution="hourly",
        units={"t_db_c": "°C", "t_dp_c": "°C", "rh_pct": "%", "p_pa": "Pa", "wind_ms": "m/s", "wind_dir_deg": "°",
               "ghi_wm2": "W/m² (hour-integrated)", "dni_wm2": "W/m²", "dhi_wm2": "W/m²", "ir_horiz_wm2": "W/m²",
               "cloud_pct": "%", "precip_mm": "mm"},
        timezone=f"UTC{tz_off:+g} (standard time, no DST)",
        reference_period="; ".join(f"{m}:{'/'.join(map(str, ys))}" for m, ys in month_years.items()),
        data_nature=data_nature if is_typical else "observed",
        licence=licence,
        citation=citation,
        missing_pct={k: float(100 * missing_mask[k][keep].mean()) for k in data},
        transformations=transformations,
        station_id=wmo,
        variables=list(data),
        notes=comments + ([
            "EPW GROUND TEMPERATURES are modeled by the weather converter from air temperature with a default soil "
            "diffusivity; they are not measurements."] if ground else []),
        extra={"source_years_by_month": month_years, "epw_source_field": src,
               "sha256": hashlib.sha256(text.encode("latin-1")).hexdigest()},
    )
    return ClimateDataset(df, prov, flags, monthly_ground_c=ground)


class EPWProvider(ClimateProvider):
    """Reads a local EPW (optionally gzipped)."""

    name = "epw_file"
    product = "EnergyPlus Weather file"

    def __init__(self, path: Path | str, **meta):
        self.path = Path(path)
        self.meta = meta

    def fetch(self, request: ClimateRequest | None = None) -> ClimateDataset:
        text = _open_text(self.path)
        kw = dict(self.meta)
        if request is not None:
            kw.setdefault("site_lat", request.latitude)
            kw.setdefault("site_lon", request.longitude)
            kw.setdefault("site_elevation_m", request.elevation_m)
        return parse_epw(text, source_name=self.path.name, **kw)


def parse_epw_bytes(raw: bytes, **kw) -> ClimateDataset:
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return parse_epw(io.BytesIO(raw).read().decode("latin-1"), **kw)
