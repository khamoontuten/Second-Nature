"""Strategy impact analysis (app.impacts): baseline-relative metrics, individual / combined / pairwise evaluation,
cost and space take-offs, status labels and the impact-study API.

The assertions check relationships that must hold for any physically consistent model (self-comparison is zero,
shared items counted once, carbon ∝ energy × factor, locality of inputs, determinism), not particular savings.
"""

from __future__ import annotations

import io
import json
import time

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.demo import illustrative_unit_rates
from app.impacts import catalogue
from app.impacts.engine import Evaluator, run_study
from app.impacts.metrics import compare, interaction, metric_values, pct_change
from app.schemas.config import ProjectConfig
from app.simulation.engine import run_simulation
from app.simulation.scenarios import baseline_config, derive

ASHBURN = dict(name="ashburn", latitude=38.98, longitude=-77.47, elevation_m=82.0, timezone="America/New_York",
               site_area_m2=60000.0, land_available_for_buried_m2=12000.0)


def cfg_with(**over) -> ProjectConfig:
    d = {"site": dict(ASHBURN), "grid": {"emission_factor_kg_per_kwh": 0.35, "source": "test assumption"},
         "economics": {"electricity_price_per_kwh": 0.08, "water_price_per_m3": 2.0, "unit_rates": illustrative_unit_rates()}}
    for k, v in over.items():
        d[k] = {**d[k], **v} if isinstance(v, dict) and isinstance(d.get(k), dict) else v
    return ProjectConfig(**d)


@pytest.fixture(scope="module")
def ds(datasets):
    return datasets("ashburn")


@pytest.fixture(scope="module")
def evaluator(ds):
    return Evaluator(cfg_with(), ds)


@pytest.fixture(scope="module")
def base(evaluator):
    return evaluator.evaluate([], sid="baseline")


def rows(base_ev, ev):
    return {r["key"]: r for r in compare(base_ev, ev, "USD")}


# 1 ------------------------------------------------------------------------------------------------------------
def test_baseline_compared_with_itself_is_zero(base):
    for r in compare(base, base, "USD"):
        if r["baseline"] not in (None, 0):
            assert r["delta"] == 0.0, r["key"]
            if r.get("pct") is not None:
                assert r["pct"] == 0.0, r["key"]
            assert r["direction"] == "no change"


# 2, 13 --------------------------------------------------------------------------------------------------------
def test_each_strategy_runs_alone_with_identical_period_and_units(ds):
    study = run_study(cfg_with(), ds, "individual")
    app = study["applicability"]
    expected = [m.id for m in catalogue.MEASURES if app[m.id]["applicable"]]
    assert [e["requested"][0] for e in study["individual"]] == expected
    b = study["baseline"]
    units = {r["key"]: r["unit"] for r in b["metrics"]}
    for e in study["individual"]:
        assert e["simulated"], e["id"]
        # only the requested strategy and its declared dependencies are switched on
        assert set(e["active"]) == set(e["resolved"]), e["id"]
        assert set(e["resolved"]) == {e["requested"][0], *e["auto_added"]}
        assert e["summary"]["period"] == b["summary"]["period"] and e["summary"]["period"]["hours"] == 8760
        assert {r["key"]: r["unit"] for r in e["metrics"]} == units
        assert all(r["status"] in ("modelled", "derived", "estimated", "insufficient_data", "not_applicable", "infeasible", "not_modelled")
                   for r in e["metrics"])
    # declared dependency is resolved and disclosed
    stack = next(e for e in study["individual"] if e["id"] == "stack")
    assert stack["auto_added"] == ["containment_full"]


# 3 ------------------------------------------------------------------------------------------------------------
def test_combination_is_simulated_not_summed(ds, monkeypatch):
    import app.impacts.engine as eng

    seen = []

    def spy(cfg, ds_, **kw):
        seen.append({k for k, v in cfg.strategies.model_dump().items() if isinstance(v, dict) and v.get("enabled")})
        return run_simulation(cfg, ds_, **kw)

    monkeypatch.setattr(eng, "run_simulation", spy)
    cfg = cfg_with()
    study = run_study(cfg, ds, "combination", ["stack", "iec"])
    assert {"stack", "evaporative"} in seen, "the combined configuration must itself be simulated"
    combo = study["combination"]
    direct = run_simulation(derive(baseline_config(cfg), catalogue.patch_for(combo["resolved"])), ds).summary
    assert combo["summary"]["energy_mwh"]["facility"] == pytest.approx(direct["energy_mwh"]["facility"], rel=1e-12)
    fac = next(r for r in study["attribution"]["rows"] if r["key"] == "facility_mwh")
    assert fac["combined"] == pytest.approx(direct["energy_mwh"]["facility"] - study["baseline"]["summary"]["energy_mwh"]["facility"])
    assert fac["non_additive"] == pytest.approx(fac["combined"] - fac["sum_individual"])
    assert abs(fac["non_additive"]) > 1.0   # these two strategies overlap in operating hours: not additive


