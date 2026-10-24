"""Moist-air psychrometrics (SI), vectorised with NumPy.

Formulations: ASHRAE Handbook—Fundamentals (2017), Chapter 1 "Psychrometrics":
* saturation pressure over ice (eq. 5) and over liquid water (eq. 6) — Hyland & Wexler (1983);
* humidity ratio (eq. 20/22), enthalpy (eq. 30), specific volume (eq. 26/28);
* thermodynamic wet-bulb (eq. 33 for t* ≥ 0 °C, eq. 35 for t* < 0 °C) solved by bisection;
* standard atmospheric pressure with altitude (eq. 3).

Ideal-gas mixture relations are used (the enhancement factor is neglected, an error below
~0.5 % in humidity ratio at normal pressures — documented in ASHRAE Ch.1).

Operating range: −100 … +200 °C for saturation pressure; psychrometric relations
are intended for atmospheric pressures 50–110 kPa.

Units: t [°C], p [Pa], W [kg_w/kg_da], h [J/kg_da], RH [%], v [m³/kg_da], ρ [kg/m³ moist air].
"""

from __future__ import annotations

import numpy as np

from app.core.constants import CP_DA, CP_V, EPS_W, H_FG0, R_DA, T0_K

ArrayLike = float | np.ndarray

# ASHRAE 2017 Ch.1 eq. 5 (ice, −100 … 0 °C)
_C1, _C2, _C3, _C4, _C5, _C6, _C7 = (
    -5.6745359e03, 6.3925247e00, -9.6778430e-03, 6.2215701e-07, 2.0747825e-09, -9.4840240e-13, 4.1635019e00,
)
# ASHRAE 2017 Ch.1 eq. 6 (liquid water, 0 … 200 °C)
_C8, _C9, _C10, _C11, _C12, _C13 = (
    -5.8002206e03, 1.3914993e00, -4.8640239e-02, 4.1764768e-05, -1.4452093e-08, 6.5459673e00,
)


def _a(x) -> np.ndarray:
    return np.asarray(x, dtype=float)


def pressure_at_elevation(z_m: ArrayLike) -> np.ndarray:
    """Standard atmospheric pressure (Pa) at elevation z (m). ASHRAE 2017 Ch.1 eq. 3."""
    return 101325.0 * (1.0 - 2.25577e-5 * _a(z_m)) ** 5.2559


def _ln_pws(t_k: np.ndarray) -> np.ndarray:
    ice = t_k < T0_K
    ln_ice = _C1 / t_k + _C2 + _C3 * t_k + _C4 * t_k**2 + _C5 * t_k**3 + _C6 * t_k**4 + _C7 * np.log(t_k)
    ln_wat = _C8 / t_k + _C9 + _C10 * t_k + _C11 * t_k**2 + _C12 * t_k**3 + _C13 * np.log(t_k)
    return np.where(ice, ln_ice, ln_wat)


def _dln_pws_dT(t_k: np.ndarray) -> np.ndarray:
    ice = t_k < T0_K
    d_ice = -_C1 / t_k**2 + _C3 + 2 * _C4 * t_k + 3 * _C5 * t_k**2 + 4 * _C6 * t_k**3 + _C7 / t_k
    d_wat = -_C8 / t_k**2 + _C10 + 2 * _C11 * t_k + 3 * _C12 * t_k**2 + _C13 / t_k
    return np.where(ice, d_ice, d_wat)


def saturation_pressure(t_c: ArrayLike) -> np.ndarray:
    """Saturation vapour pressure p_ws (Pa) over ice (t<0 °C) or liquid water (t≥0 °C)."""
    return np.exp(_ln_pws(_a(t_c) + T0_K))


def vapour_pressure_from_rh(t_c: ArrayLike, rh_pct: ArrayLike) -> np.ndarray:
    return saturation_pressure(t_c) * _a(rh_pct) / 100.0


def humidity_ratio_from_vapour_pressure(p_w: ArrayLike, p: ArrayLike) -> np.ndarray:
    """W = 0.621945 p_w / (p − p_w)   (ASHRAE 2017 eq. 20)."""
    p_w = _a(p_w)
    return EPS_W * p_w / (_a(p) - p_w)


def vapour_pressure_from_humidity_ratio(w: ArrayLike, p: ArrayLike) -> np.ndarray:
    w = _a(w)
    return _a(p) * w / (EPS_W + w)


def humidity_ratio_from_rh(t_c: ArrayLike, rh_pct: ArrayLike, p: ArrayLike) -> np.ndarray:
    return humidity_ratio_from_vapour_pressure(vapour_pressure_from_rh(t_c, rh_pct), p)


