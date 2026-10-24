"""Unit handling.

All model code works in SI (temperatures of state in °C, absolute temperatures in K
inside equations). Conversions happen only at the ingestion and display boundaries,
through the pint registry below so that dimensional mistakes raise errors instead of
silently producing wrong numbers.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pint

ureg = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)
Q_ = ureg.Quantity

# Canonical SI unit for each canonical climate column.
CANONICAL_UNITS: dict[str, str] = {
    "t_db_c": "degC",
    "t_dp_c": "degC",
    "t_wb_c": "degC",
    "rh_pct": "percent",
    "p_pa": "Pa",
    "wind_ms": "m/s",
    "wind_gust_ms": "m/s",
    "wind_dir_deg": "degree",
    "ghi_wm2": "W/m**2",
    "dni_wm2": "W/m**2",
    "dhi_wm2": "W/m**2",
    "ir_horiz_wm2": "W/m**2",
    "cloud_pct": "percent",
    "precip_mm": "mm",
}

# Accepted aliases for units supplied by providers or uploaded files.
UNIT_ALIASES: dict[str, str] = {
    "°c": "degC", "c": "degC", "degc": "degC", "celsius": "degC",
    "°f": "degF", "f": "degF", "degf": "degF", "fahrenheit": "degF",
    "k": "kelvin", "kelvin": "kelvin",
    "%": "percent", "percent": "percent", "fraction": "dimensionless",
    "pa": "Pa", "hpa": "hPa", "kpa": "kPa", "mbar": "mbar", "bar": "bar", "inhg": "inch_Hg",
    "m/s": "m/s", "km/h": "km/h", "kmh": "km/h", "mph": "mph", "kn": "knot", "knots": "knot", "kt": "knot",
    "w/m²": "W/m**2", "w/m2": "W/m**2", "w/m^2": "W/m**2", "kw/m2": "kW/m**2",
    "mj/m2": "MJ/m**2", "wh/m2": "W*hour/m**2",
    "mm": "mm", "in": "inch", "m": "m",
    "°": "degree", "deg": "degree", "degree": "degree", "degrees": "degree",
}


def normalise_unit(unit: str) -> str:
    key = unit.strip().lower().replace(" ", "")
    return UNIT_ALIASES.get(key, unit)


@lru_cache(maxsize=256)
def _factor_offset(src: str, dst: str) -> tuple[float, float]:
    """Return (scale, offset) such that dst = scale*src + offset for linear/affine units."""
    a = Q_(0.0, src).to(dst).magnitude
    b = Q_(1.0, src).to(dst).magnitude
    return b - a, a


def convert(values, src_unit: str, dst_unit: str):
    """Convert an array-like of magnitudes from src_unit to dst_unit (affine units supported)."""
    src = normalise_unit(src_unit)
    dst = normalise_unit(dst_unit)
    if src == dst:
        return np.asarray(values, dtype=float)
    scale, offset = _factor_offset(src, dst)
    return np.asarray(values, dtype=float) * scale + offset


def is_compatible(src_unit: str, dst_unit: str) -> bool:
    try:
        Q_(1.0, normalise_unit(src_unit)).to(normalise_unit(dst_unit))
        return True
    except (pint.errors.DimensionalityError, pint.errors.UndefinedUnitError):
        return False


def j_to_kwh(j):
    return np.asarray(j, dtype=float) / 3.6e6


def w_to_kwh(power_w, dt_s: float = 3600.0):
    """Energy in kWh of a constant power (W) held for dt_s seconds."""
    return np.asarray(power_w, dtype=float) * dt_s / 3.6e6