# 4 ------------------------------------------------------------------------------------------------------------
def test_shared_infrastructure_counted_once(evaluator, base):
    singles = {s: evaluator.evaluate([s]) for s in ("economiser_direct", "eahe", "radiative")}
    combo = evaluator.evaluate(["economiser_direct", "eahe", "radiative"])

    def line(ev, lid):
        return next(ln for ln in ev["capex"]["lines"] if ln["id"] == lid)

    oa = [line(e, "oa_intake")["quantity"] for e in singles.values()]
    assert line(combo, "oa_intake")["quantity"] == pytest.approx(max(oa))
    assert line(combo, "oa_intake")["quantity"] < sum(oa)
    assert line(combo, "supervisory_controls")["quantity"] == 1.0
    assert sum(line(e, "supervisory_controls")["quantity"] for e in singles.values()) == 3.0
    rate = cfg_with().economics.unit_rates["supervisory_controls"].base
    from app.impacts.costing import incremental

    inc = incremental(base["capex"], combo["capex"])
    sup = next(r for r in inc["lines"] if r["id"] == "supervisory_controls")
    assert sup["delta_cost"]["base"] == pytest.approx(rate)
    plenum = [i for i in combo["space"]["categories"]["mep_indoor"]["items"] if i["label"].startswith("Outdoor-air intake")]
    assert len(plenum) == 1
    assert plenum[0]["base"] == pytest.approx(max(oa) * cfg_with().layout.oa_intake_m2_per_m3s)


# 5 ------------------------------------------------------------------------------------------------------------
def test_invalid_combinations_are_reported(evaluator, ds):
    ev = evaluator.evaluate(["economiser_direct", "economiser_indirect"])
    assert ev["feasible"] is False and ev["simulated"] is False
    assert "cannot be combined" in ev["infeasible_reasons"][0]
    assert all(v["status"] == "infeasible" and v["value"] is None for v in metric_values(ev).values())
    dx = Evaluator(cfg_with(baseline={"architecture": "dx_crac"}), ds)
    st = dx.evaluate(["storage"])
    assert st["feasible"] is False and any("chilled-water" in r for r in st["infeasible_reasons"])
    with pytest.raises(catalogue.SelectionError):
        catalogue.select(["not_a_strategy"])
    with pytest.raises(catalogue.SelectionError):
        catalogue.select(["stack"], {"strategies.stack.chimney_height_m": 1000})


# 6 ------------------------------------------------------------------------------------------------------------
def test_missing_inputs_never_become_zero(ds):
    cfg = cfg_with(grid={"emission_factor_kg_per_kwh": None, "source": "not provided"},
                   economics={"unit_rates": {}, "electricity_price_per_kwh": None})
    e = Evaluator(cfg, ds)
    b = e.evaluate([])
    s = e.evaluate(["eahe"])
    r = rows(b, s)
    assert r["co2e_t"]["value"] is None and r["co2e_t"]["status"] == "insufficient_data"
    assert r["capex"]["value"] is None and r["capex"]["delta"] is None and r["capex"]["status"] == "insufficient_data"
    assert r["opex_electricity"]["value"] is None and r["opex_electricity"]["status"] == "insufficient_data"
    assert "eahe_pipe" in s["capex"]["missing_rates"]
    assert r["natural_sufficient_h"]["status"] == "not_applicable" and r["natural_sufficient_h"]["value"] is None


