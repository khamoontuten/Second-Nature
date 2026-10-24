"""Building envelope: multilayer transient conduction, solar and longwave exchange ("mollusc shell").

Numerical model (hourly simulation)
-----------------------------------
Each opaque surface is a 1-D multilayer wall discretised with cell-centred finite volumes
(implicit Euler, unconditionally stable). Boundary conditions:
* exterior (outdoor): combined convection + linearised longwave exchange with the sky,
  and absorbed solar flux, expressed through the sol-air temperature
      T_sa = (h_c T_air + h_r T_sky + α I_abs) / (h_c + h_r);
  tilted surfaces see the sky with view factor (1 + cos β)/2 and the ground/air otherwise.
* exterior (ground contact): undisturbed ground temperature at a reference depth beneath
  the soil layer included in the construction.
* interior: fixed zone air temperature through the interior surface resistance R_si.
The net heat flow into the conditioned zone (W, + = gain) is reported per surface and step.
Thermal storage in the layers is resolved, so heat is shifted in time, never deleted: the
integrated gain equals integrated exterior input minus the change in stored heat
(checked in tests).

Analytical model (for characterisation and benchmarking)
---------------------------------------------------------
Periodic (sinusoidal) response of a multilayer wall by the transmission-matrix method
(Carslaw & Jaeger; the basis of ISO 13786): for a layer of thickness d,
    [T₁, q₁]ᵀ = [[cosh γd, sinh γd /(kγ)], [kγ sinh γd, cosh γd]] [T₂, q₂]ᵀ,  γ = √(iω/α),
surface films [[1, R],[0, 1]]. Periodic transmittance Y = 1/M₁₂, decrement factor f = |Y|/U,
time lag = −arg(Y)/ω.

Limitations: 1-D conduction only (thermal bridges are applied as a U-value uplift factor),
constant properties, no moisture transport, interior long-wave exchange lumped into R_si.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
from scipy.linalg import solve_banded

from app.core.constants import SIGMA, T0_K


@dataclass
class Layer:
    name: str
    thickness_m: float
    k: float
    rho: float
    c: float


@dataclass
class Construction:
    name: str
    layers: list[Layer]                  # exterior → interior
    solar_absorptance: float = 0.6
    emissivity: float = 0.9
    r_si: float = 0.13
    r_se: float = 0.04
    thermal_bridge_factor: float = 1.0   # ≥ 1, multiplies conduction heat flow (bridging allowance)

    @property
    def r_layers(self) -> float:
        return sum(lay.thickness_m / lay.k for lay in self.layers)

    @property
    def u_value(self) -> float:
        """Steady U-value incl. surface resistances and bridging allowance, W/m²K."""
        return self.thermal_bridge_factor / (self.r_si + self.r_layers + self.r_se)

    @property
    def areal_heat_capacity(self) -> float:
        return sum(lay.thickness_m * lay.rho * lay.c for lay in self.layers)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["u_value"] = self.u_value
        d["areal_heat_capacity_j_m2k"] = self.areal_heat_capacity
        return d


@dataclass
class Surface:
    name: str
    construction: Construction
    area_m2: float
    tilt_deg: float          # 0 = horizontal roof, 90 = wall
    azimuth_deg: float       # outward normal, 0 = N
    exterior: str = "outdoor"  # "outdoor" | "ground"


@dataclass
class EnvelopeResult:
    q_in_w: dict[str, np.ndarray]           # per-surface heat flow into zone (W)
    t_surface_ext_c: dict[str, np.ndarray]
    absorbed_solar_w: dict[str, np.ndarray]
    stored_change_j: dict[str, float]
    exterior_input_j: dict[str, float]
    total_q_in_w: np.ndarray = field(default_factory=lambda: np.zeros(0))


def discretise(construction: Construction, max_dx: float = 0.02) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return per-cell thickness, conductivity and volumetric heat capacity arrays."""
    dx, k, rc = [], [], []
    for lay in construction.layers:
        n = max(1, int(np.ceil(lay.thickness_m / max_dx)))
        dx += [lay.thickness_m / n] * n
        k += [lay.k] * n
        rc += [lay.rho * lay.c] * n
    return np.asarray(dx), np.asarray(k), np.asarray(rc)


