"""Design scenarios 0–8 on identical climate, IT load, thermal constraints and reliability.

* 0 Conventional baseline — the configured conventional architecture, no additional strategies.
* 1 Improved conventional — full containment, warmer chilled water, water-side and/or air-side
     economisation where the architecture allows.
* 2 Passive envelope — layered shell envelope, fixed shading, cool roof, thermal mass.
* 3 Stack-assisted ventilation — termite-mound stacks with fan assist and mechanical backup.
* 4 Earth-air heat exchanger — burrow-inspired buried intake with mechanical backup.
* 5 Adaptive facade — kinetic fish-scale louvres with controlled ventilation openings.
* 6 Low-water — dry heat rejection, economisers, no evaporative water.
* 7 Biomimetic hybrid — climate-specific combination found by greedy forward selection from the
     simulation results (strategies are added only while they improve the objective by more than a
     threshold, and incompatible combinations are skipped).
* 8 User-defined — the project's own strategy settings.

The same mechanical plant (capacity, redundancy) is sized for every scenario by the same rule, so
no scenario gains an advantage from serving less load or providing less reliability.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from app.accounting.lifecycle import lifecycle
from app.climate.providers.base import ClimateDataset
from app.schemas.config import ProjectConfig
from app.simulation.engine import SimulationResult, run_simulation
from app.strategies.library import compatibility_issues

CHILLED_WATER = {"air_cooled_chiller_crah", "water_cooled_chiller_tower", "liquid_cooling_dry_cooler"}

SCENARIO_META = {
    "S0": ("Conventional baseline", "Configured conventional reference plant; no additional strategies."),
    "S1": ("Improved conventional", "Full containment, warmer chilled water, conventional economisation where feasible."),
    "S2": ("Passive envelope", "Layered shell envelope, fixed shading, cool roof and thermal mass."),
    "S3": ("Stack-assisted ventilation", "Termite-mound-inspired stacks with fan assist and mechanical backup."),
    "S4": ("Earth-air heat exchanger", "Prairie-dog-burrow-inspired buried intake with mechanical backup."),
    "S5": ("Adaptive facade", "Fish-scale kinetic louvres with controlled ventilation openings (actuator energy included)."),
    "S6": ("Low-water cooling", "Dry heat rejection, economisers and no evaporative water use."),
    "S7": ("Biomimetic hybrid", "Climate-specific combination selected from simulation results (greedy forward selection)."),
    "S8": ("User-defined", "Strategies exactly as configured by the engineer."),
}

ALL_OFF = {"stack": {"enabled": False}, "envelope": {"enabled": False}, "facade": {"enabled": False},
           "eahe": {"enabled": False}, "storage": {"enabled": False}, "radiative": {"enabled": False},
           "evaporative": {"enabled": False}, "economiser": {"enabled": False}}


def _merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def derive(cfg: ProjectConfig, patch: dict) -> ProjectConfig:
    base = cfg.model_dump(mode="python")
    sources = base.pop("sources", {})
    merged = _merge(base, patch)
    merged["sources"] = sources
    return ProjectConfig(**merged)


def improved_patch(cfg: ProjectConfig) -> dict:
    arch = cfg.baseline.architecture
    p: dict = {"it": {"containment": "full"}, "strategies": copy.deepcopy(ALL_OFF)}
    if arch in CHILLED_WATER:
        p["baseline"] = {"waterside_economiser": True, "chw_supply_c": max(cfg.baseline.chw_supply_c, 14.0)}
    else:
        p["strategies"]["economiser"] = {"enabled": True, "kind": "direct"}
    return p


# Candidate strategy additions for the greedy hybrid search (applied on top of scenario 1).
def hybrid_candidates(cfg: ProjectConfig, *, filter_water: bool = True) -> dict[str, dict]:
    arch = cfg.baseline.architecture
    cands = {
        "economiser_direct": {"strategies": {"economiser": {"enabled": True, "kind": "direct"}}},
        "economiser_indirect": {"strategies": {"economiser": {"enabled": True, "kind": "indirect"}}},
        "stack": {"strategies": {"stack": {"enabled": True}}},
        "eahe": {"strategies": {"eahe": {"enabled": True}}},
        "envelope": {"strategies": {"envelope": {"enabled": True}}},
        "facade_kinetic": {"strategies": {"facade": {"enabled": True, "mode": "kinetic"}}},
        "facade_fixed": {"strategies": {"facade": {"enabled": True, "mode": "fixed"}}},
        "iec": {"strategies": {"evaporative": {"enabled": True, "kind": "indirect"}}},
    }
    if arch in CHILLED_WATER:
        cands["storage"] = {"strategies": {"storage": {"enabled": True}}}
        cands["radiative"] = {"strategies": {"radiative": {"enabled": True}}}
    if arch in ("air_cooled_chiller_crah", "dx_crac", "dx_with_airside_economiser", "liquid_cooling_dry_cooler"):
        cands["evap_precool"] = {"strategies": {"evaporative": {"enabled": True, "kind": "precool_condenser"}}}
    cap = cfg.site.constraints.permitted_annual_withdrawal_m3
    if filter_water and cap is not None and cap <= 0:
        for k in ("iec", "evap_precool"):
            cands.pop(k, None)
    return cands


MUTUALLY_EXCLUSIVE = [{"economiser_direct", "economiser_indirect"}, {"facade_kinetic", "facade_fixed"},
                      {"iec", "evap_precool"}]


@dataclass
class ScenarioRun:
    id: str
    label: str
    description: str
    config: ProjectConfig
    result: SimulationResult
    strategies: list[str] = field(default_factory=list)
    compatibility: list[dict] = field(default_factory=list)


# Water savings are normalised by max(baseline withdrawal, REFERENCE_WUE × IT energy) so that a baseline with
# almost no water use does not turn a few cubic metres into an enormous fractional penalty.
REFERENCE_WUE_L_PER_KWH = 0.5


def objective(summary: dict, base: dict, weights: dict) -> float:
    """Weighted fractional savings vs baseline (higher is better); infeasible designs score −inf.

    S = w_E s_E + w_W s_W + w_C s_C, with s_E, s_C fractional facility-energy and carbon savings and
    s_W = (W_base − W) / max(W_base, 0.5 L/kWh × E_IT).
    """
    if summary["thermal"]["unmet_hours"] > 0 or summary["thermal"]["inlet_envelope_violation_hours"] > 0:
        return float("-inf")
    s_e = 1 - summary["energy_mwh"]["facility"] / base["energy_mwh"]["facility"]
    bw = base["water"]["withdrawal_m3"]
    w_ref = max(bw, REFERENCE_WUE_L_PER_KWH * base["energy_mwh"]["it"])  # MWh × L/kWh = m³
    s_w = (bw - summary["water"]["withdrawal_m3"]) / max(w_ref, 1e-9)
    bc = base["carbon"]["operational_t"]
    s_c = (1 - summary["carbon"]["operational_t"] / bc) if bc else s_e
    return weights.get("energy", 0.5) * s_e + weights.get("water", 0.2) * s_w + weights.get("carbon", 0.3) * s_c


def default_weights(cfg: ProjectConfig) -> dict:
    """Water weight scales with Aqueduct stress (0 → 0.10, 5 → 0.45), remainder split between energy and carbon."""
    stress = cfg.site.constraints.water_stress_score
    w_w = 0.10 + 0.07 * stress if stress is not None else 0.20
    rest = 1 - w_w
    return {"energy": rest * 0.5, "carbon": rest * 0.5, "water": w_w}


def run_scenarios(cfg: ProjectConfig, ds: ClimateDataset, *, include=None, weights: dict | None = None, hybrid_threshold=0.002,
                  progress_cb=None, cancel_cb=None, hourly_intensity=None) -> dict:
    include = include or list(SCENARIO_META)
    weights = weights or default_weights(cfg)
    caches = {"eahe_cache": {}, "envelope_cache": {}}
    runs: dict[str, ScenarioRun] = {}

    def prog(frac, msg):
        if progress_cb:
            progress_cb(frac, msg)

    def sim(sid, c, strategies):
        if cancel_cb:
            cancel_cb()
        res = run_simulation(c, ds, scenario=sid, cancel_cb=cancel_cb, hourly_intensity=hourly_intensity, **caches)
        label, desc = SCENARIO_META.get(sid, (sid, ""))
        return ScenarioRun(sid, label, desc, c, res, strategies, compatibility_issues(c))

    base_cfg = derive(cfg, {"strategies": copy.deepcopy(ALL_OFF)})
    prog(0.02, "S0 baseline")
    runs["S0"] = sim("S0", base_cfg, [])
    s0 = runs["S0"].result.summary
    s1_cfg = derive(base_cfg, improved_patch(base_cfg))
    if "S1" in include or "S7" in include:
        prog(0.10, "S1 improved conventional")
        runs["S1"] = sim("S1", s1_cfg, ["improved_conventional"])
    full = {"it": {"containment": "full"}}
    defs = {
        "S2": ({"strategies": {"envelope": {"enabled": True}, "facade": {"enabled": True, "mode": "fixed"}}}, ["envelope", "facade_fixed"]),
        "S3": (_merge(full, {"strategies": {"stack": {"enabled": True}}}), ["stack"]),
        "S4": (_merge(full, {"strategies": {"eahe": {"enabled": True}, "economiser": {"enabled": True, "kind": "direct"}}}),
               ["eahe", "economiser_direct"]),
        "S5": (_merge(full, {"strategies": {"facade": {"enabled": True, "mode": "kinetic", "open_adds_inlet_area_m2": 40.0},
                                            "stack": {"enabled": True, "chimney_height_m": 10.0}}}), ["facade_kinetic", "stack"]),
        "S6": (_merge(full, {"baseline": ({"architecture": "air_cooled_chiller_crah", "waterside_economiser": True,
                                           "chw_supply_c": max(cfg.baseline.chw_supply_c, 14.0), "humidification": "none"}),
                             "strategies": {"economiser": {"enabled": True, "kind": "indirect"}},
                             "site": {"constraints": {"permitted_annual_withdrawal_m3": 0.0}}}), ["dry_heat_rejection", "economiser_indirect"]),
    }
    step = 0.10
    for sid, (patch, strats) in defs.items():
        if sid in include:
            step += 0.08
            prog(step, f"{sid} {SCENARIO_META[sid][0]}")
            runs[sid] = sim(sid, derive(base_cfg, patch), strats)

    first_step: dict[str, ScenarioRun] = {}
    if "S7" in include:
        prog(0.62, "S7 greedy hybrid search")
        runs["S7"], search_log = greedy_hybrid(cfg, ds, s1_cfg, runs["S1"], s0, weights, hybrid_threshold, sim, cancel_cb,
                                               first_step=first_step)
    else:
        search_log = []
    if "S8" in include:
        prog(0.92, "S8 user-defined")
        runs["S8"] = sim("S8", cfg, [k for k, v in cfg.strategies.model_dump().items() if isinstance(v, dict) and v.get("enabled")])

    comparison = compare(runs, s0, weights)
    for sid, r in runs.items():
        comparison[sid]["lifecycle"] = lifecycle(r.config, r.result.summary, s0 if sid != "S0" else None,
                                                 [s for s in r.strategies])
    prog(1.0, "done")
    return {"runs": runs, "comparison": comparison, "weights": weights, "hybrid_search": search_log,
            "candidate_runs": first_step, "s1_config": s1_cfg}


def greedy_hybrid(cfg, ds, start_cfg, start_run, s0, weights, threshold, sim, cancel_cb, first_step: dict | None = None):
    """Forward selection: add the candidate with the largest objective gain while gain > threshold.

    ``first_step`` (optional dict) receives every single-strategy run on top of the start design, which the
    recommendation engine reuses instead of repeating those simulations.
    """
    cands = hybrid_candidates(cfg)
    chosen: list[str] = []
    cur_cfg, cur_run = start_cfg, start_run
    cur_obj = objective(cur_run.result.summary, s0, weights)
    log = [{"step": 0, "added": None, "objective": cur_obj, "strategies": ["improved_conventional"], "tested": []}]
    for step in range(1, 7):
        best = None
        tested = []
        for name, patch in cands.items():
            if name in chosen or any(name in grp and grp & set(chosen) for grp in MUTUALLY_EXCLUSIVE):
                continue
            trial_cfg = derive(cur_cfg, patch)
            issues = [i for i in compatibility_issues(trial_cfg) if i["level"] == "conflict"]
            if issues:
                tested.append({"strategy": name, "objective": None, "rejected": issues[0]["message"]})
                continue
            run = sim("S7", trial_cfg, ["improved_conventional"] + chosen + [name])
            if step == 1 and first_step is not None:
                first_step[name] = run
            obj = objective(run.result.summary, s0, weights)
            tested.append({"strategy": name, "objective": obj, "gain": obj - cur_obj,
                           "facility_mwh": run.result.summary["energy_mwh"]["facility"],
                           "water_m3": run.result.summary["water"]["withdrawal_m3"]})
            if best is None or obj > best[1]:
                best = (name, obj, trial_cfg, run)
        log.append({"step": step, "tested": tested})
        if best is None or best[1] - cur_obj <= threshold:
            log[-1]["added"] = None
            log[-1]["stopped"] = "no remaining candidate improves the objective by more than the threshold"
            break
        chosen.append(best[0])
        cur_obj, cur_cfg, cur_run = best[1], best[2], best[3]
        log[-1].update({"added": best[0], "objective": cur_obj, "strategies": ["improved_conventional"] + chosen})
    cur_run.id, cur_run.label, cur_run.description = "S7", *SCENARIO_META["S7"]
    cur_run.strategies = ["improved_conventional"] + chosen
    return cur_run, log


METRIC_PATHS = {
    "pue": ("pue", "annual"), "facility_mwh": ("energy_mwh", "facility"), "it_mwh": ("energy_mwh", "it"),
    "cooling_mwh": ("energy_mwh", "cooling"), "peak_facility_kw": ("power_kw", "peak_facility"),
    "peak_cooling_kw": ("power_kw", "peak_cooling"), "water_withdrawal_m3": ("water", "withdrawal_m3"),
    "water_consumption_m3": ("water", "consumption_m3"), "wue_l_per_kwh": ("water", "wue_withdrawal_l_per_kwh"),
    "co2e_t": ("carbon", "operational_t"), "heat_rejected_plant_mwh": ("heat_mwh", "paths", "plant_condenser"),
    "free_cooling_full_h": ("free_cooling", "full_hours"), "free_cooling_partial_h": ("free_cooling", "partial_hours"),
    "unmet_hours": ("thermal", "unmet_hours"), "electricity_cost": ("costs", "electricity"), "water_cost": ("costs", "water"),
}


def _get(d, path):
    for p in path:
        if d is None:
            return None
        d = d.get(p)
    return d


def compare(runs: dict[str, ScenarioRun], s0: dict, weights: dict) -> dict:
    out = {}
    for sid, r in runs.items():
        s = r.result.summary
        row = {"id": sid, "label": r.label, "description": r.description, "strategies": r.strategies,
               "objective": objective(s, s0, weights), "metrics": {}, "delta_abs": {}, "delta_pct": {},
               "flags": r.result.flags, "compatibility": r.compatibility, "config_hash": r.result.inputs_hash}
        for name, path in METRIC_PATHS.items():
            v = _get(s, path)
            b = _get(s0, path)
            row["metrics"][name] = v
            if v is not None and b is not None:
                row["delta_abs"][name] = v - b
                row["delta_pct"][name] = (100.0 * (v - b) / b) if b else None
        out[sid] = row
    return out


def incremental_benefits(cfg: ProjectConfig, ds: ClimateDataset, strategies: list[str], start_cfg: ProjectConfig) -> dict:
    """Add-one (from the start design) and leave-one-out (from the full combination) facility-energy changes.

    The interaction term = Σ individual savings − combined saving: positive means benefits overlap
    (diminishing returns, no double counting in the combined simulation); negative means synergy.
    """
    cands = hybrid_candidates(cfg)
    caches = {"eahe_cache": {}, "envelope_cache": {}}
    base = run_simulation(start_cfg, ds, **caches).summary["energy_mwh"]["facility"]
    full_cfg = start_cfg
    for s in strategies:
        full_cfg = derive(full_cfg, cands[s])
    full = run_simulation(full_cfg, ds, **caches).summary["energy_mwh"]["facility"]
    add_one, leave_one = {}, {}
    for s in strategies:
        add_one[s] = base - run_simulation(derive(start_cfg, cands[s]), ds, **caches).summary["energy_mwh"]["facility"]
        c = start_cfg
        for o in strategies:
            if o != s:
                c = derive(c, cands[o])
        leave_one[s] = run_simulation(c, ds, **caches).summary["energy_mwh"]["facility"] - full
    combined = base - full
    return {"start_facility_mwh": base, "combined_facility_mwh": full, "combined_saving_mwh": combined,
            "add_one_saving_mwh": add_one, "leave_one_out_increase_mwh": leave_one,
            "interaction_mwh": sum(add_one.values()) - combined}