# 7 ------------------------------------------------------------------------------------------------------------
def test_zero_and_near_zero_baselines_are_safe(evaluator, base):
    assert pct_change(5.0, 0.0) == (None, "baseline is zero — percentage undefined")
    assert pct_change(None, 3.0)[0] is None
    assert pct_change(110.0, 100.0) == (pytest.approx(10.0), None)
    r = rows(base, evaluator.evaluate(["eahe"]))
    assert r["underground_m2"]["baseline"] == 0.0 and r["underground_m2"]["value"] > 0
    assert r["underground_m2"]["pct"] is None and "zero" in r["underground_m2"]["pct_note"]
    w = rows(base, evaluator.evaluate(["iec"]))["water_withdrawal_m3"]
    assert w["delta"] > 0
    if w["baseline"] < 0.005 * base["summary"]["energy_mwh"]["it"]:
        assert w["pct"] is None and "materiality" in w["pct_note"]


# 8 ------------------------------------------------------------------------------------------------------------
def test_carbon_scales_with_electricity_and_grid_factor(ds):
    out = {}
    for f in (0.2, 0.4):
        e = Evaluator(cfg_with(grid={"emission_factor_kg_per_kwh": f}), ds)
        ev = e.evaluate(["stack"])
        out[f] = ev
        assert ev["summary"]["carbon"]["operational_t"] == pytest.approx(ev["summary"]["energy_mwh"]["facility"] * f, rel=1e-9)
    assert out[0.4]["summary"]["carbon"]["operational_t"] == pytest.approx(2 * out[0.2]["summary"]["carbon"]["operational_t"], rel=1e-9)


# 9 ------------------------------------------------------------------------------------------------------------
def test_energy_boundary_has_no_double_counting(evaluator, base):
    for sel in ([], ["stack"], ["iec", "eahe"]):
        ev = evaluator.evaluate(sel)
        e = ev["summary"]["energy_mwh"]
        assert sum(e["components"].values()) == pytest.approx(e["facility"], rel=1e-9)
        v = metric_values(ev)
        assert v["it_mwh"]["value"] + v["cooling_mwh"]["value"] + v["other_mwh"]["value"] == pytest.approx(v["facility_mwh"]["value"])
        assert v["it_mwh"]["value"] == pytest.approx(base["summary"]["energy_mwh"]["it"], rel=1e-12)   # same workload


# 10 -----------------------------------------------------------------------------------------------------------
def test_cost_and_space_use_the_common_baseline(ds):
    study = run_study(cfg_with(), ds, "combination", ["eahe", "radiative"])
    b = {r["key"]: r for r in study["baseline"]["metrics"]}
    base_capex = study["baseline"]["capex"]["total"]["base"]
    for e in study["individual"] + [study["combination"]]:
        r = {x["key"]: x for x in e["metrics"]}
        for k in ("site_at_grade_m2", "underground_m2", "facility_mwh", "water_withdrawal_m3"):
            assert r[k]["baseline"] == b[k]["value"]
        assert r["capex"]["baseline"] == pytest.approx(base_capex)
        assert r["capex"]["pct"] == pytest.approx(100 * r["capex"]["delta"] / base_capex)
        lo, hi = r["capex"]["delta_range"]
        assert lo <= r["capex"]["delta"] <= hi


# 11 -----------------------------------------------------------------------------------------------------------
def test_deterministic_repeat(ds):
    a = Evaluator(cfg_with(), ds).evaluate(["stack", "radiative"])
    b = Evaluator(cfg_with(), ds).evaluate(["stack", "radiative"])
    assert a["inputs_hash"] == b["inputs_hash"]
    assert metric_values(a) == metric_values(b)


# 12 -----------------------------------------------------------------------------------------------------------
def test_parameter_change_is_local(ds):
    s1 = run_study(cfg_with(), ds, "individual", ["stack", "eahe"])
    s2 = run_study(cfg_with(), ds, "individual", ["stack", "eahe"], {"strategies.eahe.length_m": 120.0})
    mv = lambda ev: {k: v["value"] for k, v in metric_values(ev).items()}  # noqa: E731
    assert mv(s1["baseline"]) == mv(s2["baseline"])
    assert mv(s1["individual"][0]) == mv(s2["individual"][0])          # stack untouched by an EAHE parameter
    e1, e2 = s1["individual"][1], s2["individual"][1]
    assert e2["parameters"] == {"strategies.eahe.length_m": 120.0}
    assert mv(e1)["facility_mwh"] != mv(e2)["facility_mwh"]
    assert mv(e2)["underground_m2"] == pytest.approx(2 * mv(e1)["underground_m2"])   # 60 m → 120 m pipes


