"""Hourly simulation context: canonical climate arrays + derived psychrometric state."""

from __future__ import annotations

from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.climate.providers.base import ClimateDataset
from app.core.errors import InsufficientData
from app.physics import psychrometrics as ps
from app.physics import radiative, solar

REQUIRED = ["t_db_c", "wind_ms"]


@dataclass
class ClimateArrays:
    index: pd.DatetimeIndex            # UTC
    local_index: pd.DatetimeIndex      # site-local time (for schedules and reporting)
    t_db: np.ndarray
    w: np.ndarray
    p: np.ndarray
    t_dp: np.ndarray
    t_wb: np.ndarray
    rh: np.ndarray
    h: np.ndarray
    rho: np.ndarray
    wind: np.ndarray
    wind_dir: np.ndarray
    ghi: np.ndarray
    dni: np.ndarray
    dhi: np.ndarray
    t_sky: np.ndarray
    zenith: np.ndarray
    azimuth: np.ndarray
    lat: float
    lon: float
    elevation_m: float
    notes: list[str] = field(default_factory=list)
    gap_filled: dict[str, int] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.t_db)


def _local_index(idx: pd.DatetimeIndex, tz: str, fallback_offset_h: float | None) -> pd.DatetimeIndex:
    try:
        return idx.tz_convert(ZoneInfo(tz))
    except Exception:  # noqa: BLE001 - invalid / unknown zone: fall back to fixed offset
        off = fallback_offset_h or 0.0
        return (idx + pd.Timedelta(hours=off)).tz_localize(None)


def build_context(ds: ClimateDataset, *, lat: float, lon: float, elevation_m: float, timezone: str = "UTC",
                  warming_offset_k: float = 0.0, max_fill_h: int = 6) -> ClimateArrays:
    """Prepare arrays for the simulation.

    * Short gaps (≤ max_fill_h) in core variables are linearly interpolated *and counted*;
      longer gaps raise InsufficientData — the engine never invents long stretches of weather.
    * Pressure missing → standard atmosphere at site elevation (recorded).
    * Humidity from dew point if present, else RH.
    * A uniform warming offset (sensitivity test, not a projection) shifts dry bulb and dew point
      equally, i.e. holding relative humidity approximately but raising absolute humidity.
    """
    df = ds.data.copy()
    notes: list[str] = []
    filled: dict[str, int] = {}
    for col in REQUIRED:
        if col not in df or df[col].isna().all():
            raise InsufficientData(f"Climate dataset lacks required variable {col}")
    if "t_dp_c" not in df and "rh_pct" not in df:
        raise InsufficientData("Climate dataset needs dew point or relative humidity")

    def fill(col):
        s = df[col]
        n_missing = int(s.isna().sum())
        if n_missing == 0:
            return s
        runs = s.isna().astype(int).groupby(s.notna().cumsum()).sum()
        if runs.max() > max_fill_h:
            raise InsufficientData(
                f"{col}: gap of {int(runs.max())} h exceeds the {max_fill_h} h interpolation limit; "
                "supply a complete dataset or another source")
        filled[col] = n_missing
        return s.interpolate(limit=max_fill_h, limit_direction="both")

    t_db = fill("t_db_c").to_numpy(float) + warming_offset_k
    wind = fill("wind_ms").to_numpy(float)
    if "p_pa" in df and df["p_pa"].notna().mean() > 0.9:
        p = fill("p_pa").to_numpy(float)
    else:
        p = np.full(len(df), float(ps.pressure_at_elevation(elevation_m)))
        notes.append("pressure: standard atmosphere at site elevation (dataset lacked pressure)")
    if "t_dp_c" in df and df["t_dp_c"].notna().mean() > 0.9:
        t_dp = fill("t_dp_c").to_numpy(float) + warming_offset_k
        t_dp = np.minimum(t_dp, t_db)
        w = ps.humidity_ratio_from_dew_point(t_dp, p)
    else:
        rh_in = fill("rh_pct").to_numpy(float)
        w = ps.humidity_ratio_from_rh(t_db, np.clip(rh_in, 0.5, 100), p)
        t_dp = ps.dew_point_from_humidity_ratio(w, p)
    rh = np.clip(ps.rh_from_humidity_ratio(t_db, w, p), 0, 100)
    t_wb = ps.wet_bulb_from_humidity_ratio(t_db, w, p)
    h = ps.enthalpy(t_db, w)
    rho = ps.moist_air_density(t_db, w, p)
    wind_dir = df["wind_dir_deg"].fillna(0.0).to_numpy(float) if "wind_dir_deg" in df else np.zeros(len(df))

    def opt(col):
        if col in df and df[col].notna().any():
            return df[col].fillna(0.0).to_numpy(float)
        return None

    ghi, dni, dhi = opt("ghi_wm2"), opt("dni_wm2"), opt("dhi_wm2")
    if ghi is None:
        notes.append("solar radiation unavailable: solar gains set to zero (envelope and facade results unreliable)")
        ghi = np.zeros(len(df))
    sp = solar.solar_position(df.index, lat, lon, elevation_m)
    zen = sp["zenith"].to_numpy(float)
    az = sp["azimuth"].to_numpy(float)
    if dni is None or dhi is None:
        # Erbs decomposition is not attempted silently; treat all GHI as diffuse (conservative for walls) and note it.
        notes.append("DNI/DHI unavailable: global radiation treated as diffuse")
        dni = np.zeros(len(df))
        dhi = ghi.copy()
    ir = df["ir_horiz_wm2"].to_numpy(float) if "ir_horiz_wm2" in df else None
    cloud = df["cloud_pct"].to_numpy(float) if "cloud_pct" in df else None
    t_sky = radiative.sky_temperature(t_db, t_dp, cloud, ir)
    local = _local_index(df.index, timezone, None)
    return ClimateArrays(df.index, local, t_db, w, p, t_dp, t_wb, rh, h, rho, wind, wind_dir, ghi, dni, dhi, t_sky,
                         zen, az, lat, lon, elevation_m, notes, filled)
