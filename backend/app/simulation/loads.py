"""IT load profiles and electrical overheads.

Installed IT capacity is never equated with IT energy: hourly IT power is installed capacity ×
a utilisation profile. Default profile shapes are assumptions; a measured or planned hourly
profile can be supplied.
"""

from __future__ import annotations

import numpy as np

from app.cooling.conventional import ElectricalOverheads, UPSModel
from app.schemas.config import ElectricalConfig, ITLoadConfig

SCENARIO_UTILISATION = {"low": 0.35, "expected": None, "peak": 0.90, "ai_hpc": 0.85}


def utilisation_profile(it: ITLoadConfig, local_index) -> np.ndarray:
    n = len(local_index)
    if it.profile_hourly_fraction is not None:
        prof = np.asarray(it.profile_hourly_fraction, dtype=float)
        if len(prof) < n:
            prof = np.resize(prof, n)
        return np.clip(prof[:n], 0.0, 1.0)
    mean = SCENARIO_UTILISATION.get(it.utilisation_scenario) or it.average_utilisation
    hours = np.asarray(local_index.hour, dtype=float)
    dow = np.asarray(local_index.day_of_week)
    diurnal = it.diurnal_amplitude * np.sin(2 * np.pi * (hours - 9.0) / 24.0)   # peak ≈ 15:00 local
    weekly = it.weekly_amplitude * np.where(dow < 5, 0.4, -1.0)
    years = np.asarray(local_index.year) - int(local_index.year.min())
    growth = (1.0 + it.annual_growth) ** years
    return np.clip(mean * (1.0 + diurnal + weekly) * growth, 0.0, 1.0)


def it_power_w(it: ITLoadConfig, local_index) -> np.ndarray:
    return it.installed_capacity_mw * 1e6 * utilisation_profile(it, local_index)


def overheads_from_config(el: ElectricalConfig) -> ElectricalOverheads:
    return ElectricalOverheads(
        ups=UPSModel(rated_kva_factor=el.ups_capacity_factor, efficiency_points=el.ups_efficiency_points),
        distribution_loss_fraction=el.distribution_loss_fraction,
        lighting_w_m2=el.lighting_w_m2,
        lighting_on_fraction=el.lighting_on_fraction,
        other_fixed_w=el.other_fixed_kw * 1000.0,
        losses_in_cooled_space_fraction=el.losses_in_cooled_space_fraction,
    )


def electrical_losses(p_it: np.ndarray, it_capacity_w: float, el: ElectricalConfig, floor_area_m2: float) -> dict[str, np.ndarray]:
    oh = overheads_from_config(el)
    ups = oh.ups.losses(p_it, it_capacity_w)
    dist = oh.distribution_loss_fraction * p_it
    light = np.full_like(p_it, oh.lighting_w_m2 * floor_area_m2 * oh.lighting_on_fraction)
    other = np.full_like(p_it, oh.other_fixed_w)
    controls = el.controls_fraction_of_it * it_capacity_w * np.ones_like(p_it)
    return {"ups_loss": ups, "dist_loss": dist, "lighting": light, "other": other, "controls": controls,
            "in_space": el.losses_in_cooled_space_fraction * (ups + dist) + light}
