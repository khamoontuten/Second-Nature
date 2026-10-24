"""User-supplied CSV climate data (station records, local met-service exports such as IMD data obtained
under the supplier's licence, or measured site data).

The engineer supplies (or accepts auto-detected) column mappings and units; every conversion is recorded.
"""

from __future__ import annotations

import io

import numpy as np
import pandas as pd

from app.climate.providers.base import ClimateDataset, Provenance, haversine_km, now_iso
from app.core.errors import ValidationFailure
from app.core.units import CANONICAL_UNITS, convert, is_compatible

AUTO_COLUMNS = {
    "t_db_c": ["t_db_c", "temperature", "temp", "dry_bulb", "drybulb", "t2m", "air_temperature", "tair"],
    "t_dp_c": ["t_dp_c", "dew_point", "dewpoint", "dew", "t2mdew", "td"],
    "rh_pct": ["rh_pct", "rh", "relative_humidity", "humidity", "rh2m"],
    "p_pa": ["p_pa", "pressure", "station_pressure", "surface_pressure", "ps"],
    "wind_ms": ["wind_ms", "wind_speed", "windspeed", "ws", "ws10m"],
    "wind_dir_deg": ["wind_dir_deg", "wind_direction", "winddir", "wd", "wd10m"],
    "ghi_wm2": ["ghi_wm2", "ghi", "global_horizontal", "shortwave_radiation", "solar_radiation"],
    "dni_wm2": ["dni_wm2", "dni", "direct_normal"],
    "dhi_wm2": ["dhi_wm2", "dhi", "diffuse_horizontal", "diffuse"],
    "cloud_pct": ["cloud_pct", "cloud_cover", "cloud"],
    "precip_mm": ["precip_mm", "precipitation", "rain", "rainfall"],
}
TIME_COLUMNS = ["time", "timestamp", "datetime", "date_time", "time_utc", "date"]


def parse_csv(raw: bytes, *, latitude: float, longitude: float, elevation_m: float | None = None,
              mapping: dict[str, dict] | None = None, time_column: str | None = None, timezone: str = "UTC",
              data_nature: str = "observed", source_description: str = "user upload", station_lat: float | None = None,
              station_lon: float | None = None, station_elevation_m: float | None = None, licence: str = "as declared by uploader"
              ) -> ClimateDataset:
    """mapping: {canonical: {"column": name, "unit": unit}}; missing entries are auto-detected (unit = canonical)."""
    try:
        df_raw = pd.read_csv(io.BytesIO(raw))
    except Exception as e:  # noqa: BLE001
        raise ValidationFailure(f"could not parse CSV: {e}") from e
    cols_lower = {c.lower().strip(): c for c in df_raw.columns}
    tcol = time_column or next((cols_lower[c] for c in TIME_COLUMNS if c in cols_lower), None)
    if tcol is None or tcol not in df_raw.columns:
        raise ValidationFailure(f"no timestamp column found; expected one of {TIME_COLUMNS} or set time_column")
    times = pd.to_datetime(df_raw[tcol], errors="coerce")
    if times.isna().any():
        raise ValidationFailure(f"{int(times.isna().sum())} unparseable timestamps in column {tcol!r}")
    if times.dt.tz is None:
        try:
            times = times.dt.tz_localize(timezone, ambiguous="NaT", nonexistent="NaT")
        except Exception as e:  # noqa: BLE001
            raise ValidationFailure(f"invalid timezone {timezone!r}: {e}") from e
        if times.isna().any():
            raise ValidationFailure(f"{int(times.isna().sum())} timestamps are ambiguous/non-existent in {timezone} (DST); "
                                    "supply UTC timestamps or a fixed-offset zone")
    idx = pd.DatetimeIndex(times.dt.tz_convert("UTC"), name="time_utc")
    mapping = dict(mapping or {})
    transformations = [f"timestamps parsed from {tcol!r} as {timezone} → UTC"]
    out = {}
    for canon, aliases in AUTO_COLUMNS.items():
        m = mapping.get(canon)
        if m is None:
            hit = next((cols_lower[a] for a in aliases if a in cols_lower), None)
            if hit is None:
                continue
            m = {"column": hit, "unit": CANONICAL_UNITS.get(canon, "")}
            transformations.append(f"auto-mapped {hit!r} → {canon} (assumed {m['unit']})")
        col, unit = m["column"], m.get("unit") or CANONICAL_UNITS.get(canon, "")
        if col not in df_raw.columns:
            raise ValidationFailure(f"mapped column {col!r} for {canon} not in file")
        target = CANONICAL_UNITS.get(canon)
        vals = pd.to_numeric(df_raw[col], errors="coerce").to_numpy(dtype=float)
        if target and unit and unit != target:
            if not is_compatible(unit, target):
                raise ValidationFailure(f"unit {unit!r} of {col!r} is not compatible with {target}")
            vals = convert(vals, unit, target)
            transformations.append(f"{col}: {unit} → {target}")
        out[canon] = vals
    if "t_db_c" not in out:
        raise ValidationFailure("CSV must contain a dry-bulb temperature column")
    df = pd.DataFrame(out, index=idx)
    dup = df.index.duplicated(keep="first")
    if dup.any():
        transformations.append(f"{int(dup.sum())} duplicate timestamps dropped (first kept)")
        df = df[~dup]
    df = df.sort_index()
    s_lat = station_lat if station_lat is not None else latitude
    s_lon = station_lon if station_lon is not None else longitude
    prov = Provenance(
        provider="csv_upload", product=source_description, source_url="", endpoint=None, retrieved_at=now_iso(),
        requested_lat=latitude, requested_lon=longitude, actual_lat=s_lat, actual_lon=s_lon,
        actual_elevation_m=station_elevation_m, site_elevation_m=elevation_m,
        distance_km=haversine_km(latitude, longitude, s_lat, s_lon),
        elevation_difference_m=(elevation_m - station_elevation_m) if (elevation_m is not None and station_elevation_m is not None) else None,
        spatial_resolution="point (as declared)", temporal_resolution=_infer_resolution(df.index), units={c: CANONICAL_UNITS.get(c, "") for c in df},
        timezone=timezone, reference_period=f"{df.index[0]} – {df.index[-1]}", data_nature=data_nature, licence=licence, citation="",
        missing_pct={c: float(100 * np.isnan(df[c]).mean()) for c in df}, transformations=transformations, variables=list(df),
        notes=["Uploaded data; provenance as declared by the uploader."],
    )
    return ClimateDataset(df, prov)


def _infer_resolution(idx: pd.DatetimeIndex) -> str:
    if len(idx) < 2:
        return "unknown"
    step = pd.Series(idx).diff().median()
    return f"{step}"
