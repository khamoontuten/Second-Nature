"""NetCDF climate files (ERA5 / ERA5-Land downloads or user uploads) → canonical hourly table.

ERA5 conventions handled:
* temperatures in K → °C; surface pressure in Pa; wind from u/v components → speed and direction;
* accumulated fields (ssrd, strd, tp): ERA5 single-level hourly data are 1-hour accumulations
  (J/m², m) → divided by 3600 s / ×1000 mm. ERA5-Land accumulates from 00 UTC, so values are
  de-accumulated by differencing within each day (``accumulation="daily"``);
* the field at a timestamp is the accumulation over the preceding hour → shifted −1 h;
* soil temperature levels stl1..stl4 (ERA5 layers 0–7, 7–28, 28–100, 100–289 cm).
The nearest grid point to the site is selected and its coordinates recorded.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from app.climate.providers.base import ClimateDataset, Provenance, haversine_km, now_iso
from app.core.errors import ValidationFailure

ALIASES = {
    "t2m": "t2m", "2m_temperature": "t2m", "d2m": "d2m", "2m_dewpoint_temperature": "d2m", "sp": "sp",
    "surface_pressure": "sp", "u10": "u10", "v10": "v10", "ssrd": "ssrd", "strd": "strd", "tcc": "tcc", "tp": "tp",
    "stl1": "stl1", "stl2": "stl2", "stl3": "stl3", "stl4": "stl4", "fdir": "fdir",
}
SOIL = {"stl1": ("t_soil_0_07_c", 0.035), "stl2": ("t_soil_07_28_c", 0.175), "stl3": ("t_soil_28_100_c", 0.64),
        "stl4": ("t_soil_100_289_c", 1.945)}


def _time_dim(ds: xr.Dataset) -> str:
    for name in ("valid_time", "time"):
        if name in ds.dims or name in ds.coords:
            return name
    raise ValidationFailure("NetCDF file has no time / valid_time coordinate")


def parse_netcdf(source: bytes | str | Path, *, latitude: float, longitude: float, elevation_m: float | None = None,
                 accumulation: str = "hourly", product: str = "ERA5 (NetCDF)", data_nature: str = "reanalysis",
                 source_url: str = "https://cds.climate.copernicus.eu/", licence: str = "Copernicus licence (CC BY 4.0 compatible); attribution required") -> ClimateDataset:
    if isinstance(source, bytes):
        ds = xr.open_dataset(io.BytesIO(source), engine="h5netcdf")
    else:
        ds = xr.open_dataset(source, engine="h5netcdf")
    tdim = _time_dim(ds)
    lat_name = "latitude" if "latitude" in ds.coords else "lat"
    lon_name = "longitude" if "longitude" in ds.coords else "lon"
    lon_q = longitude
    if float(ds[lon_name].max()) > 180 and longitude < 0:
        lon_q = longitude + 360
    pt = ds.sel({lat_name: latitude, lon_name: lon_q}, method="nearest")
    act_lat, act_lon = float(pt[lat_name]), float(pt[lon_name])
    if act_lon > 180:
        act_lon -= 360
    times = pd.DatetimeIndex(pd.to_datetime(pt[tdim].values), name="time_utc").tz_localize("UTC")
    vars_ = {ALIASES[v]: np.asarray(pt[v].values, dtype=float).reshape(-1) for v in pt.data_vars if v in ALIASES}
    transformations = [f"nearest grid point selected ({act_lat:.3f}, {act_lon:.3f})"]
    out = {}

    def deacc(x):
        if accumulation == "daily":
            # ERA5-Land: the value at 01 UTC is the 00–01 accumulation; later hours accumulate further; the
            # 00 UTC value closes the previous day. Hourly amount = difference with the previous hour,
            # except at 01 UTC where the stored value is already the hourly amount.
            s = pd.Series(x, index=times)
            d = s.diff()
            first = s.index.hour == 1
            d[first] = s[first]
            return d.clip(lower=0).to_numpy()
        return x

    if "t2m" in vars_:
        out["t_db_c"] = vars_["t2m"] - 273.15
    if "d2m" in vars_:
        out["t_dp_c"] = vars_["d2m"] - 273.15
    if "sp" in vars_:
        out["p_pa"] = vars_["sp"]
    if "u10" in vars_ and "v10" in vars_:
        u, v = vars_["u10"], vars_["v10"]
        out["wind_ms"] = np.hypot(u, v)
        out["wind_dir_deg"] = (np.degrees(np.arctan2(-u, -v)) + 360.0) % 360.0
        transformations.append("wind speed/direction from u10/v10 (direction the wind blows from)")
    shifted = []
    if "ssrd" in vars_:
        out["ghi_wm2"] = deacc(vars_["ssrd"]) / 3600.0
        shifted.append("ghi_wm2")
    if "strd" in vars_:
        out["ir_horiz_wm2"] = deacc(vars_["strd"]) / 3600.0
        shifted.append("ir_horiz_wm2")
    if "tp" in vars_:
        out["precip_mm"] = deacc(vars_["tp"]) * 1000.0
        shifted.append("precip_mm")
    if "tcc" in vars_:
        out["cloud_pct"] = vars_["tcc"] * 100.0
    soil = {}
    for k, (col, depth) in SOIL.items():
        if k in vars_:
            out[col] = vars_[k] - 273.15
            soil[col] = depth
    if not out:
        raise ValidationFailure("NetCDF file contains no recognised climate variables (expected ERA5 short names)")
    df = pd.DataFrame(out, index=times).sort_index()
    for c in shifted:
        df[c] = df[c].shift(-1)
    transformations += ["K → °C", "accumulations J/m² → W/m² (÷3600), m → mm",
                        f"accumulation type: {accumulation}", "accumulated fields shifted −1 h to hour-beginning"]
    prov = Provenance(
        provider="netcdf", product=product, source_url=source_url, endpoint=None, retrieved_at=now_iso(),
        requested_lat=latitude, requested_lon=longitude, actual_lat=act_lat, actual_lon=act_lon, actual_elevation_m=None,
        site_elevation_m=elevation_m, distance_km=haversine_km(latitude, longitude, act_lat, act_lon),
        elevation_difference_m=None, spatial_resolution="grid (see product)", temporal_resolution="hourly",
        units={"t_db_c": "°C", "t_dp_c": "°C", "p_pa": "Pa", "wind_ms": "m/s", "ghi_wm2": "W/m²", "ir_horiz_wm2": "W/m²",
               "precip_mm": "mm", "cloud_pct": "%"},
        timezone="UTC", reference_period=f"{df.index[0]} – {df.index[-1]}", data_nature=data_nature, licence=licence,
        citation="Hersbach, H. et al. (2020) The ERA5 global reanalysis. QJRMS 146, 1999–2049.",
        missing_pct={c: float(100 * df[c].isna().mean()) for c in df.columns}, transformations=transformations,
        variables=list(df.columns), notes=["Reanalysis grid-cell values; soil levels are model layers, not site measurements."],
    )
    return ClimateDataset(df, prov, soil_layers_m=soil)
