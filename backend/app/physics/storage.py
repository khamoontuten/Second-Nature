"""Thermal-mass buffering and thermal energy storage (TES).

Thermal mass shifts heat in time; it does not remove it. Every model here keeps an explicit
state of charge so that stored heat is either rejected later in the simulation or reported
as a residual at the end of the run (included in the energy balance).

* ``ThermalStorage``: chilled-water or PCM store charged by low-cost cooling (economiser
  surplus, cool night ambient) and discharged at high-cost hours. Standing losses and
  round-trip efficiency are explicit; transfer pumps draw power per kW transferred.
* ``ride_through``: two-node (air + coupled structural/storage mass) temperature rise after a
  total cooling failure, giving the time until the IT inlet reaches a limit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass
class ThermalStorage:
    capacity_kwh_th: float = 0.0
    max_charge_kw: float = 0.0
    max_discharge_kw: float = 0.0
    standing_loss_per_h: float = 0.0005    # fraction of stored energy lost per hour
    discharge_efficiency: float = 0.95
    pump_w_per_kw: float = 8.0             # transfer pumping per kW thermal moved
    initial_soc: float = 0.5

    def to_dict(self):
        return asdict(self)

    @property
    def enabled(self) -> bool:
        return self.capacity_kwh_th > 0 and self.max_discharge_kw > 0


def ride_through(q_w: float, air_volume_m3: float, mass_heat_capacity_j_k: float, coupling_w_k: float,
                 t_start_c: float, t_limit_c: float, rho_cp_air: float = 1.2 * 1006.0, dt_s: float = 1.0,
                 t_max_s: float = 7200.0, mass_cooling_w_k: float = 0.0, t_mass_c: float | None = None) -> dict:
    """Time (s) for zone air to rise from t_start to t_limit after loss of all cooling.

    C_a dT_a/dt = Q − hA (T_a − T_m)
    C_m dT_m/dt = hA (T_a − T_m) − G_m (T_m − T_m0)     (G_m: e.g., a charged TES loop still circulating)
    IT equipment mass is lumped with the air via ``air_volume_m3`` effective capacity if desired.
    """
    c_a = air_volume_m3 * rho_cp_air
    c_m = max(mass_heat_capacity_j_k, 1e-9)
    ta = t_start_c
    tm = t_start_c if t_mass_c is None else t_mass_c
    tm0 = tm
    t = 0.0
    trace_t, trace_ta = [0.0], [ta]
    while ta < t_limit_c and t < t_max_s:
        qa = q_w - coupling_w_k * (ta - tm)
        qm = coupling_w_k * (ta - tm) - mass_cooling_w_k * (tm - tm0)
        ta += qa / c_a * dt_s
        tm += qm / c_m * dt_s
        t += dt_s
        if int(t) % 10 == 0:
            trace_t.append(t)
            trace_ta.append(ta)
    reached = ta >= t_limit_c
    return {"time_to_limit_s": t if reached else None, "reached_limit": bool(reached), "t_air_end_c": ta,
            "t_mass_end_c": tm, "trace_s": trace_t, "trace_t_air_c": trace_ta,
            "air_only_time_s": (t_limit_c - t_start_c) * c_a / q_w if q_w > 0 else None}


def storage_step(soc_kwh: float, request_kw: float, store: ThermalStorage, dt_h: float = 1.0) -> tuple[float, float, float]:
    """Advance storage one step. request_kw > 0 discharges (delivers cooling), < 0 charges.

    Returns (new_soc_kwh, delivered_cooling_kw (≥0 when discharging), charging_load_kw (≥0 when charging)).
    """
    soc = soc_kwh * (1.0 - store.standing_loss_per_h * dt_h)
    if request_kw > 0:
        deliverable = min(request_kw, store.max_discharge_kw, soc * store.discharge_efficiency / dt_h)
        soc -= deliverable * dt_h / store.discharge_efficiency
        return soc, deliverable, 0.0
    if request_kw < 0:
        accept = min(-request_kw, store.max_charge_kw, (store.capacity_kwh_th - soc) / dt_h)
        soc += accept * dt_h
        return soc, 0.0, accept
    return soc, 0.0, 0.0


def stored_energy_change(soc_series_kwh: np.ndarray, initial_kwh: float) -> float:
    return float(soc_series_kwh[-1] - initial_kwh) if len(soc_series_kwh) else 0.0
