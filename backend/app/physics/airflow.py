"""Buoyancy- and wind-driven airflow: elements and a multizone airflow-network solver.

Model basis
-----------
* Opening / path flow (ASHRAE Fundamentals 2017 Ch.16 eq. 37; CONTAM orifice element):
  ṁ = C_d A √(2 ρ |Δp|) · sign(Δp). A series path of components with loss coefficients
  K_i referred to areas A_i is represented by one equivalent coefficient
  K_eq = Σ K_i (A_ref/A_i)² at reference area A_ref, so ṁ = A_ref √(2 ρ |Δp| / K_eq).
  (For a sharp-edged orifice K = 1/C_d².)
* Duct friction: Darcy–Weisbach with the Haaland (1983) explicit friction factor.
* Hydrostatics: the pressure on side *i* of a link at height z is P_i − ρ_i g (z − z_i), so
  stack (buoyancy) pressures arise from density differences between zones automatically.
  For two openings separated by H this reduces to Δp_stack = g H (ρ_o − ρ_i)
  ≈ ρ_o g H (T_i − T_o)/T_i (ideal gas, same humidity), the relationship in the brief.
* Wind pressure: p_w = ½ ρ_o U_H² C_p, with U_H from the ASHRAE Fundamentals 2017 Ch.24
  power-law profile and C_p(θ) from the Swami & Chandra (1988) low-rise wall correlation.
* Network solution: unknown zone pressures are solved per time step by Newton–Raphson on
  zone mass balances, vectorised over all time steps (batched linear solves).

Limitations
-----------
Zones are well mixed (no internal stratification or recirculation patterns).
The network cannot replace CFD for high-density halls: it predicts bulk flows, not
rack-level inlet temperatures. Discharge coefficients and C_p values are assumptions
unless measured or taken from wind-tunnel data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.core.constants import MU_AIR, G

# ASHRAE Fundamentals 2017 Ch.24 Table 1: atmospheric boundary layer parameters.
TERRAIN = {
    "large_city": (0.33, 460.0),
    "urban_suburban": (0.22, 370.0),
    "open": (0.14, 270.0),
    "flat_unobstructed": (0.10, 210.0),
}


def wind_speed_at_height(u_met, h_m: float, terrain: str = "urban_suburban", h_met: float = 10.0,
                         met_terrain: str = "open"):
    """Wind speed at height h (m/s) from a meteorological-station speed (ASHRAE 2017 Ch.24 eq. 4).

    U_H = U_met (δ_met / H_met)^a_met (H / δ)^a
    """
    a, delta = TERRAIN[terrain]
    a_met, d_met = TERRAIN[met_terrain]
    h = max(h_m, 1.0)
    return np.asarray(u_met, dtype=float) * (d_met / h_met) ** a_met * (h / delta) ** a


def swami_chandra_cp(wind_dir_deg, facade_azimuth_deg: float, side_ratio: float = 1.0, cp0: float = 0.6):
    """Surface-averaged wind pressure coefficient on a low-rise wall (Swami & Chandra, 1988).

    wind_dir_deg: meteorological direction the wind blows *from* (0 = N, 90 = E).
    facade_azimuth_deg: outward normal of the facade (0 = facing N).
    side_ratio: facade width / adjacent wall width (G = ln of this ratio).
    Cp(θ)/Cp(0) = ln[1.248 − 0.703 sin(θ/2) − 1.175 sin²θ + 0.131 sin³(2θG) + 0.769 cos(θ/2)
                      + 0.07 G² sin²(θ/2) + 0.717 cos²(θ/2)]
    """
    theta = np.deg2rad(np.abs(((np.asarray(wind_dir_deg, dtype=float) - facade_azimuth_deg) + 180.0) % 360.0 - 180.0))
    g = np.log(side_ratio)
    arg = (1.248 - 0.703 * np.sin(theta / 2) - 1.175 * np.sin(theta) ** 2 + 0.131 * np.sin(2 * theta * g) ** 3
           + 0.769 * np.cos(theta / 2) + 0.07 * g**2 * np.sin(theta / 2) ** 2 + 0.717 * np.cos(theta / 2) ** 2)
    return cp0 * np.log(arg)


def wind_pressure(rho_o, u_h, cp):
    """p_w = ½ ρ U_H² C_p (Pa)."""
    return 0.5 * np.asarray(rho_o) * np.asarray(u_h) ** 2 * np.asarray(cp)


def stack_pressure(t_inside_c, t_outside_c, height_m, rho_ref=None):
    """Approximate stack pressure (Pa): Δp ≈ ρ_o g H (T_i − T_o)/T_i (dry air, ideal gas).

    Positive when the inside is warmer (upward flow through a tall shaft: in at bottom, out at top).
    rho_ref defaults to dry-air density at T_o and 101 325 Pa.
    """
    ti = np.asarray(t_inside_c, dtype=float) + 273.15
    to = np.asarray(t_outside_c, dtype=float) + 273.15
    rho = 101325.0 / (287.042 * to) if rho_ref is None else rho_ref
    return rho * G * np.asarray(height_m, dtype=float) * (ti - to) / ti


def orifice_flow(dp, area_m2, cd: float = 0.6, rho: float = 1.2):
    """Volumetric flow (m³/s) through an opening: V̇ = C_d A √(2|Δp|/ρ), signed with Δp."""
    dp = np.asarray(dp, dtype=float)
    return np.sign(dp) * cd * np.asarray(area_m2) * np.sqrt(2.0 * np.abs(dp) / rho)


def haaland_friction_factor(re, rel_roughness):
    """Darcy friction factor (Haaland 1983); laminar 64/Re below Re = 2300."""
    re = np.maximum(np.asarray(re, dtype=float), 1e-9)
    turb = (-1.8 * np.log10((rel_roughness / 3.7) ** 1.11 + 6.9 / re)) ** -2
    return np.where(re < 2300.0, 64.0 / re, turb)


def duct_pressure_drop(v_dot, diameter_m, length_m, roughness_m=1.5e-5, k_minor=0.0, rho=1.2, mu=MU_AIR):
    """Pressure drop (Pa) of a circular duct: (f L/D + ΣK) ρ v²/2. Returns (dp, f, velocity, Re)."""
    area = np.pi * diameter_m**2 / 4.0
    vel = np.asarray(v_dot, dtype=float) / area
    re = rho * np.abs(vel) * diameter_m / mu
    f = haaland_friction_factor(re, roughness_m / diameter_m)
    dp = (f * length_m / diameter_m + k_minor) * 0.5 * rho * vel**2
    return dp, f, vel, re


def series_loss_coefficient(components: list[tuple[float, float]], area_ref: float) -> float:
    """Combine series components [(K_i, A_i), ...] into K_eq referred to area_ref."""
    return float(sum(k * (area_ref / a) ** 2 for k, a in components))


def required_mass_flow(q_sensible_w, delta_t_k, w=0.010):
    """Dry-air mass flow (kg/s) to remove a sensible load: ṁ = Q / (c_p ΔT), c_p = 1006 + 1860 W."""
    cp = 1006.0 + 1860.0 * w
    return np.asarray(q_sensible_w, dtype=float) / (cp * np.asarray(delta_t_k, dtype=float))


# --------------------------------------------------------------------------------------------
# Airflow network
# --------------------------------------------------------------------------------------------

@dataclass
class Zone:
    """A network node. Boundary zones (outdoors) have known pressure 0 at z_ref = 0."""

    name: str
    t_c: np.ndarray
    rho: np.ndarray
    boundary: bool = False
    z_ref: float = 0.0


@dataclass
class Link:
    """Flow element from zone i to zone j at height z (m).

    kind = "quadratic": ṁ = A √(2 ρ_up |Δp| / K) sign(Δp)   (orifice: K = 1/C_d²)
    kind = "fixed":     ṁ = m_fixed (kg/s, i→j), independent of Δp (e.g., server fans)
    wind_i / wind_j: additional (wind) pressure on the side of a boundary zone (Pa, per step).
    """

    name: str
    i: int
    j: int
    z: float
    kind: str = "quadratic"
    area: float = 0.0
    k_loss: float = 1.0
    m_fixed: np.ndarray | float = 0.0
    wind_i: np.ndarray | float = 0.0
    wind_j: np.ndarray | float = 0.0
    dp_lam: float = 0.01  # Pa; flow is linearised below this to keep Newton well conditioned


@dataclass
class NetworkResult:
    pressures: np.ndarray            # (T, Z) zone reference pressures, Pa (boundary zones = 0)
    flows: dict[str, np.ndarray]     # link name -> mass flow i→j, kg/s
    dps: dict[str, np.ndarray]       # link name -> pressure difference across link (side i − side j), Pa
    converged: np.ndarray            # (T,) bool
    iterations: int
    max_residual: float
    info: dict = field(default_factory=dict)


def _link_flow(link: Link, dp: np.ndarray, rho_i: np.ndarray, rho_j: np.ndarray):
    if link.kind == "fixed":
        m = np.broadcast_to(np.asarray(link.m_fixed, dtype=float), dp.shape).copy()
        return m, np.zeros_like(dp)
    rho_up = np.where(dp >= 0, rho_i, rho_j)
    c = link.area * np.sqrt(2.0 * rho_up / link.k_loss)
    adp = np.abs(dp)
    lam = adp < link.dp_lam
    sq = np.sqrt(np.maximum(adp, link.dp_lam))
    m = np.where(lam, c * dp / np.sqrt(link.dp_lam), c * np.sign(dp) * sq)
    dm = np.where(lam, c / np.sqrt(link.dp_lam), c / (2.0 * sq))
    return m, dm


def solve_network(zones: list[Zone], links: list[Link], max_iter: int = 60, tol_kg_s: float = 1e-6,
                  p_init: np.ndarray | None = None) -> NetworkResult:
    """Solve zone pressures so that mass is conserved in every internal zone at every time step."""
    n_t = len(zones[0].t_c)
    internal = [k for k, z in enumerate(zones) if not z.boundary]
    idx = {k: n for n, k in enumerate(internal)}
    n_u = len(internal)
    p = np.zeros((n_t, len(zones))) if p_init is None else p_init.copy()

    def side_pressures(link: Link):
        zi, zj = zones[link.i], zones[link.j]
        pi = p[:, link.i] - zi.rho * G * (link.z - zi.z_ref) + (link.wind_i if zi.boundary else 0.0)
        pj = p[:, link.j] - zj.rho * G * (link.z - zj.z_ref) + (link.wind_j if zj.boundary else 0.0)
        return pi, pj

    converged = np.zeros(n_t, dtype=bool)
    it = 0
    max_res = np.inf
    for it in range(1, max_iter + 1):
        resid = np.zeros((n_t, n_u))
        jac = np.zeros((n_t, n_u, n_u))
        for link in links:
            pi, pj = side_pressures(link)
            dp = pi - pj
            m, dm = _link_flow(link, dp, zones[link.i].rho, zones[link.j].rho)
            if link.i in idx:
                a = idx[link.i]
                resid[:, a] -= m
                jac[:, a, a] -= dm
                if link.j in idx:
                    jac[:, a, idx[link.j]] += dm
            if link.j in idx:
                b = idx[link.j]
                resid[:, b] += m
                jac[:, b, b] -= dm
                if link.i in idx:
                    jac[:, b, idx[link.i]] += dm
        max_res_t = np.max(np.abs(resid), axis=1)
        converged = max_res_t < tol_kg_s
        max_res = float(np.max(max_res_t))
        if np.all(converged):
            break
        # Regularise singular Jacobians (e.g., a zone connected only by fixed-flow links).
        jac = jac - 1e-12 * np.eye(n_u)[None, :, :]
        step = np.linalg.solve(jac, -resid[..., None])[..., 0]
        step = np.clip(step, -200.0, 200.0)
        relax = 1.0 if it < 20 else 0.5
        for n, k in enumerate(internal):
            p[:, k] += np.where(converged, 0.0, relax * step[:, n])

    flows, dps = {}, {}
    for link in links:
        pi, pj = side_pressures(link)
        dp = pi - pj
        m, _ = _link_flow(link, dp, zones[link.i].rho, zones[link.j].rho)
        flows[link.name] = m
        dps[link.name] = dp
    return NetworkResult(p, flows, dps, converged, it, max_res)


def two_opening_stack_flow(t_in_c: float, t_out_c: float, height_m: float, a_low: float, a_high: float,
                           cd: float = 0.6, p_atm: float = 101325.0) -> float:
    """Analytical mass flow (kg/s) for one zone with a low and a high opening, no wind.

    ṁ = C_d A* √(2 ρ_ref Δp_stack), with 1/A*² = 1/A_low² + 1/A_high² and Δp_stack = g H (ρ_o − ρ_i).
    Used as an independent check of the network solver.
    """
    rho_o = p_atm / (287.042 * (t_out_c + 273.15))
    rho_i = p_atm / (287.042 * (t_in_c + 273.15))
    dp = G * height_m * (rho_o - rho_i)
    if dp <= 0:
        return 0.0
    # Flow enters low (outdoor density) and leaves high (indoor density): solve exactly.
    # Δp_low = ṁ²/(2 ρ_o (C_d A_low)²), Δp_high = ṁ²/(2 ρ_i (C_d A_high)²), Δp_low + Δp_high = Δp.
    inv = 1.0 / (2 * rho_o * (cd * a_low) ** 2) + 1.0 / (2 * rho_i * (cd * a_high) ** 2)
    return float(np.sqrt(dp / inv))
