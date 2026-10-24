"""Transient earth-air heat exchanger (EAHE) — "prairie-dog burrow" strategy.

Reduced-order model
-------------------
N identical parallel pipes (inner diameter D, length L, centreline depth z, spacing w).
One representative pipe is divided into ``n_seg`` axial segments. For each segment:

* Air side: exponential approach to the pipe-wall temperature,
      T_out,j = T_w,j + (T_in,j − T_w,j) e^{−NTU_j},  NTU_j = UA_j / (ṁ c_p),
  with UA_j combining the convective film (Gnielinski / laminar Nu = 3.66), the pipe wall
  and the soil half-cell resistance to the first soil node.
* Soil side: implicit radial finite differences in the *disturbance* temperature
  θ = T − T_g(z, t) on a log-spaced grid from the pipe's outer radius to R. The outer node
  is coupled to θ = 0 through a conductance chosen so that the steady-state resistance of
  the whole path equals the conduction shape factor of a row of buried cylinders below an
  isothermal surface (Incropera, Table 4.1):
      S/L = 2π / ln[(2w/(π D_o)) sinh(2π z / w)]   (row, spacing w)
      S/L = 2π / cosh⁻¹(2 z / D_o)                   (single pipe)
  Thus the short-term transient (near-pipe heat storage, saturation) is resolved by the
  radial grid while the long-term limit includes neighbour-pipe interference and coupling to
  the ground surface. The undisturbed ground temperature T_g(z,t) comes from
  ``ground.GroundModel`` (or measured data). Linear conduction allows this superposition.
* Moisture: if the wall is below the air dew point, condensation is computed with the
  heat/mass-transfer analogy (Lewis number ≈ 1, same NTU), latent heat is released to the
  soil and condensate volume is reported (drainage, mould and biofilm risk).
* Hydraulics: Darcy–Weisbach (Haaland) friction + entry/exit/bend losses + filter; fan
  power = V̇ Δp / η_fan, and fan heat is added to the delivered air.

Energy is conserved exactly between air and soil by construction: the air temperature drop
is computed from the same segment heat flows that are injected into the soil nodes.

Limitations: homogeneous, constant soil properties; no soil moisture migration, freezing,
groundwater flow or surface-cover effects; no axial conduction in soil; identical pipes
(no header maldistribution); pipe-wall capacity neglected. The model does **not** replace
site ground characterisation (thermal response test, groundwater survey) or detailed
numerical modelling where the decision depends on exact performance.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from app.core.constants import K_AIR, MU_AIR, PR_AIR
from app.core.errors import InsufficientData
from app.physics import psychrometrics as ps
from app.physics.airflow import haaland_friction_factor
from app.physics.ground import damping_depth


@dataclass
class EAHEGeometry:
    n_pipes: int = 40
    length_m: float = 60.0
    diameter_m: float = 0.40          # inner diameter
    wall_thickness_m: float = 0.02
    pipe_k: float = 0.40              # W/(m·K), HDPE ≈ 0.4; PVC ≈ 0.19; concrete ≈ 1.4
    depth_m: float = 3.0              # centreline depth
    spacing_m: float = 2.0            # centre-to-centre spacing of parallel pipes
    roughness_m: float = 5e-5
    n_bends: int = 2
    bend_k: float = 0.30              # smooth / spiral bend ≈ 0.2–0.3; sharp mitred 90° ≈ 1.0–1.2
    entry_k: float = 0.5
    exit_k: float = 1.0
    filter_dp_design_pa: float = 120.0  # average (clean→dirty) filter drop at design flow
    filter_exponent: float = 1.4
    design_flow_m3s: float = 40.0     # total design airflow through all pipes
    fan_efficiency: float = 0.60      # total fan + motor + drive efficiency
    fan_heat_to_air: float = 1.0      # fraction of fan power appearing as heat in the delivered air

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def footprint_m2(self) -> float:
        """Approximate plan area of the pipe field (pipes laid side by side)."""
        return self.n_pipes * self.spacing_m * self.length_m

    @property
    def outer_radius_m(self) -> float:
        return self.diameter_m / 2.0 + self.wall_thickness_m


@dataclass
class SoilProperties:
    k: float
    rho: float
    c: float
    source: str = "assumption"

    @property
    def alpha(self) -> float:
        return self.k / (self.rho * self.c)


@dataclass
class EAHEResult:
    t_out_c: np.ndarray            # delivered air temperature incl. fan heat
    w_out: np.ndarray
    q_sensible_w: np.ndarray       # heat removed from air (+ = air cooled), all pipes
    q_latent_w: np.ndarray         # latent heat removed by condensation, all pipes
    condensate_kg: np.ndarray      # per hour, all pipes
    t_wall_min_c: np.ndarray
    soil_drift_k: np.ndarray       # mean wall-node disturbance θ (K) per hour
    fan_power_w: np.ndarray
    dp_pa: np.ndarray
    velocity_ms: np.ndarray
    yearly: list[dict] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)
    t_out_no_fan_c: np.ndarray | None = None


def gnielinski_h(re: np.ndarray, diameter_m: float, rel_rough: float) -> np.ndarray:
    """Internal convection coefficient (W/m²K): Gnielinski (Re ≥ 3000), laminar Nu = 3.66, blended between."""
    re = np.asarray(re, dtype=float)
    f = haaland_friction_factor(np.maximum(re, 3000.0), rel_rough)
    nu_t = (f / 8.0) * (np.maximum(re, 3000.0) - 1000.0) * PR_AIR / (1.0 + 12.7 * np.sqrt(f / 8.0) * (PR_AIR ** (2 / 3) - 1.0))
    nu_l = 3.66
    blend = np.clip((re - 2300.0) / 700.0, 0.0, 1.0)
    nu = np.where(re >= 3000.0, nu_t, nu_l + blend * (nu_t - nu_l))
    return nu * K_AIR / diameter_m


def shape_factor_per_length(depth_m: float, outer_diameter_m: float, spacing_m: float, n_pipes: int) -> float:
    """Conduction shape factor per unit length S/L (dimensionless) — Incropera Table 4.1."""
    if n_pipes <= 1 or spacing_m <= 0:
        return 2.0 * np.pi / np.arccosh(max(2.0 * depth_m / outer_diameter_m, 1.0001))
    arg = (2.0 * spacing_m / (np.pi * outer_diameter_m)) * np.sinh(2.0 * np.pi * depth_m / spacing_m)
    return 2.0 * np.pi / np.log(max(arg, 1.0001))


def hydraulics(geom: EAHEGeometry, flow_total_m3s, rho=1.2):
    """Pressure drop (Pa), per-pipe velocity (m/s) and fan power (W) for the total flow."""
    flow = np.asarray(flow_total_m3s, dtype=float)
    area = np.pi * geom.diameter_m**2 / 4.0
    v = flow / geom.n_pipes / area
    re = rho * np.abs(v) * geom.diameter_m / MU_AIR
    f = haaland_friction_factor(np.maximum(re, 1.0), geom.roughness_m / geom.diameter_m)
    k_minor = geom.entry_k + geom.exit_k + geom.n_bends * geom.bend_k
    dp_duct = (f * geom.length_m / geom.diameter_m + k_minor) * 0.5 * rho * v**2
    ratio = np.abs(flow) / max(geom.design_flow_m3s, 1e-9)
    dp_filter = geom.filter_dp_design_pa * ratio**geom.filter_exponent
    dp = np.where(flow > 0, dp_duct + dp_filter, 0.0)
    fan = np.abs(flow) * dp / geom.fan_efficiency
    return dp, v, fan, f, re


def simulate(geom: EAHEGeometry, soil: SoilProperties, t_ground_c: np.ndarray, t_in_c: np.ndarray, w_in: np.ndarray,
             p_pa: np.ndarray, flow_m3s: np.ndarray, *, years: int = 1, n_seg: int = 10, n_rad: int = 8,
             substeps: int = 4, h_override: float | None = None, initial_theta: np.ndarray | None = None,
             cancel_cb=None) -> EAHEResult:
    """Run the EAHE for ``years`` repetitions of the supplied hourly boundary conditions.

    Hourly arrays (length T): undisturbed ground temperature at pipe depth, inlet dry bulb,
    inlet humidity ratio, pressure and total airflow (m³/s, 0 = off). Results are for the last year;
    ``yearly`` holds per-year summaries to show soil saturation / degradation across years.
    """
    for name, val in (("soil k", soil.k), ("soil rho", soil.rho), ("soil c", soil.c)):
        if val is None or not np.isfinite(val) or val <= 0:
            raise InsufficientData(f"Earth-air heat exchanger needs a positive {name}; none supplied")
    if geom.n_pipes < 1 or geom.length_m <= 0 or geom.diameter_m <= 0 or geom.depth_m <= geom.outer_radius_m:
        raise InsufficientData("Earth-air heat exchanger geometry is incomplete or inconsistent")

    n_t = len(t_in_c)
    dt = 3600.0 / substeps
    dx = geom.length_m / n_seg
    r_i = geom.diameter_m / 2.0
    r_o = geom.outer_radius_m
    k_s = soil.k

    # Radial domain and far-field coupling.
    s_per_l = shape_factor_per_length(geom.depth_m, 2 * r_o, geom.spacing_m, geom.n_pipes)
    r_steady = 1.0 / (k_s * s_per_l)  # K·m/W, pipe outer surface → disturbance-free boundary
    delta = damping_depth(soil.alpha)
    r_max = min(0.48 * geom.spacing_m if geom.n_pipes > 1 else np.inf, 0.95 * geom.depth_m, max(5.0 * r_o, delta))
    # keep the radial resistance below 80 % of the steady-state resistance
    r_cap = r_o * np.exp(0.8 * 2.0 * np.pi * k_s * r_steady)
    r_far = max(min(r_max, r_cap), 1.5 * r_o)
    r_f = r_o * (r_far / r_o) ** (np.arange(n_rad + 1) / n_rad)
    r_c = np.sqrt(r_f[:-1] * r_f[1:])
    cap = soil.rho * soil.c * np.pi * (r_f[1:] ** 2 - r_f[:-1] ** 2) * dx           # J/K per node per segment
    g_int = 2.0 * np.pi * k_s * dx / np.log(r_c[1:] / r_c[:-1])                    # W/K between nodes
    r_rad_last = np.log(r_c[-1] / r_o) / (2.0 * np.pi * k_s)
    g_ext = dx / max(r_steady - r_rad_last, 1e-3)                                   # W/K to θ = 0
    r_half = np.log(r_c[0] / r_o) / (2.0 * np.pi * k_s * dx)
    r_wall = np.log(r_o / r_i) / (2.0 * np.pi * geom.pipe_k * dx)

    rho_in = ps.moist_air_density(t_in_c, w_in, p_pa)
    cp_in = ps.humid_specific_heat(w_in)
    flow = np.maximum(np.asarray(flow_m3s, dtype=float), 0.0)
    m_pipe = rho_in * flow / geom.n_pipes                                               # kg/s per pipe (dry ≈ moist)
    area = np.pi * r_i**2
    vel = flow / geom.n_pipes / area
    re = rho_in * vel * geom.diameter_m / MU_AIR
    h_i = np.full(n_t, h_override) if h_override is not None else gnielinski_h(re, geom.diameter_m, geom.roughness_m / geom.diameter_m)
    ua = np.where(m_pipe > 0, 1.0 / (1.0 / np.maximum(h_i * 2 * np.pi * r_i * dx, 1e-9) + r_wall + r_half), 0.0)
    ntu = np.where(m_pipe > 0, ua / np.maximum(m_pipe * cp_in, 1e-12), 0.0)
    eps = 1.0 - np.exp(-ntu)
    g_air = m_pipe * cp_in * eps                                                        # W/K, inlet air → wall node

    # Base implicit matrix (without g_air) for one segment.
    base = np.diag(cap / dt)
    for k in range(n_rad - 1):
        base[k, k] += g_int[k]
        base[k + 1, k + 1] += g_int[k]
        base[k, k + 1] -= g_int[k]
        base[k + 1, k] -= g_int[k]
    base[-1, -1] += g_ext
    inv_cache: dict[float, np.ndarray] = {}

    def inverse_for(g: float) -> np.ndarray:
        key = round(float(g), 6)
        inv = inv_cache.get(key)
        if inv is None:
            m = base.copy()
            m[0, 0] += g
            inv = np.linalg.inv(m)
            if len(inv_cache) < 4096:
                inv_cache[key] = inv
        return inv

    theta = np.zeros((n_seg, n_rad)) if initial_theta is None else initial_theta.copy()
    latent_carry = np.zeros(n_seg)
    dp, vel_all, fan, _, _ = hydraulics(geom, flow, rho=float(np.nanmean(rho_in)) if n_t else 1.2)

    # Inlet dew point, used to skip the moisture calculation when condensation is impossible.
    t_dp_in = ps.dew_point_from_humidity_ratio(np.maximum(w_in, 1e-7), p_pa)
    cap_dt = cap / dt
    one_minus = 1.0 - eps
    t_in_l = np.asarray(t_in_c, dtype=float).tolist()
    w_in_l = np.asarray(w_in, dtype=float).tolist()
    tg_l = np.asarray(t_ground_c, dtype=float).tolist()

    yearly = []
    out = {}
    for year in range(years):
        t_out = np.empty(n_t)
        w_out = np.empty(n_t)
        q_s = np.zeros(n_t)
        q_l = np.zeros(n_t)
        cond = np.zeros(n_t)
        twall_min = np.empty(n_t)
        drift = np.empty(n_t)
        for t in range(n_t):
            if cancel_cb is not None and t % 1000 == 0:
                cancel_cb()
            tg = tg_l[t]
            g = float(g_air[t])
            if g <= 0.0:
                # No flow: soil relaxes towards undisturbed conditions.
                rhs = theta * cap_dt[None, :] + 0.0
                rhs[:, 0] += latent_carry
                inv = inverse_for(0.0)
                for _ in range(substeps):
                    theta = rhs @ inv.T
                    rhs = theta * cap_dt[None, :]
                latent_carry = np.zeros(n_seg)
                t_out[t] = t_in_l[t]
                w_out[t] = w_in_l[t]
                th0 = theta[:, 0]
                twall_min[t] = tg + float(th0.min())
                drift[t] = float(th0.mean())
                continue
            inv = inverse_for(g)
            e = float(eps[t])
            om = float(one_minus[t])
            mcp = float(m_pipe[t] * cp_in[t])
            mp = float(m_pipe[t])
            acc_ts = acc_ws = acc_q = acc_ql = acc_c = 0.0
            for _ in range(substeps):
                # Air chain with current wall temperatures → segment inlet temperatures.
                tw = (tg + theta[:, 0]).tolist()
                t_seg_in = [0.0] * n_seg
                ta, wa = t_in_l[t], w_in_l[t]
                for j in range(n_seg):
                    t_seg_in[j] = ta
                    ta = om * ta + e * tw[j]
                cond_seg = None
                if t_dp_in[t] > min(tw) - 0.5:
                    wsat = ps.saturation_humidity_ratio(np.asarray(tw), p_pa[t]).tolist()
                    cond_seg = np.zeros(n_seg)
                    for j in range(n_seg):
                        if wa > wsat[j]:
                            w_new = wsat[j] + (wa - wsat[j]) * om
                            cond_seg[j] = mp * (wa - w_new)
                            wa = w_new
                # Implicit soil update for all segments at once.
                rhs = theta * cap_dt[None, :]
                rhs[:, 0] += g * (np.asarray(t_seg_in) - tg) + latent_carry
                theta = rhs @ inv.T
                q_tot = float(g * (sum(t_seg_in) - n_seg * tg) - g * theta[:, 0].sum())  # W, + = into soil
                if cond_seg is not None:
                    latent_carry = cond_seg * 2.45e6
                    acc_ql += float(latent_carry.sum())
                    acc_c += float(cond_seg.sum()) * dt
                else:
                    latent_carry = np.zeros(n_seg)
                acc_ts += t_in_l[t] - q_tot / mcp
                acc_ws += wa
                acc_q += q_tot
            n = substeps
            t_out[t] = acc_ts / n
            w_out[t] = acc_ws / n
            q_s[t] = acc_q / n * geom.n_pipes
            q_l[t] = acc_ql / n * geom.n_pipes
            cond[t] = acc_c * geom.n_pipes
            th0 = theta[:, 0]
            twall_min[t] = tg + float(th0.min())
            drift[t] = float(th0.mean())
        on = flow > 0
        yearly.append({
            "year": year + 1,
            "sensible_cooling_kwh": float(np.sum(np.clip(q_s, 0, None)) / 1000.0),
            "sensible_heating_kwh": float(np.sum(np.clip(-q_s, 0, None)) / 1000.0),
            "latent_kwh": float(np.sum(q_l) / 1000.0),
            "condensate_m3": float(np.sum(cond) / 1000.0),
            "mean_drift_k": float(np.mean(drift)),
            "max_drift_k": float(np.max(np.abs(drift))) if n_t else 0.0,
            "mean_dT_when_on_k": float(np.mean((t_in_c - t_out)[on])) if on.any() else 0.0,
            "monthly_drift_k": None,
        })
        out = dict(t_out=t_out, w_out=w_out, q_s=q_s, q_l=q_l, cond=cond, twall_min=twall_min, drift=drift)

    # Fan heat added downstream of the exchanger.
    fan_dt = np.where(flow > 0, geom.fan_heat_to_air * fan / np.maximum(rho_in * flow * cp_in, 1e-9), 0.0)
    t_delivered = np.where(flow > 0, out["t_out"] + fan_dt, t_in_c)
    return EAHEResult(
        t_out_c=t_delivered, w_out=out["w_out"], q_sensible_w=out["q_s"], q_latent_w=out["q_l"],
        condensate_kg=out["cond"], t_wall_min_c=out["twall_min"], soil_drift_k=out["drift"],
        fan_power_w=fan, dp_pa=dp, velocity_ms=vel_all, yearly=yearly, t_out_no_fan_c=np.where(flow > 0, out["t_out"], t_in_c),
        diagnostics={
            "r_far_m": float(r_far), "shape_factor_per_m": float(s_per_l), "r_steady_mK_per_W": float(r_steady),
            "damping_depth_m": float(delta), "n_seg": n_seg, "n_rad": n_rad, "substeps": substeps,
            "h_mean_w_m2k": float(np.mean(h_i[flow > 0])) if np.any(flow > 0) else None,
            "ntu_mean": float(np.mean(ntu[flow > 0]) * n_seg) if np.any(flow > 0) else None,
            "fan_dT_mean_k": float(np.mean(fan_dt[flow > 0])) if np.any(flow > 0) else 0.0,
            "final_theta": theta,
        },
    )
