"""Architectural recommendation engine.

Strategies are evaluated individually by simulation (each added to the improved-conventional
reference, S1), then ranked with a transparent weighted score. Hard feasibility (thermal constraints,
unmet cooling, configuration conflicts, water limits) is checked *before* scoring: an infeasible
strategy is reported as infeasible regardless of its savings. All explanatory text is generated from
the simulation and climate numbers; nothing here asserts that a strategy suits a climate type.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.climate.providers.base import ClimateDataset
from app.schemas.config import ProjectConfig
from app.simulation.engine import run_simulation
from app.simulation.scenarios import ALL_OFF, derive, hybrid_candidates, improved_patch
from app.strategies.library import STRATEGIES, compatibility_issues

CANDIDATE_TO_STRATEGY = {"stack": "A", "envelope": "B", "facade_kinetic": "C", "facade_fixed": "C", "eahe": "D", "storage": "F",
                         "radiative": "G", "iec": "H", "evap_precool": "H", "economiser_direct": "—", "economiser_indirect": "—"}
STATIC = {  # 0 (poor) … 1 (good): qualitative engineering judgements, editable via weights
    "stack": {"complexity": 0.4, "maintenance": 0.6, "reliability": 0.5},
    "envelope": {"complexity": 0.8, "maintenance": 0.9, "reliability": 0.95},
    "facade_kinetic": {"complexity": 0.3, "maintenance": 0.4, "reliability": 0.5},
    "facade_fixed": {"complexity": 0.8, "maintenance": 0.9, "reliability": 0.95},
    "eahe": {"complexity": 0.3, "maintenance": 0.4, "reliability": 0.6},
    "storage": {"complexity": 0.5, "maintenance": 0.7, "reliability": 0.8},
    "radiative": {"complexity": 0.6, "maintenance": 0.6, "reliability": 0.8},
    "iec": {"complexity": 0.6, "maintenance": 0.5, "reliability": 0.75},
    "evap_precool": {"complexity": 0.7, "maintenance": 0.5, "reliability": 0.75},
    "economiser_direct": {"complexity": 0.7, "maintenance": 0.7, "reliability": 0.7},
    "economiser_indirect": {"complexity": 0.6, "maintenance": 0.7, "reliability": 0.85},
}
DEFAULT_WEIGHTS = {"energy": 0.30, "water": 0.15, "carbon": 0.15, "peak": 0.05, "cost": 0.10, "land": 0.05,
                   "complexity": 0.05, "maintenance": 0.05, "reliability": 0.05, "confidence": 0.05}
STUDIES = {
    "stack": ["CFD of hall and stack airflow incl. recirculation", "wind-tunnel or CFD C_p for inlets/outlets",
              "fire/smoke compartmentation review", "filtration and air-quality monitoring plan"],
    "eahe": ["thermal response test / soil conductivity measurement", "groundwater level survey", "radon / soil-gas assessment",
             "condensate drainage and hygiene design", "detailed numerical ground model if exact performance matters"],
    "envelope": ["detailed building-energy simulation", "structural check of added mass", "embodied-carbon assessment"],
    "facade_kinetic": ["wind-load and fatigue design of panels", "actuator reliability data", "façade access/maintenance plan"],
    "facade_fixed": ["shading geometry optimisation", "wind loads"],
    "storage": ["plant control sequence design", "tank siting and structural loads"],
    "radiative": ["material-specific performance data (manufacturer)", "soiling/cleaning regime", "roof area allocation vs PV"],
    "iec": ["water availability confirmation", "water treatment and Legionella risk assessment", "manufacturer performance curves"],
    "evap_precool": ["water availability", "condenser coil corrosion/scaling assessment"],
    "economiser_direct": ["outdoor-air contaminant monitoring (gaseous, particulate)", "filtration design"],
    "economiser_indirect": ["heat-exchanger selection and footprint"],
}


@dataclass
class StrategyEvaluation:
    candidate: str
    summary: dict
    flags: list
    issues: list
    config: ProjectConfig
    diagnostics: dict


def evaluate_candidates(cfg: ProjectConfig, ds: ClimateDataset, candidates: list[str] | None = None, cancel_cb=None,
                        progress_cb=None) -> tuple[dict, dict[str, StrategyEvaluation]]:
    base = derive(cfg, {"strategies": ALL_OFF})
    ref_cfg = derive(base, improved_patch(base))
    caches = {"eahe_cache": {}, "envelope_cache": {}}
    ref = run_simulation(ref_cfg, ds, **caches)
    cands = hybrid_candidates(cfg, filter_water=False)  # infeasible options are reported, not hidden
    names = [n for n in (candidates or list(cands)) if n in cands]
    out = {}
    for i, name in enumerate(names):
        if progress_cb:
            progress_cb((i + 1) / (len(names) + 1), f"evaluating {name}")
        if cancel_cb:
            cancel_cb()
        c = derive(ref_cfg, cands[name])
        issues = compatibility_issues(c)
        if any(x["level"] == "conflict" for x in issues):
            out[name] = StrategyEvaluation(name, {}, [], issues, c, {})
            continue
        r = run_simulation(c, ds, **caches)
        out[name] = StrategyEvaluation(name, r.summary, r.flags, issues, c, r.diagnostics)
    return {"summary": ref.summary, "config": ref_cfg, "diagnostics": ref.diagnostics}, out


def evaluated_from_scenarios(scen: dict) -> tuple[dict, dict[str, StrategyEvaluation]]:
    """Build recommendation inputs from a run_scenarios() result (reuses the greedy search's first step)."""
    s1 = scen["runs"]["S1"]
    ref = {"summary": s1.result.summary, "config": s1.config, "diagnostics": s1.result.diagnostics}
    evals = {name: StrategyEvaluation(name, r.result.summary, r.result.flags, r.compatibility, r.config, r.result.diagnostics)
             for name, r in scen.get("candidate_runs", {}).items()}
    # candidates rejected before simulation (conflicts) are reported from the search log
    for step in scen.get("hybrid_search", []):
        if step.get("step") == 1:
            for t in step.get("tested", []):
                if t.get("rejected") and t["strategy"] not in evals:
                    evals[t["strategy"]] = StrategyEvaluation(t["strategy"], {}, [], [{"level": "conflict", "message": t["rejected"]}],
                                                              s1.config, {})
    return ref, evals


def _norm(vals: dict[str, float], higher_better=True) -> dict[str, float]:
    finite = {k: v for k, v in vals.items() if v is not None}
    if not finite:
        return dict.fromkeys(vals, 0.5)
    lo, hi = min(finite.values()), max(finite.values())
    out = {}
    for k, v in vals.items():
        if v is None:
            out[k] = 0.5
        elif hi - lo < 1e-12:
            out[k] = 0.5
        else:
            x = (v - lo) / (hi - lo)
            out[k] = x if higher_better else 1 - x
    return out


def recommend(cfg: ProjectConfig, ds: ClimateDataset, climate_summary: dict | None = None, weights: dict | None = None,
              candidates: list[str] | None = None, cancel_cb=None, progress_cb=None, evaluated=None) -> dict:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    ref, evals = evaluated or evaluate_candidates(cfg, ds, candidates, cancel_cb, progress_cb)
    rs = ref["summary"]
    e_ref, w_ref, c_ref = rs["energy_mwh"]["facility"], rs["water"]["withdrawal_m3"], rs["carbon"]["operational_t"]
    peak_ref = rs["power_kw"]["peak_facility"]
    rows = {}
    for name, ev in evals.items():
        s = ev.summary
        conflicts = [i for i in ev.issues if i["level"] == "conflict"]
        if not s:
            rows[name] = {"candidate": name, "feasible": False, "infeasible_reasons": [c["message"] for c in conflicts]}
            continue
        reasons = [c["message"] for c in conflicts]
        if s["thermal"]["unmet_hours"] > 0:
            reasons.append(f"{s['thermal']['unmet_hours']} h of unmet cooling")
        if s["thermal"]["inlet_envelope_violation_hours"] > 0:
            reasons.append(f"{s['thermal']['inlet_envelope_violation_hours']} h outside the server-inlet envelope")
        cap = cfg.site.constraints.permitted_annual_withdrawal_m3
        if cap is not None and s["water"]["withdrawal_m3"] > cap:
            reasons.append(f"water withdrawal {s['water']['withdrawal_m3']:.0f} m³ exceeds the permitted {cap:.0f} m³")
        stress = cfg.site.constraints.water_stress_score
        if stress is not None and stress >= 4 and s["water"]["withdrawal_m3"] > w_ref + 1.0:
            reasons.append(f"site water stress score {stress:.1f} (≥ 4): additional water use is excluded by policy")
        land = cfg.site.land_available_for_buried_m2
        foot = (s.get("eahe") or {}).get("footprint_m2")
        if land is not None and foot and foot > land:
            reasons.append(f"EAHE footprint {foot:.0f} m² exceeds available land {land:.0f} m²")
        lc = None
        item = cfg.economics.capex.get(name)
        if item and item.value is not None:
            lc = item.value
        rows[name] = {
            "candidate": name, "strategy_id": CANDIDATE_TO_STRATEGY.get(name), "feasible": not reasons, "infeasible_reasons": reasons,
            "energy_saved_mwh": e_ref - s["energy_mwh"]["facility"],
            "energy_saved_pct": 100 * (e_ref - s["energy_mwh"]["facility"]) / e_ref,
            "pue": s["pue"]["annual"], "pue_reduction": rs["pue"]["annual"] - s["pue"]["annual"],
            "water_change_m3": s["water"]["withdrawal_m3"] - w_ref,
            "carbon_avoided_t": (c_ref - s["carbon"]["operational_t"]) if c_ref is not None and s["carbon"]["operational_t"] is not None else None,
            "peak_reduction_kw": peak_ref - s["power_kw"]["peak_facility"],
            "capex": lc, "land_m2": foot or 0.0, **STATIC.get(name, {}),
            "confidence": _confidence(name, cfg, ds), "free_cooling_full_h": s["free_cooling"]["full_hours"],
            "flags": [f for f in ev.flags if f["level"] != "info"], "warnings": [i["message"] for i in ev.issues if i["level"] == "warning"],
        }
    feas = {k: v for k, v in rows.items() if v["feasible"]}
    crit = {
        "energy": _norm({k: v["energy_saved_mwh"] for k, v in feas.items()}),
        "water": _norm({k: -v["water_change_m3"] for k, v in feas.items()}),
        "carbon": _norm({k: v["carbon_avoided_t"] for k, v in feas.items()}),
        "peak": _norm({k: v["peak_reduction_kw"] for k, v in feas.items()}),
        "cost": _norm({k: v["capex"] for k, v in feas.items()}, higher_better=False),
        "land": _norm({k: v["land_m2"] for k, v in feas.items()}, higher_better=False),
        "complexity": {k: v.get("complexity", 0.5) for k, v in feas.items()},
        "maintenance": {k: v.get("maintenance", 0.5) for k, v in feas.items()},
        "reliability": {k: v.get("reliability", 0.5) for k, v in feas.items()},
        "confidence": {k: v["confidence"]["score"] for k, v in feas.items()},
    }
    for k in feas:
        parts = {c: w[c] * crit[c][k] for c in crit}
        rows[k]["score_breakdown"] = parts
        rows[k]["score"] = sum(parts.values()) / sum(w.values())
        # A strategy that does not save energy, water or carbon is not recommended however simple it is.
        rows[k]["beneficial"] = (rows[k]["energy_saved_mwh"] > 0.001 * e_ref) or (rows[k]["water_change_m3"] < -1.0)
    ranked = sorted(feas.values(), key=lambda r: (r["beneficial"], r["score"]), reverse=True)
    for r in ranked:
        r["explanation"] = explain(r, evals[r["candidate"]], ref, cfg, ds, climate_summary)
    for r in rows.values():
        if not r["feasible"]:
            r["explanation"] = {"verdict": "Infeasible: " + "; ".join(r["infeasible_reasons"])}
    return {"reference": "S1 improved conventional", "reference_metrics": {"facility_mwh": e_ref, "water_m3": w_ref, "co2e_t": c_ref,
                                                                         "pue": rs["pue"]["annual"]},
            "weights": w, "ranking": [r["candidate"] for r in ranked], "candidates": rows,
            "method": "each strategy simulated on top of S1; hard constraints checked first; criteria min-max normalised across feasible "
                      "candidates and combined with the weights shown; qualitative criteria (complexity, maintenance, reliability) "
                      "are engineering-judgement scores"}


def _confidence(name: str, cfg: ProjectConfig, ds: ClimateDataset) -> dict:
    score, notes = 0.7, []
    nature = ds.provenance.data_nature
    if nature == "typical_year":
        score -= 0.1
        notes.append("typical-year weather (understates extremes)")
    elif nature in ("reanalysis", "modeled"):
        score -= 0.05
        notes.append(f"{nature} weather (grid-cell, not site)")
    if name == "eahe" and cfg.strategies.eahe.soil_k is None:
        score -= 0.25
        notes.append("soil properties assumed (no site measurement)")
    if name == "stack":
        score -= 0.2
        notes.append("lumped airflow network; CFD required")
    if name.startswith("facade"):
        score -= 0.05
    if name == "radiative":
        score -= 0.15
        notes.append("panel performance is material-specific")
    if cfg.grid.emission_factor_kg_per_kwh is None and cfg.grid.hourly_dataset_id is None:
        notes.append("no grid emission factor supplied: carbon results unavailable")
    return {"score": max(min(score, 1.0), 0.05), "notes": notes}


def explain(row: dict, ev: StrategyEvaluation, ref: dict, cfg: ProjectConfig, ds: ClimateDataset, climate: dict | None) -> dict:
    s = ev.summary
    name = row["candidate"]
    lib_key = {"iec": "evaporative", "evap_precool": "evaporative", "facade_kinetic": "facade", "facade_fixed": "facade"}.get(name, name)
    lib = next((x for x in STRATEGIES if x["key"] == lib_key and x["id"] != "E"), None)
    elig = s["free_cooling"]["eligibility"]
    clim = []
    if climate:
        t = climate["overall"].get("t_db_c", {})
        clim.append(f"Dry bulb {t.get('p05', float('nan')):.1f}–{t.get('p95', float('nan')):.1f} °C (5th–95th percentile), "
                    f"0.4 % design {climate['design_conditions'].get('t_db_c', {}).get('0.4%', float('nan')):.1f} °C.")
        dp = climate["overall"].get("t_dp_c", {})
        if dp:
            clim.append(f"Dew point exceeds {dp.get('p95', float('nan')):.1f} °C for 5 % of hours; "
                        f"hot-and-humid hours (T ≥ 30 °C, T_dp ≥ 20 °C): {climate['hot_humid_hours']['hours']}.")
    clim.append(f"Outdoor air met the full inlet envelope for {elig['full_envelope_eligible_h']} h "
                f"({elig['full_envelope_eligible_pct']:.0f} %); rejected for humidity {elig['rejected_humidity_high_h']} h, "
                f"temperature {elig['rejected_temperature_h']} h.")
    why = []
    if name in ("stack", "economiser_direct", "economiser_indirect"):
        nat = ev.diagnostics.get("natural_ventilation")
        why.append(f"Free cooling delivered the full load for {s['free_cooling']['full_hours']} h and part of it for "
                   f"{s['free_cooling']['partial_hours']} h.")
        if nat:
            why.append(f"Natural draught met server airflow without fans for {nat['sufficient_hours']} h; "
                       f"flow reversal predicted for {nat['reversed_hours']} h.")
    if name == "eahe" and s.get("eahe"):
        e = s["eahe"]
        g = ev.diagnostics.get("ground", {})
        why.append(f"Modelled ground at {cfg.strategies.eahe.depth_m:.1f} m: mean {g.get('t_mean_c', float('nan')):.1f} °C, "
                   f"annual swing ±{(g.get('amplitude_k', 0) * 2.718281828 ** (-cfg.strategies.eahe.depth_m / max(g.get('damping_depth_m', 1), 1e-6))):.1f} K "
                   f"({g.get('calibration', '')}).")
        why.append(f"The exchanger was useful for {e['useful_hours']} h, delivering {e['sensible_cooling_mwh']:.0f} MWh of sensible "
                   f"cooling with {e['fan_energy_mwh']:.0f} MWh of fan energy; soil drift up to {e['max_soil_drift_k']:.1f} K.")
    if name in ("envelope", "facade_fixed", "facade_kinetic"):
        why.append(f"Envelope net heat gain changed from {ref['summary']['heat_mwh']['envelope_net_gain']:.0f} to "
                   f"{s['heat_mwh']['envelope_net_gain']:.0f} MWh/yr against {s['heat_mwh']['it_heat']:.0f} MWh of IT heat.")
    if name in ("iec", "evap_precool"):
        why.append(f"Wet-bulb-based cooling cut cooling electricity to {s['energy_mwh']['cooling']:.0f} MWh but used "
                   f"{s['water']['withdrawal_m3']:.0f} m³ of water (WUE {s['water']['wue_withdrawal_l_per_kwh']:.2f} L/kWh).")
    if name == "storage":
        why.append(f"Thermal storage: {s['heat_mwh']['tes']}")
    if name == "radiative":
        why.append(f"Sky radiation removed {s['heat_mwh']['paths']['radiative_sky']:.0f} MWh/yr from the chilled-water return.")
    dis = []
    if row["water_change_m3"] > 1:
        dis.append(f"adds {row['water_change_m3']:.0f} m³/yr of water withdrawal")
    if row["energy_saved_mwh"] < 0:
        dis.append(f"increases facility energy by {-row['energy_saved_mwh']:.0f} MWh/yr in this climate")
    if s.get("eahe", {}).get("condensate_m3", 0) > 0:
        dis.append(f"{s['eahe']['condensate_m3']:.0f} m³/yr condensate in buried ducts (drainage, mould and biofilm risk)")
    if lib:
        dis += lib["failure_modes"][:3]
    return {
        "verdict": ("Recommended for further study" if row["beneficial"] and row["score"] >= 0.5 else
                    "Marginal benefit" if row["beneficial"] else "Not beneficial at this site"),
        "why_suitable": why, "climate_evidence": clim,
        "mechanism": lib["physical_mechanism"] if lib else (
            "Outdoor air replaces mechanically cooled air whenever its temperature and humidity satisfy the inlet envelope"
            if name == "economiser_direct" else
            "An air-to-air heat exchanger rejects hall heat to outdoor air without admitting it (no moisture or contaminant ingress)"),
        "annual_benefit": f"{row['energy_saved_mwh']:.0f} MWh/yr facility energy ({row['energy_saved_pct']:.1f} %) vs S1; "
                          f"PUE {row['pue']:.3f}" + (f"; {row['carbon_avoided_t']:.0f} t CO₂e/yr avoided" if row["carbon_avoided_t"] is not None else ""),
        "peak_benefit": f"peak facility power change {(-row['peak_reduction_kw'] if abs(row['peak_reduction_kw']) >= 0.5 else 0.0):+.0f} kW "
                        "(mechanical capacity is not reduced unless credited)",
        "geometry": _geometry(name, ev.config),
        "auxiliary": f"cooling electricity {s['energy_mwh']['cooling']:.0f} MWh/yr incl. fans/pumps/actuators; "
                     f"water {s['water']['withdrawal_m3']:.0f} m³/yr",
        "disadvantages": dis,
        "uncertainty": "; ".join(row["confidence"]["notes"]) or "see uncertainty analysis",
        "required_studies": STUDIES.get(name, []),
    }


def _geometry(name: str, cfg: ProjectConfig) -> dict:
    st = cfg.strategies
    if name == "stack":
        return {"chimney_height_m": st.stack.chimney_height_m, "chimney_area_m2": st.stack.chimney_area_m2,
                "n_chimneys": st.stack.n_chimneys, "inlet_area_m2": st.stack.inlet_area_m2_per_facade}
    if name == "eahe":
        e = st.eahe
        return {"n_pipes": e.n_pipes, "length_m": e.length_m, "diameter_m": e.diameter_m, "depth_m": e.depth_m,
                "spacing_m": e.spacing_m, "footprint_m2": e.n_pipes * e.spacing_m * e.length_m}
    if name == "envelope":
        return {"insulation_m": st.envelope.insulation_m, "thermal_mass_m": st.envelope.thermal_mass_m,
                "roof_solar_reflectance": st.envelope.roof_solar_reflectance}
    if name.startswith("facade"):
        return {"mode": st.facade.mode, "facades": st.facade.facades, "overhang_pf": st.facade.overhang_pf}
    if name == "storage":
        return {"capacity_kwh_th": st.storage.capacity_kwh_th, "max_discharge_kw": st.storage.max_discharge_kw}
    if name == "radiative":
        return {"panel_area_m2": st.radiative.panel_area_m2}
    return {}
