"""Solar geometry, surface irradiance and shading (fixed vs kinetic "fish-scale" facade).

* Solar position: pvlib (NREL SPA, numpy implementation), validated library.
* Plane-of-array irradiance: beam DNI·cos θ + isotropic sky diffuse DHI (1 + cos β)/2
  + ground-reflected GHI ρ_g (1 − cos β)/2. Isotropic diffuse is conservative (it
  under-predicts circumsolar diffuse on sun-facing walls); Perez can be selected.
* Fixed shading: horizontal overhang (projection factor PF = depth/height) and vertical fins,
  giving a sunlit fraction of the beam from the profile angles; diffuse sky is reduced by a
  view-factor reduction for the overhang.
* Kinetic louvres: each hour the louvres close (block beam fraction f_block and diffuse
  fraction f_diffuse) when beam irradiance on the facade exceeds a threshold, open for
  ventilation when outdoor air is usable, and lock in a safe (open) position above a wind
  gust limit. Actuator energy = standby power + energy per movement × number of movements.
  Stuck-actuator failure is evaluated as a separate case.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
import pvlib


def solar_position(times_utc: pd.DatetimeIndex, lat: float, lon: float, elevation_m: float = 0.0,
                   pressure_pa: np.ndarray | None = None, temperature_c: np.ndarray | None = None) -> pd.DataFrame:
    p = 101325.0 if pressure_pa is None else pressure_pa
    t = 12.0 if temperature_c is None else temperature_c
    sp = pvlib.solarposition.get_solarposition(times_utc + pd.Timedelta(minutes=30), lat, lon, altitude=elevation_m,
                                               pressure=p, temperature=t, method="nrel_numpy")
    sp.index = times_utc
    return sp[["apparent_zenith", "zenith", "azimuth", "apparent_elevation"]]


def incidence_cosine(zenith_deg, sun_azimuth_deg, tilt_deg: float, surface_azimuth_deg: float):
    z = np.deg2rad(np.asarray(zenith_deg, dtype=float))
    a = np.deg2rad(np.asarray(sun_azimuth_deg, dtype=float))
    b = np.deg2rad(tilt_deg)
    g = np.deg2rad(surface_azimuth_deg)
    return np.cos(z) * np.cos(b) + np.sin(z) * np.sin(b) * np.cos(a - g)


def surface_irradiance(dni, dhi, ghi, zenith_deg, sun_azimuth_deg, tilt_deg: float, surface_azimuth_deg: float,
                       albedo: float = 0.2) -> dict[str, np.ndarray]:
    """Isotropic-sky plane-of-array irradiance components (W/m²)."""
    cos_t = np.clip(incidence_cosine(zenith_deg, sun_azimuth_deg, tilt_deg, surface_azimuth_deg), 0.0, None)
    sun_up = np.asarray(zenith_deg) < 90.0
    beam = np.where(sun_up, np.asarray(dni, dtype=float) * cos_t, 0.0)
    b = np.deg2rad(tilt_deg)
    sky = np.asarray(dhi, dtype=float) * (1.0 + np.cos(b)) / 2.0
    ground = np.asarray(ghi, dtype=float) * albedo * (1.0 - np.cos(b)) / 2.0
    return {"beam": beam, "sky_diffuse": sky, "ground": ground, "total": beam + sky + ground, "cos_incidence": cos_t}


def profile_angles(zenith_deg, sun_azimuth_deg, surface_azimuth_deg: float):
    """Vertical-shadow (profile) angle and horizontal-shadow angle for a vertical surface, degrees."""
    alt = np.deg2rad(90.0 - np.asarray(zenith_deg, dtype=float))
    gamma = np.deg2rad(((np.asarray(sun_azimuth_deg, dtype=float) - surface_azimuth_deg) + 180.0) % 360.0 - 180.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        vsa = np.arctan(np.tan(alt) / np.cos(gamma))
    return np.rad2deg(vsa), np.rad2deg(gamma)


@dataclass
class FixedShading:
    overhang_pf: float = 0.0     # overhang depth / shaded wall height
    fin_pf: float = 0.0          # fin depth / fin spacing
    diffuse_reduction: float = 0.0  # fraction of sky diffuse intercepted

    def sunlit_fraction(self, zenith_deg, sun_azimuth_deg, surface_azimuth_deg: float) -> np.ndarray:
        vsa, hsa = profile_angles(zenith_deg, sun_azimuth_deg, surface_azimuth_deg)
        facing = np.abs(hsa) < 90.0
        shaded_v = np.clip(self.overhang_pf * np.tan(np.deg2rad(np.clip(vsa, 0, 89.9))), 0.0, 1.0)
        shaded_h = np.clip(self.fin_pf * np.abs(np.tan(np.deg2rad(np.clip(hsa, -89.9, 89.9)))), 0.0, 1.0)
        sunlit = (1.0 - shaded_v) * (1.0 - shaded_h)
        return np.where(facing, sunlit, 0.0)

    def to_dict(self):
        return asdict(self)


@dataclass
class KineticFacade:
    """Fish-scale-inspired adaptive louvres."""

    close_beam_threshold_wm2: float = 150.0
    beam_block_closed: float = 0.85
    diffuse_block_closed: float = 0.50
    beam_block_neutral: float = 0.40
    diffuse_block_neutral: float = 0.20
    gust_lock_ms: float = 20.0         # above this, panels lock open (safe, minimal wind load)
    panel_area_m2: float = 2.0
    standby_w_per_panel: float = 0.5
    energy_per_move_wh: float = 0.05
    stuck_fraction: float = 0.0        # fraction of panels stuck open (failure case)

    def to_dict(self):
        return asdict(self)

    def schedule(self, beam_on_facade, ventilate_ok, wind_ms):
        """Hourly state per facade: 2 = closed (shade), 1 = neutral, 0 = open (ventilate)."""
        beam_on_facade = np.asarray(beam_on_facade, dtype=float)
        state = np.where(beam_on_facade > self.close_beam_threshold_wm2, 2, 1)
        state = np.where(np.asarray(ventilate_ok, dtype=bool) & (beam_on_facade <= self.close_beam_threshold_wm2), 0, state)
        state = np.where(np.asarray(wind_ms) > self.gust_lock_ms, 0, state)
        return state

    def transmitted(self, beam, diffuse, state):
        bb = np.select([state == 2, state == 1], [self.beam_block_closed, self.beam_block_neutral], 0.0)
        db = np.select([state == 2, state == 1], [self.diffuse_block_closed, self.diffuse_block_neutral], 0.0)
        working = 1.0 - self.stuck_fraction
        beam_t = beam * (1.0 - bb * working)
        diff_t = diffuse * (1.0 - db * working)
        return beam_t, diff_t

    def actuator_power_w(self, state, facade_area_m2: float) -> np.ndarray:
        n_panels = max(facade_area_m2 / self.panel_area_m2, 0.0)
        moves = np.concatenate([[0], (np.diff(state) != 0).astype(float)])
        return n_panels * (self.standby_w_per_panel + self.energy_per_move_wh * moves)  # W averaged over 1 h
