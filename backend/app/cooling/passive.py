"""Passive / low-energy strategy wrappers used by the hybrid controller.

Natural ventilation network (strategies A and E)
-------------------------------------------------
Zones: outdoors (boundary), cold aisle / supply plenum, hot aisle + chimney column.
Links: one inlet per facade with wind pressure (Swami–Chandra C_p), server fans as a fixed-flow
element (the IT equipment moves its own air), containment leakage between cold and hot zones
(positive = bypass of surplus air, negative = recirculation of hot air into the cold aisle), and
the chimney outlet with an outlet wind-pressure coefficient. If the natural chimney flow is below
the server demand, hot air would recirculate. That is a thermal-safety violation, so either an
exhaust fan supplies the missing pressure (fan-assisted mode, power computed) or natural mode is
infeasible.

Solar chimney: the chimney column is warmed by η·G·A_collector, raising buoyancy; solved by
fixed-point iteration on the chimney flow. Solar heat leaves with the exhaust and is not added to
the hall load.

Spiral intakes (mollusc shell, part B): treated as a *hypothesis*: the bend component (K ≈ 1.0)
of the inlet path loss is reduced by ``spiral_loss_reduction``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.physics import airflow as af
from app.physics import earth_air
from app.physics import psychrometrics as ps
from app.physics.ground import SOIL_TYPES, GroundModel
from app.schemas.config import EAHEConfig, ProjectConfig
from app.simulation.context import ClimateArrays
from app.simulation.envelope_model import facade_azimuths

BASE_BEND_K = 1.0


@dataclass
class NaturalVentResult:
    m_natural_kg_s: np.ndarray       # natural chimney (exhaust) mass flow
    m_required_kg_s: np.ndarray
    sufficient: np.ndarray           # natural flow ≥ demand without recirculation
    reversed_flow: np.ndarray
    fan_dp_pa: np.ndarray            # exhaust-fan pressure needed for fan-assisted mode
    fan_power_w: np.ndarray
    t_chimney_c: np.ndarray
    stack_dp_pa: np.ndarray
    wind_dp_pa: np.ndarray
    leakage_kg_s: np.ndarray
    converged_frac: float


def _inlet_k(cfg) -> float:
    k = 1.0 / cfg.discharge_coefficient**2 + cfg.inlet_path_loss_k
    if cfg.spiral_intakes:
        k -= cfg.spiral_loss_reduction * BASE_BEND_K
    return max(k, 1.0)


def natural_ventilation(cfg: ProjectConfig, ctx: ClimateArrays, t_cold_c, t_hot_c, m_server_kg_s,
                        extra_inlet_area_m2=None) -> NaturalVentResult:
    sc = cfg.strategies.stack
    b = cfg.building
    n = ctx.n
    t_cold = np.broadcast_to(np.asarray(t_cold_c, dtype=float), (n,)).copy()
    t_hot0 = np.broadcast_to(np.asarray(t_hot_c, dtype=float), (n,)).copy()
    m_srv = np.broadcast_to(np.asarray(m_server_kg_s, dtype=float), (n,)).copy()
    rho_o = ctx.rho
    w = ctx.w
    rho_cold = ps.moist_air_density(t_cold, w, ctx.p)
    az = facade_azimuths(b.orientation_deg)
    side_ratio = {"N": b.length_m / b.width_m, "S": b.length_m / b.width_m, "E": b.width_m / b.length_m, "W": b.width_m / b.length_m}
    u_inlet = af.wind_speed_at_height(ctx.wind, max(b.height_m, 3.0), cfg.site.terrain)
    u_top = af.wind_speed_at_height(ctx.wind, b.height_m + sc.chimney_height_m, cfg.site.terrain)
    a_ch = sc.chimney_area_m2 * sc.n_chimneys
    k_in = _inlet_k(sc)
    p_wind_top = af.wind_pressure(rho_o, u_top, sc.chimney_cp)
    z_top = sc.chimney_height_m + b.height_m

    def build(t_hot, chimney_fixed=None):
        rho_hot = ps.moist_air_density(t_hot, w, ctx.p)
        zones = [af.Zone("out", ctx.t_db, rho_o, boundary=True), af.Zone("cold", t_cold, rho_cold), af.Zone("hot", t_hot, rho_hot)]
        links = []
        for f, area in sc.inlet_area_m2_per_facade.items():
            a = area + (np.mean(extra_inlet_area_m2) if (extra_inlet_area_m2 is not None and f in cfg.strategies.facade.facades) else 0.0)
            if a <= 0:
                continue
            cp = af.swami_chandra_cp(ctx.wind_dir, az[f], side_ratio[f])
            links.append(af.Link(f"in_{f}", 0, 1, z=sc.inlet_height_m, area=a, k_loss=k_in,
                                 wind_i=af.wind_pressure(rho_o, u_inlet, cp)))
        links.append(af.Link("servers", 1, 2, z=b.height_m / 2, kind="fixed", m_fixed=m_srv))
        if sc.containment_leak_area_m2 > 0:
            links.append(af.Link("leak", 1, 2, z=b.height_m / 2, area=sc.containment_leak_area_m2,
                                 k_loss=1.0 / sc.discharge_coefficient**2))
        if chimney_fixed is None:
            links.append(af.Link("chimney", 2, 0, z=z_top, area=a_ch, k_loss=sc.chimney_loss_k + 1.0, wind_j=p_wind_top))
        else:
            links.append(af.Link("chimney", 2, 0, z=z_top, kind="fixed", m_fixed=chimney_fixed, wind_j=p_wind_top))
        return zones, links, rho_hot

    t_hot = t_hot0.copy()
    res = None
    for _ in range(3 if sc.solar_chimney else 1):
        zones, links, rho_hot = build(t_hot)
        res = af.solve_network(zones, links)
        if not sc.solar_chimney:
            break
        m_ch = np.maximum(res.flows["chimney"], 0.05 * m_srv + 1e-3)
        boost = sc.collector_efficiency * ctx.ghi * sc.collector_area_m2 * sc.n_chimneys / (m_ch * 1006.0)
        t_hot = t_hot0 + np.minimum(boost, 30.0)
    m_nat = res.flows["chimney"]
    leak = res.flows.get("leak", np.zeros(n))
    reversed_flow = m_nat < 0
    sufficient = (m_nat >= m_srv) & ~reversed_flow & (leak >= -1e-6)

    # Fan-assisted operation: chimney flow fixed at demand + 5 % bypass margin.
    m_req = 1.05 * m_srv
    zones, links, rho_hot = build(t_hot, chimney_fixed=m_req)
    res_f = af.solve_network(zones, links)
    dp_avail = res_f.dps["chimney"]
    v_ch = m_req / rho_hot / a_ch
    dp_needed = (sc.chimney_loss_k + 1.0) * 0.5 * rho_hot * v_ch**2
    fan_dp = np.maximum(dp_needed - dp_avail, 0.0)
    fan_power = np.where(sufficient, 0.0, (m_req / rho_hot) * fan_dp / sc.fan_efficiency)
    stack_dp = af.G * z_top * (rho_o - rho_hot)
    wind_dp = 0.5 * rho_o * u_inlet**2 * 0.6 - p_wind_top
    return NaturalVentResult(m_nat, m_srv, sufficient, reversed_flow, fan_dp, fan_power, t_hot, stack_dp, wind_dp, leak,
                             float(np.mean(res.converged)))


# ----------------------------------------------------------------------------------- EAHE
def soil_properties(e: EAHEConfig) -> earth_air.SoilProperties | None:
    if e.soil_type == "unknown":
        return None
    if e.soil_type == "custom" or (e.soil_k and e.soil_rho and e.soil_c):
        if not (e.soil_k and e.soil_rho and e.soil_c):
            return None
        return earth_air.SoilProperties(e.soil_k, e.soil_rho, e.soil_c, source="user")
    p = SOIL_TYPES[e.soil_type]
    return earth_air.SoilProperties(p["k"], p["rho"], p["c"], source=p["source"])


def eahe_geometry(e: EAHEConfig, spiral: bool = False, spiral_reduction: float = 0.0) -> earth_air.EAHEGeometry:
    bend_k = e.bend_k * (1 - spiral_reduction) if spiral else e.bend_k
    return earth_air.EAHEGeometry(
        n_pipes=e.n_pipes, length_m=e.length_m, diameter_m=e.diameter_m, wall_thickness_m=e.wall_thickness_m, pipe_k=e.pipe_k,
        depth_m=e.depth_m, spacing_m=e.spacing_m, roughness_m=e.roughness_m, n_bends=e.n_bends, bend_k=bend_k,
        filter_dp_design_pa=e.filter_dp_design_pa, design_flow_m3s=e.design_flow_m3s, fan_efficiency=e.fan_efficiency,
    )


def burrow_wind_assist_pa(ctx: ClimateArrays, terrain: str, z_in: float = 0.5, z_out: float = 4.0,
                          cp_in: float = 0.6, cp_out: float = -0.5) -> np.ndarray:
    """Prairie-dog-burrow principle: wind-induced pressure between a low intake and a raised outlet mound.

    ΔP_w = ½ ρ (C_p,in v(z_in)² − C_p,out v(z_out)²); only the positive part helps the fans.
    """
    v_in = af.wind_speed_at_height(ctx.wind, z_in, terrain)
    v_out = af.wind_speed_at_height(ctx.wind, z_out, terrain)
    return np.maximum(0.5 * ctx.rho * (cp_in * v_in**2 - cp_out * v_out**2), 0.0)


def run_eahe(cfg: ProjectConfig, ctx: ClimateArrays, ground: GroundModel, flow_m3s: np.ndarray, cancel_cb=None):
    e = cfg.strategies.eahe
    soil = soil_properties(e)
    if soil is None:
        return None
    geom = eahe_geometry(e, cfg.strategies.stack.spiral_intakes, cfg.strategies.stack.spiral_loss_reduction)
    t_g = ground.temperature(e.depth_m, ctx.index)
    res = earth_air.simulate(geom, soil, t_g, ctx.t_db, ctx.w, ctx.p, flow_m3s, years=e.years,
                             substeps=cfg.simulation.eahe_substeps, cancel_cb=cancel_cb)
    assist = burrow_wind_assist_pa(ctx, cfg.site.terrain)
    dp_eff = np.maximum(res.dp_pa - assist, 0.0)
    res.fan_power_w = np.where(flow_m3s > 0, flow_m3s * dp_eff / geom.fan_efficiency, 0.0)
    # Delivered-air temperature must carry the fan heat of the (wind-assisted) fan power actually drawn.
    m_cp = ps.moist_air_density(ctx.t_db, ctx.w, ctx.p) * flow_m3s * ps.humid_specific_heat(ctx.w)
    res.t_out_c = np.where(flow_m3s > 0, res.t_out_no_fan_c + geom.fan_heat_to_air * res.fan_power_w / np.maximum(m_cp, 1e-9),
                           ctx.t_db)
    res.diagnostics["wind_assist_mean_pa"] = float(np.mean(assist[flow_m3s > 0])) if np.any(flow_m3s > 0) else 0.0
    res.diagnostics["t_ground_c"] = t_g
    res.diagnostics["soil"] = {"k": soil.k, "rho": soil.rho, "c": soil.c, "alpha": soil.alpha, "source": soil.source}
    res.diagnostics["geometry"] = geom.to_dict()
    res.diagnostics["footprint_m2"] = geom.footprint_m2
    return res