def saturation_humidity_ratio(t_c: ArrayLike, p: ArrayLike) -> np.ndarray:
    return humidity_ratio_from_vapour_pressure(saturation_pressure(t_c), p)


def rh_from_humidity_ratio(t_c: ArrayLike, w: ArrayLike, p: ArrayLike) -> np.ndarray:
    return 100.0 * vapour_pressure_from_humidity_ratio(w, p) / saturation_pressure(t_c)


def dew_point_from_vapour_pressure(p_w: ArrayLike, tol: float = 1e-6, max_iter: int = 50) -> np.ndarray:
    """Dew/frost point (°C) by Newton inversion of the ASHRAE saturation-pressure equations.

    Below 0 °C the result is the frost point (saturation over ice), consistent with ASHRAE.
    """
    p_w = np.maximum(_a(p_w), 1e-6)
    ln_p = np.log(p_w)
    # Initial estimate: Magnus formula over water.
    gamma = np.log(p_w / 610.94)
    t = 243.04 * gamma / (17.625 - gamma) + T0_K
    for _ in range(max_iter):
        f = _ln_pws(t) - ln_p
        step = f / _dln_pws_dT(t)
        t = t - step
        if np.all(np.abs(step) < tol):
            break
    return t - T0_K


def dew_point_from_rh(t_c: ArrayLike, rh_pct: ArrayLike) -> np.ndarray:
    return dew_point_from_vapour_pressure(vapour_pressure_from_rh(t_c, np.clip(_a(rh_pct), 1e-3, 100.0)))


def dew_point_from_humidity_ratio(w: ArrayLike, p: ArrayLike) -> np.ndarray:
    return dew_point_from_vapour_pressure(vapour_pressure_from_humidity_ratio(w, p))


def humidity_ratio_from_dew_point(t_dp_c: ArrayLike, p: ArrayLike) -> np.ndarray:
    return humidity_ratio_from_vapour_pressure(saturation_pressure(t_dp_c), p)


def rh_from_dew_point(t_c: ArrayLike, t_dp_c: ArrayLike) -> np.ndarray:
    return 100.0 * saturation_pressure(t_dp_c) / saturation_pressure(t_c)


def enthalpy(t_c: ArrayLike, w: ArrayLike) -> np.ndarray:
    """Moist-air specific enthalpy, J/kg dry air: h = 1006 t + W (2 501 000 + 1860 t)  (ASHRAE eq. 30)."""
    t = _a(t_c)
    return CP_DA * t + _a(w) * (H_FG0 + CP_V * t)


def temperature_from_enthalpy(h: ArrayLike, w: ArrayLike) -> np.ndarray:
    w = _a(w)
    return (_a(h) - H_FG0 * w) / (CP_DA + CP_V * w)


def humid_specific_heat(w: ArrayLike) -> np.ndarray:
    """c_p of moist air per kg dry air, J/(kg_da·K)."""
    return CP_DA + CP_V * _a(w)


def specific_volume(t_c: ArrayLike, w: ArrayLike, p: ArrayLike) -> np.ndarray:
    """v = R_da T (1 + 1.607858 W) / p, m³/kg dry air (ASHRAE eq. 26/28)."""
    return R_DA * (_a(t_c) + T0_K) * (1.0 + 1.607858 * _a(w)) / _a(p)


def moist_air_density(t_c: ArrayLike, w: ArrayLike, p: ArrayLike) -> np.ndarray:
    """Density of moist air, kg/m³: ρ = (1 + W) / v."""
    return (1.0 + _a(w)) / specific_volume(t_c, w, p)


def _w_from_wet_bulb(t_c: np.ndarray, t_wb_c: np.ndarray, p: np.ndarray) -> np.ndarray:
    """ASHRAE eq. 33 (t* ≥ 0) and eq. 35 (t* < 0); kJ-based coefficients as in the Handbook."""
    ws_star = saturation_humidity_ratio(t_wb_c, p)
    w_pos = ((2501.0 - 2.326 * t_wb_c) * ws_star - 1.006 * (t_c - t_wb_c)) / (2501.0 + 1.86 * t_c - 4.186 * t_wb_c)
    w_neg = ((2830.0 - 0.24 * t_wb_c) * ws_star - 1.006 * (t_c - t_wb_c)) / (2830.0 + 1.86 * t_c - 2.1 * t_wb_c)
    return np.where(t_wb_c >= 0.0, w_pos, w_neg)


def humidity_ratio_from_wet_bulb(t_c: ArrayLike, t_wb_c: ArrayLike, p: ArrayLike) -> np.ndarray:
    t_c, t_wb_c, p = np.broadcast_arrays(_a(t_c), _a(t_wb_c), _a(p))
    return _w_from_wet_bulb(t_c, t_wb_c, p)