def test_interaction_classification():
    assert interaction(-100, -50, -150, 1.0)["class"] == "additive"
    assert interaction(-100, -50, -200, 1.0)["class"] == "complementary"
    assert interaction(-100, -50, -120, 1.0)["class"] == "overlapping"
    assert interaction(-100, -50, -60, 1.0)["class"] == "conflicting"
    i = interaction(-100, -50, -120, 1.0)
    assert i["interaction"] == pytest.approx(30.0) and i["sum_individual"] == -150
    assert interaction(None, -50, -60, 1.0)["class"] == "not evaluated"


def test_payback_requires_maintenance_and_rates(ds):
    no_maint = Evaluator(cfg_with(), ds)
    b = no_maint.evaluate([])
    s = no_maint.evaluate(["eahe"])
    from app.impacts.costing import economics, incremental

    inc = incremental(b["capex"], s["capex"])
    eco = economics(cfg_with(), inc, b["opex"], s["opex"], b["capex"], s["capex"])
    assert eco["simple_payback_years"] is None and "maintenance" in eco["note"]
    cfg = cfg_with(economics={"maintenance_per_year": {"eahe": {"value": 5000.0, "source": "assumed"}}})
    e2 = Evaluator(cfg, ds)
    b2, s2 = e2.evaluate([]), e2.evaluate(["eahe"])
    inc2 = incremental(b2["capex"], s2["capex"])
    eco2 = economics(cfg, inc2, b2["opex"], s2["opex"], b2["capex"], s2["capex"])
    saving = eco2["annual_opex_baseline"] - eco2["annual_opex_scenario"]
    assert saving == pytest.approx(eco2["annual_energy_water_saving"] - 5000.0)
    if saving > 0:
        assert eco2["simple_payback_years"] == pytest.approx(inc2["delta"]["base"] / saving)
    assert eco2["npv"] is not None


# API ----------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def _wait(client, job_id, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("succeeded", "failed", "cancelled"):
            return j
        time.sleep(0.3)
    raise AssertionError("job timeout")


def test_impact_study_api_and_exports(client):
    from app.demo import create_demo

    demo = create_demo("ashburn_va", run=False)
    pid = demo["project_id"]
    cat = client.get("/api/impacts/catalogue", params={"project_id": pid}).json()
    assert {s["id"] for s in cat["strategies"]} == {m.id for m in catalogue.MEASURES}
    assert cat["applicability"]["stack"]["applicable"] is True
    bl = client.get(f"/api/projects/{pid}/baseline").json()
    assert bl["it"]["installed_capacity_mw"] == 5.0 and bl["assumptions"]
    bad = client.post("/api/impact-studies", json={"project_id": pid, "kind": "combination", "strategies": ["stack", "warp_drive"]})
    assert bad.status_code == 422
    r = client.post("/api/impact-studies", json={"project_id": pid, "kind": "combination", "strategies": ["stack", "radiative"],
                                                 "parameters": {"strategies.stack.chimney_height_m": 30}})
    assert r.status_code == 202, r.text
    sid = r.json()["impact_study_id"]
    assert _wait(client, r.json()["job_id"])["status"] == "succeeded"
    st = client.get(f"/api/impact-studies/{sid}").json()
    res = st["result"]
    assert res["combination"]["parameters"] == {"strategies.stack.chimney_height_m": 30}
    assert res["combination"]["resolved"] == ["containment_full", "stack", "radiative"]
    fac = next(m for m in res["combination"]["metrics"] if m["key"] == "facility_mwh")
    assert fac["status"] == "modelled" and fac["delta"] < 0
    assert client.get(f"/api/projects/{pid}/impact-studies").json()[0]["id"] == sid
    csv = client.get(f"/api/impact-studies/{sid}/export", params={"format": "csv"})
    assert csv.status_code == 200 and "status" in csv.text.splitlines()[0]
    line = next(ln for ln in csv.text.splitlines() if ln.startswith("combination,") and ",facility_mwh," in ln)
    assert f"{fac['value']}" in line
    xl = client.get(f"/api/impact-studies/{sid}/export", params={"format": "xlsx"})
    wb = openpyxl.load_workbook(io.BytesIO(xl.content))
    assert {"Summary", "Metrics (long)", "Comparison (wide)", "CAPEX changes", "Space", "Attribution", "Assumptions"} <= set(wb.sheetnames)
    js = json.loads(client.get(f"/api/impact-studies/{sid}/export", params={"format": "json"}).content)
    assert js["result"]["combination"]["id"] == "combination" and js["limitations"]
