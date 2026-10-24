"""Mechanical cooling plant: sizing and hourly evaluation for the configured conventional architecture.

The same sizing rule is applied to the baseline and every alternative, so all scenarios carry
identical mechanical capacity and redundancy unless the engineer explicitly credits passive
capacity (reported as a risk). Capacity is sized for the *installed* IT capacity at the design
(worst) hour of the dataset, not for the average load.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.cooling.conventional import ChillerSpec, DryCooler, FanSystem, PumpSpec, chiller_plant
from app.physics import evaporative as evap
from app.physics import psychrometrics as ps
from app.physics import radiative
from app.schemas.config import ProjectConfig
from app.simulation.context import ClimateArrays

DEFAULT_COP = {
    "air_cooled_chiller_crah": 3.0,       # ≈ ASHRAE 90.1-2019 Path A full-load minimum for air-cooled ≥ 150 tons (assumption)
    "water_cooled_chiller_tower": 6.1,    # ≈ 0.576 kW/ton centrifugal (assumption)
    "dx_crac": 3.2,
    "dx_with_airside_economiser": 3.2,
    "direct_evaporative": 3.2,            # DX trim
    "indirect_evaporative": 3.2,          # DX trim
    "liquid_cooling_dry_cooler": 3.0,
}
REDUNDANT_UNITS = {"N": lambda n: 0, "N+1": lambda n: 1, "N+2": lambda n: 2, "2N": lambda n: n}

CHILLED_WATER = {"air_cooled_chiller_crah", "water_cooled_chiller_tower", "liquid_cooling_dry_cooler"}


@dataclass
class PlantSizing:
    architecture: str
    q_design_w: float
    n_duty: int
    n_installed: int
    unit_capacity_w: float
    chiller: ChillerSpec
    crah: FanSystem
    chw_pump: PumpSpec
    cw_pump: PumpSpec
    tower: evap.TowerParams
    dry_cooler: DryCooler
    q_liquid_design_w: float
    v_fac_design_m3s: float
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "architecture": self.architecture, "q_design_kw": self.q_design_w / 1e3, "n_duty": self.n_duty,
            "n_installed": self.n_installed, "unit_capacity_kw": self.unit_capacity_w / 1e3,
            "chiller": self.chiller.to_dict(), "eta_II": self.chiller.eta_ii(),
            "crah": self.crah.to_dict(), "chw_pump": self.chw_pump.to_dict(), "tower": self.tower.to_dict(),
            "dry_cooler": self.dry_cooler.to_dict(), "q_liquid_design_kw": self.q_liquid_design_w / 1e3,
            "v_fac_design_m3s": self.v_fac_design_m3s, "notes": self.notes,
        }


def chiller_spec_for(cfg: ProjectConfig) -> ChillerSpec:
    b = cfg.baseline
    arch = b.architecture
    cop = b.rated_cop or DEFAULT_COP[arch]
    if arch == "water_cooled_chiller_tower":
        spec = ChillerSpec(kind="water_cooled", rated_cop=cop, cond_approach_k=7.5, min_lift_k=12.0, min_condensing_c=20.0)
    elif arch in CHILLED_WATER:
        spec = ChillerSpec(kind="air_cooled", rated_cop=cop, cond_approach_k=12.0)
    else:
        # DX: evaporating ≈ supply air − 10 K; AHRI 340/360-style rating at 35 °C ambient, ~17 °C supply air
        spec = ChillerSpec(kind="air_cooled", rated_cop=cop, evap_approach_k=10.0, cond_approach_k=14.0,
                           rating_override={"t_cond_medium_c": 35.0, "t_chw_c": 17.0})
    spec.model = b.chiller_model
    if b.chiller_model == "biquadratic":
        spec.capft, spec.eirft, spec.eirfplr = b.capft, b.eirft, b.eirfplr
    return spec


def size_plant(cfg: ProjectConfig, q_design_w: float, v_fac_design_m3s: float, q_liquid_design_w: float) -> PlantSizing:
    b = cfg.baseline
    n = b.n_units
    q = q_design_w * (1.0 + b.design_margin)
    n_inst = n + REDUNDANT_UNITS[b.cooling_redundancy](n)
    unit = q / n
    crah = FanSystem(design_flow_m3s_per_unit=v_fac_design_m3s * (1 + b.design_margin) / n, n_units_installed=n_inst,
                     dp_pa=b.crah_dp_pa, efficiency=b.crah_fan_efficiency, run_redundant=b.run_redundant_fans)
    tower = evap.TowerParams(approach_design_k=b.tower_approach_k, cycles_of_concentration=b.tower_cycles,
                             drift_fraction=b.tower_drift_fraction)
    return PlantSizing(b.architecture, q, n, n_inst, unit, chiller_spec_for(cfg), crah,
                       PumpSpec(design_head_pa=b.pump_head_pa, efficiency=b.pump_efficiency),
                       PumpSpec(design_head_pa=0.8 * b.pump_head_pa, efficiency=b.pump_efficiency),
                       tower, DryCooler(approach_k=b.dry_cooler_approach_k), q_liquid_design_w, v_fac_design_m3s)


def evaluate_mechanical(sz: PlantSizing, cfg: ProjectConfig, ctx: ClimateArrays, q_coil_w, *, q_liquid_w=None,
                        t_supply_air_c=None, precool: bool = False, n_units_available: int | None = None) -> dict:
    """Electricity, water and heat rejection to meet a coil (air-side) load with the mechanical plant."""
    b = cfg.baseline
    st = cfg.strategies
    arch = sz.architecture
    n_t = ctx.n
    q_coil = np.maximum(np.asarray(q_coil_w, dtype=float), 0.0) * np.ones(n_t)
    q_liq = np.zeros(n_t) if q_liquid_w is None else np.maximum(np.asarray(q_liquid_w, dtype=float), 0.0)
    out = {k: np.zeros(n_t) for k in (
        "p_chiller", "p_chw_pumps", "p_cw_pumps", "p_tower_fans", "p_dry_cooler_fans", "p_liquid_pumps",
        "p_radiative_pumps", "wl_tower_evap", "wl_tower_blowdown", "wl_tower_drift", "wl_precool",
        "q_rejected_condenser", "q_free_economiser", "q_radiative", "unmet_w", "q_liquid_rejected")}
    t_cond = ctx.t_db.copy()
    chw = b.chw_supply_c
    chw_ret = chw + b.chw_delta_t_k
    is_cw = arch in CHILLED_WATER
    # Chilled-water pump heat adds to the evaporator load.
    if is_cw:
        out["p_chw_pumps"] = sz.chw_pump.power(q_coil, sz.q_design_w, b.chw_delta_t_k)
        q_evap = q_coil + out["p_chw_pumps"]
    else:
        q_evap = q_coil.copy()

    # Radiative sky-cooling panels pre-cool the chilled-water return (chilled-water systems only).
    if st.radiative.enabled and is_cw and st.radiative.panel_area_m2 > 0:
        q_net = radiative.radiative_panel_net_flux(chw_ret - 1.0, ctx.t_db, ctx.t_sky, ctx.ghi, ctx.wind,
                                                   st.radiative.emissivity, st.radiative.solar_absorptance)
        q_rad = np.clip(q_net, 0.0, None) * st.radiative.panel_area_m2 * st.radiative.soiling_derate
        q_rad = np.minimum(q_rad, q_evap)
        out["q_radiative"] = q_rad
        out["p_radiative_pumps"] = np.where(q_rad > 0, st.radiative.pump_w_per_m2 * st.radiative.panel_area_m2, 0.0)
        q_evap = q_evap - q_rad

    # Water-side economiser (series, pre-cools CHW return).
    q_chiller = q_evap.copy()
    if b.waterside_economiser and is_cw:
        if arch == "water_cooled_chiller_tower":
            tw = evap.cooling_tower(q_evap, ctx.t_db, ctx.w, ctx.p, sz.q_design_w, sz.tower)
            t_free = tw["t_leaving_water_c"] + 1.5   # plate heat-exchanger approach
        else:
            t_free = sz.dry_cooler.leaving_temperature(ctx.t_db, np.clip(q_evap / sz.q_design_w, 0.2, 1.0)) + 1.0
        frac = np.clip((chw_ret - t_free) / (chw_ret - chw), 0.0, 1.0)
        out["q_free_economiser"] = frac * q_evap
        q_chiller = q_evap - out["q_free_economiser"]
        if arch != "water_cooled_chiller_tower":
            out["p_dry_cooler_fans"] += sz.dry_cooler.fan_power(out["q_free_economiser"], sz.q_design_w)

    # Condenser medium temperature.
    if arch == "water_cooled_chiller_tower":
        cop_guess = 6.0
        for _ in range(2):
            q_rej_est = q_chiller * (1 + 1 / cop_guess) + out["q_free_economiser"]
            tw = evap.cooling_tower(q_rej_est, ctx.t_db, ctx.w, ctx.p, sz.q_design_w * 1.2, sz.tower)
            t_cond = tw["t_leaving_water_c"]
            cop_guess = np.maximum(sz.chiller.full_load_cop(chw, t_cond), 1.0)
    elif precool:
        pre = evap.direct_evaporative(ctx.t_db, ctx.w, ctx.p, 1.0, evap.DECParams(st.evaporative.effectiveness,
                                                                                    st.evaporative.cycles_of_concentration))
        use = ctx.t_db > 24.0
        t_cond = np.where(use, pre["t_out_c"], ctx.t_db)

    t_evap_side = chw if is_cw else (np.asarray(t_supply_air_c, dtype=float) if t_supply_air_c is not None else cfg.it.inlet_setpoint_c)
    ch = chiller_plant(q_chiller, t_evap_side, t_cond, sz.chiller, sz.unit_capacity_w, sz.n_installed,
                       n_units_available=n_units_available)
    out["p_chiller"] = ch.power_w
    out["unmet_w"] = ch.unmet_w
    out["cop"] = ch.cop_effective
    out["units_running"] = ch.units_running
    delivered = q_chiller - ch.unmet_w
    q_rej = delivered + ch.power_w + out["q_free_economiser"]

    if precool and arch not in ("water_cooled_chiller_tower",):
        use = ctx.t_db > 24.0
        m_cond_air = np.where(use, q_rej / (1006.0 * 10.0), 0.0)
        pre = evap.direct_evaporative(ctx.t_db, ctx.w, ctx.p, m_cond_air, evap.DECParams(st.evaporative.effectiveness,
                                                                                           st.evaporative.cycles_of_concentration))
        out["wl_precool"] = (pre["evaporation_kg_s"] + pre["bleed_kg_s"]) * 3600.0

    # Liquid-cooling loop: dry cooler, chiller trim when ambient too warm.
    if q_liq.any():
        lt = cfg.it.liquid_supply_c
        t_dc = sz.dry_cooler.leaving_temperature(ctx.t_db, np.clip(q_liq / max(sz.q_liquid_design_w, 1.0), 0.2, 1.0))
        trim_frac = np.clip((t_dc - lt) / 10.0, 0.0, 1.0)
        q_trim = trim_frac * q_liq
        trim = chiller_plant(q_trim, lt - 2.0, t_cond, sz.chiller, max(sz.q_liquid_design_w, 1.0), 2)
        out["p_chiller"] = out["p_chiller"] + trim.power_w
        out["p_dry_cooler_fans"] += sz.dry_cooler.fan_power(q_liq - q_trim, max(sz.q_liquid_design_w, 1.0))
        out["p_liquid_pumps"] = 0.015 * q_liq  # CDU + facility water pumping, 1.5 % of captured heat (assumption)
        out["q_liquid_rejected"] = q_liq + trim.power_w
        out["unmet_w"] = out["unmet_w"] + trim.unmet_w
        q_rej = q_rej + trim.power_w

    if arch == "water_cooled_chiller_tower":
        q_tower = q_rej + out["p_chw_pumps"] * 0.0
        tw = evap.cooling_tower(q_tower, ctx.t_db, ctx.w, ctx.p, sz.q_design_w * 1.2, sz.tower)
        out["p_tower_fans"] = tw["fan_w"]
        out["wl_tower_evap"] = tw["evaporation_kg_s"] * 3600.0
        out["wl_tower_blowdown"] = tw["blowdown_kg_s"] * 3600.0
        out["wl_tower_drift"] = tw["drift_kg_s"] * 3600.0
        out["p_cw_pumps"] = sz.cw_pump.power(q_tower, sz.q_design_w * 1.2, sz.tower.range_design_k)
    out["q_rejected_condenser"] = q_rej
    return out


def oa_air_density(ctx: ClimateArrays) -> np.ndarray:
    return ps.moist_air_density(ctx.t_db, ctx.w, ctx.p)
