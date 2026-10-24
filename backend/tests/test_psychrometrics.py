"""Psychrometrics: benchmarks against an independent ASHRAE implementation and reference values."""

import numpy as np
import psychrolib
import pytest

from app.physics import psychrometrics as ps

psychrolib.SetUnitSystem(psychrolib.SI)

# Saturation vapour pressure over liquid water, IAPWS-95 / steam-table values (Pa).
IAPWS_PWS = {20.0: 2339.3, 25.0: 3169.9, 30.0: 4246.9, 40.0: 7384.9, 50.0: 12352.0, 100.0: 101418.0}

# ASHRAE Handbook—Fundamentals (2017) Ch.1 Table 1, standard atmosphere pressure (kPa) vs altitude (m).
ASHRAE_ALTITUDE = {0.0: 101.325, 500.0: 95.461, 1000.0: 89.875, 2000.0: 79.495}


@pytest.mark.benchmark
@pytest.mark.parametrize("t,ref", IAPWS_PWS.items())
def test_saturation_pressure_vs_iapws(t, ref):
    assert ps.saturation_pressure(t) == pytest.approx(ref, rel=1.5e-3)


@pytest.mark.benchmark
@pytest.mark.parametrize("z,ref", ASHRAE_ALTITUDE.items())
def test_pressure_with_altitude(z, ref):
    assert ps.pressure_at_elevation(z) / 1000 == pytest.approx(ref, abs=0.01)


@pytest.mark.benchmark
def test_against_psychrolib_random_states():
    rng = np.random.default_rng(1)
    t = rng.uniform(-15, 45, 200)
    rh = rng.uniform(5, 99, 200)
    p = rng.uniform(85000, 102000, 200)
    w = ps.humidity_ratio_from_rh(t, rh, p)
    twb = ps.wet_bulb_from_rh(t, rh, p)
    tdp = ps.dew_point_from_rh(t, rh)
    h = ps.enthalpy(t, w)
    for i in range(200):
        assert w[i] == pytest.approx(psychrolib.GetHumRatioFromRelHum(t[i], rh[i] / 100, p[i]), rel=1e-6)
        assert twb[i] == pytest.approx(psychrolib.GetTWetBulbFromRelHum(t[i], rh[i] / 100, p[i]), abs=2e-3)
        assert tdp[i] == pytest.approx(psychrolib.GetTDewPointFromRelHum(t[i], rh[i] / 100), abs=2e-3)
        assert h[i] == pytest.approx(psychrolib.GetMoistAirEnthalpy(t[i], w[i]), rel=1e-6)
        assert ps.moist_air_density(t[i], w[i], p[i]) == pytest.approx(psychrolib.GetMoistAirDensity(t[i], w[i], p[i]), rel=1e-6)


def test_humidity_ratio_definition():
    pv, p = 2000.0, 101325.0
    assert ps.humidity_ratio_from_vapour_pressure(pv, p) == pytest.approx(0.621945 * pv / (p - pv))


def test_round_trips():
    t, rh, p = 27.0, 55.0, 98000.0
    w = ps.humidity_ratio_from_rh(t, rh, p)
    assert ps.rh_from_humidity_ratio(t, w, p) == pytest.approx(rh, rel=1e-9)
    tdp = ps.dew_point_from_humidity_ratio(w, p)
    assert ps.humidity_ratio_from_dew_point(tdp, p) == pytest.approx(w, rel=1e-6)
    h = ps.enthalpy(t, w)
    assert ps.temperature_from_enthalpy(h, w) == pytest.approx(t, abs=1e-9)


def test_wet_bulb_bounds_and_saturation():
    t = np.linspace(-10, 45, 30)
    for rh in (5, 40, 80):
        twb = ps.wet_bulb_from_rh(t, rh, 101325)
        tdp = ps.dew_point_from_rh(t, rh)
        assert np.all(twb <= t + 1e-6)
        assert np.all(twb >= tdp - 1e-3)
    assert ps.wet_bulb_from_rh(25.0, 100.0, 101325) == pytest.approx(25.0, abs=1e-3)


def test_stull_approximation_close_to_thermodynamic():
    t = np.array([10.0, 20.0, 30.0, 40.0])
    rh = np.array([30.0, 50.0, 60.0, 20.0])
    err = ps.wet_bulb_stull(t, rh) - ps.wet_bulb_from_rh(t, rh, 101325)
    assert np.all((err > -1.0) & (err < 0.7))  # error band reported by Stull (2011)


def test_mixing_conserves_mass_and_energy():
    t, w = ps.mix(2.0, 30.0, 0.012, 1.0, 10.0, 0.004)
    assert w == pytest.approx((2 * 0.012 + 0.004) / 3)
    h = (2 * ps.enthalpy(30.0, 0.012) + ps.enthalpy(10.0, 0.004)) / 3
    assert ps.enthalpy(t, w) == pytest.approx(h)