def periodic_response(construction: Construction, period_s: float = 86400.0) -> dict:
    """Exact periodic transmittance, decrement factor and time lag (transmission-matrix method)."""
    omega = 2.0 * np.pi / period_s
    m = np.array([[1.0, construction.r_se], [0.0, 1.0]], dtype=complex)
    for lay in construction.layers:
        alpha = lay.k / (lay.rho * lay.c)
        g = np.sqrt(1j * omega / alpha)
        gd = g * lay.thickness_m
        layer = np.array([[np.cosh(gd), np.sinh(gd) / (lay.k * g)], [lay.k * g * np.sinh(gd), np.cosh(gd)]])
        m = m @ layer
    m = m @ np.array([[1.0, construction.r_si], [0.0, 1.0]], dtype=complex)
    y = construction.thermal_bridge_factor / m[0, 1]
    u = construction.u_value
    lag_s = (-np.angle(y)) % (2 * np.pi) / omega
    return {"periodic_transmittance_w_m2k": float(abs(y)), "decrement_factor": float(abs(y) / u),
            "time_lag_h": float(lag_s / 3600.0), "u_value": u}


def simulate(surfaces: list[Surface], *, t_air_c: np.ndarray, t_sky_c: np.ndarray, wind_ms: np.ndarray,
             incident_wm2: dict[str, np.ndarray], t_inside_c: np.ndarray | float, t_ground_c: np.ndarray | None = None,
             substeps: int = 2, max_dx: float = 0.02, h_c_override: float | None = None, warmup_days: int = 14,
             initial_c: float | None = None) -> EnvelopeResult:
    """Hourly transient envelope simulation.

    incident_wm2[surface.name] is the irradiance incident on the (possibly shaded) exterior
    surface; absorbed flux = α · incident. ``warmup_days`` repeats the first days to remove
    the initial-condition transient.
    """
    n_t = len(t_air_c)
    t_inside = np.broadcast_to(np.asarray(t_inside_c, dtype=float), (n_t,))
    dt = 3600.0 / substeps
    cells = [discretise(s.construction, max_dx) for s in surfaces]
    sizes = [len(c[0]) for c in cells]
    offsets = np.concatenate([[0], np.cumsum(sizes)])
    n = int(offsets[-1])

    # Static parts of the banded matrix.
    cap = np.concatenate([dxs * rcs for dxs, _, rcs in cells]) / dt          # J/(m²K·s) per cell
    g_between = np.zeros(n)                                                  # conductance cell i ↔ i+1
    g_in = np.zeros(len(surfaces))
    half_ext = np.zeros(len(surfaces))
    for s_idx, (surf, (dxs, ks, _)) in enumerate(zip(surfaces, cells, strict=True)):
        o = offsets[s_idx]
        bf = surf.construction.thermal_bridge_factor
        for c in range(len(dxs) - 1):
            g_between[o + c] = bf / (dxs[c] / (2 * ks[c]) + dxs[c + 1] / (2 * ks[c + 1]))
        g_in[s_idx] = bf / (surf.construction.r_si + dxs[-1] / (2 * ks[-1]))
        half_ext[s_idx] = dxs[0] / (2 * ks[0]) / bf

    temps = np.full(n, float(initial_c if initial_c is not None else np.nanmean(t_air_c)))
    q_in = {s.name: np.zeros(n_t) for s in surfaces}
    t_se = {s.name: np.zeros(n_t) for s in surfaces}
    q_abs = {s.name: np.zeros(n_t) for s in surfaces}
    ext_in_j = {s.name: 0.0 for s in surfaces}
    first = [offsets[i] for i in range(len(surfaces))]
    last = [offsets[i + 1] - 1 for i in range(len(surfaces))]
    vf_sky = np.array([(1 + np.cos(np.deg2rad(s.tilt_deg))) / 2 for s in surfaces])

    warm = min(warmup_days * 24, n_t)
    sequence = list(range(warm)) + list(range(n_t))
    stored0 = None
    for step_no, t in enumerate(sequence):
        record = step_no >= warm
        if record and stored0 is None:
            stored0 = temps.copy()
        hc = h_c_override if h_c_override is not None else 5.7 + 3.8 * float(wind_ms[t])
        ab = np.zeros(3 * n).reshape(3, n)
        rhs_base = np.zeros(n)
        g_ext = np.zeros(len(surfaces))
        t_eq = np.zeros(len(surfaces))
        for s_idx, surf in enumerate(surfaces):
            if surf.exterior == "ground":
                g_ext[s_idx] = 1.0 / max(half_ext[s_idx], 1e-6)
                t_eq[s_idx] = float(t_ground_c[t]) if t_ground_c is not None else float(np.nanmean(t_air_c))
                continue
            con = surf.construction
            ts_prev = temps[first[s_idx]]
            t_rad = vf_sky[s_idx] * float(t_sky_c[t]) + (1 - vf_sky[s_idx]) * float(t_air_c[t])
            tsk, trk = ts_prev + T0_K, t_rad + T0_K
            hr = con.emissivity * SIGMA * (tsk**2 + trk**2) * (tsk + trk)
            ho = hc + hr
            absorbed = con.solar_absorptance * float(incident_wm2[surf.name][t])
            t_eq[s_idx] = (hc * float(t_air_c[t]) + hr * t_rad + absorbed) / ho
            g_ext[s_idx] = 1.0 / (1.0 / ho + half_ext[s_idx])
            if record:
                q_abs[surf.name][t] = absorbed * surf.area_m2
        diag = cap.copy()
        upper = np.zeros(n)
        lower = np.zeros(n)
        diag[:-1] += g_between[:-1]
        diag[1:] += g_between[:-1]
        upper[1:] = -g_between[:-1]
        lower[:-1] = -g_between[:-1]
        for s_idx in range(len(surfaces)):
            # g_between is zero across surface boundaries, so surfaces are decoupled.
            f, la = first[s_idx], last[s_idx]
            diag[f] += g_ext[s_idx]
            diag[la] += g_in[s_idx]
            rhs_base[f] += g_ext[s_idx] * t_eq[s_idx]
            rhs_base[la] += g_in[s_idx] * t_inside[t]
        ab[0] = upper
        ab[1] = diag
        ab[2] = lower
        for _ in range(substeps):
            rhs = cap * temps + rhs_base
            temps = solve_banded((1, 1), ab, rhs)
            if record:
                for s_idx, surf in enumerate(surfaces):
                    q_in[surf.name][t] += g_in[s_idx] * (temps[last[s_idx]] - t_inside[t]) * surf.area_m2 / substeps
                    ext_in_j[surf.name] += g_ext[s_idx] * (t_eq[s_idx] - temps[first[s_idx]]) * surf.area_m2 * dt
        if record:
            for s_idx, surf in enumerate(surfaces):
                # exterior surface temperature from the half-cell conduction
                q_e = g_ext[s_idx] * (t_eq[s_idx] - temps[first[s_idx]])
                t_se[surf.name][t] = temps[first[s_idx]] + q_e * half_ext[s_idx]

    stored = {}
    for s_idx, surf in enumerate(surfaces):
        sl = slice(first[s_idx], last[s_idx] + 1)
        stored[surf.name] = float(np.sum((temps[sl] - stored0[sl]) * cap[sl] * dt) * surf.area_m2)
    total = sum(q_in.values()) if q_in else np.zeros(n_t)
    return EnvelopeResult(q_in, t_se, q_abs, stored, ext_in_j, total_q_in_w=np.asarray(total))


