"""Hourly evaluation of candidate cooling modes (strategy I: adaptive hybrid airflow).

Each mode returns arrays over all hours: feasibility, constraint-violation reasons, supply-air
state, electricity by component, water by component and heat by rejection path. Hard
constraints (server-inlet temperature and humidity envelope, airflow sufficiency, plant
capacity, air quality) are applied *before* any objective weighting, so the optimiser can never
trade thermal safety for savings. Mechanical cooling is always evaluated as the fallback.

Thermal model of the hall (zonal, well mixed):
  ṁ_srv = Q_IT,air / (c_p ΔT_IT)               server airflow set by the IT equipment fans
  ṁ_fac = ṁ_srv (1 + bypass)                     facility airflow (containment-dependent bypass)
  T_inlet = T_supply + ΔT_recirculation          containment-dependent recirculation allowance
  T_return = T_supply + Q_zone / (ṁ_fac c_p)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.cooling import passive
from app.cooling.plant import PlantSizing, evaluate_mechanical
from app.core.environmental_classes import Envelope
from app.physics import evaporative as evap
from app.physics import psychrometrics as ps
from app.schemas.config import ProjectConfig
from app.simulation.context import ClimateArrays

MODES = ["mechanical", "mech_precool", "econ_direct_fan", "econ_natural", "econ_eahe", "econ_indirect", "iec_wet", "dec_direct"]
MODE_LABELS = {
    "mechanical": "Mechanical (closed loop)",
    "mech_precool": "Mechanical + evaporative condenser pre-cooling",
    "econ_direct_fan": "Outdoor-air economiser (fans)",
    "econ_natural": "Stack/wind-assisted outdoor-air economiser",
    "econ_eahe": "Earth-air pre-cooled intake",
    "econ_indirect": "Indirect air economiser (dry heat exchanger)",
    "iec_wet": "Indirect evaporative cooling",
    "dec_direct": "Direct evaporative cooling",
}

# Reason bit flags (why a mode is infeasible or partial in an hour).
R_TEMP_HIGH = 1
R_TEMP_LOW = 2
R_HUMID_HIGH = 4
R_HUMID_LOW = 8
R_AIR_QUALITY = 16
R_AIRFLOW = 32
R_CAPACITY = 64
R_NO_BENEFIT = 128
R_WATER_CAP = 256
R_NOT_AVAILABLE = 512
R_DEHUMIDIFY = 1024  # feasible but needs mechanical dehumidification (partial)
REASONS = {R_TEMP_HIGH: "temperature too high", R_TEMP_LOW: "temperature too low", R_HUMID_HIGH: "humidity/dew point too high",
           R_HUMID_LOW: "humidity/dew point too low", R_AIR_QUALITY: "outdoor air quality", R_AIRFLOW: "insufficient airflow",
           R_CAPACITY: "plant capacity", R_NO_BENEFIT: "no benefit (outdoor air warmer than return)",
           R_WATER_CAP: "water limit", R_NOT_AVAILABLE: "strategy not installed", R_DEHUMIDIFY: "needs dehumidification"}

POWER_KEYS = ["p_crah_fans", "p_econ_fans", "p_fan_assist", "p_eahe_fans", "p_iec_fans", "p_evap_pumps", "p_chiller",
              "p_chw_pumps", "p_cw_pumps", "p_tower_fans", "p_dry_cooler_fans", "p_radiative_pumps", "p_humidifier"]
WATER_KEYS = ["wl_tower_evap", "wl_tower_blowdown", "wl_tower_drift", "wl_evap_media", "wl_evap_bleed", "wl_humidification", "wl_precool"]
HEAT_KEYS = ["q_coil", "q_rejected_condenser", "q_exhaust", "q_exfiltration", "q_ground", "q_iec_secondary", "q_radiative",
             "q_free_economiser", "unmet_w", "q_indoor_fan_heat", "q_zone"]


@dataclass
class HallState:
    """Mode-independent hourly quantities."""

    q_it_air: np.ndarray
    q_space: np.ndarray        # electrical losses + lighting in the cooled space
    q_env: np.ndarray          # envelope (signed)
    m_srv: np.ndarray
    m_fac: np.ndarray
    m_oa_min: np.ndarray       # minimum outdoor air + infiltration (dry-air kg/s)
    t_set: float               # supply setpoint (°C)
    t_econ_max: float          # maximum supply temperature allowed in economiser modes
    t_econ_min: float
    recirc_k: float
    envelope: Envelope
    elevation_m: float
    poor_aq: np.ndarray

    @property
    def q_zone_base(self) -> np.ndarray:
        return self.q_it_air + self.q_space + self.q_env


@dataclass
class ModeResult:
    name: str
    available: bool
    feasible: np.ndarray
    reasons: np.ndarray
    partial: np.ndarray
    t_supply: np.ndarray
    w_supply: np.ndarray
    oa_fraction: np.ndarray
    power: dict[str, np.ndarray]
    water: dict[str, np.ndarray]
    heat: dict[str, np.ndarray]
    extra: dict = field(default_factory=dict)

    def cooling_power(self) -> np.ndarray:
        return sum(self.power.values())

    def water_total(self) -> np.ndarray:
        return sum(self.water.values())


def humidity_limits(env: Envelope, t_inlet, p):
    """Upper and lower humidity-ratio limits at the server inlet temperature."""
    w_hi = np.minimum(ps.humidity_ratio_from_dew_point(env.dp_max_c, p), ps.humidity_ratio_from_rh(t_inlet, env.rh_max_pct, p))
    w_lo = ps.humidity_ratio_from_dew_point(env.dp_min_c, p)
    if env.rh_min_pct > 0:
        w_lo = np.maximum(w_lo, ps.humidity_ratio_from_rh(t_inlet, env.rh_min_pct, p))
    return w_hi, w_lo


def _zeros(n, keys):
    return {k: np.zeros(n) for k in keys}


def _unavailable(name, n, t_set):
    return ModeResult(name, False, np.zeros(n, bool), np.full(n, R_NOT_AVAILABLE), np.zeros(n, bool), np.full(n, t_set),
                      np.zeros(n), np.zeros(n), _zeros(n, POWER_KEYS), _zeros(n, WATER_KEYS), _zeros(n, HEAT_KEYS))


def _merge_plant(power, water, heat, plant):
    for k in ("p_chiller", "p_chw_pumps", "p_cw_pumps", "p_tower_fans", "p_dry_cooler_fans", "p_radiative_pumps"):
        power[k] = power[k] + plant[k]
    for k in ("wl_tower_evap", "wl_tower_blowdown", "wl_tower_drift", "wl_precool"):
        water[k] = water[k] + plant[k]
    heat["q_rejected_condenser"] = heat["q_rejected_condenser"] + plant["q_rejected_condenser"]
    heat["q_radiative"] = heat["q_radiative"] + plant["q_radiative"]
    heat["q_free_economiser"] = heat["q_free_economiser"] + plant["q_free_economiser"]
    heat["unmet_w"] = heat["unmet_w"] + plant["unmet_w"]


def _humidification(cfg, m_w):
    """Water (L/h) and power (W) to add m_w kg/s of moisture."""
    kind = cfg.baseline.humidification
    water = m_w * 3600.0
    power = np.where(kind == "steam", m_w * 2.6e6 / 0.98, m_w * 0.05e6)  # adiabatic ≈ 50 kJ/kg pumping/ultrasonic (assumption)
    return water, power


def mechanical_mode(cfg: ProjectConfig, ctx: ClimateArrays, hs: HallState, sz: PlantSizing, *, precool=False,
                    name="mechanical", n_units_available=None) -> ModeResult:
    n = ctx.n
    t_sup = np.full(n, hs.t_set)
    t_in = t_sup + hs.recirc_k
    w_hi, w_lo = humidity_limits(hs.envelope, t_in, ctx.p)
    w_room = np.clip(ctx.w, w_lo, w_hi)
    power, water, heat = _zeros(n, POWER_KEYS), _zeros(n, WATER_KEYS), _zeros(n, HEAT_KEYS)
    v_fac = hs.m_fac * ps.specific_volume(t_sup, w_room, ctx.p)
    fan_p, _, deficit = sz.crah.power(v_fac, n_units_available)
    power["p_crah_fans"] = fan_p
    heat["q_indoor_fan_heat"] = fan_p
    q_zone = hs.q_zone_base + fan_p
    t_ret = t_sup + q_zone / (hs.m_fac * ps.humid_specific_heat(w_room))
    h_ret = ps.enthalpy(t_ret, w_room)
    q_oa = hs.m_oa_min * (ctx.h - h_ret)
    m_hum = hs.m_oa_min * np.maximum(w_lo - ctx.w, 0.0)
    wl_h, p_h = _humidification(cfg, m_hum)
    if cfg.baseline.humidification == "none":
        wl_h, p_h = np.zeros(n), np.zeros(n)
    water["wl_humidification"] = wl_h
    power["p_humidifier"] = p_h
    if cfg.baseline.humidification == "steam":
        q_zone = q_zone + p_h  # steam enthalpy added to the air must be removed by the coil
    q_coil = np.maximum(q_zone + q_oa, 0.0)
    plant = evaluate_mechanical(sz, cfg, ctx, q_coil, t_supply_air_c=t_sup - 2.0, precool=precool,
                                n_units_available=n_units_available)
    _merge_plant(power, water, heat, plant)
    heat["q_coil"] = q_coil
    heat["q_exfiltration"] = hs.m_oa_min * (h_ret - ctx.h)
    heat["q_zone"] = q_zone
    reasons = np.where(plant["unmet_w"] > 1.0, R_CAPACITY, 0) | np.where(deficit > 0, R_AIRFLOW, 0)
    feasible = reasons == 0
    return ModeResult(name, True, feasible, reasons.astype(int), np.zeros(n, bool), t_sup, w_room, hs.m_oa_min / hs.m_fac,
                      power, water, heat, extra={"t_return": t_ret, "cop": plant.get("cop"), "units_running": plant.get("units_running")})


def _direct_economiser(cfg, ctx, hs, sz, *, name, t_in_air, w_in_air, h_oa_ref, fan_mode: str, extra_power=None,
                       extra_water=None, extra_heat=None, natural: passive.NaturalVentResult | None = None,
                       fan_full_override=None, extra_path_dp_pa: float = 0.0) -> ModeResult:
    """Shared outdoor-air economiser logic (direct air paths, with mixing, partial trim and humidity control)."""
    n = ctx.n
    b = cfg.baseline
    power, water, heat = _zeros(n, POWER_KEYS), _zeros(n, WATER_KEYS), _zeros(n, HEAT_KEYS)
    cp = ps.humid_specific_heat(w_in_air)
    v_fac = hs.m_fac * ps.specific_volume(np.full(n, hs.t_set), w_in_air, ctx.p)
    if fan_full_override is not None:
        fan_full = np.asarray(fan_full_override, dtype=float)
    elif fan_mode == "fans":
        fan_full = v_fac * (cfg.strategies.economiser.path_dp_pa + extra_path_dp_pa) / b.crah_fan_efficiency
    else:
        fan_full = np.zeros(n)
    q_src = hs.q_zone_base
    t_target = np.full(n, hs.t_set)
    reasons = np.zeros(n, dtype=int)
    partial = np.zeros(n, bool)

    # Air path: intake → (humidifier | dehumidifying coil) → mixing box with return air → supply fans
    # (economiser fans, plus CRAH fans when the coil is used) → trim coil → zone → return → exhaust.
    # 1. Humidity limits at the expected inlet temperature; steady-state supply humidity = intake humidity.
    t_inlet_guess = np.clip(t_in_air, hs.t_set, hs.t_econ_max) + hs.recirc_k
    w_hi, w_lo = humidity_limits(hs.envelope, t_inlet_guess, ctx.p)
    too_wet = w_in_air > w_hi
    too_dry = w_in_air < w_lo

    # 2. Humidification of the intake (adiabatic: isenthalpic, cools the air; steam: isothermal).
    t_raw = np.asarray(t_in_air, dtype=float).copy()
    w_i = np.asarray(w_in_air, dtype=float).copy()
    if b.humidification == "none":
        reasons |= np.where(too_dry, R_HUMID_LOW, 0)
    else:
        w_i = np.where(too_dry, w_lo, w_i)
        if b.humidification == "adiabatic":
            t_raw = np.where(too_dry, ps.temperature_from_enthalpy(ps.enthalpy(t_in_air, w_in_air), w_i), t_raw)
    cp = ps.humid_specific_heat(w_i)
    t_ret_target = t_target + q_src / (hs.m_fac * cp)

    def classify(dt_f):
        t_eff = t_raw + dt_f
        mix = t_eff <= t_target
        flt = (t_eff > t_target) & (t_eff <= hs.t_econ_max)
        trim = (t_eff > hs.t_econ_max) & (t_raw < t_ret_target - 0.5)
        return mix, flt, trim

    fan_dt = fan_full / (hs.m_fac * cp)
    case_mix, case_float, case_trim = classify(fan_dt)
    dehum = too_wet & bool(b.dehumidification_allowed)
    uses_coil = (case_trim & ~too_wet) | dehum
    crah_p, _, _ = sz.crah.power(np.where(uses_coil, v_fac, 0.0))
    dt_f = fan_dt + crah_p / (hs.m_fac * cp)
    case_mix, case_float, case_trim = classify(dt_f)

    t_sup = np.where(case_float, t_raw + dt_f, t_target)
    t_ret = t_sup + q_src / (hs.m_fac * cp)
    with np.errstate(divide="ignore", invalid="ignore"):
        x = np.where(case_mix, (t_ret - t_target + dt_f) / np.maximum(t_ret - t_raw, 1e-6), 1.0)
    x = np.clip(x, 0.0, 1.0)
    q_coil = np.where(case_trim, hs.m_fac * cp * (t_raw + dt_f - t_target), 0.0)
    w_sup = w_i.copy()
    no_benefit = ~(case_mix | case_float | case_trim)

    # 3. Too humid: coil cools the outdoor-air stream to the dew-point limit, return air re-mixes to setpoint.
    if b.dehumidification_allowed:
        t_coil = ps.dew_point_from_humidity_ratio(w_hi, ctx.p)
        cp_dh = ps.humid_specific_heat(w_hi)          # recirculated air carries the dehumidified moisture content
        t_ret_dh = t_target + q_src / (hs.m_fac * cp_dh)
        dt_f_dh = dt_f * cp / cp_dh
        with np.errstate(divide="ignore", invalid="ignore"):
            x_dh = np.clip((t_ret_dh - t_target + dt_f_dh) / np.maximum(t_ret_dh - t_coil, 1e-6), 0.0, 1.0)
        q_dh = x_dh * hs.m_fac * np.maximum(ps.enthalpy(t_raw, w_i) - ps.enthalpy(t_coil, w_hi), 0.0)
        worthwhile = t_raw < t_ret_dh - 0.5
        x = np.where(too_wet, x_dh, x)
        q_coil = np.where(too_wet, q_dh, q_coil)
        t_sup = np.where(too_wet, t_target, t_sup)
        t_ret = np.where(too_wet, t_ret_dh, t_ret)
        w_sup = np.where(too_wet, w_hi, w_sup)
        no_benefit = np.where(too_wet, ~worthwhile, no_benefit)
        partial |= too_wet
        reasons |= np.where(too_wet, R_DEHUMIDIFY, 0)
    else:
        reasons |= np.where(too_wet, R_HUMID_HIGH, 0)
    partial |= case_trim & ~too_wet
    reasons |= np.where(no_benefit, R_NO_BENEFIT, 0)
    if b.humidification != "none":
        m_hum = np.where(too_dry, x * hs.m_fac * (w_lo - w_in_air), 0.0)
        wl_h, p_h = _humidification(cfg, m_hum)
        water["wl_humidification"] = wl_h
        power["p_humidifier"] = p_h
    reasons |= np.where(hs.poor_aq, R_AIR_QUALITY, 0)

    # 4. Air movement.
    if fan_mode == "natural" and natural is not None:
        if cfg.strategies.stack.fan_assist:
            power["p_fan_assist"] = np.where(natural.sufficient, 0.0, natural.fan_power_w)
        else:
            reasons |= np.where(natural.sufficient, 0, R_AIRFLOW)
    else:
        power["p_econ_fans"] = fan_full
    power["p_crah_fans"] = crah_p
    indoor_fan_heat = fan_full + crah_p
    heat["q_indoor_fan_heat"] = indoor_fan_heat

    for src, dst in ((extra_power, power), (extra_water, water), (extra_heat, heat)):
        if src:
            for k, v in src.items():
                dst[k] = dst[k] + v

    plant = evaluate_mechanical(sz, cfg, ctx, q_coil, t_supply_air_c=t_target - 2.0)
    _merge_plant(power, water, heat, plant)
    heat["q_coil"] = q_coil
    heat["q_zone"] = q_src + indoor_fan_heat
    h_ret = ps.enthalpy(t_ret, w_sup)
    heat["q_exhaust"] = x * hs.m_fac * (h_ret - h_oa_ref) + power["p_fan_assist"]
    reasons |= np.where(plant["unmet_w"] > 1.0, R_CAPACITY, 0)
    hard = reasons & ~R_DEHUMIDIFY
    feasible = hard == 0
    return ModeResult(name, True, feasible, reasons, partial, t_sup, w_sup, x, power, water, heat,
                      extra={"t_return": t_ret, "t_intake": t_raw})


def econ_direct_fan(cfg, ctx, hs, sz):
    return _direct_economiser(cfg, ctx, hs, sz, name="econ_direct_fan", t_in_air=ctx.t_db, w_in_air=ctx.w,
                              h_oa_ref=ctx.h, fan_mode="fans")


def econ_natural(cfg, ctx, hs, sz):
    n = ctx.n
    # Supply temperature the economiser would deliver, for the network's zone temperatures.
    t_sup_est = np.clip(ctx.t_db, hs.t_set, hs.t_econ_max)
    cp = ps.humid_specific_heat(ctx.w)
    t_ret_est = t_sup_est + hs.q_zone_base / (hs.m_fac * cp)
    extra_area = None
    if cfg.strategies.facade.enabled and cfg.strategies.facade.mode == "kinetic" and cfg.strategies.facade.open_adds_inlet_area_m2 > 0:
        extra_area = np.full(n, cfg.strategies.facade.open_adds_inlet_area_m2)
    nat = passive.natural_ventilation(cfg, ctx, t_sup_est, t_ret_est, hs.m_srv, extra_inlet_area_m2=extra_area)
    res = _direct_economiser(cfg, ctx, hs, sz, name="econ_natural", t_in_air=ctx.t_db, w_in_air=ctx.w, h_oa_ref=ctx.h,
                             fan_mode="natural", natural=nat)
    res.extra["natural"] = nat
    return res


def econ_eahe(cfg, ctx, hs, sz, eahe_res, eahe_flow_m3s):
    """Outdoor-air economiser whose intake is (partly) pre-cooled in the earth-air exchanger.

    The intake stream is x·ṁ_fac (x from the economiser logic); the EAHE supplies up to its scheduled
    flow of it, the remainder enters through facade louvres with economiser fans. x and the intake
    composition are solved by damped fixed-point iteration. EAHE flow beyond the intake demand is
    assumed throttled (VFD), so ground heat and EAHE fan energy are scaled by the utilisation u.
    """
    n = ctx.n
    rho_da = ctx.rho / (1 + ctx.w)
    m_e_avail = eahe_flow_m3s * rho_da
    v_oa = ps.specific_volume(ctx.t_db, ctx.w, ctx.p)
    dp = cfg.strategies.economiser.path_dp_pa
    eff = cfg.baseline.crah_fan_efficiency
    x = np.ones(n)
    res = None
    for it in range(20):
        m_intake = np.maximum(x * hs.m_fac, 1e-9)
        m_e = np.minimum(m_e_avail, m_intake)
        share = m_e / m_intake
        t_mix, w_mix = ps.mix(share, eahe_res.t_out_c, eahe_res.w_out, 1 - share, ctx.t_db, ctx.w)
        fan = (1 - share) * x * hs.m_fac * v_oa * dp / eff
        res = _direct_economiser(cfg, ctx, hs, sz, name="econ_eahe", t_in_air=t_mix, w_in_air=w_mix, h_oa_ref=ctx.h,
                                 fan_mode="eahe", fan_full_override=fan)
        x_new = res.oa_fraction
        if np.max(np.abs(x_new - x)) < 1e-5:
            x = x_new
            break
        x = x_new if it < 3 else 0.5 * (x + x_new)
    # Final evaluation with the converged intake composition; EAHE utilisation then follows from the
    # resulting outdoor-air fraction so the ground heat matches the EAHE air actually admitted.
    m_intake = np.maximum(x * hs.m_fac, 1e-9)
    share = np.minimum(m_e_avail, m_intake) / m_intake
    t_mix, w_mix = ps.mix(share, eahe_res.t_out_c, eahe_res.w_out, 1 - share, ctx.t_db, ctx.w)
    fan = (1 - share) * x * hs.m_fac * v_oa * dp / eff
    res = _direct_economiser(cfg, ctx, hs, sz, name="econ_eahe", t_in_air=t_mix, w_in_air=w_mix, h_oa_ref=ctx.h,
                             fan_mode="eahe", fan_full_override=fan)
    u = np.where(m_e_avail > 0, np.clip(share * res.oa_fraction * hs.m_fac / np.maximum(m_e_avail, 1e-9), 0.0, 1.0), 0.0)
    q_ground = u * (eahe_res.q_sensible_w + eahe_res.q_latent_w)
    p_eahe = u * eahe_res.fan_power_w
    res.power["p_eahe_fans"] = res.power["p_eahe_fans"] + p_eahe
    res.heat["q_ground"] = res.heat["q_ground"] + q_ground
    # The EAHE fan heat is already in the exchanger outlet temperature (delivered air).
    res.heat["q_indoor_fan_heat"] = res.heat["q_indoor_fan_heat"] + p_eahe
    res.heat["q_zone"] = res.heat["q_zone"] + p_eahe
    off = eahe_flow_m3s <= 0
    res.feasible = res.feasible & ~off
    res.reasons = np.where(off, res.reasons | R_NOT_AVAILABLE, res.reasons)
    res.extra["eahe_share_of_intake"] = share
    res.extra["eahe_utilisation"] = u
    return res


def dec_direct(cfg, ctx, hs, sz):
    eff = cfg.strategies.evaporative.effectiveness if cfg.strategies.evaporative.enabled else cfg.baseline.dec_effectiveness
    cyc = cfg.strategies.evaporative.cycles_of_concentration
    params = evap.DECParams(eff, cyc)
    dec = evap.direct_evaporative(ctx.t_db, ctx.w, ctx.p, hs.m_fac, params)
    res = _direct_economiser(cfg, ctx, hs, sz, name="dec_direct", t_in_air=dec["t_out_c"], w_in_air=dec["w_out"],
                             h_oa_ref=ctx.h, fan_mode="fans", extra_path_dp_pa=params.pad_dp_pa)
    x = res.oa_fraction
    # Evaporation occurs only in the outdoor-air stream actually admitted (x·ṁ_fac).
    res.water["wl_evap_media"] = dec["evaporation_kg_s"] * x * 3600.0
    res.water["wl_evap_bleed"] = dec["bleed_kg_s"] * x * 3600.0
    res.power["p_evap_pumps"] = dec["pump_w"] * x
    # Only worthwhile when evaporation actually cools (warm hours); in cold hours the plain economiser is used instead.
    pointless = ctx.t_db < hs.t_set
    res.feasible = res.feasible & ~pointless
    res.reasons = np.where(pointless, res.reasons | R_NO_BENEFIT, res.reasons)
    return res


def _indirect(cfg, ctx, hs, sz, *, wet: bool, name: str) -> ModeResult:
    n = ctx.n
    b = cfg.baseline
    power, water, heat = _zeros(n, POWER_KEYS), _zeros(n, WATER_KEYS), _zeros(n, HEAT_KEYS)
    t_sup = np.full(n, hs.t_set)
    t_in = t_sup + hs.recirc_k
    w_hi, w_lo = humidity_limits(hs.envelope, t_in, ctx.p)
    w_room = np.clip(ctx.w, w_lo, w_hi)
    cp = ps.humid_specific_heat(w_room)
    params = evap.IECParams(wet_bulb_effectiveness=(cfg.strategies.evaporative.effectiveness if cfg.strategies.evaporative.enabled
                                                    else b.iec_wet_bulb_effectiveness),
                            dry_effectiveness=cfg.strategies.economiser.indirect_effectiveness,
                            cycles_of_concentration=cfg.strategies.evaporative.cycles_of_concentration)
    v_fac = hs.m_fac * ps.specific_volume(t_sup, w_room, ctx.p)
    p_primary = v_fac * params.primary_dp_pa / b.crah_fan_efficiency
    q_zone = hs.q_zone_base + p_primary
    t_ret = t_sup + q_zone / (hs.m_fac * cp)
    r = evap.indirect_evaporative(t_ret, w_room, ctx.t_db, ctx.w, ctx.p, hs.m_fac, params, wet=wet)
    q_hx_max = r["q_w"]
    h_ret = ps.enthalpy(t_ret, w_room)
    q_oa = hs.m_oa_min * (ctx.h - h_ret)
    q_need = np.maximum(hs.m_fac * cp * (t_ret - t_sup) + q_oa, 0.0)   # zone + minimum outdoor-air load
    q_hx = np.minimum(q_hx_max, q_need)
    load_ratio = np.where(q_hx_max > 0, q_hx / np.maximum(q_hx_max, 1e-9), 0.0)
    p_secondary = r["v_secondary_m3s"] * params.secondary_dp_pa / b.crah_fan_efficiency * np.clip(load_ratio, 0.2, 1.0) ** 3
    benefit = q_hx > 0.02 * np.maximum(q_need, 1.0)
    p_secondary = np.where(benefit, p_secondary, 0.0)
    q_hx = np.where(benefit, q_hx, 0.0)
    q_coil = np.maximum(q_need - q_hx, 0.0)
    partial = q_coil > 1.0
    power["p_iec_fans"] = p_primary + p_secondary
    heat["q_indoor_fan_heat"] = p_primary
    if wet:
        ratio = np.where(q_hx_max > 0, q_hx / np.maximum(q_hx_max, 1e-9), 0.0)
        water["wl_evap_media"] = r["evaporation_kg_s"] * ratio * 3600.0
        water["wl_evap_bleed"] = r["bleed_kg_s"] * ratio * 3600.0
        power["p_evap_pumps"] = r["pump_w"] * np.where(benefit, 1.0, 0.0)
    m_hum = hs.m_oa_min * np.maximum(w_lo - ctx.w, 0.0)
    wl_h, p_h = _humidification(cfg, m_hum)
    if b.humidification != "none":
        water["wl_humidification"] = wl_h
        power["p_humidifier"] = p_h
    # IEC units include their own supply fans (p_primary); the trim coil sits in the IEC air path.
    plant = evaluate_mechanical(sz, cfg, ctx, q_coil, t_supply_air_c=t_sup - 2.0)
    _merge_plant(power, water, heat, plant)
    heat["q_coil"] = q_coil
    heat["q_iec_secondary"] = q_hx + p_secondary
    heat["q_exfiltration"] = hs.m_oa_min * (h_ret - ctx.h)
    heat["q_zone"] = q_zone
    reasons = np.where(~benefit, R_NO_BENEFIT, 0) | np.where(plant["unmet_w"] > 1.0, R_CAPACITY, 0)
    feasible = reasons == 0
    return ModeResult(name, True, feasible, reasons.astype(int), partial, t_sup, w_room, hs.m_oa_min / hs.m_fac,
                      power, water, heat, extra={"t_return": t_ret, "q_hx": q_hx})


def econ_indirect(cfg, ctx, hs, sz):
    return _indirect(cfg, ctx, hs, sz, wet=False, name="econ_indirect")


def iec_wet(cfg, ctx, hs, sz):
    return _indirect(cfg, ctx, hs, sz, wet=True, name="iec_wet")


def available_modes(cfg: ProjectConfig) -> dict[str, bool]:
    st = cfg.strategies
    arch = cfg.baseline.architecture
    return {
        "mechanical": True,
        "mech_precool": st.evaporative.enabled and st.evaporative.kind == "precool_condenser"
        and arch in ("air_cooled_chiller_crah", "dx_crac", "dx_with_airside_economiser", "liquid_cooling_dry_cooler"),
        "econ_direct_fan": (st.economiser.enabled and st.economiser.kind == "direct") or arch == "dx_with_airside_economiser",
        "econ_natural": st.stack.enabled,
        "econ_eahe": st.eahe.enabled,
        "econ_indirect": (st.economiser.enabled and st.economiser.kind == "indirect") or arch == "indirect_evaporative"
        or (st.evaporative.enabled and st.evaporative.kind == "indirect"),
        "iec_wet": arch == "indirect_evaporative" or (st.evaporative.enabled and st.evaporative.kind == "indirect"),
        "dec_direct": arch == "direct_evaporative" or (st.evaporative.enabled and st.evaporative.kind == "direct"),
    }
