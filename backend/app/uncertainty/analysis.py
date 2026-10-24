"""Uncertainty and sensitivity analysis.

* One-at-a-time (OAT) sensitivity: each uncertain input at its low and high value, all others central
  → tornado chart of the change in the chosen metric for the design *and* for its saving vs baseline.
* Monte Carlo with paired (common-random-number) sampling of baseline and design, so the saving
  distribution reflects shared uncertainty correctly. Reported: central estimate, P5/P50/P95, the
  probability that the design beats the baseline, and Spearman rank correlations.
* Uncertainty by source: inputs are grouped (climate, physical parameters, model form, operational,
  data). Grouped Monte Carlo runs vary one group at a time to separate the contributions.

Climate: with a single (typical) year, climate variability is represented by a uniform warming /
cooling offset — a sensitivity test, *not* a climate projection. With multi-year data, per-year
results are reported separately (``climate_years``).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import spearmanr

from app.climate.providers.base import ClimateDataset
from app.schemas.config import ProjectConfig
from app.simulation.engine import run_simulation
from app.simulation.scenarios import derive


@dataclass
class UncertainInput:
    path: str
    low: float
    central: float
    high: float
    dist: str = "triangular"   # "triangular" | "uniform" | "choice"
    category: str = "physical"  # climate | physical | model | operational | data
    label: str = ""
    choices: list | None = None


def _get(cfg: ProjectConfig, path: str):
    obj = cfg
    for p in path.split("."):
        obj = getattr(obj, p)
    return obj


def _patch(path: str, value) -> dict:
    out: dict = {}
    cur = out
    parts = path.split(".")
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value
    return out


def default_inputs(design: ProjectConfig) -> list[UncertainInput]:
    """Default uncertain inputs (ranges are assumptions documented in docs/assumptions.md)."""
    from app.cooling.plant import DEFAULT_COP

    st = design.strategies
    cop = design.baseline.rated_cop or DEFAULT_COP[design.baseline.architecture]
    u = design.it.average_utilisation
    ins = [
        UncertainInput("it.average_utilisation", max(u - 0.15, 0.1), u, min(u + 0.15, 1.0), category="operational", label="IT utilisation"),
        UncertainInput("baseline.rated_cop", 0.85 * cop, cop, 1.15 * cop, category="physical", label="Chiller/DX rated COP"),
        UncertainInput("baseline.crah_fan_efficiency", 0.50, design.baseline.crah_fan_efficiency, 0.72, category="physical", label="Fan efficiency"),
        UncertainInput("simulation.warming_offset_k", -1.0, 0.0, 1.5, category="climate", label="Outdoor temperature offset (climate variability proxy)"),
        UncertainInput("baseline.chiller_model", 0, 0, 1, dist="choice", category="model", label="Chiller model form",
                       choices=["carnot", "constant"]),
    ]
    if design.grid.emission_factor_kg_per_kwh is not None:
        g = design.grid.emission_factor_kg_per_kwh
        ins.append(UncertainInput("grid.emission_factor_kg_per_kwh", 0.85 * g, g, 1.15 * g, category="data", label="Grid emission factor"))
    if st.stack.enabled:
        ins += [UncertainInput("strategies.stack.discharge_coefficient", 0.5, st.stack.discharge_coefficient, 0.7, category="model",
                               label="Opening discharge coefficient"),
                UncertainInput("strategies.stack.chimney_cp", -0.7, st.stack.chimney_cp, -0.3, category="model", label="Chimney outlet C_p")]
    if st.eahe.enabled:
        from app.physics.ground import SOIL_TYPES

        k = st.eahe.soil_k or SOIL_TYPES.get(st.eahe.soil_type, SOIL_TYPES["soil_generic"])["k"]
        ins += [UncertainInput("strategies.eahe.soil_k", 0.6 * k, k, 1.4 * k, category="physical", label="Soil thermal conductivity"),
                UncertainInput("strategies.eahe.surface_offset_k", -1.0, st.eahe.surface_offset_k, 2.0, category="data",
                               label="Ground surface offset vs air")]
    if st.evaporative.enabled or design.baseline.architecture == "indirect_evaporative":
        ins.append(UncertainInput("baseline.iec_wet_bulb_effectiveness", 0.6, design.baseline.iec_wet_bulb_effectiveness, 0.8,
                                  category="physical", label="IEC wet-bulb effectiveness"))
    return ins


def _value(inp: UncertainInput, which: str):
    if inp.dist == "choice":
        return inp.choices[0] if which in ("low", "central") else inp.choices[-1]
    return {"low": inp.low, "central": inp.central, "high": inp.high}[which]


def _sample(inp: UncertainInput, rng) -> object:
    if inp.dist == "choice":
        return inp.choices[int(rng.integers(len(inp.choices)))]
    if inp.dist == "uniform":
        return float(rng.uniform(inp.low, inp.high))
    if inp.high - inp.low <= 0:
        return inp.central
    c = (inp.central - inp.low) / (inp.high - inp.low)
    return float(inp.low + (inp.high - inp.low) * rng.triangular(0, c, 1))


def _metrics(s: dict) -> dict:
    return {"facility_mwh": s["energy_mwh"]["facility"], "cooling_mwh": s["energy_mwh"]["cooling"], "pue": s["pue"]["annual"],
            "water_m3": s["water"]["withdrawal_m3"], "co2e_t": s["carbon"]["operational_t"],
            "unmet_hours": s["thermal"]["unmet_hours"], "violations": s["thermal"]["inlet_envelope_violation_hours"]}


class _Runner:
    def __init__(self, ds, cancel_cb=None, progress_cb=None, total=1):
        self.ds = ds
        self.cache: dict[str, dict] = {}
        self.caches = {"eahe_cache": {}, "envelope_cache": {}}
        self.cancel_cb, self.progress_cb, self.total, self.done = cancel_cb, progress_cb, max(total, 1), 0

    def __call__(self, cfg: ProjectConfig) -> dict:
        h = cfg.config_hash()
        if h not in self.cache:
            if self.cancel_cb:
                self.cancel_cb()
            self.cache[h] = _metrics(run_simulation(cfg, self.ds, **self.caches).summary)
        self.done += 1
        if self.progress_cb:
            self.progress_cb(min(self.done / self.total, 0.99), f"uncertainty run {self.done}/{self.total}")
        return self.cache[h]


def _apply(cfg: ProjectConfig, values: dict) -> ProjectConfig:
    patch: dict = {}
    for path, v in values.items():
        p = _patch(path, v)
        _deep_update(patch, p)
    return derive(cfg, patch)


def _deep_update(a: dict, b: dict):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _deep_update(a[k], v)
        else:
            a[k] = v


def _applicable(cfg: ProjectConfig, inp: UncertainInput) -> bool:
    try:
        _get(cfg, inp.path)
        return True
    except AttributeError:
        return False


def tornado(baseline: ProjectConfig, design: ProjectConfig, ds: ClimateDataset, inputs: list[UncertainInput] | None = None,
            metric: str = "facility_mwh", cancel_cb=None, progress_cb=None) -> dict:
    inputs = inputs or default_inputs(design)
    run = _Runner(ds, cancel_cb, progress_cb, total=2 + 4 * len(inputs))
    central_d = run(design)
    central_b = run(baseline)
    rows = []
    for inp in inputs:
        r = {"input": inp.path, "label": inp.label or inp.path, "category": inp.category,
             "low_value": _value(inp, "low"), "high_value": _value(inp, "high")}
        for which in ("low", "high"):
            v = {inp.path: _value(inp, which)}
            d = run(_apply(design, v))
            b = run(_apply(baseline, v)) if _applicable(baseline, inp) else central_b
            r[f"{which}_design"] = d[metric]
            r[f"{which}_saving"] = (b[metric] - d[metric]) if (b[metric] is not None and d[metric] is not None) else None
        r["swing_design"] = abs((r["high_design"] or 0) - (r["low_design"] or 0))
        r["swing_saving"] = abs((r["high_saving"] or 0) - (r["low_saving"] or 0))
        rows.append(r)
    rows.sort(key=lambda r: r["swing_saving"], reverse=True)
    return {"metric": metric, "central_design": central_d[metric], "central_baseline": central_b[metric],
            "central_saving": central_b[metric] - central_d[metric], "rows": rows,
            "method": "one-at-a-time low/high with all other inputs at central values"}


def monte_carlo(baseline: ProjectConfig, design: ProjectConfig, ds: ClimateDataset, inputs: list[UncertainInput] | None = None,
                n: int = 24, seed: int = 1, by_category: bool = True, n_category: int = 8, cancel_cb=None, progress_cb=None) -> dict:
    inputs = inputs or default_inputs(design)
    cats = sorted({i.category for i in inputs})
    total = 2 * n + (2 * n_category * len(cats) if by_category else 0)
    run = _Runner(ds, cancel_cb, progress_cb, total=total)
    rng = np.random.default_rng(seed)

    def batch(active: list[UncertainInput], m: int):
        samples, out = [], []
        for _ in range(m):
            vals = {i.path: _sample(i, rng) for i in active}
            d = run(_apply(design, vals))
            b = run(_apply(baseline, {k: v for k, v in vals.items() if _applicable(baseline, next(x for x in inputs if x.path == k))}))
            samples.append(vals)
            out.append((b, d))
        return samples, out

    samples, pairs = batch(inputs, n)
    metrics = ["facility_mwh", "cooling_mwh", "pue", "water_m3", "co2e_t"]
    res = {"n": n, "seed": seed, "inputs": [i.__dict__ for i in inputs], "metrics": {}, "spearman": {},
           "method": "paired Monte Carlo (common random numbers for baseline and design)"}
    for m in metrics:
        d = np.array([p[1][m] if p[1][m] is not None else np.nan for p in pairs], dtype=float)
        b = np.array([p[0][m] if p[0][m] is not None else np.nan for p in pairs], dtype=float)
        s = b - d
        if np.all(np.isnan(d)):
            continue
        res["metrics"][m] = {
            "design": _pct(d), "baseline": _pct(b), "saving": _pct(s),
            "prob_design_better": float(np.mean(s > 0)) if np.isfinite(s).any() else None,
            "design_samples": d.tolist(), "saving_samples": s.tolist(),
        }
    s_fac = np.array([p[0]["facility_mwh"] - p[1]["facility_mwh"] for p in pairs])
    for inp in inputs:
        x = [smp[inp.path] for smp in samples]
        if inp.dist == "choice":
            x = [inp.choices.index(v) for v in x]
        if np.ptp(np.asarray(x, dtype=float)) == 0:
            continue
        rho, pval = spearmanr(x, s_fac)
        res["spearman"][inp.path] = {"rho": float(rho), "p_value": float(pval), "label": inp.label, "category": inp.category}
    res["violations_in_samples"] = int(sum(1 for p in pairs if p[1]["unmet_hours"] or p[1]["violations"]))
    if by_category:
        res["by_category"] = {}
        for cat in cats:
            _, cp = batch([i for i in inputs if i.category == cat], n_category)
            sv = np.array([p[0]["facility_mwh"] - p[1]["facility_mwh"] for p in cp])
            res["by_category"][cat] = {"saving_std_mwh": float(np.std(sv, ddof=1)) if len(sv) > 1 else 0.0, **_pct(sv)}
    return res


def _pct(x: np.ndarray) -> dict:
    x = x[np.isfinite(x)]
    if x.size == 0:
        return {}
    return {"mean": float(np.mean(x)), "p5": float(np.percentile(x, 5)), "p50": float(np.percentile(x, 50)),
            "p95": float(np.percentile(x, 95)), "min": float(np.min(x)), "max": float(np.max(x))}


def rank_stability(designs: dict[str, ProjectConfig], ds: ClimateDataset, inputs: list[UncertainInput] | None = None, n: int = 12,
                   metric: str = "facility_mwh", seed: int = 3, cancel_cb=None, progress_cb=None) -> dict:
    """Probability that each design is best under joint uncertainty (conditions under which the recommendation changes)."""
    first = next(iter(designs.values()))
    inputs = inputs or default_inputs(first)
    rng = np.random.default_rng(seed)
    run = _Runner(ds, cancel_cb, progress_cb, total=n * len(designs))
    wins = dict.fromkeys(designs, 0)
    flips = []
    for _ in range(n):
        vals = {i.path: _sample(i, rng) for i in inputs}
        scores = {}
        for name, cfg in designs.items():
            ok = {k: v for k, v in vals.items() if _applicable(cfg, next(x for x in inputs if x.path == k))}
            m = run(_apply(cfg, ok))
            scores[name] = m[metric] if not (m["unmet_hours"] or m["violations"]) else np.inf
        best = min(scores, key=scores.get)
        wins[best] += 1
        flips.append({"best": best, "inputs": vals})
    leader = max(wins, key=wins.get)
    return {"n": n, "metric": metric, "probability_best": {k: v / n for k, v in wins.items()}, "leader": leader,
            "samples_where_leader_loses": [f for f in flips if f["best"] != leader][:20]}
