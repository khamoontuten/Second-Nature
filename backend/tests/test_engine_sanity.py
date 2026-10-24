"""Facility-level physical sanity tests (§22.2) on real reference weather."""

import numpy as np
import pytest

from app.cooling.plant import evaluate_mechanical, size_plant
from app.simulation.engine import run_simulation
from app.simulation.scenarios import ALL_OFF, derive, improved_patch, incremental_benefits

POWER_COLS = lambda h: [c for c in h.columns if c.startswith("p_") and c.endswith("_w")]  # noqa: E731


@pytest.fixture(scope="module")
def ashburn_runs(datasets):
    from tests.conftest import site_config

    ds = datasets("ashburn")
    base = site_config("ashburn")
    hybrid = site_config("ashburn", it={"containment": "full"},
                         strategies={"economiser": {"enabled": True}, "stack": {"enabled": True}, "eahe": {"enabled": True}})
    return ds, run_simulation(base, ds), run_simulation(hybrid, ds), base, hybrid


def test_energy_balance_closes(ashburn_runs):
    _, b, h, *_ = ashburn_runs
    for r in (b, h):
        c = r.summary["heat_mwh"]["closure"]
        assert c["passed"], c
        assert abs(c["annual_residual_kwh"]) < 1e-3 * r.summary["energy_mwh"]["facility"] * 1000


def test_no_negative_electricity_or_water(ashburn_runs):
    _, b, h, *_ = ashburn_runs
    for r in (b, h):
        hh = r.hourly
        for c in POWER_COLS(hh):
            assert (hh[c] >= -1e-6).all(), c
        assert (hh["wl_withdrawal"] >= 0).all()
        assert (hh["wl_consumption"] <= hh["wl_withdrawal"] + 1e-9).all()


def test_no_thermal_violations_and_inlet_within_envelope(ashburn_runs):
    _, b, h, *_ = ashburn_runs
    for r in (b, h):
        t = r.summary["thermal"]
        assert t["unmet_hours"] == 0 and t["inlet_envelope_violation_hours"] == 0
        env = r.diagnostics["envelope_limits"]
        assert r.hourly["t_inlet_c"].max() <= env["t_max_c"] + 1e-6
        assert r.hourly["t_inlet_c"].min() >= env["t_min_c"] - 1e-6
        assert r.hourly["dp_inlet_c"].max() <= env["dp_max_c"] + 0.1


def test_identical_it_workload_and_capacity(ashburn_runs):
    _, b, h, *_ = ashburn_runs
    assert np.allclose(b.hourly["p_it_w"], h.hourly["p_it_w"])
    assert b.diagnostics["sizing"]["n_installed"] == h.diagnostics["sizing"]["n_installed"]


def test_pue_definition_is_ratio_of_energies(ashburn_runs):
    _, b, *_ = ashburn_runs
    hh = b.hourly
    assert b.summary["pue"]["annual"] == pytest.approx(hh["p_facility_w"].sum() / hh["p_it_w"].sum())
    assert b.summary["pue"]["mean_of_hourly"] == pytest.approx((hh["p_facility_w"] / hh["p_it_w"]).mean())


def test_hybrid_saves_energy_without_extra_it_or_reliability(ashburn_runs):
    _, b, h, *_ = ashburn_runs
    assert h.summary["energy_mwh"]["cooling"] < b.summary["energy_mwh"]["cooling"]
    assert h.summary["energy_mwh"]["it"] == pytest.approx(b.summary["energy_mwh"]["it"])
    assert h.diagnostics["failure"]["n_minus_1"]["passed"]


def test_dry_configuration_uses_no_water(datasets, make_config):
    cfg = make_config("phoenix", baseline={"humidification": "none"})
    r = run_simulation(cfg, datasets("phoenix"))
    assert r.summary["water"]["withdrawal_m3"] == pytest.approx(0.0, abs=1e-9)


def test_evaporative_water_scales_with_effectiveness(datasets, make_config):
    ds = datasets("phoenix")
    lo = run_simulation(make_config("phoenix", baseline={"architecture": "indirect_evaporative", "iec_wet_bulb_effectiveness": 0.5}), ds)
    hi = run_simulation(make_config("phoenix", baseline={"architecture": "indirect_evaporative", "iec_wet_bulb_effectiveness": 0.8}), ds)
    assert hi.summary["water"]["components_m3"]["wl_evap_media"] > lo.summary["water"]["components_m3"]["wl_evap_media"] > 0


def test_lower_cop_increases_cooling_electricity(datasets, make_config):
    ds = datasets("ashburn")
    good = run_simulation(make_config("ashburn", baseline={"rated_cop": 3.5}), ds)
    poor = run_simulation(make_config("ashburn", baseline={"rated_cop": 2.5}), ds)
    assert poor.summary["energy_mwh"]["components"]["p_chiller"] > good.summary["energy_mwh"]["components"]["p_chiller"]


