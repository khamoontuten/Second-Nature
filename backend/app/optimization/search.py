"""Design optimisation over strategy sizes and operating thresholds.

Two modes:
1. ``constrained`` — minimise facility energy (or carbon) subject to hard thermal constraints and a
   water limit. Grid search for small spaces; SciPy differential evolution (feasibility-rule constraint
   handling, so constraints are never traded against the objective) for continuous variables.
2. ``pareto`` — evaluate a grid / Latin-hypercube sample and return the non-dominated set over
   facility energy, water withdrawal, CO₂e, land use and a complexity index (and capital cost when the
   engineer has supplied it).

Results are optimal *relative to the tested alternatives*, which are all returned for inspection.
Simulations are cached by configuration hash; climate preparation, envelope and EAHE sub-results are
shared between evaluations.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import NonlinearConstraint, differential_evolution
from scipy.stats import qmc

from app.climate.providers.base import ClimateDataset
from app.core.errors import ValidationFailure
from app.schemas.config import ProjectConfig
from app.simulation.engine import run_simulation
from app.simulation.scenarios import derive
from app.strategies.library import compatibility_issues

COMPLEXITY = {"stack": 2.0, "eahe": 3.0, "envelope": 1.0, "facade": 2.5, "storage": 2.0, "radiative": 1.5,
              "evaporative": 2.0, "economiser": 1.0}


@dataclass
class Variable:
    path: str
    values: list | None = None
    low: float | None = None
    high: float | None = None
    integer: bool = False

    def __post_init__(self):
        if self.values is None and (self.low is None or self.high is None):
            raise ValidationFailure(f"variable {self.path}: give either values or low/high bounds")
        if self.values is None and self.low >= self.high:
            raise ValidationFailure(f"variable {self.path}: low must be < high")


@dataclass
class Evaluation:
    params: dict
    feasible: bool
    reasons: list[str]
    metrics: dict
    config_hash: str
    summary: dict | None = None


@dataclass
class OptimisationResult:
    mode: str
    objective: str
    evaluations: list[Evaluation]
    best: Evaluation | None
    pareto: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode, "objective": self.objective, "notes": self.notes,
            "best": _ev(self.best) if self.best else None, "pareto_indices": self.pareto,
            "evaluations": [_ev(e) for e in self.evaluations],
        }


def _ev(e: Evaluation) -> dict:
    return {"params": e.params, "feasible": e.feasible, "reasons": e.reasons, "metrics": e.metrics, "config_hash": e.config_hash}


def patch_for(params: dict) -> dict:
    patch: dict = {}
    for path, val in params.items():
        cur = patch
        parts = path.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = val
    return patch


def land_use_m2(cfg: ProjectConfig) -> float:
    st = cfg.strategies
    land = 0.0
    if st.eahe.enabled:
        land += st.eahe.n_pipes * st.eahe.spacing_m * st.eahe.length_m
    if st.stack.enabled:
        land += st.stack.n_chimneys * st.stack.chimney_area_m2 * 1.5
    return land


def complexity_index(cfg: ProjectConfig) -> float:
    st = cfg.strategies.model_dump()
    k = 0.0
    for key, w in COMPLEXITY.items():
        if st.get(key, {}).get("enabled"):
            k += w
    if cfg.strategies.facade.enabled and cfg.strategies.facade.mode == "kinetic":
        k += 1.0
    return k


class Evaluator:
    def __init__(self, base: ProjectConfig, ds: ClimateDataset, water_cap_m3: float | None = None, cancel_cb=None, progress_cb=None,
                 budget: int | None = None):
        self.base = base
        self.ds = ds
        self.water_cap = water_cap_m3
        self.cache: dict[str, Evaluation] = {}
        self.caches = {"eahe_cache": {}, "envelope_cache": {}}
        self.cancel_cb = cancel_cb
        self.progress_cb = progress_cb
        self.budget = budget
        self.count = 0

    def __call__(self, params: dict) -> Evaluation:
        cfg = derive(self.base, patch_for(params))
        h = cfg.config_hash()
        if h in self.cache:
            return self.cache[h]
        if self.cancel_cb:
            self.cancel_cb()
        reasons = [i["message"] for i in compatibility_issues(cfg) if i["level"] == "conflict"]
        metrics = {"land_m2": land_use_m2(cfg), "complexity": complexity_index(cfg)}
        summary = None
        if not reasons:
            res = run_simulation(cfg, self.ds, **self.caches)
            s = res.summary
            summary = s
            metrics.update({"facility_mwh": s["energy_mwh"]["facility"], "cooling_mwh": s["energy_mwh"]["cooling"],
                            "pue": s["pue"]["annual"], "water_m3": s["water"]["withdrawal_m3"], "co2e_t": s["carbon"]["operational_t"],
                            "peak_facility_kw": s["power_kw"]["peak_facility"], "unmet_hours": s["thermal"]["unmet_hours"],
                            "violations": s["thermal"]["inlet_envelope_violation_hours"]})
            if s["thermal"]["unmet_hours"] > 0 or s["thermal"]["inlet_envelope_violation_hours"] > 0:
                reasons.append("thermal constraint violated")
            if self.water_cap is not None and s["water"]["withdrawal_m3"] > self.water_cap + 1e-9:
                reasons.append(f"water withdrawal {s['water']['withdrawal_m3']:.0f} m³ exceeds limit {self.water_cap:.0f} m³")
            capex = 0.0
            known = True
            for key in COMPLEXITY:
                if getattr(cfg.strategies, key).enabled:
                    item = cfg.economics.capex.get(key)
                    if item is None or item.value is None:
                        known = False
                    else:
                        capex += item.value
            metrics["capex"] = capex if known else None
        ev = Evaluation(params, not reasons, reasons, metrics, h, summary)
        self.cache[h] = ev
        self.count += 1
        if self.progress_cb and self.budget:
            self.progress_cb(min(self.count / self.budget, 0.99), f"evaluated {self.count} designs")
        return ev


def grid(variables: list[Variable], levels: int = 3) -> list[dict]:
    axes = []
    for v in variables:
        if v.values is not None:
            axes.append(list(v.values))
        else:
            vals = np.linspace(v.low, v.high, levels)
            axes.append([int(round(x)) if v.integer else float(x) for x in vals])
    return [dict(zip([v.path for v in variables], combo, strict=True)) for combo in itertools.product(*axes)]


def lhs(variables: list[Variable], n: int, seed: int = 0) -> list[dict]:
    cont = [v for v in variables if v.values is None]
    disc = [v for v in variables if v.values is not None]
    sampler = qmc.LatinHypercube(d=max(len(cont), 1), seed=seed)
    u = sampler.random(n)
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        p = {}
        for j, v in enumerate(cont):
            x = v.low + u[i, j] * (v.high - v.low)
            p[v.path] = int(round(x)) if v.integer else float(x)
        for v in disc:
            p[v.path] = v.values[int(rng.integers(len(v.values)))]
        out.append(p)
    return out


def pareto_front(points: np.ndarray) -> list[int]:
    """Indices of non-dominated rows (all objectives minimised)."""
    idx = []
    for i, p in enumerate(points):
        dominated = False
        for j, q in enumerate(points):
            if j != i and np.all(q <= p) and np.any(q < p):
                dominated = True
                break
        if not dominated:
            idx.append(i)
    return idx


def optimise(base: ProjectConfig, ds: ClimateDataset, variables: list[Variable], *, mode: str = "constrained",
             objective: str = "facility_mwh", water_cap_m3: float | None = None, method: str = "auto", budget: int = 40,
             levels: int = 3, seed: int = 0, cancel_cb=None, progress_cb=None) -> OptimisationResult:
    if objective not in ("facility_mwh", "co2e_t", "cooling_mwh", "pue"):
        raise ValidationFailure(f"unsupported objective {objective!r}")
    ev = Evaluator(base, ds, water_cap_m3, cancel_cb, progress_cb, budget)
    notes = []
    n_grid = math.prod(len(v.values) if v.values is not None else levels for v in variables)
    use_grid = method == "grid" or (method == "auto" and n_grid <= budget)
    if mode == "pareto" or use_grid:
        candidates = grid(variables, levels) if use_grid else lhs(variables, budget, seed)
        notes.append(f"{'full grid' if use_grid else 'Latin-hypercube sample'} of {len(candidates)} designs")
        evaluations = [ev(p) for p in candidates[:budget] if True]
        if len(candidates) > budget:
            notes.append(f"grid truncated to the first {budget} designs (budget)")
    else:
        cont = [v for v in variables if v.values is None]
        disc = [v for v in variables if v.values is not None]
        if disc:
            raise ValidationFailure("differential evolution needs continuous bounds for every variable; use grid or pareto mode")
        bounds = [(v.low, v.high) for v in cont]

        def to_params(x):
            return {v.path: (int(round(xi)) if v.integer else float(xi)) for v, xi in zip(cont, x, strict=True)}

        def f(x):
            e = ev(to_params(x))
            val = e.metrics.get(objective)
            return float(val) if val is not None and np.isfinite(val) else 1e12

        def g(x):
            return 0.0 if ev(to_params(x)).feasible else 1.0

        popsize = max(4, budget // (len(cont) * 4))
        res = differential_evolution(f, bounds, constraints=(NonlinearConstraint(g, -np.inf, 0.5),), maxiter=3, popsize=popsize,
                                     seed=seed, polish=False, tol=1e-6, init="latinhypercube", updating="immediate")
        notes.append(f"differential evolution: {res.nfev} evaluations ({ev.count} distinct simulations), message: {res.message}")
        evaluations = list(ev.cache.values())
    feasible = [e for e in evaluations if e.feasible]
    best = min(feasible, key=lambda e: e.metrics[objective]) if feasible else None
    pareto_idx: list[int] = []
    if feasible:
        keys = ["facility_mwh", "water_m3", "co2e_t", "land_m2", "complexity"]
        if all(e.metrics.get("capex") is not None for e in feasible):
            keys.append("capex")
        rows = np.array([[e.metrics.get(k) if e.metrics.get(k) is not None else 0.0 for k in keys] for e in feasible], dtype=float)
        front = pareto_front(rows)
        pareto_idx = [evaluations.index(feasible[i]) for i in front]
        notes.append("Pareto objectives (minimised): " + ", ".join(keys))
    if best is None:
        notes.append("no feasible design among those tested")
    return OptimisationResult(mode, objective, evaluations, best, pareto_idx, notes)


DEFAULT_VARIABLES = {
    "stack": [Variable("strategies.stack.chimney_height_m", values=[10.0, 20.0, 30.0]),
              Variable("strategies.stack.chimney_area_m2", values=[15.0, 30.0, 45.0])],
    "eahe": [Variable("strategies.eahe.length_m", values=[30.0, 60.0, 100.0]),
             Variable("strategies.eahe.n_pipes", values=[20, 40, 80]),
             Variable("strategies.eahe.depth_m", values=[2.0, 3.0, 4.0])],
    "envelope": [Variable("strategies.envelope.insulation_m", values=[0.1, 0.2, 0.3]),
                 Variable("strategies.envelope.roof_solar_reflectance", values=[0.3, 0.6, 0.85])],
    "economiser": [Variable("it.economiser_supply_margin_k", values=[0.5, 1.0, 2.0])],
}
