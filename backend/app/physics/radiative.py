"""Longwave sky exchange, surface heat balance and radiative-cooling panels.

* Sky temperature: from measured/reanalysed downwelling horizontal infrared
  (T_sky = (IR/σ)^¼) when available (EPW field 12, ERA5 strd). Otherwise the
  Berdahl & Martin (1984) clear-sky emissivity
      ε_clear = 0.711 + 0.56 (T_dp/100) + 0.73 (T_dp/100)²,   T_dp in °C
  with the Walton (1983) cloud correction
      ε_sky = ε_clear (1 + 0.0224 N − 0.0035 N² + 0.00028 N³),  N = cloud cover in tenths.
  These are labeled empirical approximations.
* Exterior convection: h_c = 5.7 + 3.8 V (McAdams-type simplified, W/m²K) — a simplified
  correlation; detailed models (e.g., DOE-2 / TARP) depend on surface roughness and orientation.
* Ordinary cool roofs (high solar reflectance) mainly cut absorbed solar gain; they are **not**
  sub-ambient coolers in daytime. Purpose-built daytime radiative-cooling materials need solar
  reflectance ≳ 0.94 and high atmospheric-window emittance; their net flux is computed here
  from the same balance with the panel temperature held by the coolant.
"""

from __future__ import annotations

import numpy as np

from app.core.constants import SIGMA, T0_K


def sky_temperature_from_ir(ir_horiz_wm2):
    return (np.maximum(np.asarray(ir_horiz_wm2, dtype=float), 1.0) / SIGMA) ** 0.25 - T0_K


def sky_emissivity_berdahl_martin(t_dp_c, cloud_tenths=0.0):
    tdp = np.asarray(t_dp_c, dtype=float) / 100.0
    eps_clear = 0.711 + 0.56 * tdp + 0.73 * tdp**2
    n = np.asarray(cloud_tenths, dtype=float)
    return np.clip(eps_clear * (1.0 + 0.0224 * n - 0.0035 * n**2 + 0.00028 * n**3), 0.0, 1.0)


def sky_temperature(t_db_c, t_dp_c=None, cloud_pct=None, ir_horiz_wm2=None):
    """Effective sky temperature (°C); uses IR when finite, else Berdahl–Martin + cloud correction."""
    t_db = np.asarray(t_db_c, dtype=float)
    est = None
    if t_dp_c is not None:
        cloud = 0.0 if cloud_pct is None else np.nan_to_num(np.asarray(cloud_pct, dtype=float) / 10.0)
        eps = sky_emissivity_berdahl_martin(t_dp_c, cloud)
        est = eps**0.25 * (t_db + T0_K) - T0_K
    if ir_horiz_wm2 is not None:
        ir = np.asarray(ir_horiz_wm2, dtype=float)
        from_ir = sky_temperature_from_ir(np.where(np.isfinite(ir), ir, 1.0))
        if est is None:
            return np.where(np.isfinite(ir), from_ir, t_db - 10.0)
        return np.where(np.isfinite(ir) & (ir > 50), from_ir, est)
    if est is None:
        raise ValueError("Need dew point (and optionally cloud cover) or horizontal infrared radiation")
    return est


def exterior_convection_coefficient(wind_ms):
    return 5.7 + 3.8 * np.asarray(wind_ms, dtype=float)


def linear_radiation_coefficient(t_surface_c, t_sky_c, emissivity: float):
    """h_r = ε σ (T_s² + T_sky²)(T_s + T_sky), W/m²K."""
    ts = np.asarray(t_surface_c, dtype=float) + T0_K
    tk = np.asarray(t_sky_c, dtype=float) + T0_K
    return emissivity * SIGMA * (ts**2 + tk**2) * (ts + tk)


def radiative_panel_net_flux(t_panel_c, t_air_c, t_sky_c, ghi_wm2, wind_ms, emissivity: float = 0.95,
                             solar_absorptance: float = 0.05, cover_h_factor: float = 0.4):
    """Net cooling flux (W/m², + = heat leaving the panel) of a radiative sky-cooling panel.

    q = ε σ (T_p⁴ − T_sky⁴) − α_s G − h_c,eff (T_air − T_p)
    ``cover_h_factor`` scales the convective gain to represent an IR-transparent wind cover.
    """
    tp = np.asarray(t_panel_c, dtype=float) + T0_K
    tsky = np.asarray(t_sky_c, dtype=float) + T0_K
    q_rad = emissivity * SIGMA * (tp**4 - tsky**4)
    q_sol = solar_absorptance * np.asarray(ghi_wm2, dtype=float)
    q_conv = cover_h_factor * exterior_convection_coefficient(wind_ms) * (np.asarray(t_air_c) - (tp - T0_K))
    return q_rad - q_sol - q_conv


def roof_surface_temperature_steady(t_air_c, t_sky_c, ghi_wm2, wind_ms, solar_absorptance: float,
                                    emissivity: float, u_value_to_inside: float, t_inside_c: float, iters: int = 30):
    """Quasi-steady roof surface temperature from the surface energy balance (used for checks and badges).

    α G + h_c (T_a − T_s) + ε σ (T_sky⁴ − T_s⁴) + U (T_i − T_s) = 0, solved by Newton iterations.
    """
    t_a = np.asarray(t_air_c, dtype=float)
    ts = t_a.copy()
    hc = exterior_convection_coefficient(wind_ms)
    for _ in range(iters):
        tsk = ts + T0_K
        f = (solar_absorptance * np.asarray(ghi_wm2) + hc * (t_a - ts)
             + emissivity * SIGMA * ((np.asarray(t_sky_c) + T0_K) ** 4 - tsk**4) + u_value_to_inside * (t_inside_c - ts))
        df = -hc - 4 * emissivity * SIGMA * tsk**3 - u_value_to_inside
        ts = ts - f / df
    return ts
