"""Undisturbed ground temperature and soil thermal properties.

Model: one-dimensional periodic conduction into a homogeneous semi-infinite medium driven
by an annual sinusoidal surface temperature (Kusuda & Achenbach, 1965; Carslaw & Jaeger):

    T_g(z, t) = T_m − A_s · exp(−z/δ) · cos(ω (t − t_0) − z/δ),   δ = √(2 α / ω),  ω = 2π / year

T_m is the annual mean surface temperature, A_s the annual surface amplitude, t_0 the time
of minimum surface temperature, α = k/(ρ c) the soil thermal diffusivity.

Calibration options (provenance recorded by the caller):
1. From near-surface soil temperature layers (e.g., ERA5-Land levels) — preferred; the
   surface harmonic is back-calculated from each layer's amplitude and phase.
2. From air temperature plus a user-entered surface offset (default 0 K). This assumes the
   soil surface follows air temperature, which ignores solar heating, vegetation, snow
   cover and evaporation; it is an approximation, flagged as such.
3. A measured (geotechnical/borehole) temperature at depth entered by the engineer, which
   replaces the modeled mean at that depth.

Limitations: no latent effects (freezing/thawing), no moisture migration, no groundwater
advection, homogeneous properties, no surface cover heterogeneity.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

YEAR_S = 365.25 * 86400.0
OMEGA = 2.0 * np.pi / YEAR_S

# Soil thermal properties. Dry values: Incropera et al., Fundamentals of Heat and Mass Transfer,
# Table A.3 (soil, clay, sand, granite). "Moist/saturated" values are typical literature ranges
# and are explicitly assumptions — site testing (thermal response test or needle probe) is needed.
SOIL_TYPES: dict[str, dict] = {
    "soil_generic": {"k": 0.52, "rho": 2050.0, "c": 1840.0, "source": "Incropera Table A.3 (soil)"},
    "clay": {"k": 1.3, "rho": 1460.0, "c": 880.0, "source": "Incropera Table A.3 (clay)"},
    "sand_dry": {"k": 0.27, "rho": 1515.0, "c": 800.0, "source": "Incropera Table A.3 (sand)"},
    "granite": {"k": 2.79, "rho": 2630.0, "c": 775.0, "source": "Incropera Table A.3 (granite)"},
    "sand_moist": {"k": 1.8, "rho": 1900.0, "c": 1250.0, "source": "typical range, assumption"},
    "loam_moist": {"k": 1.2, "rho": 1750.0, "c": 1300.0, "source": "typical range, assumption"},
}


def diffusivity(k: float, rho: float, c: float) -> float:
    """α_s = k / (ρ c), m²/s."""
    return k / (rho * c)


def damping_depth(alpha_m2s: float, omega: float = OMEGA) -> float:
    """δ = √(2α/ω), m."""
    return float(np.sqrt(2.0 * alpha_m2s / omega))


def fit_annual_harmonic(series: pd.Series) -> tuple[float, float, float]:
    """Least-squares fit of T = m + a cos(ω t) + b sin(ω t) to a time-indexed series.

    Returns (mean, amplitude, t_min_s) where t_min_s is seconds after 1 Jan 00:00 of the
    minimum of the fitted sinusoid.
    """
    s = series.dropna()
    if len(s) < 24 * 60:
        raise ValueError("At least ~60 days of data are needed to fit an annual harmonic")
    idx = s.index
    t = (idx.day_of_year.to_numpy() - 1) * 86400.0 + idx.hour.to_numpy() * 3600.0
    x = np.column_stack([np.ones_like(t), np.cos(OMEGA * t), np.sin(OMEGA * t)])
    coef, *_ = np.linalg.lstsq(x, s.to_numpy(dtype=float), rcond=None)
    m, a, b = coef
    amp = float(np.hypot(a, b))
    phase_max = np.arctan2(b, a)  # time of maximum: ω t_max = phase_max
    t_min = ((phase_max + np.pi) / OMEGA) % YEAR_S
    return float(m), amp, float(t_min)


@dataclass
class GroundModel:
    t_mean_c: float
    amplitude_k: float
    t_min_s: float          # seconds after 1 Jan of the minimum surface temperature
    alpha_m2s: float
    calibration: str
    measured_override: dict | None = None  # {"depth_m": z, "t_c": value} replaces the mean at depth

    @property
    def damping_depth_m(self) -> float:
        return damping_depth(self.alpha_m2s)

    def temperature(self, depth_m: float, times: pd.DatetimeIndex) -> np.ndarray:
        """Undisturbed ground temperature (°C) at depth for each timestamp."""
        t = ((times.day_of_year.to_numpy() - 1) * 86400.0 + times.hour.to_numpy() * 3600.0
             + times.minute.to_numpy() * 60.0)
        d = self.damping_depth_m
        val = self.t_mean_c - self.amplitude_k * np.exp(-depth_m / d) * np.cos(OMEGA * (t - self.t_min_s) - depth_m / d)
        if self.measured_override:
            # Shift the mean so the annual mean at the measured depth equals the measured value.
            val = val + (self.measured_override["t_c"] - self.t_mean_c)
        return val

    def amplitude_at(self, depth_m: float) -> float:
        return float(self.amplitude_k * np.exp(-depth_m / self.damping_depth_m))

    def profile(self, depths_m: np.ndarray, times: pd.DatetimeIndex) -> np.ndarray:
        """(len(times), len(depths)) array of ground temperatures."""
        return np.column_stack([self.temperature(float(z), times) for z in depths_m])

    def to_dict(self) -> dict:
        return {
            "t_mean_c": self.t_mean_c, "amplitude_k": self.amplitude_k, "t_min_day": self.t_min_s / 86400.0,
            "alpha_m2s": self.alpha_m2s, "damping_depth_m": self.damping_depth_m, "calibration": self.calibration,
            "measured_override": self.measured_override,
        }

    @classmethod
    def from_air_temperature(cls, t_air: pd.Series, alpha_m2s: float, surface_offset_k: float = 0.0,
                             amplitude_factor: float = 1.0) -> GroundModel:
        m, a, tmin = fit_annual_harmonic(t_air)
        return cls(m + surface_offset_k, a * amplitude_factor, tmin, alpha_m2s,
                   calibration=(f"annual harmonic of air temperature; surface offset {surface_offset_k:+.1f} K, "
                                f"amplitude factor {amplitude_factor:.2f} (approximation: surface ≈ air)"))

    @classmethod
    def from_soil_layers(cls, layers: dict[float, pd.Series], alpha_m2s: float) -> GroundModel:
        """Back-calculate the surface harmonic from soil temperature at one or more depths (m).

        Each layer gives A_s = A_z e^{z/δ} and t_0 = t_min,z − (z/δ)/ω. Results are averaged
        across layers weighted by inverse depth (shallow layers have the strongest signal).
        """
        d = damping_depth(alpha_m2s)
        means, amps, phases, weights = [], [], [], []
        for z, s in layers.items():
            m, a, tmin = fit_annual_harmonic(s)
            means.append(m)
            amps.append(a * np.exp(z / d))
            phases.append(tmin - (z / d) / OMEGA)
            weights.append(1.0 / max(z, 0.05))
        w = np.asarray(weights) / np.sum(weights)
        ang = np.asarray(phases) * OMEGA
        mean_phase = np.arctan2(np.sum(w * np.sin(ang)), np.sum(w * np.cos(ang))) / OMEGA % YEAR_S
        return cls(float(np.sum(w * means)), float(np.sum(w * amps)), float(mean_phase), alpha_m2s,
                   calibration=f"back-calculated from soil layers at depths {sorted(layers)} m")


def fit_diffusivity_from_layers(layers: dict[float, pd.Series]) -> float | None:
    """Estimate α from amplitude decay between the shallowest and deepest layer (needs ≥ 2 depths).

    A(z2)/A(z1) = exp(−(z2 − z1)/δ)  ⇒  δ = (z2 − z1) / ln(A1/A2),  α = ω δ² / 2.
    Returns None if the decay is not physically consistent.
    """
    if len(layers) < 2:
        return None
    zs = sorted(layers)
    a1 = fit_annual_harmonic(layers[zs[0]])[1]
    a2 = fit_annual_harmonic(layers[zs[-1]])[1]
    if a2 <= 0 or a1 <= a2:
        return None
    delta = (zs[-1] - zs[0]) / np.log(a1 / a2)
    return float(OMEGA * delta**2 / 2.0)
