"""Envelope conduction, thermal mass, solar geometry, shading and radiative models."""

import numpy as np
import pandas as pd
import pytest

from app.physics import envelope as env
from app.physics import radiative, solar


def _wall(mass=0.2, alpha=0.0, eps=0.0):
    return env.Construction("w", [env.layer("brick", 0.1), env.layer("mineral_wool", 0.08), env.layer("concrete_dense", mass)],
                            solar_absorptance=alpha, emissivity=eps, r_se=1 / 20.0, r_si=0.13)


@pytest.mark.benchmark
def test_fd_periodic_response_matches_transmission_matrix():
    con = _wall()
    exact = env.periodic_response(con)
    n = 24 * 40
    t = np.arange(n)
    tair = 20 + 10 * np.cos(2 * np.pi * t / 24)
    r = env.simulate([env.Surface("w", con, 1.0, 90, 180)], t_air_c=tair, t_sky_c=tair, wind_ms=np.zeros(n),
                     incident_wm2={"w": np.zeros(n)}, t_inside_c=20.0, substeps=12, h_c_override=20.0, max_dx=0.005,
                     warmup_days=10)
    q = r.q_in_w["w"][-24 * 20:]
    tt = np.arange(len(q)) + (n - 24 * 20)
    c = np.sum(q * np.exp(-1j * 2 * np.pi * tt / 24)) * 2 / len(q)
    assert abs(c) / 10.0 == pytest.approx(exact["periodic_transmittance_w_m2k"], rel=0.05)
    lag = (-np.angle(c)) % (2 * np.pi) / (2 * np.pi) * 24
    assert lag == pytest.approx(exact["time_lag_h"], abs=0.5)


def test_steady_state_u_value():
    con = _wall()
    n = 24 * 30
    r = env.simulate([env.Surface("w", con, 10.0, 90, 180)], t_air_c=np.full(n, 35.0), t_sky_c=np.full(n, 35.0),
                     wind_ms=np.zeros(n), incident_wm2={"w": np.zeros(n)}, t_inside_c=25.0, h_c_override=20.0, warmup_days=20)
    assert r.q_in_w["w"][-1] == pytest.approx(con.u_value * 10.0 * 10.0, rel=0.01)


def test_thermal_mass_shifts_timing_but_conserves_energy():
    light, heavy = _wall(mass=0.0001, alpha=0.7), _wall(mass=0.3, alpha=0.7)
    n = 24 * 20
    t = np.arange(n)
    sun = np.clip(800 * np.sin(2 * np.pi * (t % 24 - 6) / 24), 0, None)
    out = {}
    for name, con in (("light", light), ("heavy", heavy)):
        r = env.simulate([env.Surface("w", con, 1.0, 90, 180)], t_air_c=np.full(n, 25.0), t_sky_c=np.full(n, 25.0),
                         wind_ms=np.zeros(n), incident_wm2={"w": sun}, t_inside_c=25.0, h_c_override=15.0, warmup_days=5)
        q = r.q_in_w["w"]
        out[name] = q
        # integrated gain = exterior input − change in storage (heat shifted, not deleted)
        assert np.sum(q) * 3600 == pytest.approx(r.exterior_input_j["w"] - r.stored_change_j["w"], rel=1e-6, abs=1.0)
    def lag_h(q):
        tail = q[-24 * 10:]
        tt = np.arange(len(tail))
        c_q = np.sum(tail * np.exp(-1j * 2 * np.pi * tt / 24))
        c_s = np.sum(sun[-24 * 10:] * np.exp(-1j * 2 * np.pi * tt / 24))
        return ((np.angle(c_s) - np.angle(c_q)) % (2 * np.pi)) / (2 * np.pi) * 24

    assert lag_h(out["heavy"]) > lag_h(out["light"]) + 3.0
    day = slice(n - 24, n)
    assert np.ptp(out["heavy"][day]) < 0.5 * np.ptp(out["light"][day])


def test_reflective_roof_reduces_absorbed_solar_and_gain():
    n = 24 * 10
    t = np.arange(n)
    ghi = np.clip(900 * np.sin(2 * np.pi * (t % 24 - 6) / 24), 0, None)
    gains = []
    for refl in (0.2, 0.85):
        cons = env.standard_constructions(0.1, 1 - refl, 0.9)
        surf = [env.Surface("roof", cons["roof"], 100.0, 0.0, 0.0)]
        r = env.simulate(surf, t_air_c=np.full(n, 30.0), t_sky_c=np.full(n, 15.0), wind_ms=np.full(n, 3.0),
                         incident_wm2={"roof": ghi}, t_inside_c=26.0, warmup_days=3)
        gains.append((r.q_in_w["roof"].sum(), r.absorbed_solar_w["roof"].sum()))
    assert gains[1][1] < gains[0][1]
    assert gains[1][0] < gains[0][0]


def test_solar_position_and_facade_irradiance():
    idx = pd.date_range("2001-06-21 12:00", periods=1, freq="h", tz="UTC")
    sp = solar.solar_position(idx - pd.Timedelta(minutes=30), 0.0, 0.0)  # mid-hour = 12:00 UTC at lon 0
    assert sp["zenith"].iloc[0] == pytest.approx(23.44, abs=0.5)
    irr_s = solar.surface_irradiance(800, 100, 900, 45.0, 180.0, 90.0, 180.0)
    irr_n = solar.surface_irradiance(800, 100, 900, 45.0, 180.0, 90.0, 0.0)
    assert irr_s["beam"] > 0 and irr_n["beam"] == 0


def test_fixed_overhang_and_kinetic_shading():
    fs = solar.FixedShading(overhang_pf=1.0)
    assert fs.sunlit_fraction(20.0, 180.0, 180.0) < fs.sunlit_fraction(70.0, 180.0, 180.0)  # high sun shaded more
    kin = solar.KineticFacade()
    state = kin.schedule(np.array([500.0, 50.0, 50.0, 500.0]), np.array([False, True, False, False]), np.array([2, 2, 2, 25.0]))
    assert list(state) == [2, 0, 1, 0]  # shade, ventilate, neutral, storm lock-open
    beam_t, _ = kin.transmitted(np.full(4, 500.0), np.full(4, 100.0), state)
    assert beam_t[0] < beam_t[2] < beam_t[1] + 1e-9
    assert kin.actuator_power_w(state, 100.0).min() > 0  # standby power is not free


def test_sky_temperature_models():
    t_ir = radiative.sky_temperature_from_ir(np.array([300.0]))
    assert t_ir[0] == pytest.approx((300 / 5.670374419e-8) ** 0.25 - 273.15)
    clear = radiative.sky_temperature(25.0, 10.0, 0.0)
    cloudy = radiative.sky_temperature(25.0, 10.0, 100.0)
    assert clear < cloudy < 25.0
    assert radiative.sky_temperature(25.0, 20.0, 0.0) > clear  # humid sky is warmer


def test_radiative_panel_needs_subambient_sky():
    night = radiative.radiative_panel_net_flux(20.0, 20.0, 0.0, 0.0, 1.0)
    day = radiative.radiative_panel_net_flux(20.0, 20.0, 0.0, 900.0, 1.0, solar_absorptance=0.5)
    assert night > 50 and day < night
