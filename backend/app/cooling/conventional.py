"""Conventional cooling equipment models (vectorised over hours).

Chiller performance
-------------------
Two options, selectable per project:
* ``carnot`` (default when no manufacturer data): COP = η_II · T_evap / (T_cond − T_evap), with the
  second-law efficiency η_II calibrated so that COP equals the user's rated COP at the AHRI 550/590
  rating point (air-cooled: 35 °C ambient, 6.7 °C leaving chilled water; water-cooled: 29.4 °C
  entering condenser water). Evaporating temperature = chilled-water supply − evaporator
  approach; condensing temperature = condenser medium temperature + condenser approach.
  A minimum lift (head-pressure control) bounds COP at low ambient. This is a *preliminary*
  temperature-dependent model, not a manufacturer curve.
* ``biquadratic``: EnergyPlus Chiller:Electric:EIR form with user coefficients:
  CAPFT(T_chw, T_cond), EIRFT(T_chw, T_cond) biquadratic, EIRFPLR(PLR) quadratic.
* ``constant``: fixed COP — allowed only as an explicit simplification (flagged in results).

Part load: P = P_full(T) · PLF(PLR) with default PLF = 0.10 + 0.60 PLR + 0.30 PLR² (assumption,
mildly better efficiency at mid loads; replace with manufacturer IPLV data). Units are staged so each
running unit stays below the staging threshold; redundant units run only if ``run_redundant``.

Air movers: fan power = V̇ Δp / η; units sharing flow follow the fan affinity law (P ∝ flow³),
so running N+1 units at reduced speed is captured. All indoor fan power becomes heat in the air
stream. Pumps: P_design · PLR^n (n = 2.5 default for variable-speed with some static head).

UPS: loss = S_rated (a₀ + a₁ LF + a₂ LF²) fitted to user efficiency points (default typical
double-conversion values, assumption). Distribution: fixed fraction of IT power (assumption).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from app.core.constants import CP_W, RHO_W, T0_K

RATING_AIR_COOLED = {"t_cond_medium_c": 35.0, "t_chw_c": 6.7}
RATING_WATER_COOLED = {"t_cond_medium_c": 29.4, "t_chw_c": 6.7}


@dataclass
class ChillerSpec:
    kind: str = "air_cooled"          # "air_cooled" | "water_cooled"
    rated_cop: float = 3.0            # AHRI full-load COP (air-cooled includes condenser fans)
    model: str = "carnot"             # "carnot" | "biquadratic" | "constant"
    evap_approach_k: float = 2.5
    cond_approach_k: float = 12.0     # air-cooled: condensing − ambient; water-cooled: condensing − entering CW (incl. range)
    min_lift_k: float = 12.0
    min_condensing_c: float = 25.0    # head-pressure control floor on condensing temperature
    max_cop: float = 10.0
    plf_coeffs: tuple[float, float, float] = (0.10, 0.60, 0.30)
    min_plr: float = 0.15
    capacity_derate_per_k: float = 0.008  # fractional capacity loss per K of condenser medium above rating
    capft: tuple[float, ...] | None = None   # biquadratic: a + b x + c x² + d y + e y² + f x y
    eirft: tuple[float, ...] | None = None
    eirfplr: tuple[float, float, float] | None = None
    rating_override: dict | None = None   # {"t_cond_medium_c": .., "t_chw_c": ..} e.g. DX (AHRI 340/360)

    def to_dict(self):
        return asdict(self)

    def _rating(self):
        if self.rating_override:
            return self.rating_override
        return RATING_AIR_COOLED if self.kind == "air_cooled" else RATING_WATER_COOLED

    def eta_ii(self) -> float:
        r = self._rating()
        t_e = r["t_chw_c"] - self.evap_approach_k + T0_K
        t_c = r["t_cond_medium_c"] + self.cond_approach_k + T0_K
        return self.rated_cop * (t_c - t_e) / t_e

    def full_load_cop(self, t_chw_c, t_cond_medium_c):
        t_chw_c = np.asarray(t_chw_c, dtype=float)
        t_cond_medium_c = np.asarray(t_cond_medium_c, dtype=float)
        if self.model == "constant":
            return np.full(np.broadcast(t_chw_c, t_cond_medium_c).shape, self.rated_cop)
        if self.model == "biquadratic" and self.eirft is not None:
            x, y = t_chw_c, t_cond_medium_c
            e = self.eirft
            eir_ft = e[0] + e[1] * x + e[2] * x**2 + e[3] * y + e[4] * y**2 + e[5] * x * y
            return self.rated_cop / np.maximum(eir_ft, 1e-3)
        t_e = t_chw_c - self.evap_approach_k + T0_K
        t_c = np.maximum(t_cond_medium_c + self.cond_approach_k, self.min_condensing_c) + T0_K
        lift = np.maximum(t_c - t_e, self.min_lift_k)
        return np.minimum(self.eta_ii() * t_e / lift, self.max_cop)

    def capacity_factor(self, t_chw_c, t_cond_medium_c):
        if self.model == "biquadratic" and self.capft is not None:
            x, y = np.asarray(t_chw_c, dtype=float), np.asarray(t_cond_medium_c, dtype=float)
            c = self.capft
            return np.maximum(c[0] + c[1] * x + c[2] * x**2 + c[3] * y + c[4] * y**2 + c[5] * x * y, 0.05)
        over = np.maximum(np.asarray(t_cond_medium_c, dtype=float) - self._rating()["t_cond_medium_c"], 0.0)
        over = np.broadcast_to(over, np.broadcast(np.asarray(t_chw_c), over).shape)
        return np.clip(1.0 - self.capacity_derate_per_k * over, 0.3, 1.1)

    def plf(self, plr):
        a, b, c = self.eirfplr if (self.model == "biquadratic" and self.eirfplr) else self.plf_coeffs
        plr = np.asarray(plr, dtype=float)
        # below minimum PLR the unit cycles: power scales linearly from the min-PLR point
        p_min = a + b * self.min_plr + c * self.min_plr**2
        return np.where(plr >= self.min_plr, a + b * plr + c * plr**2, p_min * plr / self.min_plr)


@dataclass
class ChillerPlantResult:
    power_w: np.ndarray
    cop_effective: np.ndarray
    units_running: np.ndarray
    unmet_w: np.ndarray
    capacity_available_w: np.ndarray


def chiller_plant(q_w, t_chw_c, t_cond_medium_c, spec: ChillerSpec, unit_capacity_w: float, n_units_installed: int,
                  n_units_available: int | None = None, stage_up_fraction: float = 0.9) -> ChillerPlantResult:
    """Stage identical chillers to meet q_w; returns compressor power (incl. air-cooled condenser fans)."""
    q = np.maximum(np.asarray(q_w, dtype=float), 0.0)
    n_avail = n_units_installed if n_units_available is None else n_units_available
    cap_unit = unit_capacity_w * spec.capacity_factor(t_chw_c, t_cond_medium_c)
    cap_unit = np.broadcast_to(cap_unit, q.shape)
    n_run = np.where(q > 0, np.ceil(q / np.maximum(cap_unit * stage_up_fraction, 1.0)), 0)
    n_run = np.clip(n_run, 0, n_avail)
    cap_avail = n_run * cap_unit
    delivered = np.minimum(q, np.where(n_run > 0, cap_avail, 0.0))
    unmet = q - delivered
    plr = np.where(n_run > 0, delivered / np.maximum(cap_avail, 1.0), 0.0)
    cop_full = np.broadcast_to(spec.full_load_cop(t_chw_c, t_cond_medium_c), q.shape)
    p_full_unit = cap_unit / np.maximum(cop_full, 1e-3)
    power = n_run * p_full_unit * spec.plf(plr)
    power = np.where(delivered > 0, power, 0.0)
    cop_eff = np.where(power > 0, delivered / np.maximum(power, 1e-9), 0.0)
    return ChillerPlantResult(power, cop_eff, n_run, unmet, np.broadcast_to(n_avail * cap_unit, q.shape).copy())


@dataclass
class FanSystem:
    """Air handlers (CRAH/CRAC/fan walls) sharing a required airflow."""

    design_flow_m3s_per_unit: float
    n_units_installed: int
    dp_pa: float = 350.0
    efficiency: float = 0.62           # fan × motor × drive
    run_redundant: bool = True
    min_speed: float = 0.25
    part_load_exponent: float = 2.7    # < 3 (ideal affinity) to reflect system effects and motor/VFD losses

    def to_dict(self):
        return asdict(self)

    def power(self, flow_m3s, n_units_available: int | None = None):
        flow = np.maximum(np.asarray(flow_m3s, dtype=float), 0.0)
        n_av = self.n_units_installed if n_units_available is None else n_units_available
        n_needed = np.ceil(flow / max(self.design_flow_m3s_per_unit, 1e-9))
        n_run = np.where(flow > 0, n_av if self.run_redundant else np.minimum(n_needed, n_av), 0)
        n_run = np.maximum(n_run, np.where(flow > 0, 1, 0))
        per_unit = np.where(n_run > 0, flow / np.maximum(n_run, 1), 0.0)
        speed = np.clip(per_unit / self.design_flow_m3s_per_unit, 0.0, 1.5)
        speed_eff = np.where(flow > 0, np.maximum(speed, self.min_speed), 0.0)
        p_design_unit = self.design_flow_m3s_per_unit * self.dp_pa / self.efficiency
        power = n_run * p_design_unit * speed_eff**self.part_load_exponent
        deficit = np.maximum(flow - n_av * self.design_flow_m3s_per_unit * 1.0, 0.0)
        return power, n_run, deficit


@dataclass
class PumpSpec:
    design_head_pa: float = 250_000.0
    efficiency: float = 0.70
    part_load_exponent: float = 2.5
    min_fraction: float = 0.15

    def to_dict(self):
        return asdict(self)

    def power(self, q_w, design_q_w: float, delta_t_k: float):
        """Pump power for a water loop carrying q_w with design ΔT."""
        q = np.maximum(np.asarray(q_w, dtype=float), 0.0)
        v_design = design_q_w / (RHO_W * CP_W * delta_t_k)
        p_design = v_design * self.design_head_pa / self.efficiency
        frac = np.clip(q / max(design_q_w, 1.0), 0.0, 1.5)
        frac_eff = np.where(q > 0, np.maximum(frac, self.min_fraction), 0.0)
        return p_design * frac_eff**self.part_load_exponent


@dataclass
class DryCooler:
    approach_k: float = 6.0            # leaving fluid − ambient dry bulb at design
    fan_kw_per_kw: float = 0.02        # design fan power per kW rejected
    min_speed: float = 0.2

    def to_dict(self):
        return asdict(self)

    def leaving_temperature(self, t_db, plr):
        return np.asarray(t_db, dtype=float) + self.approach_k * np.clip(np.asarray(plr, dtype=float), 0.2, 1.2) ** 0.8

    def fan_power(self, q_w, design_q_w: float):
        q = np.maximum(np.asarray(q_w, dtype=float), 0.0)
        speed = np.where(q > 0, np.clip(q / max(design_q_w, 1.0), self.min_speed, 1.2), 0.0)
        return self.fan_kw_per_kw * design_q_w * speed**3


@dataclass
class UPSModel:
    rated_kva_factor: float = 1.25    # UPS capacity / IT capacity (redundancy & headroom; 2N ≈ 2.0+)
    a0: float = 0.010
    a1: float = 0.020
    a2: float = 0.015
    efficiency_points: list[tuple[float, float]] | None = None  # [(load fraction, efficiency), ...]

    def to_dict(self):
        return asdict(self)

    def fit(self):
        if not self.efficiency_points:
            return self.a0, self.a1, self.a2
        lf = np.array([p[0] for p in self.efficiency_points])
        eta = np.array([p[1] for p in self.efficiency_points])
        loss_frac_rated = lf * (1.0 / eta - 1.0)
        a = np.polyfit(lf, loss_frac_rated, 2)
        return float(a[2]), float(a[1]), float(a[0])

    def losses(self, p_it_w, it_capacity_w: float):
        a0, a1, a2 = self.fit()
        s = self.rated_kva_factor * it_capacity_w
        lf = np.asarray(p_it_w, dtype=float) / max(s, 1.0)
        return np.maximum(s * (a0 + a1 * lf + a2 * lf**2), 0.0)


@dataclass
class ElectricalOverheads:
    ups: UPSModel = field(default_factory=UPSModel)
    distribution_loss_fraction: float = 0.015
    lighting_w_m2: float = 4.0
    lighting_on_fraction: float = 0.3
    other_fixed_w: float = 50_000.0
    losses_in_cooled_space_fraction: float = 1.0   # share of UPS/PDU losses rejected into the cooled zone
    lighting_in_cooled_space_fraction: float = 1.0

    def to_dict(self):
        return asdict(self)


def heat_rejection_equals(q_load_w, compressor_w):
    """Heat rejected by a vapour-compression plant at the condenser = evaporator load + compressor work."""
    return np.asarray(q_load_w, dtype=float) + np.asarray(compressor_w, dtype=float)
