"""Ground temperature model and transient earth-air heat exchanger."""

import numpy as np
import pandas as pd
import pytest

from app.core.errors import InsufficientData
from app.physics import earth_air as ea
from app.physics import psychrometrics as ps
from app.physics.ground import OMEGA, GroundModel, damping_depth, diffusivity, fit_annual_harmonic, fit_diffusivity_from_layers

IDX = pd.date_range("2001-01-01", periods=8760, freq="h", tz="UTC")


def _synthetic(mean=12.0, amp=10.0, tmin_day=20.0, depth=0.0, alpha=5e-7):
    t = (IDX.day_of_year.to_numpy() - 1) * 86400.0 + IDX.hour.to_numpy() * 3600.0
    d = damping_depth(alpha)
    return pd.Series(mean - amp * np.exp(-depth / d) * np.cos(OMEGA * (t - tmin_day * 86400.0) - depth / d), index=IDX)


def test_harmonic_fit_recovers_parameters():
    m, a, tmin = fit_annual_harmonic(_synthetic())
    assert m == pytest.approx(12.0, abs=1e-6)
    assert a == pytest.approx(10.0, abs=1e-6)
    assert tmin / 86400 == pytest.approx(20.0, abs=0.05)


def test_ground_amplitude_decays_and_lags_with_depth():
    gm = GroundModel(12.0, 10.0, 20 * 86400.0, 5e-7, "test")
    shallow = gm.temperature(0.5, IDX)
    deep = gm.temperature(6.0, IDX)
    assert np.ptp(deep) < np.ptp(shallow)
    assert abs(np.mean(deep) - 12.0) < 0.05
    assert np.argmin(deep) > np.argmin(shallow)  # minimum arrives later at depth
    assert gm.amplitude_at(3 * gm.damping_depth_m) == pytest.approx(10.0 * np.exp(-3), rel=1e-9)


def test_back_calculation_from_soil_layers_and_diffusivity_fit():
    alpha = 6e-7
    layers = {0.5: _synthetic(depth=0.5, alpha=alpha), 2.0: _synthetic(depth=2.0, alpha=alpha)}
    gm = GroundModel.from_soil_layers(layers, alpha)
    assert gm.t_mean_c == pytest.approx(12.0, abs=1e-3)
    assert gm.amplitude_k == pytest.approx(10.0, rel=1e-3)
    assert fit_diffusivity_from_layers(layers) == pytest.approx(alpha, rel=1e-3)


def test_diffusivity():
    assert diffusivity(1.3, 1460.0, 880.0) == pytest.approx(1.3 / (1460 * 880))


SOIL = ea.SoilProperties(1.3, 1460.0, 880.0)


def _run(geom, t_in, t_g, flow, years=1, rh=30.0, n=None, **kw):
    n = n or 24 * 30
    t_in = np.full(n, t_in) if np.ndim(t_in) == 0 else t_in
    p = np.full(n, 101325.0)
    w = ps.humidity_ratio_from_rh(t_in, rh, p)
    return ea.simulate(geom, SOIL, np.full(n, t_g), t_in, w, p, np.full(n, flow), years=years, **kw)


@pytest.mark.benchmark
def test_constant_wall_limit_matches_analytical_exponential():
    """With an extremely conductive, high-capacity soil the wall stays at T_g: T_out = T_g + (T_in − T_g) e^{−NTU}."""
    geom = ea.EAHEGeometry(n_pipes=1, length_m=40.0, diameter_m=0.3, wall_thickness_m=0.001, pipe_k=50.0, depth_m=3.0,
                           spacing_m=0.0, design_flow_m3s=0.5, fan_heat_to_air=0.0)
    soil = ea.SoilProperties(5000.0, 3000.0, 5000.0)
    n = 48
    t_in = np.full(n, 30.0)
    p = np.full(n, 101325.0)
    w = ps.humidity_ratio_from_rh(t_in, 20.0, p)
    r = ea.simulate(geom, soil, np.full(n, 15.0), t_in, w, p, np.full(n, 0.5), h_override=8.0, n_seg=20)
    rho = ps.moist_air_density(30.0, w[0], 101325.0)
    m = rho * 0.5
    ua = 8.0 * np.pi * 0.3 * 40.0
    expected = 15.0 + 15.0 * np.exp(-ua / (m * ps.humid_specific_heat(w[0])))
    assert r.t_out_c[-1] == pytest.approx(expected, abs=0.05)


def test_long_exchanger_approaches_ground_temperature():
    geom = ea.EAHEGeometry(n_pipes=10, length_m=200.0, diameter_m=0.2, depth_m=3.0, spacing_m=3.0, design_flow_m3s=0.5,
                           fan_heat_to_air=0.0)
    r = _run(geom, 32.0, 14.0, 0.5, n=24)
    assert r.t_out_no_fan_c[0] == pytest.approx(14.0, abs=0.5)