# --------------------------------------------------------------------------------------------
# Material and construction library (properties: CIBSE Guide A Table 3.x / ISO 10456 typical values;
# all are editable assumptions until replaced by product data).
# --------------------------------------------------------------------------------------------

MATERIALS = {
    "concrete_dense": dict(k=1.75, rho=2300.0, c=1000.0),
    "concrete_light": dict(k=0.50, rho=1200.0, c=1000.0),
    "brick": dict(k=0.77, rho=1700.0, c=1000.0),
    "mineral_wool": dict(k=0.035, rho=30.0, c=1030.0),
    "pir_board": dict(k=0.022, rho=32.0, c=1400.0),
    "steel": dict(k=50.0, rho=7800.0, c=450.0),
    "gypsum": dict(k=0.25, rho=900.0, c=1000.0),
    "membrane": dict(k=0.23, rho=1100.0, c=1000.0),
    "soil": dict(k=1.3, rho=1460.0, c=880.0),
    "rammed_earth": dict(k=1.1, rho=1900.0, c=900.0),
    "air_cavity_ventilated": dict(k=0.278, rho=1.2, c=1005.0),  # 50 mm cavity ≈ R 0.18 m²K/W (ISO 6946)
}


def layer(material: str, thickness_m: float) -> Layer:
    p = MATERIALS[material]
    return Layer(material, thickness_m, p["k"], p["rho"], p["c"])


