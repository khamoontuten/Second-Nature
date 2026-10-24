"""Interactive engineering calculators (synchronous, fast). Each returns intermediate values, not only results."""

from __future__ import annotations

import numpy as np
from fastapi import APIRouter

from app.core.constants import G
from app.core.errors import ValidationFailure
from app.physics import airflow as af
from app.physics import earth_air as ea
from app.physics import envelope as env
from app.physics import psychrometrics as ps
from app.physics.ground import GroundModel, damping_depth, diffusivity
from app.schemas.api import (
    AirflowReqRequest,
    EAHECalcRequest,
    EnvelopeCalcRequest,
    PsychroRequest,
    StackCalcRequest,
)
from app.services.climate import _jsonable

router = APIRouter(prefix="/api/calculators", tags=["calculators"])


@router.post("/psychrometrics")
def psychrometrics(body: PsychroRequest):
    p = body.p_pa or float(ps.pressure_at_elevation(body.elevation_m))
    if body.rh_pct is None and body.t_dp_c is None:
        raise ValidationFailure("give rh_pct or t_dp_c")
    if body.t_dp_c is not None and body.t_dp_c > body.t_db_c + 1e-9:
        raise ValidationFailure("dew point cannot exceed dry bulb")
    st = ps.state(body.t_db_c, p, rh_pct=body.rh_pct, t_dp_c=body.t_dp_c if body.rh_pct is None else None)
    out = {k: float(np.squeeze(v)) for k, v in st.items()}
    out.update({"p_pa": p, "p_ws_pa": float(ps.saturation_pressure(body.t_db_c)),
                "p_w_pa": float(ps.vapour_pressure_from_humidity_ratio(out["w"], p)),
                "cp_humid_j_kgk": float(ps.humid_specific_heat(out["w"])),
                "equations": {"W": "0.621945 p_w/(p − p_w)", "h": "1006 t + W(2 501 000 + 1860 t) [J/kg_da]",
                              "v": "287.042 (t + 273.15)(1 + 1.607858 W)/p", "source": "ASHRAE Fundamentals 2017 Ch.1"}})
    return out


@router.post("/stack")
def stack(body: StackCalcRequest):
    """Two-opening stack + wind network for each chimney height (independent check of the hall network)."""
    p = float(ps.pressure_at_elevation(body.elevation_m))
    rho_o = p / (287.042 * (body.t_outside_c + 273.15))
    rho_i = p / (287.042 * (body.t_inside_c + 273.15))
    k = 1.0 / body.discharge_coefficient**2 + body.extra_loss_k
    rows = []
    for h in body.heights_m:
        zones = [af.Zone("out", np.array([body.t_outside_c]), np.array([rho_o]), boundary=True),
                 af.Zone("in", np.array([body.t_inside_c]), np.array([rho_i]))]
        u_in = float(af.wind_speed_at_height(body.wind_ms, 3.0))
        u_out = float(af.wind_speed_at_height(body.wind_ms, 3.0 + h))
        links = [af.Link("inlet", 0, 1, z=1.0, area=body.inlet_area_m2, k_loss=k, wind_i=0.5 * rho_o * u_in**2 * body.cp_inlet),
                 af.Link("outlet", 1, 0, z=1.0 + h, area=body.outlet_area_m2, k_loss=k, wind_j=0.5 * rho_o * u_out**2 * body.cp_outlet)]
        r = af.solve_network(zones, links)
        m = float(r.flows["inlet"][0])
        v = m / rho_o
        dp_stack = G * h * (rho_o - rho_i)
        dp_wind = 0.5 * rho_o * (u_in**2 * body.cp_inlet - u_out**2 * body.cp_outlet)
        a_eff = 1 / np.sqrt(1 / body.inlet_area_m2**2 + 1 / body.outlet_area_m2**2)
        rows.append({"height_m": h, "stack_dp_pa": dp_stack, "stack_dp_approx_pa": float(af.stack_pressure(body.t_inside_c, body.t_outside_c, h)),
                     "wind_dp_pa": dp_wind, "mass_flow_kg_s": m, "volume_flow_m3s": v, "effective_area_m2": a_eff,
                     "sufficient": None if body.required_flow_m3s is None else bool(v >= body.required_flow_m3s),
                     "reversed": m < 0, "heat_removed_kw": m * 1006 * (body.t_inside_c - body.t_outside_c) / 1000})
    return {"rows": rows, "rho_outside": rho_o, "rho_inside": rho_i, "loss_coefficient_k": k,
            "equations": {"stack": "Δp = g H (ρ_o − ρ_i) ≈ ρ_o g H (T_i − T_o)/T_i", "opening": "ṁ = A √(2ρ|Δp|/K), K = 1/C_d² + ΣK",
                          "wind": "p_w = ½ ρ U_H² C_p"},
            "warning": "A single stack calculation is not evidence that a data hall can be naturally ventilated; see the hall network results."}