def test_heat_transfer_direction_reverses():
    geom = ea.EAHEGeometry(n_pipes=10, length_m=60.0, design_flow_m3s=5.0, fan_heat_to_air=0.0)
    hot = _run(geom, 30.0, 15.0, 5.0, n=24)
    cold = _run(geom, 2.0, 15.0, 5.0, n=24)
    assert np.all(hot.q_sensible_w > 0) and np.all(hot.t_out_no_fan_c < 30.0)
    assert np.all(cold.q_sensible_w < 0) and np.all(cold.t_out_no_fan_c > 2.0)
    assert np.all((hot.t_out_no_fan_c >= 15.0 - 1e-6) & (cold.t_out_no_fan_c <= 15.0 + 1e-6))


@pytest.mark.benchmark
def test_steady_state_limit_matches_shape_factor():
    """After long continuous operation the heat flow per metre tends to ΔT / (R_air + R_wall + R_soil,shape-factor)."""
    geom = ea.EAHEGeometry(n_pipes=40, length_m=60.0, diameter_m=0.4, depth_m=3.0, spacing_m=2.0, design_flow_m3s=40.0,
                           fan_heat_to_air=0.0)
    r = _run(geom, 30.0, 15.0, 40.0, n=8760)
    q_per_m = r.q_sensible_w[-1] / (geom.n_pipes * geom.length_m)
    s = ea.shape_factor_per_length(geom.depth_m, 2 * geom.outer_radius_m, geom.spacing_m, geom.n_pipes)
    r_soil = 1 / (SOIL.k * s)
    r_wall = np.log(geom.outer_radius_m / (geom.diameter_m / 2)) / (2 * np.pi * geom.pipe_k)
    h = r.diagnostics["h_mean_w_m2k"]
    r_air = 1 / (h * np.pi * geom.diameter_m)
    t_air_mean = 0.5 * (30.0 + r.t_out_no_fan_c[-1])
    assert q_per_m == pytest.approx((t_air_mean - 15.0) / (r_air + r_wall + r_soil), rel=0.05)


def test_soil_saturates_and_performance_degrades_over_years():
    geom = ea.EAHEGeometry(n_pipes=20, length_m=60.0, spacing_m=1.0, design_flow_m3s=20.0)
    r = _run(geom, 30.0, 15.0, 20.0, n=24 * 120, years=2)
    y1, y2 = r.yearly[0]["sensible_cooling_kwh"], r.yearly[1]["sensible_cooling_kwh"]
    assert y2 < y1
    assert r.yearly[1]["mean_drift_k"] > 0
    first = _run(geom, 30.0, 15.0, 20.0, n=24 * 120, years=1)
    assert first.q_sensible_w[-1] < 0.5 * first.q_sensible_w[0]  # strong decline as the soil saturates


def test_closer_spacing_means_more_interference():
    q = []
    for spacing in (0.8, 3.0):
        geom = ea.EAHEGeometry(n_pipes=20, length_m=60.0, spacing_m=spacing, design_flow_m3s=20.0)
        q.append(_run(geom, 30.0, 15.0, 20.0, n=24 * 120).q_sensible_w[-1])
    assert q[0] < q[1]


def test_condensation_when_wall_below_dew_point():
    geom = ea.EAHEGeometry(n_pipes=10, length_m=60.0, design_flow_m3s=5.0)
    wet = _run(geom, 30.0, 15.0, 5.0, rh=80.0, n=48)   # dew point ≈ 26 °C > wall
    dry = _run(geom, 30.0, 15.0, 5.0, rh=20.0, n=48)   # dew point ≈ 4 °C
    assert wet.condensate_kg.sum() > 0 and np.all(wet.w_out <= ps.humidity_ratio_from_rh(30.0, 80.0, 101325) + 1e-12)
    assert dry.condensate_kg.sum() == 0


def test_fan_power_grows_with_flow_and_length():
    g1 = ea.EAHEGeometry(length_m=40.0)
    g2 = ea.EAHEGeometry(length_m=120.0)
    dp1, _, f1, *_ = ea.hydraulics(g1, np.array([20.0, 40.0]))
    dp2, _, f2, *_ = ea.hydraulics(g2, np.array([20.0, 40.0]))
    assert f1[1] > f1[0] and f2[0] > f1[0] and dp2[1] > dp1[1]
    assert np.all(f1 >= 0)


def test_missing_soil_properties_gives_insufficient_data():
    geom = ea.EAHEGeometry()
    n = 24
    with pytest.raises(InsufficientData):
        ea.simulate(geom, ea.SoilProperties(float("nan"), 1500, 900), np.full(n, 15.0), np.full(n, 30.0), np.full(n, 0.01),
                    np.full(n, 101325.0), np.full(n, 10.0))
