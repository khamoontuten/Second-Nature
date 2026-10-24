"""Supervisory mode selection (hysteresis, minimum run time, water limits) and TES dispatch.

Selection rule per hour
-----------------------
1. Candidate set = installed modes that satisfy every hard constraint in that hour
   (and would not exceed the daily / annual water withdrawal limit).
2. Objective J_m = w_E·E_m/E₀ + w_C·C_m/C₀ + w_W·W_m/W₀ + w_K·K_m/K₀, normalised by the mechanical
   mode's values in that hour (E: cooling electricity, C: CO₂e, W: water withdrawal, K: operating cost).
3. Stability: the current mode is kept while it remains feasible and either (a) its minimum run
   time has not elapsed, or (b) the best alternative does not improve J by more than the hysteresis
   margin. Safety overrides stability: an infeasible current mode is left immediately.
4. If no mode is feasible, mechanical cooling runs as the emergency fallback and any unmet load is
   reported as a thermal-constraint violation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.cooling.hybrid import MODES, ModeResult
from app.physics.storage import ThermalStorage, storage_step
from app.schemas.config import ProjectConfig


@dataclass
class Selection:
    mode_index: np.ndarray          # index into MODES
    objective: np.ndarray
    switches: int
    emergency_hours: int
    water_limited_hours: int


def objective_matrix(cfg: ProjectConfig, modes: dict[str, ModeResult], intensity: np.ndarray, price_e: float | None,
                     price_w: float | None) -> np.ndarray:
    c = cfg.strategies.controller
    mech = modes["mechanical"]
    e0 = np.maximum(mech.cooling_power(), 1.0)
    w0 = np.maximum(mech.water_total(), 1.0)
    c0 = np.maximum(e0 * intensity, 1e-6)
    k0 = np.maximum(e0 / 1000 * (price_e or 0) + w0 / 1000 * (price_w or 0), 1e-9)
    rows = []
    for name in MODES:
        m = modes[name]
        e = m.cooling_power()
        w = m.water_total()
        j = c.w_energy * e / e0 + c.w_carbon * e * intensity / c0 + c.w_water * w / w0
        if c.w_cost > 0:
            j = j + c.w_cost * (e / 1000 * (price_e or 0) + w / 1000 * (price_w or 0)) / k0
        if c.w_energy + c.w_carbon + c.w_water + c.w_cost == 0:
            j = e / e0
        rows.append(np.where(m.available & m.feasible, j, np.inf))
    return np.vstack(rows)


def select_modes(cfg: ProjectConfig, modes: dict[str, ModeResult], intensity: np.ndarray, local_dates: np.ndarray,
                 price_e: float | None = None, price_w: float | None = None) -> Selection:
    j = objective_matrix(cfg, modes, intensity, price_e, price_w)
    n = j.shape[1]
    ctl = cfg.strategies.controller
    cons = cfg.site.constraints
    water = np.vstack([modes[m].water_total() for m in MODES]) / 1000.0  # m³ per hour
    annual_cap = cons.permitted_annual_withdrawal_m3
    daily_cap = cons.permitted_daily_withdrawal_m3
    mech_idx = MODES.index("mechanical")
    chosen = np.empty(n, dtype=int)
    obj = np.empty(n)
    cur = mech_idx
    run = 0
    switches = 0
    emergency = 0
    water_limited = 0
    cum_annual = 0.0
    cum_day = 0.0
    day = None
    jl = j.T.tolist()
    wl = water.T.tolist()
    mech_feasible = modes["mechanical"].feasible
    for t in range(n):
        if local_dates[t] != day:
            day = local_dates[t]
            cum_day = 0.0
        row = jl[t]
        wrow = wl[t]
        limited = False
        if annual_cap is not None or daily_cap is not None:
            row = list(row)
            for k in range(len(row)):
                if row[k] == np.inf or wrow[k] <= 0:
                    continue
                if (annual_cap is not None and cum_annual + wrow[k] > annual_cap) or \
                   (daily_cap is not None and cum_day + wrow[k] > daily_cap):
                    row[k] = np.inf
                    limited = True
        best = min(range(len(row)), key=row.__getitem__)
        if row[best] == np.inf:
            best = mech_idx
            emergency += 0 if mech_feasible[t] else 1
        if row[cur] != np.inf and cur != best:
            keep = run < ctl.min_run_h or row[best] > row[cur] * (1.0 - ctl.hysteresis)
            nxt = cur if keep else best
        else:
            nxt = best
        if nxt != cur:
            switches += 1
            run = 0
        cur = nxt
        run += 1
        chosen[t] = cur
        obj[t] = row[cur] if row[cur] != np.inf else np.nan
        cum_annual += wrow[cur]
        cum_day += wrow[cur]
        water_limited += int(limited)
    return Selection(chosen, obj, switches, emergency, water_limited)


def tes_dispatch(store: ThermalStorage, q_coil_w: np.ndarray, marginal_cost: np.ndarray, local_dates: np.ndarray) -> dict:
    """Daily greedy dispatch: discharge in the most expensive plant hours, charge in the cheapest.

    marginal_cost: electricity per unit of plant cooling (W/W) in each hour (plant power / coil load).
    Returns the adjusted coil load, delivered/charged energy and state of charge (kWh_th).
    """
    n = len(q_coil_w)
    if not store.enabled:
        return {"q_coil_w": q_coil_w, "soc_kwh": np.zeros(n), "discharge_kw": np.zeros(n), "charge_kw": np.zeros(n),
                "pump_w": np.zeros(n), "initial_kwh": 0.0}
    q_kw = np.asarray(q_coil_w, dtype=float) / 1000.0
    hours_full = max(int(np.ceil(store.capacity_kwh_th / max(store.max_discharge_kw, 1e-9))), 1)
    request = np.zeros(n)
    dates = np.asarray(local_dates)
    for d in np.unique(dates):
        idx = np.where(dates == d)[0]
        cost = marginal_cost[idx]
        valid = q_kw[idx] > 0
        if valid.sum() < 2:
            continue
        order = idx[np.argsort(cost)]
        order = [i for i in order if q_kw[i] > 0]
        k = min(hours_full, len(order) // 2)
        cheap, dear = order[:k], order[-k:]
        spread = np.mean(marginal_cost[dear]) - np.mean(marginal_cost[cheap]) / store.discharge_efficiency
        if spread <= 0:
            continue
        for i in dear:
            request[i] = min(store.max_discharge_kw, q_kw[i])
        for i in cheap:
            request[i] = -store.max_charge_kw
    soc = store.initial_soc * store.capacity_kwh_th
    initial = soc
    socs = np.zeros(n)
    dis = np.zeros(n)
    chg = np.zeros(n)
    gain = np.zeros(n)
    for t in range(n):
        prev = soc
        soc, delivered, accepted = storage_step(soc, request[t], store)
        dis[t], chg[t], socs[t] = delivered, accepted, soc
        # Heat leaking into the cold store from its surroundings (standing loss + discharge inefficiency), kW.
        gain[t] = (prev - soc) - delivered + accepted
    q_new = np.maximum(q_kw - dis + chg, 0.0) * 1000.0
    pump = store.pump_w_per_kw * (dis + chg)
    return {"q_coil_w": q_new, "soc_kwh": socs, "discharge_kw": dis, "charge_kw": chg, "pump_w": pump, "initial_kwh": initial,
            "env_gain_kw": gain}
