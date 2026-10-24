"""Initial capital cost (CAPEX) and annual operating cost (OPEX) estimates.

CAPEX = Σ quantity × installed unit rate × (1 + contingency) over the line items of the scenario's quantity
take-off. Unit rates are never invented by the platform: each comes from ``economics.unit_rates`` with its source,
basis year and low/base/high range. A line with a non-zero quantity and no rate makes the total incomplete; it is
reported as missing, never as zero.

The incremental CAPEX of a scenario is evaluated line by line against the baseline take-off, so a line whose
quantity is unchanged contributes exactly zero even when its rate is unknown, and shared lines (sized once per
scenario) cannot be double-counted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app.schemas.config import ProjectConfig


@dataclass(frozen=True)
class CostLine:
    id: str
    label: str
    category: str      # building | equipment | installation & civil | controls
    scope: str         # baseline | shared | strategy
    owners: tuple[str, ...]
    includes: str
    excludes: str = ""


COST_LINES: list[CostLine] = [
    CostLine("building_shell", "Building shell and core", "building", "baseline", (),
             "structure, baseline envelope, roof, internal core per m² of gross floor area", "land, site works, fees, IT equipment"),
    CostLine("it_electrical_fitout", "Power train and white-space fit-out", "equipment", "baseline", (),
             "UPS, switchgear, generators, distribution and white-space fit-out per kW of installed IT capacity", "IT equipment"),
    CostLine("mech_plant", "Mechanical cooling plant (incl. redundancy)", "equipment", "baseline", (),
             "chillers/DX units, chilled-water pumps and piping per installed kW_th"),
    CostLine("heat_rejection", "Cooling towers / dry coolers", "equipment", "baseline", (), "heat-rejection equipment per kW_th rejected"),
    CostLine("crah", "CRAH / air-handling units", "equipment", "baseline", (), "computer-room air handlers per m³/s installed"),
    CostLine("oa_intake", "Outdoor-air intake louvres, dampers and filters", "installation & civil", "shared",
             ("economiser_direct", "stack", "evap_direct", "eahe"), "intake louvres, motorised dampers, filter banks, relief path",
             "gas-phase filtration for corrosive sites"),
    CostLine("water_treatment", "Water treatment and storage", "equipment", "shared", ("iec", "evap_direct", "evap_precool"),
             "treatment, softening and buffer storage per m³/day of peak withdrawal"),
    CostLine("supervisory_controls", "Hybrid supervisory control platform", "controls", "shared",
             ("economiser_direct", "economiser_indirect", "stack", "solar_chimney", "facade_kinetic", "eahe", "storage", "radiative", "iec",
              "evap_direct", "evap_precool"), "controller hardware, software and commissioning (once per facility)"),
    CostLine("controls_integration", "Controls integration per subsystem", "controls", "shared",
             ("economiser_direct", "economiser_indirect", "stack", "solar_chimney", "facade_kinetic", "eahe", "storage", "radiative", "iec",
              "evap_direct", "evap_precool"), "sensors, actuators wiring and BMS points per controlled subsystem"),
    CostLine("containment", "Full aisle containment", "installation & civil", "strategy", ("containment_full",), "doors, roof panels, blanking per rack",
             "fire-suppression modifications"),
    CostLine("stack_shafts", "Stack shafts (concrete)", "installation & civil", "strategy", ("stack",), "shaft walls in place incl. formwork",
             "structural reinforcement of the main frame, fire/smoke control, planning costs"),
    CostLine("stack_outlet_dampers", "Stack outlet dampers and weather protection", "equipment", "strategy", ("stack",), "outlet dampers, cowls"),
    CostLine("solar_collectors", "Solar-chimney collector glazing", "installation & civil", "strategy", ("solar_chimney",), "glazed collector area"),
    CostLine("eahe_pipe", "Earth-air pipes", "installation & civil", "strategy", ("eahe",), "pipe supply and laying per metre", "condensate drainage pits"),
    CostLine("eahe_excavation", "EAHE excavation and backfill", "installation & civil", "strategy", ("eahe",), "trenching, bedding and backfill per m³",
             "dewatering, rock excavation, contaminated soil"),
    CostLine("eahe_fans", "EAHE fans", "equipment", "strategy", ("eahe",), "intake fans per m³/s"),
    CostLine("envelope_insulation", "Added envelope insulation", "installation & civil", "strategy", ("envelope",), "insulation per m³ installed"),
    CostLine("envelope_mass", "Added thermal mass", "installation & civil", "strategy", ("envelope",), "concrete/masonry per m³ installed",
             "structural strengthening for the added mass"),
    CostLine("cool_roof", "Cool-roof finish", "installation & civil", "strategy", ("envelope",), "reflective membrane/coating per m²"),
    CostLine("facade_fixed", "Fixed external shading", "installation & civil", "strategy", ("facade_fixed",), "overhangs and fins per m² of facade"),
    CostLine("facade_kinetic", "Kinetic louvre facade", "installation & civil", "strategy", ("facade_kinetic",), "louvres, actuators per m² of facade"),
    CostLine("tes", "Chilled-water thermal storage", "equipment", "strategy", ("storage",), "tank, diffusers, pumps per kWh_th"),
    CostLine("radiative_panels", "Radiative sky-cooling panels", "equipment", "strategy", ("radiative",), "panels, piping, roof mounting per m²",
             "roof structural upgrade"),
    CostLine("iec_units", "Indirect evaporative cooling units", "equipment", "strategy", ("iec",), "packaged IEC units per m³/s"),
    CostLine("dec_units", "Direct evaporative media sections", "equipment", "strategy", ("evap_direct",), "media sections per m³/s"),
    CostLine("indirect_economiser_hx", "Indirect economiser heat exchangers", "equipment", "strategy", ("economiser_indirect",),
             "air-to-air heat-exchanger units per m³/s"),
    CostLine("evap_precool_media", "Condenser pre-cooling media", "equipment", "strategy", ("evap_precool",), "pads and distribution per kW_th"),
]
LINES = {c.id: c for c in COST_LINES}

# Legacy lump sums (economics.capex[strategy key]) replace these lines for that strategy.
LUMP_SUM_LINES = {
    "stack": ["stack_shafts", "stack_outlet_dampers", "solar_collectors"],
    "envelope": ["envelope_insulation", "envelope_mass", "cool_roof"],
    "facade": ["facade_fixed", "facade_kinetic"],
    "eahe": ["eahe_pipe", "eahe_excavation", "eahe_fans"],
    "storage": ["tes"], "radiative": ["radiative_panels"],
    "evaporative": ["iec_units", "dec_units", "evap_precool_media"], "economiser": ["indirect_economiser_hx"],
}

EXCLUDED_GLOBAL = ["land acquisition", "design and permitting fees", "IT equipment", "financing", "escalation beyond the rate basis year",
                   "structural, fire and planning items not listed per line"]


def _rng(rate, qty: float, factor: float) -> dict | None:
    if rate is None or rate.base is None:
        return None
    lo = rate.low if rate.low is not None else rate.base
    hi = rate.high if rate.high is not None else rate.base
    return {"low": qty * lo * factor, "base": qty * rate.base * factor, "high": qty * hi * factor}


def estimate(cfg: ProjectConfig, takeoff: dict[str, dict], active: list[str]) -> dict:
    """CAPEX of one scenario from its take-off."""
    ec = cfg.economics
    factor = 1.0 + ec.contingency_fraction
    lump_keys = {k for k, v in ec.capex.items() if v.value is not None and k in LUMP_SUM_LINES}
    replaced = {line for k in lump_keys for line in LUMP_SUM_LINES[k]}
    lines, missing = [], []
    total = {"low": 0.0, "base": 0.0, "high": 0.0}
    for c in COST_LINES:
        qd = takeoff[c.id]
        qty = qd["value"]
        rate = ec.unit_rates.get(c.id)
        if c.id in replaced:
            cost, status = {"low": 0.0, "base": 0.0, "high": 0.0}, "replaced by lump sum"
        elif qty == 0:
            cost, status = {"low": 0.0, "base": 0.0, "high": 0.0}, "not present"
        else:
            cost = _rng(rate, qty, factor)
            status = "estimated" if cost is not None else "insufficient data"
            if cost is None:
                missing.append(c.id)
        if cost is not None:
            for k in total:
                total[k] += cost[k]
        lines.append({**asdict(c), "quantity": qty, "unit": qd["unit"], "quantity_status": qd["status"], "quantity_basis": qd["basis"],
                      "rate": rate.model_dump() if rate else None, "cost": cost, "status": status})
    for k in sorted(lump_keys):
        owners = [a for a in active if _maint_key(a) == k]
        if not owners:
            continue
        v = ec.capex[k].value * factor
        for key in total:
            total[key] += v
        lines.append({"id": f"lump_sum_{k}", "label": f"Lump-sum capital cost: {k}", "category": "strategy lump sum", "scope": "strategy",
                      "owners": owners, "includes": ec.capex[k].note, "excludes": "",
                      "quantity": 1.0, "unit": "lump sum", "quantity_status": "user", "quantity_basis": ec.capex[k].source,
                      "rate": {"base": ec.capex[k].value, "source": ec.capex[k].source}, "cost": {"low": v, "base": v, "high": v},
                      "status": "user lump sum"})
    return {"currency": ec.currency, "contingency_fraction": ec.contingency_fraction, "lines": lines, "missing_rates": missing,
            "complete": not missing, "total": total if not missing else None, "partial_total": total,
            "excluded": EXCLUDED_GLOBAL}


def incremental(base_est: dict, scen_est: dict) -> dict:
    """Scenario − baseline CAPEX, line by line (unchanged quantities cancel exactly)."""
    b_lines = {ln["id"]: ln for ln in base_est["lines"]}
    rows, missing = [], []
    delta = {"low": 0.0, "base": 0.0, "high": 0.0}
    for ln in scen_est["lines"]:
        bl = b_lines.get(ln["id"])
        qb = bl["quantity"] if bl else 0.0
        qs = ln["quantity"]
        cb = bl["cost"] if bl else {"low": 0.0, "base": 0.0, "high": 0.0}
        cs = ln["cost"]
        if abs(qs - qb) <= 1e-9 * max(1.0, abs(qb)) and ln["status"] == (bl or {}).get("status"):
            continue
        if cs is None or cb is None:
            missing.append(ln["id"])
            d = None
        else:
            lo, hi = cs["low"] - cb["low"], cs["high"] - cb["high"]
            d = {"low": min(lo, hi), "base": cs["base"] - cb["base"], "high": max(lo, hi)}
            for k in delta:
                delta[k] += d[k]
        rows.append({"id": ln["id"], "label": ln["label"], "unit": ln["unit"], "quantity_baseline": qb, "quantity_scenario": qs,
                     "delta_quantity": qs - qb, "delta_cost": d, "owners": ln["owners"], "scope": ln["scope"]})
    complete = not missing
    out = {"lines": rows, "missing_rates": missing, "complete": complete, "delta": delta if complete else None}
    if complete and base_est["complete"] and base_est["total"]["base"] > 0:
        out["pct"] = 100.0 * delta["base"] / base_est["total"]["base"]
        out["pct_range"] = [100.0 * delta["low"] / base_est["total"]["base"], 100.0 * delta["high"] / base_est["total"]["base"]]
    else:
        out["pct"] = None
        out["pct_note"] = ("baseline CAPEX incomplete (unit rates missing for: " + ", ".join(base_est["missing_rates"]) + ")"
                           if not base_est["complete"] else "incremental CAPEX incomplete" if not complete else "baseline CAPEX is zero")
    return out


def opex(cfg: ProjectConfig, summary: dict, active: list[str]) -> dict:
    """Annual operating cost from simulated electricity and water and user-supplied maintenance."""
    ec = cfg.economics
    years = summary["period"]["years"] or 1.0
    elec = summary["costs"]["electricity"]
    water = summary["costs"]["water"]
    keys = sorted({_maint_key(a) for a in active if _maint_key(a)})
    maint_missing = [k for k in keys if k not in ec.maintenance_per_year or ec.maintenance_per_year[k].value is None]
    maint = sum(ec.maintenance_per_year[k].value for k in keys if k not in maint_missing)
    return {"currency": ec.currency,
            "electricity_per_year": elec / years if elec is not None else None,
            "water_per_year": water / years if water is not None else None,
            "maintenance_per_year": maint if not maint_missing else None,
            "maintenance_missing": maint_missing,
            "maintenance_note": "baseline plant maintenance is common to all scenarios and excluded; only added strategies' maintenance counts"}


def _maint_key(measure: str) -> str | None:
    for k in ("economiser", "stack", "envelope", "facade", "eahe", "storage", "radiative"):
        if measure.startswith(k):
            return k
    if measure in ("iec", "evap_direct", "evap_precool"):
        return "evaporative"
    if measure == "solar_chimney":
        return "stack"
    return None


def economics(cfg: ProjectConfig, inc: dict, base_opex: dict, scen_opex: dict, base_est: dict, scen_est: dict) -> dict:
    """Payback and NPV only when every ingredient is known; otherwise state what is missing."""
    ec = cfg.economics
    missing = []
    if not inc["complete"]:
        missing.append("unit rates for: " + ", ".join(inc["missing_rates"]))
    if base_opex["electricity_per_year"] is None:
        missing.append("electricity tariff")
    if scen_opex["maintenance_per_year"] is None:
        missing.append("maintenance cost for: " + ", ".join(scen_opex["maintenance_missing"]))
    out = {"discount_rate": ec.discount_rate, "horizon_years": ec.horizon_years, "currency": ec.currency}

    def tot(o):
        return (o["electricity_per_year"] or 0) + (o["water_per_year"] or 0) + (o["maintenance_per_year"] or 0)

    if base_opex["electricity_per_year"] is not None:
        out["annual_opex_baseline"] = tot(base_opex)
        out["annual_opex_scenario"] = tot(scen_opex) if scen_opex["maintenance_per_year"] is not None else None
        out["annual_energy_water_saving"] = ((base_opex["electricity_per_year"] or 0) + (base_opex["water_per_year"] or 0)
                                             - (scen_opex["electricity_per_year"] or 0) - (scen_opex["water_per_year"] or 0))
    if missing:
        out.update({"npv": None, "simple_payback_years": None, "discounted_payback_years": None,
                    "note": "insufficient data: " + "; ".join(missing)})
        return out
    saving = out["annual_opex_baseline"] - out["annual_opex_scenario"]
    d_capex = inc["delta"]["base"]
    r, n = ec.discount_rate, ec.horizon_years
    # Replacement of incremental components whose service life is shorter than the horizon.
    repl = []
    s_lines = {ln["id"]: ln for ln in scen_est["lines"]}
    for row in inc["lines"]:
        rate = (s_lines.get(row["id"]) or {}).get("rate") or {}
        life = rate.get("service_life_years")
        if life and row["delta_cost"] and life < n:
            t = life
            while t < n:
                repl.append((t, row["delta_cost"]["base"]))
                t += life
    cash = [-d_capex] + [saving] * n
    for t, c in repl:
        cash[int(round(t))] -= c
    disc = [c / (1 + r) ** t for t, c in enumerate(cash)]
    out["npv"] = sum(disc)
    out["annual_net_saving"] = saving
    out["replacements"] = [{"year": t, "cost": c} for t, c in repl]
    if d_capex <= 0 and saving >= 0:
        out["simple_payback_years"] = 0.0
        out["payback_note"] = "no incremental capital cost"
    elif saving > 0:
        out["simple_payback_years"] = d_capex / saving
    else:
        out["simple_payback_years"] = None
        out["payback_note"] = "annual operating cost does not decrease"
    cum, dp = 0.0, None
    for t, v in enumerate(disc):
        cum += v
        if t > 0 and cum >= 0:
            dp = t
            break
    out["discounted_payback_years"] = dp
    if dp is None and out.get("simple_payback_years") not in (None, 0.0):
        out["payback_note"] = f"not repaid within the {n}-year horizon (discounted)"
    return out