@router.post("/earth-air")
def earth_air(body: EAHECalcRequest):
    soil = ea.SoilProperties(body.soil_k, body.soil_rho, body.soil_c, "calculator input")

    def run(length=body.length_m, diameter=body.diameter_m, flow=body.flow_m3s, depth=body.depth_m, hours=body.hours):
        geom = ea.EAHEGeometry(n_pipes=body.n_pipes, length_m=length, diameter_m=diameter, depth_m=depth, spacing_m=body.spacing_m,
                               design_flow_m3s=flow, bend_k=body.bend_k)
        n = hours
        p = np.full(n, 101325.0)
        t_in = np.full(n, body.t_in_c)
        w = ps.humidity_ratio_from_rh(t_in, body.rh_pct, p)
        r = ea.simulate(geom, soil, np.full(n, body.t_ground_c), t_in, w, p, np.full(n, flow), substeps=2, n_seg=8, n_rad=6)
        return geom, r

    geom, r = run()
    idx = np.unique(np.linspace(0, body.hours - 1, min(body.hours, 120)).astype(int))
    rho = float(ps.moist_air_density(body.t_in_c, ps.humidity_ratio_from_rh(body.t_in_c, body.rh_pct, 101325.0), 101325.0))
    out = {
        "series": {"hour": idx.tolist(), "t_out_c": r.t_out_c[idx].tolist(), "q_sensible_kw": (r.q_sensible_w[idx] / 1000).tolist(),
                   "soil_drift_k": r.soil_drift_k[idx].tolist()},
        "first_hour": {"t_out_c": float(r.t_out_c[0]), "q_kw": float(r.q_sensible_w[0] / 1000)},
        "last_hour": {"t_out_c": float(r.t_out_c[-1]), "q_kw": float(r.q_sensible_w[-1] / 1000)},
        "pressure_drop_pa": float(r.dp_pa[0]), "velocity_ms": float(r.velocity_ms[0]), "fan_power_kw": float(r.fan_power_w[0] / 1000),
        "net_cooling_minus_fan_kw_last": float((r.q_sensible_w[-1] - r.fan_power_w[-1]) / 1000),
        "condensate_kg_total": float(r.condensate_kg.sum()), "footprint_m2": geom.footprint_m2,
        "mass_flow_kg_s": rho * body.flow_m3s, "diagnostics": {k: v for k, v in r.diagnostics.items() if k != "final_theta"},
        "equations": {"segment": "T_out = T_w + (T_in − T_w) e^{−UA/(ṁ c_p)}", "soil": "implicit radial FD, shape factor S/L far field",
                      "fan": "P = V̇ Δp / η"},
    }
    if body.sweep != "none":
        base = {"length": body.length_m, "diameter": body.diameter_m, "flow": body.flow_m3s, "depth": body.depth_m}[body.sweep]
        factors = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0]
        sweep = []
        for f in factors:
            kw = {body.sweep: base * f}
            if body.sweep == "depth" and base * f <= body.diameter_m:
                continue
            g, rr = run(hours=min(body.hours, 240), **kw)
            sweep.append({body.sweep: base * f, "q_last_kw": float(rr.q_sensible_w[-1] / 1000), "t_out_last_c": float(rr.t_out_c[-1]),
                          "fan_kw": float(rr.fan_power_w[0] / 1000), "dp_pa": float(rr.dp_pa[0]),
                          "net_kw": float((rr.q_sensible_w[-1] - rr.fan_power_w[-1]) / 1000)})
        out["sweep"] = {"variable": body.sweep, "rows": sweep}
    return _jsonable(out)


