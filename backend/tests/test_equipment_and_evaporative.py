"""Conventional equipment and evaporative heat-and-mass balances."""

import numpy as np
import pytest

from app.cooling.conventional import ChillerSpec, FanSystem, PumpSpec, UPSModel, chiller_plant
from app.physics import evaporative as evap
from app.physics import psychrometrics as ps


def test_carnot_model_reproduces_rated_cop_at_rating_point():
    ac = ChillerSpec(kind="air_cooled", rated_cop=3.0)
    wc = ChillerSpec(kind="water_cooled", rated_cop=6.1, cond_approach_k=7.5, min_condensing_c=20.0)
    assert ac.full_load_cop(6.7, 35.0) == pytest.approx(3.0, rel=1e-9)
    assert wc.full_load_cop(6.7, 29.4) == pytest.approx(6.1, rel=1e-9)


def test_cop_falls_with_condenser_temperature_and_rises_with_chw():
    ch = ChillerSpec()
    cops = ch.full_load_cop(7.0, np.array([15.0, 25.0, 35.0, 45.0]))
    assert np.all(np.diff(cops) <= 0)
    assert ch.full_load_cop(14.0, 35.0) > ch.full_load_cop(7.0, 35.0)
    assert np.all(ch.full_load_cop(7.0, np.array([-20.0, 0.0])) <= ch.max_cop)


def test_lower_cop_means_more_electricity_for_same_duty():
    q = np.full(10, 2e6)
    hi = chiller_plant(q, 7.0, 30.0, ChillerSpec(rated_cop=3.5), 1e6, 4)
    lo = chiller_plant(q, 7.0, 30.0, ChillerSpec(rated_cop=2.5), 1e6, 4)
    assert np.all(lo.power_w > hi.power_w)
    assert np.all(hi.power_w > 0)


def test_staging_capacity_and_unmet_load():
    r = chiller_plant(np.array([0.0, 0.5e6, 3.5e6, 6e6]), 7.0, 35.0, ChillerSpec(), 1e6, 4)
    assert r.power_w[0] == 0 and r.units_running[0] == 0
    assert r.unmet_w[2] == pytest.approx(0.0, abs=1.0)
    assert r.unmet_w[3] == pytest.approx(2e6, rel=1e-6)
    failed = chiller_plant(np.array([3.5e6]), 7.0, 35.0, ChillerSpec(), 1e6, 4, n_units_available=3)
    assert failed.unmet_w[0] > 0


def test_constant_cop_option_is_constant():
    ch = ChillerSpec(model="constant", rated_cop=4.0)
    assert np.allclose(ch.full_load_cop(7.0, np.array([0.0, 20.0, 40.0])), 4.0)


def test_fan_affinity_and_redundant_sharing():
    fs = FanSystem(design_flow_m3s_per_unit=10.0, n_units_installed=5, dp_pa=400, efficiency=0.6, run_redundant=True)
    p_all, n_all, _ = fs.power(np.array([40.0]))
    fs.run_redundant = False
    p_min, n_min, _ = fs.power(np.array([40.0]))
    assert n_all[0] == 5 and n_min[0] == 4
    assert p_all[0] < p_min[0]  # more units at lower speed use less power
    fs.run_redundant = True
    p_half, *_ = fs.power(np.array([20.0]))
    assert p_half[0] < p_all[0] / 4  # shared units at half the flow: affinity law (exponent 2.7)


def test_pump_and_ups_models():
    pump = PumpSpec()
    assert pump.power(np.array([0.0]), 1e6, 6.0)[0] == 0
    assert pump.power(np.array([1e6]), 1e6, 6.0)[0] == pytest.approx(1e6 / (998.2 * 4186 * 6) * 250000 / 0.7, rel=1e-6)
    ups = UPSModel(efficiency_points=[(0.25, 0.93), (0.5, 0.955), (0.75, 0.96), (1.0, 0.96)], rated_kva_factor=1.0)
    loss = ups.losses(np.array([0.5e6]), 1e6)[0]
    assert loss == pytest.approx(0.5e6 * (1 / 0.955 - 1), rel=0.03)


def test_dec_effectiveness_one_reaches_wet_bulb_and_is_isenthalpic():
    p = 101325.0
    w = ps.humidity_ratio_from_rh(35.0, 20.0, p)
    r = evap.direct_evaporative(35.0, w, p, 10.0, evap.DECParams(saturation_effectiveness=1.0))
    assert float(r["t_out_c"]) == pytest.approx(float(r["t_wb_c"]), abs=1e-9)
    assert ps.enthalpy(r["t_out_c"], r["w_out"]) == pytest.approx(ps.enthalpy(35.0, w), rel=1e-9)
    assert float(r["evaporation_kg_s"]) == pytest.approx(10.0 * (float(r["w_out"]) - w), rel=1e-9)


def test_dec_water_increases_with_evaporation():
    p = 101325.0
    w = ps.humidity_ratio_from_rh(35.0, 20.0, p)
    lo = evap.direct_evaporative(35.0, w, p, 10.0, evap.DECParams(saturation_effectiveness=0.5))
    hi = evap.direct_evaporative(35.0, w, p, 10.0, evap.DECParams(saturation_effectiveness=0.9))
    assert float(hi["evaporation_kg_s"]) > float(lo["evaporation_kg_s"]) > 0


def test_iec_dry_mode_uses_no_water_and_wet_mode_balances_heat():
    p = 101325.0
    w = ps.humidity_ratio_from_rh(35.0, 20.0, p)
    dry = evap.indirect_evaporative(38.0, 0.009, 20.0, w, p, 10.0, evap.IECParams(), wet=False)
    wet = evap.indirect_evaporative(38.0, 0.009, 35.0, w, p, 10.0, evap.IECParams(), wet=True)
    assert float(dry["evaporation_kg_s"]) == 0.0
    assert float(wet["w_out"]) == pytest.approx(0.009)  # product air not humidified
    # evaporation carries at least the transferred heat as latent heat when the secondary air leaves warmer
    assert float(wet["evaporation_kg_s"]) * 2.45e6 > 0.8 * float(wet["q_w"])


def test_cooling_tower_water_balance():
    p = 101325.0
    w = ps.humidity_ratio_from_rh(30.0, 40.0, p)
    r = evap.cooling_tower(1e6, 30.0, w, p, 1e6, evap.TowerParams())
    l_per_kwh = float(r["evaporation_kg_s"]) * 3600 / 1000.0
    assert 1.0 < l_per_kwh < 2.0  # ≈ latent-heat bound 3.6 MJ/kWh ÷ 2.45 MJ/kg = 1.47 kg/kWh
    assert float(r["makeup_kg_s"]) == pytest.approx(float(r["evaporation_kg_s"] + r["blowdown_kg_s"] + r["drift_kg_s"]))
    assert float(r["consumption_kg_s"]) < float(r["makeup_kg_s"])  # blowdown withdrawn, not consumed
    assert float(r["t_leaving_water_c"]) > float(r["t_wb_c"])
    zero = evap.cooling_tower(0.0, 30.0, w, p, 1e6, evap.TowerParams())
    assert float(zero["makeup_kg_s"]) == 0.0