def test_adding_an_economiser_never_increases_cooling_energy(datasets, make_config):
    ds = datasets("chicago")
    off = run_simulation(make_config("chicago"), ds)
    on = run_simulation(make_config("chicago", strategies={"economiser": {"enabled": True}}), ds)
    assert on.summary["energy_mwh"]["cooling"] <= off.summary["energy_mwh"]["cooling"] * 1.001


def test_cold_dry_site_has_more_free_cooling_than_hot_humid_site(datasets, make_config):
    chicago = run_simulation(make_config("chicago", strategies={"economiser": {"enabled": True}}), datasets("chicago"))
    miami = run_simulation(make_config("miami", strategies={"economiser": {"enabled": True}}), datasets("miami"))
    assert chicago.summary["free_cooling"]["full_hours"] > 2 * miami.summary["free_cooling"]["full_hours"]


def test_baseline_pue_in_realistic_range(ashburn_runs):
    _, b, *_ = ashburn_runs
    assert 1.15 < b.summary["pue"]["annual"] < 1.8


def test_water_cooled_wue_near_industry_magnitude(datasets, make_config):
    r = run_simulation(make_config("ashburn", baseline={"architecture": "water_cooled_chiller_tower"}), datasets("ashburn"))
    assert 0.8 < r.summary["water"]["wue_withdrawal_l_per_kwh"] < 3.0


def test_eahe_outlet_between_air_and_ground_when_running(ashburn_runs):
    _, _, h, *_ = ashburn_runs
    hh = h.hourly
    on = hh["eahe_flow_m3s"] > 0
    if on.any():
        t_out_nofan = hh.loc[on, "eahe_t_out_c"] - 1.0  # fan heat ≤ ~1 K
        lo = np.minimum(hh.loc[on, "t_oa_c"], hh.loc[on, "t_ground_c"]) - 1.5
        hi = np.maximum(hh.loc[on, "t_oa_c"], hh.loc[on, "t_ground_c"]) + 1.5
        assert ((t_out_nofan >= lo) & (t_out_nofan <= hi)).all()


def test_cool_roof_and_shell_are_not_double_counted(datasets, make_config):
    """Shell strategy with the *same* roof optics as the baseline roof must not add a second solar reduction."""
    ds = datasets("phoenix")
    a = run_simulation(make_config("phoenix", building={"roof_solar_reflectance": 0.85}), ds)
    b = run_simulation(make_config("phoenix", building={"roof_solar_reflectance": 0.85},
                                   strategies={"envelope": {"enabled": True, "roof_solar_reflectance": 0.85}}), ds)
    # Absorbed roof solar identical (single source of roof flux); only conduction differs.
    abs_a = a.summary["heat_mwh"]["envelope_net_gain"]
    abs_b = b.summary["heat_mwh"]["envelope_net_gain"]
    assert abs(abs_a - abs_b) < 0.5 * abs(abs_a) + 50.0


def test_mechanical_capacity_respects_n_plus_1(ashburn_runs):
    ds, b, _, cfg, _ = ashburn_runs
    assert b.diagnostics["failure"]["n_minus_1"]["passed"]
    assert b.diagnostics["sizing"]["n_installed"] == b.diagnostics["sizing"]["n_duty"] + 1


def test_unmet_load_reported_when_plant_too_small(datasets, make_config):
    from app.simulation.context import build_context

    cfg = make_config("phoenix")
    ds = datasets("phoenix")
    ctx = build_context(ds, lat=cfg.site.latitude, lon=cfg.site.longitude, elevation_m=cfg.site.elevation_m)
    sz = size_plant(cfg, 1e6, 100.0, 0.0)
    res = evaluate_mechanical(sz, cfg, ctx, np.full(ctx.n, 5e6))
    assert (res["unmet_w"] > 0).all()


def test_reproducible_results(datasets, make_config):
    cfg = make_config("ashburn", strategies={"economiser": {"enabled": True}})
    ds = datasets("ashburn")
    r1, r2 = run_simulation(cfg, ds), run_simulation(cfg, ds)
    assert r1.inputs_hash == r2.inputs_hash
    assert np.array_equal(r1.hourly["p_facility_w"].to_numpy(), r2.hourly["p_facility_w"].to_numpy())


@pytest.mark.slow
def test_incremental_benefits_report_interaction(datasets, make_config):
    cfg = make_config("ashburn")
    base = derive(cfg, {"strategies": ALL_OFF})
    s1 = derive(base, improved_patch(base))
    inc = incremental_benefits(cfg, datasets("ashburn"), ["economiser_indirect", "stack"], s1)
    assert inc["combined_saving_mwh"] > 0
    # the combined design must not claim more than the sum of individual savings by a large margin (no double counting)
    assert inc["combined_saving_mwh"] <= sum(inc["add_one_saving_mwh"].values()) * 1.25 + 1.0
