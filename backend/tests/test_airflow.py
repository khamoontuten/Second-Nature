"""Stack/wind airflow and network solver: analytical checks and physical sanity."""

import numpy as np
import pytest

from app.physics import airflow as af


def _rho(t):
    return 101325.0 / (287.042 * (np.asarray(t) + 273.15))


def _two_zone(t_out, t_in, a_low=2.0, a_high=1.0, cd=0.6, height=20.0, k_extra=0.0, wind_low=0.0, wind_high=0.0):
    t_out = np.atleast_1d(np.asarray(t_out, dtype=float))
    t_in = np.broadcast_to(np.asarray(t_in, dtype=float), t_out.shape)
    zones = [af.Zone("out", t_out, _rho(t_out), boundary=True), af.Zone("in", t_in, _rho(t_in))]
    links = [af.Link("low", 0, 1, z=1.0, area=a_low, k_loss=1 / cd**2 + k_extra, wind_i=wind_low),
             af.Link("high", 1, 0, z=1.0 + height, area=a_high, k_loss=1 / cd**2 + k_extra, wind_j=wind_high)]
    return af.solve_network(zones, links)


@pytest.mark.benchmark
def test_network_matches_analytical_two_opening_stack():
    t_out = np.array([0.0, 10.0, 20.0, 30.0])
    r = _two_zone(t_out, 35.0)
    for i, to in enumerate(t_out):
        assert r.flows["low"][i] == pytest.approx(af.two_opening_stack_flow(35.0, to, 20.0, 2.0, 1.0), rel=1e-6)
    assert np.allclose(r.flows["low"], r.flows["high"])  # mass conservation
    assert r.converged.all()


def test_stack_pressure_vanishes_without_temperature_difference():
    assert af.stack_pressure(20.0, 20.0, 30.0) == pytest.approx(0.0, abs=1e-12)
    r = _two_zone(np.array([25.0]), 25.0)
    assert abs(r.flows["low"][0]) < 1e-3


def test_stack_pressure_formula_and_sign():
    dp = af.stack_pressure(35.0, 15.0, 10.0)
    rho = _rho(15.0)
    assert dp == pytest.approx(rho * 9.80665 * 10.0 * 20.0 / 308.15, rel=1e-9)
    assert af.stack_pressure(10.0, 30.0, 10.0) < 0


def test_flow_reverses_when_outside_warmer():
    r = _two_zone(np.array([40.0]), 25.0)
    assert r.flows["low"][0] < 0  # air now flows out at the low opening


def test_more_resistance_never_increases_passive_flow():
    flows = [_two_zone(np.array([15.0]), 35.0, k_extra=k).flows["low"][0] for k in (0, 1, 3, 10)]
    assert all(a >= b - 1e-9 for a, b in zip(flows, flows[1:], strict=False))


def test_flow_increases_with_height_and_area():
    q = [_two_zone(np.array([15.0]), 35.0, height=h).flows["low"][0] for h in (5, 10, 20, 40)]
    assert np.all(np.diff(q) > 0)
    qa = [_two_zone(np.array([15.0]), 35.0, a_low=a, a_high=a).flows["low"][0] for a in (0.5, 1, 2)]
    assert np.all(np.diff(qa) > 0)


def test_wind_assists_and_opposes():
    base = _two_zone(np.array([15.0]), 35.0).flows["low"][0]
    assist = _two_zone(np.array([15.0]), 35.0, wind_low=5.0, wind_high=-5.0).flows["low"][0]
    oppose = _two_zone(np.array([15.0]), 35.0, wind_low=-5.0, wind_high=5.0).flows["low"][0]
    assert assist > base > oppose


def test_swami_chandra_coefficients():
    assert af.swami_chandra_cp(0.0, 0.0) == pytest.approx(0.6, rel=0.02)          # normal incidence
    assert af.swami_chandra_cp(180.0, 0.0) < 0                                    # leeward suction
    assert af.swami_chandra_cp(30.0, 0.0) == pytest.approx(af.swami_chandra_cp(-30.0, 0.0))


def test_wind_profile_monotonic():
    u = [float(af.wind_speed_at_height(5.0, h)) for h in (5, 10, 30, 60)]
    assert np.all(np.diff(u) > 0)


def test_required_airflow():
    m = af.required_mass_flow(1e6, 12.0, w=0.0)
    assert m == pytest.approx(1e6 / (1006 * 12))


def test_duct_drop_increases_with_flow_and_roughness():
    dp1, *_ = af.duct_pressure_drop(1.0, 0.4, 50)
    dp2, *_ = af.duct_pressure_drop(2.0, 0.4, 50)
    dp3, *_ = af.duct_pressure_drop(2.0, 0.4, 50, roughness_m=1e-3)
    assert dp2 > dp1 and dp3 > dp2
