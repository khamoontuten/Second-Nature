import numpy as np
import pytest

from app.optimization.search import Variable, optimise, pareto_front
from app.recommendation.engine import recommend
from app.simulation.scenarios import ALL_OFF, derive, improved_patch, objective
from app.uncertainty.analysis import UncertainInput, monte_carlo, rank_stability, tornado


def test_pareto_front_basic():
    pts = np.array([[1, 5], [2, 2], [3, 1], [3, 3], [4, 4]])
    assert pareto_front(pts) == [0, 1, 2]


def test_variable_validation():
    from app.core.errors import ValidationFailure

    with pytest.raises(ValidationFailure):
        Variable("strategies.stack.chimney_height_m")
    with pytest.raises(ValidationFailure):
        Variable("x", low=2, high=1)


def test_grid_optimisation_respects_water_constraint(datasets, make_config):
    ds = datasets("phoenix")
    cfg = make_config("phoenix")
    base = derive(cfg, {"strategies": ALL_OFF})
    s1 = derive(base, improved_patch(base))
    res = optimise(s1, ds, [Variable("strategies.evaporative.enabled", values=[False, True]),
                            Variable("strategies.evaporative.kind", values=["indirect"])], water_cap_m3=100.0, budget=4)
    wet = [e for e in res.evaluations if e.params["strategies.evaporative.enabled"]]
    dry = [e for e in res.evaluations if not e.params["strategies.evaporative.enabled"]]
    assert wet and not wet[0].feasible and any("water" in r for r in wet[0].reasons)
    assert res.best is not None and res.best in dry  # never trades the water limit for energy


def test_objective_marks_thermal_violation_infeasible():
    ok = {"thermal": {"unmet_hours": 0, "inlet_envelope_violation_hours": 0}, "energy_mwh": {"facility": 90, "it": 80},
          "water": {"withdrawal_m3": 0}, "carbon": {"operational_t": 9}}
    bad = {**ok, "thermal": {"unmet_hours": 3, "inlet_envelope_violation_hours": 0}}
    base = {**ok, "energy_mwh": {"facility": 100, "it": 80}, "carbon": {"operational_t": 10}}
    w = {"energy": 0.5, "water": 0.2, "carbon": 0.3}
    assert objective(ok, base, w) > 0
    assert objective(bad, base, w) == float("-inf")


@pytest.mark.slow
def test_tornado_and_monte_carlo(datasets, make_config):
    ds = datasets("ashburn")
    cfg = make_config("ashburn")
    base = derive(cfg, {"strategies": ALL_OFF})
    design = derive(derive(base, improved_patch(base)), {"strategies": {"economiser": {"enabled": True}}})
    inputs = [UncertainInput("it.average_utilisation", 0.5, 0.65, 0.8, category="operational", label="util"),
              UncertainInput("baseline.rated_cop", 2.6, 3.0, 3.4, category="physical", label="cop")]
    tor = tornado(base, design, ds, inputs)
    assert tor["central_saving"] > 0
    util = next(r for r in tor["rows"] if r["input"] == "it.average_utilisation")
    assert util["high_design"] > util["low_design"]  # more IT load → more facility energy
    mc = monte_carlo(base, design, ds, inputs, n=5, n_category=2)
    s = mc["metrics"]["facility_mwh"]["saving"]
    assert s["p5"] <= s["p50"] <= s["p95"]
    assert set(mc["by_category"]) == {"operational", "physical"}
    rs = rank_stability({"baseline": base, "design": design}, ds, inputs, n=3)
    assert abs(sum(rs["probability_best"].values()) - 1.0) < 1e-9


@pytest.mark.slow
def test_recommendation_is_data_driven_and_flags_infeasible(datasets, make_config):
    ds = datasets("miami")
    cfg = make_config("miami", site={"name": "miami", "latitude": 25.82, "longitude": -80.30, "elevation_m": 11.0,
                                     "timezone": "America/New_York", "constraints": {"permitted_annual_withdrawal_m3": 0.0}})
    rec = recommend(cfg, ds, candidates=["stack", "economiser_indirect", "iec", "envelope"])
    assert not rec["candidates"]["iec"]["feasible"]          # water limit zero → evaporative infeasible
    assert rec["ranking"]
    top = rec["candidates"][rec["ranking"][0]]
    assert top["feasible"] and "climate_evidence" in top["explanation"]
    assert all(set(r["explanation"]) >= {"why_suitable", "required_studies", "uncertainty"} for r in rec["candidates"].values()
               if r["feasible"])
