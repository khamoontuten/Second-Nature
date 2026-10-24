"""Impact studies: baseline, individual strategies, user combinations and pairwise interactions.

Every scenario follows the same path:

  baseline configuration (S0: the project with all strategies off)
    → apply the resolved strategy selection and parameters (app.impacts.catalogue)
    → compatibility rules (conflicts make the scenario infeasible without simulating it)
    → run_simulation: hourly physics, controller, plant, water and carbon on the same climate record
    → quantity take-off → CAPEX/OPEX (unit rates) and space (layout ratios)
    → hard-constraint checks (thermal envelope, unmet load, water caps, roof/site/buried land)
    → baseline-relative metrics with status labels

Combined results are never computed by summing individual results; the sum is only used afterwards to report the
non-additive part of a combination.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable

from app import MODEL_VERSION
from app.accounting.lifecycle import embodied_carbon
from app.climate.providers.base import ClimateDataset
from app.impacts import catalogue, costing, quantities, space
from app.impacts.metrics import INTERACTION_METRICS, METRICS, compare, interaction, metric_values
from app.schemas.config import ProjectConfig
from app.simulation.engine import run_simulation
from app.simulation.scenarios import baseline_config, derive
from app.strategies.library import compatibility_issues

SUMMARY_KEYS = ("period", "energy_mwh", "pue", "power_kw", "water", "carbon", "heat_mwh", "free_cooling", "modes", "thermal",
                "costs", "monthly", "sizing", "eahe")


def _compact_summary(s: dict) -> dict:
    out = {k: s[k] for k in SUMMARY_KEYS if k in s}
    fc = dict(out.get("free_cooling", {}))
    fc.pop("eligibility", None)
    out["free_cooling"] = fc
    return out


class Evaluator:
    """Runs scenarios against one baseline on one climate dataset, with shared physics caches."""

    def __init__(self, project_cfg: ProjectConfig, ds: ClimateDataset, *, hourly_intensity=None, cancel_cb=None):
        self.project_cfg = project_cfg
        self.base_cfg = baseline_config(project_cfg)
        self.ds = ds
        self.hourly_intensity = hourly_intensity
        self.cancel_cb = cancel_cb
        self.caches = {"eahe_cache": {}, "envelope_cache": {}}
        self.simulations = 0
        self._memo: dict[str, dict] = {}

    def evaluate(self, requested: list[str], parameters: dict | None = None, *, sid: str = "scenario", label: str | None = None,
                 keep_summary: bool = True) -> dict:
        sel = catalogue.select(requested, parameters)
        ev = {"id": sid, "label": label or (" + ".join(requested) if requested else "Baseline (S0)"), "requested": list(requested),
              "resolved": sel.resolved, "auto_added": sel.auto_added, "parameters": sel.parameters,
              "ignored_parameters": sel.ignored_parameters, "warnings": [], "infeasible_reasons": list(sel.conflicts), "simulated": False}
        if sel.conflicts:
            ev["feasible"] = False
            return ev
        cfg = derive(self.base_cfg, catalogue.patch_for(sel.resolved, sel.parameters))
        issues = compatibility_issues(cfg)
        ev["infeasible_reasons"] += [i["message"] for i in issues if i["level"] == "conflict"]
        ev["warnings"] += [i["message"] for i in issues if i["level"] == "warning"]
        ev["config_hash"] = cfg.config_hash()
        if ev["infeasible_reasons"]:
            ev["feasible"] = False
            return ev
        key = ev["config_hash"]
        if key in self._memo:
            sim = self._memo[key]
        else:
            if self.cancel_cb:
                self.cancel_cb()
            res = run_simulation(cfg, self.ds, scenario=sid, hourly_intensity=self.hourly_intensity, **self.caches)
            self.simulations += 1
            active = quantities.active_measures(cfg, self.base_cfg)
            take = quantities.takeoff(cfg, res.summary, self.base_cfg)
            sim = {"summary": _compact_summary(res.summary), "diagnostics": {k: res.diagnostics[k] for k in ("natural_ventilation",)
                                                                             if k in res.diagnostics},
                   "flags": [f for f in res.flags if f["level"] != "info"], "inputs_hash": res.inputs_hash, "active": active,
                   "takeoff": take, "capex": costing.estimate(cfg, take, active), "opex": costing.opex(cfg, res.summary, active),
                   "space": space.estimate(cfg, res.summary, take, self.base_cfg), "embodied": embodied_carbon(cfg, self.base_cfg)}
            self._memo[key] = sim
        ev.update(sim)
        ev["simulated"] = True
        ev["warnings"] += [f["message"] for f in sim["flags"]] + sim["space"]["checks"]
        th = sim["summary"]["thermal"]
        reasons = []
        if th["unmet_hours"] > 0:
            reasons.append(f"cooling load not met for {th['unmet_hours']} h")
        if th["inlet_envelope_violation_hours"] > 0:
            reasons.append(f"server inlet outside the {cfg.it.ashrae_class} {cfg.it.envelope_mode} envelope for "
                           f"{th['inlet_envelope_violation_hours']} h")
        cons = cfg.site.constraints
        w = sim["summary"]["water"]
        if cons.permitted_annual_withdrawal_m3 is not None and w["withdrawal_m3"] > cons.permitted_annual_withdrawal_m3 + 1e-9:
            reasons.append(f"water withdrawal {w['withdrawal_m3']:,.0f} m³/yr exceeds the permitted {cons.permitted_annual_withdrawal_m3:,.0f} m³/yr")
        if cons.permitted_daily_withdrawal_m3 is not None and w["peak_daily_withdrawal_m3"] > cons.permitted_daily_withdrawal_m3 + 1e-9:
            reasons.append(f"peak daily withdrawal {w['peak_daily_withdrawal_m3']:,.1f} m³ exceeds the permitted {cons.permitted_daily_withdrawal_m3:,.1f} m³")
        reasons += sim["space"]["infeasible"]
        ev["infeasible_reasons"] += reasons
        ev["feasible"] = not ev["infeasible_reasons"]
        if not keep_summary:
            ev = {k: v for k, v in ev.items() if k not in ("summary", "takeoff")} | {"summary": _metric_summary(sim["summary"])}
        return ev


def _metric_summary(s: dict) -> dict:
    """The subset of the summary that metric getters read (keeps pairwise studies small)."""
    keep = {k: s[k] for k in ("energy_mwh", "pue", "power_kw", "water", "carbon", "free_cooling", "thermal", "sizing", "period", "costs")}
    keep["heat_mwh"] = {"paths": s["heat_mwh"]["paths"]}
    keep["energy_mwh"] = {k: v for k, v in keep["energy_mwh"].items() if k != "by_mode_cooling"}
    return keep


def _impact(cfg: ProjectConfig, base: dict, ev: dict) -> dict:
    """Attach baseline-relative CAPEX, payback/NPV (only when every input exists) and metric records."""
    if ev.get("simulated"):
        ev["capex_incremental"] = costing.incremental(base["capex"], ev["capex"])
        ev["economics"] = costing.economics(cfg, ev["capex_incremental"], base["opex"], ev["opex"], base["capex"], ev["capex"])
    ev["metrics"] = compare(base, ev, cfg.economics.currency)
    return ev


def _deltas(ev: dict) -> dict:
    return {r["key"]: r["delta"] for r in ev.get("metrics", [])}


def run_study(project_cfg: ProjectConfig, ds: ClimateDataset, kind: str, strategies: list[str] | None = None,
              parameters: dict | None = None, *, hourly_intensity=None, progress_cb: Callable | None = None,
              cancel_cb: Callable | None = None) -> dict:
    """kind: "baseline" (the reference only), "individual" (each strategy alone), "combination" (the selection +
    each member alone) or "pairwise" (each strategy alone + every pair)."""
    ev = Evaluator(project_cfg, ds, hourly_intensity=hourly_intensity, cancel_cb=cancel_cb)
    app_ = catalogue.applicability(ev.base_cfg)
    if kind == "baseline":
        strategies = []
    elif strategies is None:
        strategies = [m.id for m in catalogue.MEASURES if app_[m.id]["applicable"]]
    catalogue.resolve(strategies)  # raises on unknown ids

    def prog(f, msg):
        if progress_cb:
            progress_cb(f, msg)

    prog(0.02, "baseline")
    base = ev.evaluate([], sid="baseline", label="Baseline (S0 conventional)")
    if not base.get("simulated") or not base["feasible"]:
        raise ValueError("the baseline configuration is infeasible: " + "; ".join(base["infeasible_reasons"]))
    _impact(project_cfg, base, base)

    def finish(e):
        return _impact(project_cfg, base, e)

    def params_for(ids: list[str]) -> dict:
        allowed = catalogue.allowed_parameters(catalogue.resolve(ids)[0])
        return {k: v for k, v in (parameters or {}).items() if k in allowed}

    individual = []
    n_ind = len(strategies)
    for i, sid in enumerate(strategies):
        prog(0.05 + 0.45 * i / max(n_ind, 1), f"individual: {sid}")
        individual.append(finish(ev.evaluate([sid], params_for([sid]), sid=sid, label=catalogue.BY_ID[sid].name)))

    out = {"kind": kind, "model_version": MODEL_VERSION, "baseline": base, "individual": individual, "requested": strategies,
           "parameters": parameters or {}, "applicability": app_, "controller_note": catalogue.CONTROLLER_NOTE}

    if kind == "combination":
        prog(0.6, "combination")
        combo = finish(ev.evaluate(strategies, params_for(strategies), sid="combination",
                                   label=" + ".join(catalogue.BY_ID[s].name for s in strategies)))
        out["combination"] = combo
        out["attribution"] = attribution(individual, combo, project_cfg.economics.currency)
    elif kind == "pairwise":
        pairs = list(itertools.combinations(strategies, 2))
        out["pairs"] = []
        by_id = {e["requested"][0]: e for e in individual}
        for k, (a, b) in enumerate(pairs):
            prog(0.5 + 0.48 * k / max(len(pairs), 1), f"pair {a} + {b}")
            pe = finish(ev.evaluate([a, b], params_for([a, b]), sid=f"{a}+{b}", label=f"{a} + {b}", keep_summary=False))
            pe.pop("takeoff", None)
            row = {"a": a, "b": b, "feasible": pe["feasible"], "simulated": pe["simulated"], "infeasible_reasons": pe["infeasible_reasons"],
                   "auto_added": pe["auto_added"], "metrics": {r["key"]: {"value": r["value"], "delta": r["delta"], "pct": r.get("pct"),
                                                                         "status": r["status"]} for r in pe["metrics"]},
                   "interactions": {}}
            da, db, dab = _deltas(by_id[a]), _deltas(by_id[b]), {k: v["delta"] for k, v in row["metrics"].items()}
            for key, tol in INTERACTION_METRICS:
                if pe["simulated"]:
                    row["interactions"][key] = interaction(da.get(key), db.get(key), dab.get(key), tol)
            out["pairs"].append(row)
    elif kind not in ("individual", "baseline"):
        raise ValueError(f"unknown study kind {kind!r}")
    out["simulations_run"] = ev.simulations
    out["period"] = base["summary"]["period"]
    out["assumptions"] = assumptions(project_cfg)
    prog(1.0, "done")
    return out


def attribution(individual: list[dict], combo: dict, currency: str = "") -> dict:
    """Individual changes of the selected strategies next to the combined change; the remainder is non-additive."""
    rows = []
    for m in METRICS:
        ind = {e["requested"][0]: next((r["delta"] for r in e["metrics"] if r["key"] == m.key), None) for e in individual}
        comb = next((r["delta"] for r in combo.get("metrics", []) if r["key"] == m.key), None)
        known = [v for v in ind.values() if v is not None]
        s = sum(known) if len(known) == len(ind) and ind else None
        unit = m.unit.replace("currency", currency) if currency else m.unit
        rows.append({"key": m.key, "label": m.label, "unit": unit, "individual": ind, "sum_individual": s, "combined": comb,
                     "non_additive": (comb - s) if (comb is not None and s is not None) else None})
    shared = sorted({d for e in individual for d in e.get("auto_added", [])})
    return {"rows": rows, "shared_dependencies": shared,
            "note": ("Individual changes are each relative to the baseline and need not add up to the combined change. "
                     "The non-additive remainder is a diagnostic of interaction (shared heat load, airflow path, operating "
                     "hours, shared infrastructure), not a causal attribution."
                     + (f" Dependencies added to several individual runs ({', '.join(shared)}) are counted in each of them."
                        if shared else ""))}


def assumptions(cfg: ProjectConfig) -> dict:
    ec, lay = cfg.economics, cfg.layout
    rates = {k: v.model_dump() for k, v in ec.unit_rates.items()}
    return {
        "baseline": "S0: the project configuration with every strategy switched off; same climate record, IT load profile, "
                    "environmental class, redundancy and plant-sizing rule for every scenario.",
        "energy_boundary": "facility electricity = IT + cooling (chillers, towers/dry coolers, pumps, CRAH/AHU/EAHE fans, humidifiers, "
                           "actuators, controls) + UPS/distribution losses + lighting + other fixed loads",
        "water_boundary": "site operational water (towers, evaporative media, humidification, pre-cooling); excludes water embedded in electricity",
        "carbon_boundary": "operational: facility electricity × grid factor (hourly series when supplied); embodied: added strategy "
                           "materials only, reported separately",
        "plant_sizing": "mechanical plant sized for the design hour without passive credit in every scenario, so CAPEX for the "
                        "mechanical plant is unchanged unless the sizing rule changes",
        "currency": ec.currency, "contingency_fraction": ec.contingency_fraction, "unit_rates": rates,
        "unit_rate_sources": sorted({v.source for v in ec.unit_rates.values()}),
        "layout": lay.model_dump(), "excluded_costs": costing.EXCLUDED_GLOBAL,
        "space_note": "Preliminary planning estimates from layout ratios and configured geometry; underground and roof areas "
                      "are reported separately from the at-grade footprint.",
    }


__all__ = ["Evaluator", "run_study", "attribution", "metric_values"]