@router.post("/envelope")
def envelope(body: EnvelopeCalcRequest):
    layers = []
    for spec in body.layers:
        if spec.material:
            if spec.material not in env.MATERIALS:
                raise ValidationFailure(f"unknown material {spec.material}; known: {sorted(env.MATERIALS)}")
            lay = env.layer(spec.material, spec.thickness_m)
        else:
            if None in (spec.k, spec.rho, spec.c):
                raise ValidationFailure("custom layers need k, rho and c")
            lay = env.Layer("custom", spec.thickness_m, spec.k, spec.rho, spec.c)
        layers.append(lay)
    con = env.Construction("calc", layers, body.solar_absorptance, r_si=body.r_si, r_se=body.r_se)
    pr = env.periodic_response(con)
    return {**pr, "r_total_m2k_w": body.r_si + con.r_layers + body.r_se, "areal_heat_capacity_j_m2k": con.areal_heat_capacity,
            "layers": [{"name": x.name, "thickness_m": x.thickness_m, "k": x.k, "rho": x.rho, "c": x.c, "r": x.thickness_m / x.k} for x in layers],
            "materials": env.MATERIALS,
            "equations": {"U": "1/(R_si + Σd/k + R_se)", "periodic": "transmission matrix, Y = 1/M₁₂, f = |Y|/U, lag = −arg(Y)/ω"}}


@router.post("/airflow-requirement")
def airflow_requirement(body: AirflowReqRequest):
    p = float(ps.pressure_at_elevation(body.elevation_m))
    w = 0.009
    m = af.required_mass_flow(body.it_load_kw * 1000, body.delta_t_k, w)
    v = m * float(ps.specific_volume(body.t_supply_c, w, p))
    per_rack = v / body.rack_count
    out = {"mass_flow_kg_s": float(m), "volume_flow_m3s": float(v), "per_rack_m3s": per_rack, "per_rack_cfm": per_rack * 2118.88,
           "equation": "Q_s = ṁ c_p ΔT → ṁ = Q/(c_p ΔT), c_p = 1006 + 1860 W"}
    if body.max_rack_kw:
        mr = af.required_mass_flow(body.max_rack_kw * 1000, body.delta_t_k, w)
        out["peak_rack_m3s"] = float(mr * ps.specific_volume(body.t_supply_c, w, p))
    return out


@router.post("/ground")
def ground(t_mean_c: float = 15.0, amplitude_k: float = 10.0, t_min_day: float = 20.0, soil_k: float = 1.3, soil_rho: float = 1460.0,
           soil_c: float = 880.0, depths: str = "0.5,1,2,3,4,6"):
    import pandas as pd

    alpha = diffusivity(soil_k, soil_rho, soil_c)
    gm = GroundModel(t_mean_c, amplitude_k, t_min_day * 86400, alpha, "calculator")
    idx = pd.date_range("2001-01-01", periods=365, freq="D", tz="UTC")
    zs = [float(z) for z in depths.split(",")]
    return {"alpha_m2s": alpha, "damping_depth_m": damping_depth(alpha), "day": list(range(1, 366)),
            "profiles": {str(z): gm.temperature(z, idx).round(3).tolist() for z in zs},
            "equation": "T(z,t) = T_m − A_s e^{−z/δ} cos(ω(t − t₀) − z/δ), δ = √(2α/ω)"}