def standard_constructions(insulation_m: float = 0.10, roof_absorptance: float = 0.7, roof_emissivity: float = 0.9,
                           wall_absorptance: float = 0.6, mass_m: float = 0.0) -> dict[str, Construction]:
    """Baseline lightweight data-hall constructions (insulated metal panel walls, steel deck roof)."""
    wall_layers = [layer("steel", 0.0007), layer("mineral_wool", insulation_m), layer("steel", 0.0007)]
    roof_layers = [layer("membrane", 0.005), layer("pir_board", insulation_m), layer("steel", 0.001)]
    if mass_m > 0:
        wall_layers.append(layer("concrete_dense", mass_m))
        roof_layers.append(layer("concrete_dense", mass_m))
    return {
        "wall": Construction("Insulated metal panel wall", wall_layers, solar_absorptance=wall_absorptance),
        "roof": Construction("Steel deck roof", roof_layers, solar_absorptance=roof_absorptance,
                             emissivity=roof_emissivity, r_si=0.10),
        "slab": Construction("Slab on grade (incl. 1 m soil)", [layer("soil", 1.0), layer("concrete_dense", 0.25)],
                             solar_absorptance=0.0, r_si=0.17, r_se=0.0),
    }


def shell_constructions(insulation_m: float = 0.20, mass_m: float = 0.15, roof_reflectance: float = 0.85,
                        roof_emissivity: float = 0.90, wall_absorptance: float = 0.35) -> dict[str, Construction]:
    """Nacre-inspired layered envelope: alternating hard (mass) and soft (insulation) layers + ventilated rainscreen."""
    wall = [layer("steel", 0.0007), layer("air_cavity_ventilated", 0.05), layer("mineral_wool", insulation_m / 2),
            layer("concrete_dense", mass_m / 2), layer("mineral_wool", insulation_m / 2), layer("concrete_dense", mass_m / 2)]
    roof = [layer("membrane", 0.005), layer("pir_board", insulation_m / 2), layer("concrete_dense", mass_m / 2),
            layer("pir_board", insulation_m / 2), layer("concrete_dense", mass_m / 2)]
    return {
        "wall": Construction("Layered shell wall", wall, solar_absorptance=wall_absorptance),
        "roof": Construction("Layered shell roof (cool surface)", roof, solar_absorptance=1 - roof_reflectance,
                             emissivity=roof_emissivity, r_si=0.10),
        "slab": Construction("Slab on grade (incl. 1 m soil)", [layer("soil", 1.0), layer("concrete_dense", 0.25)],
                             solar_absorptance=0.0, r_si=0.17, r_se=0.0),
    }
