"""Evaporative processes with explicit heat and mass balances.

Direct evaporative cooling (DEC)
    T_out = T_db − ε_sat (T_db − T_wb)        (saturation effectiveness, adiabatic, h ≈ const)
    W_out from the enthalpy balance at T_out; evaporation ṁ_w = ṁ_da (W_out − W_in).
Indirect evaporative cooling (IEC)
    Product (recirculated) air is cooled sensibly without moisture change:
        wet mode: T_p,out = T_p,in − ε_wb (T_p,in − T_wb,secondary)
        dry mode: T_p,out = T_p,in − ε_dry (T_p,in − T_db,secondary)   (no water)
    The secondary (scavenger) airstream absorbs the transferred heat Q. In a counter-flow
    exchanger it leaves close to the product inlet temperature (T_s,out = T_p,in − approach);
    its humidity follows from the enthalpy balance h_s,out = h_s,in + Q/ṁ_s, clamped to
    saturation, giving the evaporation ṁ_s (W_s,out − W_s,in). Water use therefore follows the heat and mass
    balance, not the temperature drop alone.
Cooling tower
    Air-side balance with saturated exit air (standard engineering assumption):
        h_out = h_in + Q/ṁ_a,   T_out: h_sat(T_out) = h_out,   ṁ_evap = ṁ_a (W_sat(T_out) − W_in)
    Blowdown = ṁ_evap / (CoC − 1); drift = drift fraction × circulating water flow.
    Withdrawal (make-up) = evaporation + blowdown + drift; consumption = evaporation + drift
    (blowdown is discharged, i.e. withdrawn but not consumed).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from app.core.constants import CP_W, H_FG0
from app.physics import psychrometrics as ps


@dataclass
class DECParams:
    saturation_effectiveness: float = 0.85
    cycles_of_concentration: float = 4.0       # bleed control for pad water
    pump_w_per_m3s: float = 25.0               # recirculation pump power per m³/s of air
    pad_dp_pa: float = 60.0


@dataclass
class IECParams:
    wet_bulb_effectiveness: float = 0.70
    dry_effectiveness: float = 0.55
    secondary_to_primary_ratio: float = 1.0    # ṁ_secondary / ṁ_product
    secondary_exit_approach_k: float = 3.0     # T_secondary,out = T_product,in − approach
    cycles_of_concentration: float = 4.0
    pump_w_per_m3s: float = 20.0
    primary_dp_pa: float = 200.0
    secondary_dp_pa: float = 250.0


@dataclass
class TowerParams:
    approach_design_k: float = 4.0
    range_design_k: float = 6.0
    wb_design_c: float = 24.0
    lg_ratio: float = 1.2                      # water/air mass-flow ratio at design
    fan_kw_per_kw_rejected: float = 0.010      # design fan power per kW of heat rejected
    min_fan_speed: float = 0.3
    cycles_of_concentration: float = 5.0
    drift_fraction: float = 1e-5               # of circulating water (0.001 %)
    min_leaving_water_c: float = 12.0

    def to_dict(self):
        return asdict(self)


def direct_evaporative(t_db, w, p, m_da, params: DECParams):
    """Returns dict with supply T, W, evaporation (kg/s), bleed (kg/s), sensible cooling (W), pump power (W)."""
    t_db, w, p, m_da = (np.asarray(x, dtype=float) for x in (t_db, w, p, m_da))
    t_wb = ps.wet_bulb_from_humidity_ratio(t_db, w, p)
    t_out = t_db - params.saturation_effectiveness * (t_db - t_wb)
    h_in = ps.enthalpy(t_db, w)
    w_out = (h_in - 1006.0 * t_out) / (H_FG0 + 1860.0 * t_out)
    w_out = np.maximum(w_out, w)
    evap = m_da * (w_out - w)
    bleed = evap / max(params.cycles_of_concentration - 1.0, 1e-6)
    v_dot = m_da * ps.specific_volume(t_db, w, p)
    return {
        "t_out_c": t_out, "w_out": w_out, "evaporation_kg_s": evap, "bleed_kg_s": bleed,
        "sensible_w": m_da * ps.humid_specific_heat(w) * (t_db - t_out),
        "pump_w": params.pump_w_per_m3s * v_dot, "t_wb_c": t_wb,
    }


def _state_on_rh_line(h, rh_pct, p):
    """Find (T, W) with enthalpy h and relative humidity rh by bisection (T in −30…70 °C)."""
    h, p = np.broadcast_arrays(np.asarray(h, dtype=float), np.asarray(p, dtype=float))
    lo = np.full(h.shape, -30.0)
    hi = np.full(h.shape, 70.0)
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        h_mid = ps.enthalpy(mid, ps.humidity_ratio_from_rh(mid, rh_pct, p))
        above = h_mid > h
        hi = np.where(above, mid, hi)
        lo = np.where(above, lo, mid)
    t = 0.5 * (lo + hi)
    return t, ps.humidity_ratio_from_rh(t, rh_pct, p)


def indirect_evaporative(t_product_in, w_product, t_sec_db, w_sec, p, m_product, params: IECParams, wet: np.ndarray | bool = True):
    """Indirect evaporative cooler; wet=False gives the dry (air-to-air heat exchanger) mode."""
    tp, wp, ts, wsec, p, mp = (np.asarray(x, dtype=float) for x in (t_product_in, w_product, t_sec_db, w_sec, p, m_product))
    wet = np.broadcast_to(np.asarray(wet, dtype=bool), tp.shape)
    t_wb_s = ps.wet_bulb_from_humidity_ratio(ts, wsec, p)
    t_out_wet = tp - params.wet_bulb_effectiveness * (tp - t_wb_s)
    t_out_dry = tp - params.dry_effectiveness * (tp - ts)
    t_out = np.where(wet, t_out_wet, t_out_dry)
    t_out = np.minimum(t_out, tp)  # never heats the product air
    q = mp * ps.humid_specific_heat(wp) * (tp - t_out)
    ms = params.secondary_to_primary_ratio * mp
    h_s_out = ps.enthalpy(ts, wsec) + q / np.maximum(ms, 1e-9)
    t_s_out = tp - params.secondary_exit_approach_k
    w_s_out = (h_s_out - 1006.0 * t_s_out) / (H_FG0 + 1860.0 * t_s_out)
    # If that state is supersaturated, the air leaves saturated at the same enthalpy.
    t_sat = ps.saturation_temperature_from_enthalpy(h_s_out, p)
    supersat = w_s_out > ps.saturation_humidity_ratio(t_s_out, p)
    w_s_out = np.where(supersat, ps.saturation_humidity_ratio(t_sat, p), w_s_out)
    evap = np.where(wet, np.maximum(ms * (w_s_out - wsec), 0.0), 0.0)
    bleed = evap / max(params.cycles_of_concentration - 1.0, 1e-6)
    v_p = mp * ps.specific_volume(tp, wp, p)
    v_s = ms * ps.specific_volume(ts, wsec, p)
    return {
        "t_out_c": t_out, "w_out": wp, "q_w": q, "evaporation_kg_s": evap, "bleed_kg_s": bleed,
        "pump_w": np.where(wet, params.pump_w_per_m3s * v_p, 0.0),
        "fan_dp_primary_pa": params.primary_dp_pa, "fan_dp_secondary_pa": params.secondary_dp_pa,
        "v_primary_m3s": v_p, "v_secondary_m3s": v_s, "t_wb_secondary_c": t_wb_s,
    }


def cooling_tower(q_reject_w, t_db, w, p, q_design_w: float, params: TowerParams):
    """Cooling tower leaving-water temperature, fan power and water balance (vectorised).

    Approach model (simplified, empirical): approach = A_d · PLR^0.7 · (1 + 0.02 (WB_d − WB)),
    bounded to 1.5–12 K, with variable-speed fans at speed ∝ PLR (≥ min speed), fan power ∝ speed³.
    """
    q = np.maximum(np.asarray(q_reject_w, dtype=float), 0.0)
    t_db, w, p = (np.asarray(x, dtype=float) for x in (t_db, w, p))
    t_wb = ps.wet_bulb_from_humidity_ratio(t_db, w, p)
    plr = np.clip(q / max(q_design_w, 1.0), 0.0, 1.5)
    approach = np.clip(params.approach_design_k * np.maximum(plr, 0.05) ** 0.7 * (1 + 0.02 * (params.wb_design_c - t_wb)), 1.5, 12.0)
    t_leaving = np.maximum(t_wb + approach, params.min_leaving_water_c)
    speed = np.where(q > 0, np.clip(plr, params.min_fan_speed, 1.2), 0.0)
    fan_w = params.fan_kw_per_kw_rejected * q_design_w * speed**3
    # air mass flow at design: from L/G and design water flow (range)
    m_water_design = q_design_w / (CP_W * params.range_design_k)
    m_air = (m_water_design / params.lg_ratio) * speed
    h_in = ps.enthalpy(t_db, w)
    h_out = h_in + q / np.maximum(m_air, 1e-9)
    t_out = ps.saturation_temperature_from_enthalpy(h_out, p)
    w_out = ps.saturation_humidity_ratio(t_out, p)
    evap = np.where(q > 0, np.maximum(m_air * (w_out - w), 0.0), 0.0)
    m_circ = q / (CP_W * params.range_design_k)
    drift = params.drift_fraction * m_circ
    blowdown = evap / max(params.cycles_of_concentration - 1.0, 1e-6)
    return {
        "t_leaving_water_c": t_leaving, "t_wb_c": t_wb, "approach_k": approach, "fan_w": fan_w,
        "evaporation_kg_s": evap, "blowdown_kg_s": blowdown, "drift_kg_s": drift,
        "makeup_kg_s": evap + blowdown + drift, "consumption_kg_s": evap + drift,
        "t_air_out_c": t_out, "latent_fraction": np.where(q > 0, evap * 2.45e6 / np.maximum(q, 1e-9), 0.0),
    }


def adiabatic_precooler(t_db, w, p, m_air, effectiveness: float = 0.75, cycles: float = 3.0):
    """Evaporative pre-cooling of condenser / dry-cooler inlet air."""
    r = direct_evaporative(t_db, w, p, m_air, DECParams(saturation_effectiveness=effectiveness, cycles_of_concentration=cycles))
    return r
