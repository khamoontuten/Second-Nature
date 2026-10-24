"""Lifecycle cost and embodied carbon.

Only costs and emission factors supplied by the engineer (tagged quoted / measured / assumed) are
used. Where an input is missing the result is a *data gap*, never a manufactured estimate. Material
quantities are derived from the configured geometry so that the engineer only needs unit factors.
"""

from __future__ import annotations

import math

from app.schemas.config import ProjectConfig

STRATEGY_KEYS = ["stack", "envelope", "facade", "eahe", "storage", "radiative", "evaporative", "economiser", "baseline_plant"]


def material_quantities(cfg: ProjectConfig, base: ProjectConfig | None = None) -> dict[str, dict]:
    """Approximate material quantities added by each enabled strategy (relative to the baseline building)."""
    st = cfg.strategies
    b = cfg.building
    q: dict[str, dict] = {}
    if st.eahe.enabled:
        e = st.eahe
        r_i = e.diameter_m / 2
        r_o = r_i + e.wall_thickness_m
        q["eahe"] = {"hdpe_kg": e.n_pipes * math.pi * (r_o**2 - r_i**2) * e.length_m * 950.0,
                     "excavation_m3": e.n_pipes * e.spacing_m * e.length_m * (e.depth_m + 0.5),
                     "footprint_m2": e.n_pipes * e.spacing_m * e.length_m}
    if st.stack.enabled:
        s = st.stack
        perim = 4 * math.sqrt(s.chimney_area_m2)
        q["stack"] = {"concrete_m3": s.n_chimneys * perim * s.chimney_height_m * 0.2,
                      "collector_glazing_m2": s.collector_area_m2 * s.n_chimneys if s.solar_chimney else 0.0}
    walls = 2 * (b.length_m + b.width_m) * b.height_m
    roof = b.length_m * b.width_m
    if st.envelope.enabled:
        base_ins = (base.building.insulation_m if base else b.insulation_m)
        base_mass = (base.building.thermal_mass_m if base else b.thermal_mass_m)
        q["envelope"] = {"mineral_wool_m3": (walls + roof) * max(st.envelope.insulation_m - base_ins, 0.0),
                         "concrete_m3": (walls + roof) * max(st.envelope.thermal_mass_m - base_mass, 0.0)}
    if st.facade.enabled:
        area = sum((b.length_m if f in "NS" else b.width_m) * b.height_m for f in st.facade.facades)
        q["facade"] = {"aluminium_kg": area * 0.003 * 2700.0 * 1.3, "panel_area_m2": area,
                       "actuators": area / st.facade.panel_area_m2 if st.facade.mode == "kinetic" else 0}
    if st.radiative.enabled:
        q["radiative"] = {"panel_area_m2": st.radiative.panel_area_m2}
    if st.storage.enabled:
        q["storage"] = {"water_m3": st.storage.capacity_kwh_th * 3.6e6 / (4186.0 * 8.0) / 1000.0}
    return q


MATERIAL_UNITS = {"hdpe_kg": "kg", "excavation_m3": "m3", "concrete_m3": "m3", "mineral_wool_m3": "m3", "aluminium_kg": "kg",
                  "panel_area_m2": "m2", "collector_glazing_m2": "m2", "water_m3": "m3"}


def embodied_carbon(cfg: ProjectConfig, base: ProjectConfig | None = None) -> dict:
    """Embodied carbon (kgCO2e) per material where the engineer supplied an emission factor; gaps otherwise."""
    quantities = material_quantities(cfg, base)
    factors = cfg.economics.embodied_factors_kgco2e_per_kg
    total, gaps, items = 0.0, [], []
    for strat, mats in quantities.items():
        for mat, qty in mats.items():
            if mat in ("footprint_m2", "actuators") or qty == 0:
                continue
            f = factors.get(mat)
            if f is None or f.value is None:
                gaps.append(f"{strat}.{mat}")
                items.append({"strategy": strat, "material": mat, "quantity": qty, "unit": MATERIAL_UNITS.get(mat, ""),
                              "factor": None, "kgco2e": None, "source": "missing"})
            else:
                kg = qty * f.value
                total += kg
                items.append({"strategy": strat, "material": mat, "quantity": qty, "unit": MATERIAL_UNITS.get(mat, ""),
                              "factor": f.value, "kgco2e": kg, "source": f.source})
    return {"total_kgco2e": total if not gaps else (total if total else None), "complete": not gaps, "items": items,
            "data_gaps": gaps, "quantities": quantities}