def wet_bulb_from_humidity_ratio(t_c: ArrayLike, w: ArrayLike, p: ArrayLike, tol: float = 1e-5) -> np.ndarray:
    """Thermodynamic wet-bulb temperature (°C) by vectorised bisection between dew point and dry bulb."""
    t_c, w, p = np.broadcast_arrays(_a(t_c), _a(w), _a(p))
    lo = dew_point_from_humidity_ratio(np.maximum(w, 1e-9), p) - 0.5
    hi = t_c.copy()
    lo = np.minimum(lo, hi)
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        w_mid = _w_from_wet_bulb(t_c, mid, p)
        # W computed from a trial t* increases with t*; root where w_mid == w.
        too_high = w_mid > w
        hi = np.where(too_high, mid, hi)
        lo = np.where(too_high, lo, mid)
        if np.all(hi - lo < tol):
            break
    return 0.5 * (lo + hi)


def wet_bulb_from_rh(t_c: ArrayLike, rh_pct: ArrayLike, p: ArrayLike) -> np.ndarray:
    return wet_bulb_from_humidity_ratio(t_c, humidity_ratio_from_rh(t_c, rh_pct, p), p)


def wet_bulb_stull(t_c: ArrayLike, rh_pct: ArrayLike) -> np.ndarray:
    """Stull (2011) empirical wet-bulb at 101.325 kPa (errors −1 … +0.65 °C, mean abs. < 0.3 °C; RH 5–99 %, t −20…50 °C).

    Provided only as a cross-check; the model uses the thermodynamic wet bulb.
    """
    t, rh = _a(t_c), _a(rh_pct)
    return (t * np.arctan(0.151977 * np.sqrt(rh + 8.313659)) + np.arctan(t + rh) - np.arctan(rh - 1.676331)
            + 0.00391838 * rh**1.5 * np.arctan(0.023101 * rh) - 4.686035)


def saturation_temperature_from_enthalpy(h: ArrayLike, p: ArrayLike, tol: float = 1e-5) -> np.ndarray:
    """Temperature (°C) of saturated air with enthalpy h (J/kg_da), by bisection in −40…90 °C."""
    h, p = np.broadcast_arrays(_a(h), _a(p))
    lo = np.full(h.shape, -40.0)
    hi = np.full(h.shape, 90.0)
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        h_mid = enthalpy(mid, saturation_humidity_ratio(mid, p))
        above = h_mid > h
        hi = np.where(above, mid, hi)
        lo = np.where(above, lo, mid)
        if np.all(hi - lo < tol):
            break
    return 0.5 * (lo + hi)


def state(t_c: ArrayLike, p: ArrayLike, *, rh_pct: ArrayLike | None = None, t_dp_c: ArrayLike | None = None,
          w: ArrayLike | None = None) -> dict[str, np.ndarray]:
    """Complete moist-air state from dry bulb, pressure and one humidity variable."""
    t_c = _a(t_c)
    p = np.broadcast_to(_a(p), t_c.shape) if np.ndim(p) == 0 else _a(p)
    if w is None:
        if rh_pct is not None:
            w = humidity_ratio_from_rh(t_c, rh_pct, p)
        elif t_dp_c is not None:
            w = humidity_ratio_from_dew_point(t_dp_c, p)
        else:
            raise ValueError("One of rh_pct, t_dp_c or w is required")
    w = _a(w)
    return {
        "t_db_c": t_c,
        "w": w,
        "rh_pct": rh_from_humidity_ratio(t_c, w, p),
        "t_dp_c": dew_point_from_humidity_ratio(w, p),
        "t_wb_c": wet_bulb_from_humidity_ratio(t_c, w, p),
        "h_jkg": enthalpy(t_c, w),
        "rho_kgm3": moist_air_density(t_c, w, p),
        "v_m3kg": specific_volume(t_c, w, p),
    }


def mix(m1: ArrayLike, t1: ArrayLike, w1: ArrayLike, m2: ArrayLike, t2: ArrayLike, w2: ArrayLike):
    """Adiabatic mixing of two dry-air mass flows; returns (t_mix, w_mix) via enthalpy/moisture balance."""
    m1, m2 = _a(m1), _a(m2)
    mt = np.maximum(m1 + m2, 1e-12)
    w = (m1 * _a(w1) + m2 * _a(w2)) / mt
    h = (m1 * enthalpy(t1, w1) + m2 * enthalpy(t2, w2)) / mt
    return temperature_from_enthalpy(h, w), w
