"""Facility simulation engine: one scenario over the full climate dataset → hourly ledger + diagnostics.

Order of operations
-------------------
1. Climate context (UTC), IT load profile, electrical overheads.
2. Ground model (soil layers if present, else air-temperature harmonic + documented offset).
3. Envelope loads (single source for roof/wall solar and conduction; strategies B, C, G-roof).
4. Hall state: server airflow, facility airflow, recirculation allowance, envelope limits.
5. Plant sizing from installed IT capacity at the dataset's design hour (same rule for every scenario).
6. Mode evaluation (all installed modes, every hour) → supervisory selection.
   With an EAHE: pass 1 schedules the exchanger by a rule; pass 2 re-runs the soil model with the
   flows the controller actually used, and modes are re-selected (fixed-point iteration).
7. Thermal storage dispatch (if installed) and re-evaluation of plant power.
8. Ledger assembly, accounting boundaries, facility energy-balance closure, failure checks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app import MODEL_VERSION
from app.climate.providers.base import ClimateDataset
from app.core.environmental_classes import get_envelope
from app.cooling import controls, hybrid, passive
from app.cooling.plant import evaluate_mechanical, size_plant
from app.physics import psychrometrics as ps
from app.physics.ground import SOIL_TYPES, GroundModel, diffusivity
from app.physics.storage import ThermalStorage, ride_through
from app.physics.thermal_balance import closure
from app.schemas.config import ProjectConfig
from app.simulation.context import ClimateArrays, build_context
from app.simulation.envelope_model import compute_envelope
from app.simulation.loads import electrical_losses, it_power_w

CONTAINMENT = {"full": {"bypass": 0.05, "recirc_k": 0.5}, "partial": {"bypass": 0.20, "recirc_k": 2.0},
               "none": {"bypass": 0.40, "recirc_k": 4.0}}

POWER_COLUMNS = ["p_it_w", "p_ups_loss_w", "p_dist_loss_w", "p_lighting_w", "p_other_w", "p_controls_w", "p_actuators_w",
                 "p_tes_pumps_w", "p_liquid_pumps_w"] + [k + "_w" for k in hybrid.POWER_KEYS]
COOLING_POWER_COLUMNS = [k + "_w" for k in hybrid.POWER_KEYS] + ["p_actuators_w", "p_tes_pumps_w", "p_liquid_pumps_w"]


@dataclass
class SimulationResult:
    scenario: str
    hourly: pd.DataFrame
    summary: dict
    diagnostics: dict
    flags: list[dict] = field(default_factory=list)
    inputs_hash: str = ""
    dataset_hash: str = ""
    model_version: str = MODEL_VERSION


def ground_model_for(cfg: ProjectConfig, ds: ClimateDataset, ctx: ClimateArrays) -> GroundModel:
    e = cfg.strategies.eahe
    soil = SOIL_TYPES.get(e.soil_type if e.soil_type in SOIL_TYPES else "soil_generic")
    k = e.soil_k or soil["k"]
    rho = e.soil_rho or soil["rho"]
    c = e.soil_c or soil["c"]
    alpha = diffusivity(k, rho, c)
    soil_cols = {col: z for col, z in ds.soil_layers_m.items() if col in ds.data and ds.data[col].notna().mean() > 0.9}
    if soil_cols:
        gm = GroundModel.from_soil_layers({z: ds.data[col] for col, z in soil_cols.items()}, alpha)
    else:
        gm = GroundModel.from_air_temperature(pd.Series(ctx.t_db, index=ctx.index), alpha, surface_offset_k=e.surface_offset_k)
    if e.measured_ground_temp_c is not None:
        gm.measured_override = {"depth_m": e.measured_ground_depth_m or e.depth_m, "t_c": e.measured_ground_temp_c}
        gm.calibration += "; mean replaced by measured ground temperature"
    return gm


def carbon_intensity(cfg: ProjectConfig, ctx: ClimateArrays, hourly: np.ndarray | None = None) -> tuple[np.ndarray, str]:
    g = cfg.grid
    if hourly is not None:
        arr, label = np.asarray(hourly, dtype=float), "hourly dataset"
    elif g.monthly_factors_kg_per_kwh:
        arr = np.asarray(g.monthly_factors_kg_per_kwh)[np.asarray(ctx.local_index.month) - 1]
        label = "monthly factors"
    elif g.emission_factor_kg_per_kwh is not None:
        arr, label = np.full(ctx.n, g.emission_factor_kg_per_kwh), "annual factor"
    else:
        return np.full(ctx.n, np.nan), "not available"
    if g.accounting_basis == "market":
        arr = arr * (1.0 - g.contracted_renewable_fraction)
    return arr, f"{label} ({g.factor_type}, {g.accounting_basis}-based)"


def _hall_state(cfg: ProjectConfig, ctx: ClimateArrays, p_it, elec, env_loads) -> hybrid.HallState:
    it = cfg.it
    env = get_envelope(it.ashrae_class, it.envelope_mode, cfg.site.constraints.gaseous_contamination_high)
    t_max = env.derated_t_max(cfg.site.elevation_m)
    c = CONTAINMENT[it.containment]
    q_it_air = p_it * (1.0 - it.liquid_cooled_fraction)
    cp = ps.humid_specific_heat(0.008)
    m_srv = q_it_air / (cp * it.server_delta_t_k)
    m_fac = np.maximum(m_srv * (1 + c["bypass"]), 1e-3)
    t_set = it.inlet_setpoint_c - c["recirc_k"]
    if not (env.t_min_c <= it.inlet_setpoint_c <= t_max):
        raise ValueError(f"Inlet setpoint {it.inlet_setpoint_c} °C lies outside the selected envelope "
                         f"{env.t_min_c}–{t_max:.1f} °C")
    t_econ_max = t_max - it.economiser_supply_margin_k - c["recirc_k"]
    t_econ_min = env.t_min_c + it.economiser_supply_margin_k - c["recirc_k"]
    b = cfg.building
    m_oa = ctx.rho / (1 + ctx.w) * (b.min_outdoor_air_m3s + b.infiltration_ach * b.volume_m3 / 3600.0)
    poor = np.zeros(ctx.n, bool)
    for h in cfg.site.constraints.poor_air_quality_hours:
        if 0 <= h < ctx.n:
            poor[h] = True
    return hybrid.HallState(q_it_air, elec["in_space"], env_loads.q_total_w, m_srv, m_fac, m_oa, t_set, t_econ_max,
                            t_econ_min, c["recirc_k"], env, cfg.site.elevation_m, poor)


def _evaluate_all(cfg, ctx, hs, sz, avail, eahe_res=None, eahe_flow=None) -> dict[str, hybrid.ModeResult]:
    m: dict[str, hybrid.ModeResult] = {}
    m["mechanical"] = hybrid.mechanical_mode(cfg, ctx, hs, sz)
    m["mech_precool"] = (hybrid.mechanical_mode(cfg, ctx, hs, sz, precool=True, name="mech_precool")
                         if avail["mech_precool"] else hybrid._unavailable("mech_precool", ctx.n, hs.t_set))
    m["econ_direct_fan"] = hybrid.econ_direct_fan(cfg, ctx, hs, sz) if avail["econ_direct_fan"] else hybrid._unavailable("econ_direct_fan", ctx.n, hs.t_set)
    m["econ_natural"] = hybrid.econ_natural(cfg, ctx, hs, sz) if avail["econ_natural"] else hybrid._unavailable("econ_natural", ctx.n, hs.t_set)
    if avail["econ_eahe"] and eahe_res is not None:
        m["econ_eahe"] = hybrid.econ_eahe(cfg, ctx, hs, sz, eahe_res, eahe_flow)
    else:
        m["econ_eahe"] = hybrid._unavailable("econ_eahe", ctx.n, hs.t_set)
    m["econ_indirect"] = hybrid.econ_indirect(cfg, ctx, hs, sz) if avail["econ_indirect"] else hybrid._unavailable("econ_indirect", ctx.n, hs.t_set)
    m["iec_wet"] = hybrid.iec_wet(cfg, ctx, hs, sz) if avail["iec_wet"] else hybrid._unavailable("iec_wet", ctx.n, hs.t_set)
    m["dec_direct"] = hybrid.dec_direct(cfg, ctx, hs, sz) if avail["dec_direct"] else hybrid._unavailable("dec_direct", ctx.n, hs.t_set)
    # Water-availability rule: evaporative modes are disabled when no water may be withdrawn.
    cap = cfg.site.constraints.permitted_annual_withdrawal_m3
    if cap is not None and cap <= 0:
        for name, res in m.items():
            if name != "mechanical" and np.any(res.water_total() > 0):
                wet = res.water_total() > 0
                res.feasible = res.feasible & ~wet
                res.reasons = np.where(wet, res.reasons | hybrid.R_WATER_CAP, res.reasons)
    return m


def _pick(modes: dict[str, hybrid.ModeResult], idx: np.ndarray, getter) -> np.ndarray:
    stack = np.vstack([getter(modes[name]) for name in hybrid.MODES])
    return np.take_along_axis(stack, idx[None, :], axis=0)[0]


def run_simulation(cfg: ProjectConfig, ds: ClimateDataset, *, scenario: str = "custom", hourly_intensity=None,
                   progress_cb=None, cancel_cb=None, eahe_cache: dict | None = None,
                   envelope_cache: dict | None = None) -> SimulationResult:
    def progress(p, msg):
        if progress_cb:
            progress_cb(p, msg)

    flags: list[dict] = []
    progress(0.02, "preparing climate")
    ctx = build_context(ds, lat=cfg.site.latitude, lon=cfg.site.longitude, elevation_m=cfg.site.elevation_m,
                        timezone=cfg.site.timezone, warming_offset_k=cfg.simulation.warming_offset_k)
    for note in ctx.notes:
        flags.append({"level": "warning", "code": "climate", "message": note})
    if ctx.gap_filled:
        flags.append({"level": "info", "code": "gap_fill", "message": f"short gaps interpolated: {ctx.gap_filled}"})
    if cfg.simulation.warming_offset_k:
        flags.append({"level": "info", "code": "warming_offset",
                      "message": f"uniform warming offset {cfg.simulation.warming_offset_k:+.1f} K applied (sensitivity test, not a climate projection)"})
    n = ctx.n
    it_cap_w = cfg.it.installed_capacity_mw * 1e6
    p_it = it_power_w(cfg.it, ctx.local_index)
    elec = electrical_losses(p_it, it_cap_w, cfg.electrical, cfg.building.floor_area_m2)
    q_liq = p_it * cfg.it.liquid_cooled_fraction

    progress(0.08, "ground model")
    ground = ground_model_for(cfg, ds, ctx)

    progress(0.12, "envelope")
    env_key = _hash({"b": cfg.building.model_dump(), "e": cfg.strategies.envelope.model_dump(), "f": cfg.strategies.facade.model_dump(),
                     "s": cfg.it.inlet_setpoint_c, "sim": cfg.simulation.envelope_substeps, "w": cfg.simulation.warming_offset_k,
                     "ds": ds.provenance.extra.get("sha256", ds.provenance.retrieved_at), "g": ground.to_dict()})
    if envelope_cache is not None and env_key in envelope_cache:
        env_loads = envelope_cache[env_key]
    else:
        env_loads = compute_envelope(cfg, ctx, ground)
        if envelope_cache is not None:
            envelope_cache[env_key] = env_loads

    hs = _hall_state(cfg, ctx, p_it, elec, env_loads)

    # Plant sizing at full installed IT load and the worst hour of the dataset.
    q_it_air_design = it_cap_w * (1.0 - cfg.it.liquid_cooled_fraction)
    elec_full = electrical_losses(np.full(n, it_cap_w), it_cap_w, cfg.electrical, cfg.building.floor_area_m2)
    m_fac_design = hs.m_fac.max() * it_cap_w / max(p_it.max(), 1.0)
    cp = ps.humid_specific_heat(0.008)
    t_ret_design = hs.t_set + q_it_air_design / (m_fac_design * cp)
    q_oa_design = float(np.max(hs.m_oa_min * (ctx.h - ps.enthalpy(t_ret_design, 0.008))))
    v_fac_design = m_fac_design * 0.86
    crah_heat_design = v_fac_design * cfg.baseline.crah_dp_pa / cfg.baseline.crah_fan_efficiency
    q_design = (q_it_air_design + float(np.max(elec_full["in_space"])) + max(float(np.max(env_loads.q_total_w)), 0.0)
                + max(q_oa_design, 0.0) + crah_heat_design)
    sz = size_plant(cfg, q_design, v_fac_design, it_cap_w * cfg.it.liquid_cooled_fraction)
    avail = hybrid.available_modes(cfg)

    eahe_res = None
    eahe_flow = None
    progress(0.25, "evaluating modes")
    if avail["econ_eahe"]:
        e = cfg.strategies.eahe
        t_g = ground.temperature(e.depth_m, ctx.index)
        flow1 = np.where(ctx.t_db > t_g + e.operate_threshold_k, e.design_flow_m3s, 0.0)
        if e.regeneration:
            flow1 = np.where(ctx.t_db < t_g - 3.0, e.design_flow_m3s, flow1)
        key = None
        if eahe_cache is not None:
            key = _hash({"e": e.model_dump(), "f": flow1.round(4).tolist()[:: max(n // 500, 1)], "sum": float(flow1.sum()),
                         "g": ground.to_dict(), "sp": cfg.strategies.stack.spiral_intakes,
                         "ds": ds.provenance.extra.get("sha256", ds.provenance.retrieved_at), "w": cfg.simulation.warming_offset_k})
        if key and key in eahe_cache:
            eahe_res = eahe_cache[key]
        else:
            eahe_res = passive.run_eahe(cfg, ctx, ground, flow1, cancel_cb=cancel_cb)
            if key and eahe_res is not None:
                eahe_cache[key] = eahe_res
        if eahe_res is None:
            flags.append({"level": "error", "code": "eahe_insufficient_data",
                          "message": "Earth-air heat exchanger not simulated: soil properties unknown (insufficient data)."})
            avail["econ_eahe"] = False
        eahe_flow = flow1
    modes = _evaluate_all(cfg, ctx, hs, sz, avail, eahe_res, eahe_flow)
    intensity, intensity_label = carbon_intensity(cfg, ctx, hourly_intensity)
    int_for_obj = np.nan_to_num(intensity, nan=0.4)
    local_dates = np.asarray(ctx.local_index.date)
    water_ref = 0.5 * p_it / 1000.0  # L/h (0.5 L/kWh_IT)
    sel = controls.select_modes(cfg, modes, int_for_obj, local_dates, cfg.economics.electricity_price_per_kwh,
                                cfg.economics.water_price_per_m3, water_ref)
    if avail["econ_eahe"] and eahe_res is not None:
        # Fixed point between controller and soil: re-run the EAHE with the flow the controller actually used.
        e = cfg.strategies.eahe
        t_g = ground.temperature(e.depth_m, ctx.index)
        eahe_idx = hybrid.MODES.index("econ_eahe")
        history = []
        for it in range(4):
            progress(0.45 + 0.08 * it, f"earth-air exchanger fixed point, iteration {it + 1}")
            used = sel.mode_index == eahe_idx
            u = modes["econ_eahe"].extra.get("eahe_utilisation", np.ones(n))
            flow_next = np.where(used, eahe_flow * u, 0.0)
            if e.regeneration:
                flow_next = np.where(~used & (ctx.t_db < t_g - 3.0), e.design_flow_m3s, flow_next)
            change = float(np.abs(flow_next - eahe_flow).sum() / max(eahe_flow.sum(), 1e-9))
            history.append(round(change, 4))
            if change < 0.02:
                break
            eahe_flow = flow_next
            eahe_res = passive.run_eahe(cfg, ctx, ground, eahe_flow, cancel_cb=cancel_cb)
            modes["econ_eahe"] = hybrid.econ_eahe(cfg, ctx, hs, sz, eahe_res, eahe_flow)
            sel = controls.select_modes(cfg, modes, int_for_obj, local_dates, cfg.economics.electricity_price_per_kwh,
                                        cfg.economics.water_price_per_m3, water_ref)
        flags.append({"level": "info", "code": "eahe_fixed_point",
                      "message": f"EAHE controller/soil fixed point: relative flow change per iteration {history}"})
        if history and history[-1] >= 0.02:
            flags.append({"level": "warning", "code": "eahe_not_converged",
                          "message": "EAHE schedule did not fully converge; soil state may not match final operation exactly"})

    progress(0.75, "assembling ledger")
    idx = sel.mode_index
    led: dict[str, np.ndarray] = {}
    for k in hybrid.POWER_KEYS:
        led[k + "_w"] = _pick(modes, idx, lambda m, k=k: m.power[k])
    for k in hybrid.WATER_KEYS:
        led[k] = _pick(modes, idx, lambda m, k=k: m.water[k])
    for k in hybrid.HEAT_KEYS:
        led[k + "_w" if not k.endswith("_w") else k] = _pick(modes, idx, lambda m, k=k: m.heat[k])
    led["t_supply_c"] = _pick(modes, idx, lambda m: m.t_supply)
    led["w_supply"] = _pick(modes, idx, lambda m: m.w_supply)
    led["oa_fraction"] = _pick(modes, idx, lambda m: m.oa_fraction)
    led["partial"] = _pick(modes, idx, lambda m: m.partial.astype(float)).astype(bool)
    led["reasons"] = _pick(modes, idx, lambda m: m.reasons.astype(float)).astype(int)
    led["t_return_c"] = _pick(modes, idx, lambda m: m.extra.get("t_return", m.t_supply))

    # Thermal storage dispatch on the plant coil load.
    st = cfg.strategies.storage
    tes_info = {"enabled": False}
    led["p_tes_pumps_w"] = np.zeros(n)
    led["tes_soc_kwh"] = np.zeros(n)
    if st.enabled:
        store = ThermalStorage(st.capacity_kwh_th, st.max_charge_kw, st.max_discharge_kw, st.standing_loss_per_h,
                               st.discharge_efficiency, st.pump_w_per_kw)
        plant_p = led["p_chiller_w"] + led["p_chw_pumps_w"] + led["p_cw_pumps_w"] + led["p_tower_fans_w"] + led["p_dry_cooler_fans_w"]
        q_coil = led["q_coil_w"]
        marg = np.where(q_coil > 0, plant_p / np.maximum(q_coil, 1.0), 0.0)
        disp = controls.tes_dispatch(store, q_coil, marg, local_dates)
        new_plant = evaluate_mechanical(sz, cfg, ctx, disp["q_coil_w"], t_supply_air_c=led["t_supply_c"] - 2.0)
        old_total = plant_p.sum()
        new_total = (new_plant["p_chiller"] + new_plant["p_chw_pumps"] + new_plant["p_cw_pumps"] + new_plant["p_tower_fans"]
                     + new_plant["p_dry_cooler_fans"]).sum() + disp["pump_w"].sum()
        if new_total < old_total:
            for k in ("p_chiller", "p_chw_pumps", "p_cw_pumps", "p_tower_fans", "p_dry_cooler_fans", "p_radiative_pumps"):
                led[k + "_w"] = new_plant[k]
            for k in ("wl_tower_evap", "wl_tower_blowdown", "wl_tower_drift"):
                led[k] = new_plant[k]
            led["q_rejected_condenser_w"] = new_plant["q_rejected_condenser"]
            led["q_free_economiser_w"] = new_plant["q_free_economiser"]
            led["q_radiative_w"] = new_plant["q_radiative"]
            led["unmet_w"] = new_plant["unmet_w"]
            led["p_tes_pumps_w"] = disp["pump_w"]
            led["tes_soc_kwh"] = disp["soc_kwh"]
            led["q_tes_w"] = (disp["discharge_kw"] - disp["charge_kw"]) * 1000.0
            led["tes_env_gain_w"] = disp["env_gain_kw"] * 1000.0
            soc_prev = np.concatenate([[disp["initial_kwh"]], disp["soc_kwh"][:-1]])
            led["tes_heat_stored_w"] = (soc_prev - disp["soc_kwh"]) * 1000.0
            tes_info = {"enabled": True, "applied": True, "energy_change_kwh": float(disp["soc_kwh"][-1] - disp["initial_kwh"]),
                        "discharged_mwh": float(disp["discharge_kw"].sum() / 1000), "charged_mwh": float(disp["charge_kw"].sum() / 1000)}
        else:
            tes_info = {"enabled": True, "applied": False,
                        "reason": "dispatch did not reduce plant electricity for this climate/plant (storage left idle)"}
            flags.append({"level": "info", "code": "tes_idle", "message": tes_info["reason"]})
    led.setdefault("q_tes_w", np.zeros(n))
    led.setdefault("tes_env_gain_w", np.zeros(n))
    led.setdefault("tes_heat_stored_w", np.zeros(n))

    # Liquid-cooling loop (independent of the air-side mode).
    led["p_liquid_pumps_w"] = np.zeros(n)
    led["q_liquid_rejected_w"] = np.zeros(n)
    if q_liq.any():
        liq = evaluate_mechanical(sz, cfg, ctx, np.zeros(n), q_liquid_w=q_liq)
        led["p_chiller_w"] = led["p_chiller_w"] + liq["p_chiller"]
        led["p_dry_cooler_fans_w"] = led["p_dry_cooler_fans_w"] + liq["p_dry_cooler_fans"]
        led["p_liquid_pumps_w"] = liq["p_liquid_pumps"]
        led["q_liquid_rejected_w"] = liq["q_liquid_rejected"] + liq["p_liquid_pumps"]
        led["unmet_w"] = led["unmet_w"] + liq["unmet_w"]

    led["p_actuators_w"] = env_loads.actuator_w
    led["p_it_w"] = p_it
    led["p_ups_loss_w"] = elec["ups_loss"]
    led["p_dist_loss_w"] = elec["dist_loss"]
    led["p_lighting_w"] = elec["lighting"]
    led["p_other_w"] = elec["other"]
    led["p_controls_w"] = elec["controls"]
    led["p_cooling_w"] = sum(led[c] for c in COOLING_POWER_COLUMNS)
    led["p_facility_w"] = (led["p_cooling_w"] + p_it + elec["ups_loss"] + elec["dist_loss"] + elec["lighting"]
                           + elec["other"] + elec["controls"])
    led["wl_withdrawal"] = sum(led[k] for k in hybrid.WATER_KEYS)
    led["wl_consumption"] = (led["wl_tower_evap"] + led["wl_tower_drift"] + led["wl_evap_media"] + led["wl_humidification"]
                             + led["wl_precool"] * (1 - 1 / max(cfg.strategies.evaporative.cycles_of_concentration, 1.1)))
    led["carbon_intensity_kg_kwh"] = intensity
    led["co2e_kg"] = led["p_facility_w"] / 1000.0 * intensity
    led["mode"] = np.asarray(hybrid.MODES, dtype=object)[idx]
    led["q_envelope_w"] = env_loads.q_total_w
    led["q_it_air_w"] = hs.q_it_air
    led["q_space_losses_w"] = hs.q_space
    led["t_inlet_c"] = led["t_supply_c"] + hs.recirc_k
    led["rh_inlet_pct"] = np.clip(ps.rh_from_humidity_ratio(led["t_inlet_c"], led["w_supply"], ctx.p), 0, 100)
    led["dp_inlet_c"] = ps.dew_point_from_humidity_ratio(np.maximum(led["w_supply"], 1e-6), ctx.p)
    led["t_oa_c"] = ctx.t_db
    led["t_wb_oa_c"] = ctx.t_wb
    led["t_dp_oa_c"] = ctx.t_dp
    led["rh_oa_pct"] = ctx.rh
    led["m_server_kg_s"] = hs.m_srv
    led["m_facility_kg_s"] = hs.m_fac
    if eahe_res is not None:
        led["eahe_flow_m3s"] = eahe_flow
        led["eahe_t_out_c"] = eahe_res.t_out_c
        led["eahe_q_sensible_w"] = eahe_res.q_sensible_w
        led["eahe_soil_drift_k"] = eahe_res.soil_drift_k
        led["eahe_condensate_kg"] = eahe_res.condensate_kg
        led["t_ground_c"] = eahe_res.diagnostics["t_ground_c"]
    nat = modes["econ_natural"].extra.get("natural")
    if nat is not None:
        led["natural_flow_kg_s"] = nat.m_natural_kg_s
        led["natural_sufficient"] = nat.sufficient
        led["natural_fan_dp_pa"] = nat.fan_dp_pa

    hourly = pd.DataFrame(led, index=ctx.index)
    hourly.index.name = "time_utc"
    hourly.insert(0, "time_local", ctx.local_index.tz_localize(None) if getattr(ctx.local_index, "tz", None) else ctx.local_index)

    # Facility energy balance: all electricity + envelope gains = heat leaving by every path (+ storage, unmet).
    outside_losses = (1 - cfg.electrical.losses_in_cooled_space_fraction) * (elec["ups_loss"] + elec["dist_loss"]) \
        + elec["other"] + elec["controls"]
    sources = {"facility_power": led["p_facility_w"], "envelope": env_loads.q_total_w, "tes_environment_gain": led["tes_env_gain_w"]}
    sinks = {
        "condenser_plant": led["q_rejected_condenser_w"], "tower_fans": led["p_tower_fans_w"],
        "dry_cooler_fans": led["p_dry_cooler_fans_w"], "cw_pumps": led["p_cw_pumps_w"], "economiser_exhaust": led["q_exhaust_w"],
        "exfiltration": led["q_exfiltration_w"], "ground": led["q_ground_w"], "iec_secondary": led["q_iec_secondary_w"],
        "radiative_sky": led["q_radiative_w"], "liquid_loop": led["q_liquid_rejected_w"], "outside_losses": outside_losses,
        "unmet_stored_in_zone": led["unmet_w"], "actuators": led["p_actuators_w"], "tes_pumps": led["p_tes_pumps_w"],
        # adiabatic humidifier pump/ultrasonic power is lost locally; steam enthalpy leaves via exhaust or coil
        "humidifier": led["p_humidifier_w"] if cfg.baseline.humidification == "adiabatic" else np.zeros(n),
        "evap_pumps": led["p_evap_pumps_w"],
        "iec_secondary_fans_outdoor": np.zeros(n), "radiative_pumps": led["p_radiative_pumps_w"],
    }
    # Water-side free cooling is rejected by the same tower/dry cooler and is already in q_rejected_condenser.
    rep = closure(sources, sinks, storage_change_w=led["tes_heat_stored_w"], tolerance=cfg.simulation.closure_tolerance)
    hourly["balance_residual_w"] = rep.residual_w
    if not rep.passed:
        flags.append({"level": "warning", "code": "energy_balance",
                      "message": f"facility energy balance residual up to {100 * rep.max_relative_residual:.2f} % "
                                 f"(tolerance {100 * rep.tolerance:.1f} %)"})

    # Failure scenarios.
    progress(0.85, "failure checks")
    worst = int(np.argmax(ctx.t_db))
    failure = failure_checks(cfg, ctx, hs, sz, worst, it_cap_w, env_loads)
    if not failure["n_minus_1"]["passed"]:
        flags.append({"level": "error", "code": "redundancy",
                      "message": "Mechanical plant cannot meet design-hour load with one unit failed (N+1 check)"})
    unmet_h = int(np.sum(hourly["unmet_w"] > 1.0))
    if unmet_h:
        flags.append({"level": "error", "code": "thermal_violation",
                      "message": f"{unmet_h} h with unmet cooling — server inlet conditions would exceed limits"})
    if hs.t_set + hs.recirc_k > hs.envelope.t_max_c:
        flags.append({"level": "error", "code": "setpoint", "message": "setpoint exceeds envelope maximum"})

    from app.accounting.metrics import summarise  # local import to avoid a cycle
    summary = summarise(cfg, hourly, ctx, modes, sel, sz, env_loads, eahe_res, tes_info, rep, failure, intensity_label)
    diagnostics = {
        "sizing": sz.to_dict(), "ground": ground.to_dict(), "envelope": {"constructions": env_loads.constructions,
                                                                       **env_loads.description,
                                                                       "stored_change_kwh": env_loads.stored_change_j / 3.6e6},
        "closure": rep.to_dict(), "failure": failure, "available_modes": avail, "tes": tes_info,
        "envelope_limits": hs.envelope.to_dict(), "t_set_supply_c": hs.t_set, "t_econ_max_c": hs.t_econ_max,
        "recirculation_allowance_k": hs.recirc_k, "climate_notes": ctx.notes,
        "mode_reasons": {name: _reason_counts(modes[name]) for name in hybrid.MODES if modes[name].available},
        "mode_feasible_hours": {name: int(np.sum(modes[name].feasible)) for name in hybrid.MODES if modes[name].available},
        "switches": sel.switches, "emergency_hours": sel.emergency_hours, "water_limited_hours": sel.water_limited_hours,
    }
    if eahe_res is not None:
        diagnostics["eahe"] = {k: v for k, v in eahe_res.diagnostics.items() if k not in ("final_theta", "t_ground_c")}
        diagnostics["eahe"]["yearly"] = eahe_res.yearly
    if nat is not None:
        diagnostics["natural_ventilation"] = {
            "sufficient_hours": int(nat.sufficient.sum()), "reversed_hours": int(nat.reversed_flow.sum()),
            "mean_natural_flow_kg_s": float(np.mean(nat.m_natural_kg_s)), "mean_required_kg_s": float(np.mean(nat.m_required_kg_s)),
            "network_converged_fraction": nat.converged_frac,
            "median_fan_dp_pa": float(np.median(nat.fan_dp_pa)),
        }
    progress(1.0, "done")
    return SimulationResult(scenario, hourly, summary, diagnostics, flags, cfg.config_hash(),
                            ds.provenance.extra.get("sha256", ""))


def _reason_counts(m: hybrid.ModeResult) -> dict:
    out = {}
    for bit, label in hybrid.REASONS.items():
        cnt = int(np.sum((m.reasons & bit) > 0))
        if cnt:
            out[label] = cnt
    return out


def failure_checks(cfg, ctx, hs, sz, worst: int, it_cap_w: float, env_loads) -> dict:
    """N+1 at the design hour with full IT load, and ride-through after total loss of cooling."""
    q_design_hour = sz.q_design_w / (1 + cfg.baseline.design_margin)
    q = np.zeros(ctx.n)
    q[worst] = q_design_hour
    n_av = sz.n_installed - 1
    res = evaluate_mechanical(sz, cfg, ctx, q, n_units_available=n_av)
    unmet = float(res["unmet_w"][worst])
    b = cfg.building
    mass = 0.0
    # Structural / slab mass participating within ~1 h (approx. 0.05 m effective depth of the slab).
    mass += b.length_m * b.width_m * 0.05 * 2300 * 1000.0
    if cfg.strategies.envelope.enabled:
        mass += (b.length_m * b.width_m + 2 * (b.length_m + b.width_m) * b.height_m) * 0.05 * 2300 * 1000.0
    coupling = 8.0 * b.length_m * b.width_m * 1.5
    t_lim = hs.envelope.t_max_c if cfg.it.envelope_mode == "allowable" else get_envelope(cfg.it.ashrae_class, "allowable").t_max_c
    rt = ride_through(it_cap_w * (1 - cfg.it.liquid_cooled_fraction), b.volume_m3, mass, coupling,
                      cfg.it.inlet_setpoint_c, t_lim, t_max_s=3600.0)
    return {
        "n_minus_1": {"passed": unmet <= 1.0, "unmet_kw": unmet / 1e3, "design_hour_utc": str(ctx.index[worst]),
                      "design_hour_t_db_c": float(ctx.t_db[worst]), "units_available": n_av,
                      "design_load_kw": q_design_hour / 1e3},
        "ride_through": {"time_to_allowable_limit_s": rt["time_to_limit_s"], "air_only_s": rt["air_only_time_s"],
                         "limit_c": t_lim, "assumptions": "zone air + ~50 mm of slab (and walls if layered shell) participating; "
                                                          "IT at installed capacity; no cooling, no UPS-backed fans"},
    }


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:24]