def lifecycle(cfg: ProjectConfig, summary: dict, baseline_summary: dict | None, strategy_keys: list[str],
              base_cfg: ProjectConfig | None = None) -> dict:
    """NPV of operating savings vs baseline, capital cost (if supplied), payback and lifecycle carbon.

    When unit rates are configured, the incremental capital cost comes from the quantity take-off of the
    simulated configurations (app.impacts.costing — the same estimate the impact studies use); otherwise only
    per-strategy lump sums in ``economics.capex`` are used.
    """
    ec = cfg.economics
    r, n = ec.discount_rate, ec.horizon_years
    annuity = sum(1 / (1 + r) ** t for t in range(1, n + 1))
    years = summary["period"]["years"] or 1.0
    elec_cost = summary["costs"]["electricity"]
    water_cost = summary["costs"]["water"]
    annual_cost = None if elec_cost is None else (elec_cost + (water_cost or 0.0)) / years
    capex, capex_gaps, capex_items = 0.0, [], []
    if base_cfg is not None and ec.unit_rates:
        from app.impacts import costing, quantities

        b_sum = baseline_summary or summary
        est_b = costing.estimate(base_cfg, quantities.takeoff(base_cfg, b_sum, base_cfg), quantities.active_measures(base_cfg, base_cfg))
        act = quantities.active_measures(cfg, base_cfg)
        est_s = costing.estimate(cfg, quantities.takeoff(cfg, summary, base_cfg), act)
        inc = costing.incremental(est_b, est_s)
        capex_gaps = list(inc["missing_rates"])
        capex = inc["delta"]["base"] if inc["complete"] else 0.0
        capex_items = [{"strategy": ", ".join(ln["owners"]) or ln["scope"], "item": ln["label"], "value": (ln["delta_cost"] or {}).get("base"),
                        "source": "unit rates (quantity take-off)", "note": f"Δ quantity {ln['delta_quantity']:.3g} {ln['unit']}"}
                       for ln in inc["lines"]]
    else:
        for k in strategy_keys:
            item = ec.capex.get(k)
            if item is None or item.value is None:
                capex_gaps.append(k)
            else:
                capex += item.value
                capex_items.append({"strategy": k, "value": item.value, "source": item.source, "note": item.note})
    maint = sum((ec.maintenance_per_year[k].value or 0.0) for k in strategy_keys if k in ec.maintenance_per_year)
    out = {"currency": ec.currency, "reference_year": ec.reference_year, "discount_rate": r, "horizon_years": n,
           "annual_operating_cost": annual_cost, "capex": capex if not capex_gaps else None, "capex_items": capex_items,
           "capex_data_gaps": capex_gaps, "annual_maintenance": maint or None}
    if baseline_summary is not None and annual_cost is not None and baseline_summary["costs"]["electricity"] is not None:
        b_years = baseline_summary["period"]["years"] or 1.0
        b_cost = (baseline_summary["costs"]["electricity"] + (baseline_summary["costs"]["water"] or 0.0)) / b_years
        saving = b_cost - annual_cost - maint
        out["annual_saving_vs_baseline"] = saving
        out["pv_of_savings"] = saving * annuity
        if not capex_gaps:
            out["npv"] = saving * annuity - capex
            out["simple_payback_years"] = (capex / saving) if saving > 0 and capex > 0 else (0.0 if capex == 0 else None)
        else:
            out["npv"] = None
            out["simple_payback_years"] = None
            out["note"] = "capital cost missing for: " + ", ".join(capex_gaps)
    ec_res = embodied_carbon(cfg)
    op = summary["carbon"]["operational_t"]
    out["embodied"] = ec_res
    out["lifecycle_carbon_t"] = (None if (op is None or ec_res["total_kgco2e"] is None or not ec_res["complete"])
                                 else ec_res["total_kgco2e"] / 1000.0 + op / years * n)
    out["operational_carbon_lifetime_t"] = None if op is None else op / years * n
    return out
