"""Building envelope loads for the simulation (strategies B, C and the cool-roof part of G).

This module is the *single* place where roof/wall solar absorption, shading and envelope
conduction are computed, so a cool roof, a layered shell and facade shading interact through
one heat balance and cannot be double-counted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.physics import envelope as env
from app.physics import solar
from app.physics.ground import GroundModel
from app.schemas.config import ProjectConfig
from app.simulation.context import ClimateArrays

FACADE_OFFSETS = {"N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0}


@dataclass
class EnvelopeLoads:
    q_opaque_w: np.ndarray
    q_glazing_w: np.ndarray
    q_total_w: np.ndarray
    per_surface_w: dict[str, np.ndarray]
    absorbed_solar_w: dict[str, np.ndarray]
    actuator_w: np.ndarray
    facade_state: dict[str, np.ndarray]
    constructions: dict[str, dict]
    surface_temps_c: dict[str, np.ndarray]
    stored_change_j: float
    exterior_input_j: float
    description: dict = field(default_factory=dict)


def facade_azimuths(orientation_deg: float) -> dict[str, float]:
    return {k: (orientation_deg + v) % 360.0 for k, v in FACADE_OFFSETS.items()}


def compute_envelope(cfg: ProjectConfig, ctx: ClimateArrays, ground: GroundModel | None) -> EnvelopeLoads:
    b = cfg.building
    st = cfg.strategies
    if st.envelope.enabled:
        cons = env.shell_constructions(st.envelope.insulation_m, st.envelope.thermal_mass_m,
                                       st.envelope.roof_solar_reflectance, st.envelope.roof_emissivity,
                                       st.envelope.wall_solar_absorptance)
    else:
        cons = env.standard_constructions(b.insulation_m, 1.0 - b.roof_solar_reflectance, b.roof_emissivity,
                                          b.wall_solar_absorptance, b.thermal_mass_m)
    for c in cons.values():
        c.thermal_bridge_factor = b.thermal_bridge_factor
    cons["slab"].thermal_bridge_factor = 1.0

    az = facade_azimuths(b.orientation_deg)
    wall_area = {"N": b.length_m * b.height_m, "S": b.length_m * b.height_m,
                 "E": b.width_m * b.height_m, "W": b.width_m * b.height_m}
    total_wall = sum(wall_area.values())
    glazing_share = {k: b.window_area_m2 * a / total_wall for k, a in wall_area.items()}

    surfaces = [env.Surface("roof", cons["roof"], b.length_m * b.width_m, 0.0, 0.0)]
    for k in ("N", "E", "S", "W"):
        surfaces.append(env.Surface(f"wall_{k}", cons["wall"], max(wall_area[k] - glazing_share[k], 0.0), 90.0, az[k]))
    surfaces.append(env.Surface("slab", cons["slab"], b.length_m * b.width_m, 180.0, 0.0, exterior="ground"))

    incident: dict[str, np.ndarray] = {}
    roof = solar.surface_irradiance(ctx.dni, ctx.dhi, ctx.ghi, ctx.zenith, ctx.azimuth, 0.0, 0.0)
    incident["roof"] = roof["total"]
    incident["slab"] = np.zeros(ctx.n)
    actuator = np.zeros(ctx.n)
    states: dict[str, np.ndarray] = {}
    glazing_solar = np.zeros(ctx.n)
    fc = st.facade
    fixed = solar.FixedShading(overhang_pf=fc.overhang_pf, fin_pf=fc.fin_pf, diffuse_reduction=0.0)
    kin = solar.KineticFacade(close_beam_threshold_wm2=fc.close_beam_threshold_wm2, beam_block_closed=fc.beam_block_closed,
                              gust_lock_ms=fc.gust_lock_ms, standby_w_per_panel=fc.standby_w_per_panel,
                              panel_area_m2=fc.panel_area_m2, stuck_fraction=fc.stuck_fraction)
    # Doc control rule: open (ventilate) when outdoor air is ≥ 2 K below the inlet setpoint and humidity is in range.
    ventilate_ok = (ctx.t_db <= cfg.it.inlet_setpoint_c - 2.0) & (ctx.t_dp <= 15.0)
    for k in ("N", "E", "S", "W"):
        irr = solar.surface_irradiance(ctx.dni, ctx.dhi, ctx.ghi, ctx.zenith, ctx.azimuth, 90.0, az[k])
        beam, diff = irr["beam"], irr["sky_diffuse"] + irr["ground"]
        if fc.enabled and k in fc.facades:
            if fc.mode == "fixed":
                sl = fixed.sunlit_fraction(ctx.zenith, ctx.azimuth, az[k])
                beam = beam * sl
                states[k] = np.zeros(ctx.n, dtype=int)
            else:
                s = kin.schedule(beam, ventilate_ok, ctx.wind)
                beam, diff = kin.transmitted(beam, diff, s)
                states[k] = s
                actuator += kin.actuator_power_w(s, wall_area[k])
        incident[f"wall_{k}"] = beam + diff
        glazing_solar += glazing_share[k] * b.window_shgc * (beam + diff)

    t_inside = cfg.it.inlet_setpoint_c + b.zone_air_offset_k
    t_ground = ground.temperature(1.0, ctx.index) if ground is not None else np.full(ctx.n, float(np.mean(ctx.t_db)))
    res = env.simulate(surfaces, t_air_c=ctx.t_db, t_sky_c=ctx.t_sky, wind_ms=ctx.wind, incident_wm2=incident,
                       t_inside_c=t_inside, t_ground_c=t_ground, substeps=cfg.simulation.envelope_substeps)
    q_glaz = glazing_solar + b.window_area_m2 * b.window_u * (ctx.t_db - t_inside)
    q_opaque = res.total_q_in_w
    return EnvelopeLoads(
        q_opaque_w=q_opaque, q_glazing_w=q_glaz, q_total_w=q_opaque + q_glaz, per_surface_w=res.q_in_w,
        absorbed_solar_w=res.absorbed_solar_w, actuator_w=actuator, facade_state=states,
        constructions={k: {**v.to_dict(), **env.periodic_response(v)} for k, v in cons.items()},
        surface_temps_c=res.t_surface_ext_c, stored_change_j=float(sum(res.stored_change_j.values())),
        exterior_input_j=float(sum(res.exterior_input_j.values())),
        description={"interior_temperature_c": t_inside, "glazing_area_m2": b.window_area_m2,
                     "facade_strategy": fc.mode if fc.enabled else "none",
                     "envelope_strategy": "layered shell" if st.envelope.enabled else "standard"},
    )
