"""Impact metrics, status labels, baseline-relative changes and the non-additivity diagnostic.

Status of a value:
  modelled          — computed by an implemented physics/plant model (hourly simulation)
  derived           — calculated from modelled outputs (ratios, sums, tariffs × energy, factors × energy)
  estimated         — quantity take-off × user unit rates, or layout ratios (ranges where available)
  insufficient_data — the model supports the metric but a required input is missing (never shown as zero)
  not_modelled      — the application cannot determine this quantity
  not_applicable    — the metric does not apply to this configuration
  infeasible        — the scenario violates a hard constraint; values are not ranked
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

STATUSES = ("modelled", "derived", "estimated", "insufficient_data", "not_modelled", "not_applicable", "infeasible")


@dataclass(frozen=True)
class MetricDef:
    key: str
    label: str
    group: str
    unit: str
    lower_is_better: bool | None
    get: Callable[[dict], tuple]   # evaluation → (value, status, note[, low, high])
    abs_tol: float = 1e-6          # changes smaller than this are "no change"


def _s(ev: dict, *path, default=None):
    cur = ev["summary"]
    for p in path:
        if cur is None:
            return default
        cur = cur.get(p)
    return cur if cur is not None else default


def _m(*path, status="modelled"):
    return lambda ev: (_s(ev, *path), status, "")


def _other(ev):
    e = ev["summary"]["energy_mwh"]
    return e["facility"] - e["it"] - e["cooling"], "derived", "facility − IT − cooling (UPS/distribution losses, lighting, controls, other)"


def _co2(ev):
    v = _s(ev, "carbon", "operational_t")
    if v is None:
        return None, "insufficient_data", "no grid emission factor supplied"
    return v, "derived", f"facility electricity × {ev['summary']['carbon']['intensity_basis']}"


def _embodied(ev):
    emb = ev.get("embodied") or {}
    if not emb.get("items"):
        return 0.0, "estimated", "no added strategy materials"
    if not emb.get("complete"):
        return None, "insufficient_data", "emission factors missing for: " + ", ".join(emb.get("data_gaps", []))
    return emb["total_kgco2e"] / 1000.0, "estimated", "added strategy materials × user emission factors (base building not included)"


def _capex(ev):
    c = ev["capex"]
    if not c["complete"]:
        return None, "insufficient_data", "unit rates missing for: " + ", ".join(c["missing_rates"])
    t = c["total"]
    return t["base"], "estimated", "Σ quantity × unit rate × (1 + contingency)", t["low"], t["high"]


def _opex(key, label):
    def f(ev):
        v = ev["opex"][key]
        if v is None:
            if key == "maintenance_per_year":
                return None, "insufficient_data", "maintenance cost not supplied for: " + ", ".join(ev["opex"]["maintenance_missing"])
            return None, "insufficient_data", f"{label} tariff not supplied"
        return v, "derived" if key != "maintenance_per_year" else "estimated", ""
    return f


def _space(cat):
    def f(ev):
        c = ev["space"]["categories"][cat]
        return c["base"], c["status"], c.get("basis", ""), c["low"], c["high"]
    return f


def _free_heat(ev):
    p = ev["summary"]["heat_mwh"]["paths"]
    return sum(v for k, v in p.items() if k not in ("plant_condenser", "unmet")), "modelled", "heat leaving through non-refrigerant paths"


def _natural(ev):
    nv = (ev.get("diagnostics") or {}).get("natural_ventilation")
    if not nv:
        return None, "not_applicable", "no stack ventilation in this configuration"
    return nv["sufficient_hours"], "modelled", "hours natural stack flow alone met server airflow"


METRICS: list[MetricDef] = [
    MetricDef("cooling_mwh", "Cooling electricity", "energy", "MWh/yr", True, _m("energy_mwh", "cooling"), 1e-3),
    MetricDef("facility_mwh", "Total facility electricity", "energy", "MWh/yr", True, _m("energy_mwh", "facility"), 1e-3),
    MetricDef("it_mwh", "IT electricity", "energy", "MWh/yr", None, _m("energy_mwh", "it"), 1e-3),
    MetricDef("other_mwh", "Other facility electricity", "energy", "MWh/yr", True, _other, 1e-3),
    MetricDef("pue", "PUE (annual)", "energy", "–", True, _m("pue", "annual", status="derived"), 1e-6),
    MetricDef("peak_facility_kw", "Peak facility power", "energy", "kW", True, _m("power_kw", "peak_facility"), 1e-3),
    MetricDef("peak_cooling_kw", "Peak cooling electricity", "energy", "kW", True, _m("power_kw", "peak_cooling"), 1e-3),
    MetricDef("water_withdrawal_m3", "Water withdrawal", "water", "m³/yr", True, _m("water", "withdrawal_m3"), 1e-3),
    MetricDef("water_consumption_m3", "Water consumption (evaporated)", "water", "m³/yr", True, _m("water", "consumption_m3"), 1e-3),
    MetricDef("wue_withdrawal", "WUE (withdrawal ÷ IT energy)", "water", "L/kWh", True, _m("water", "wue_withdrawal_l_per_kwh", status="derived"), 1e-6),
    MetricDef("wue_consumption", "WUE (consumption ÷ IT energy)", "water", "L/kWh", True, _m("water", "wue_consumption_l_per_kwh", status="derived"), 1e-6),
    MetricDef("co2e_t", "Operational CO₂e", "carbon", "t/yr", True, _co2, 1e-3),
    MetricDef("embodied_co2e_t", "Embodied CO₂e of added strategy materials", "carbon", "t", True, _embodied, 1e-3),
    MetricDef("capex", "Initial capital cost (CAPEX)", "cost", "currency", True, _capex, 0.5),
    MetricDef("opex_electricity", "Annual electricity cost", "cost", "currency/yr", True, _opex("electricity_per_year", "electricity"), 0.5),
    MetricDef("opex_water", "Annual water cost", "cost", "currency/yr", True, _opex("water_per_year", "water"), 0.5),
    MetricDef("opex_maintenance", "Annual maintenance of added strategies", "cost", "currency/yr", True, _opex("maintenance_per_year", "maintenance"), 0.5),
    MetricDef("site_at_grade_m2", "Site footprint at grade", "space", "m²", True, _space("site_at_grade"), 0.01),
    MetricDef("building_footprint_m2", "Building footprint", "space", "m²", True, _space("building_footprint"), 0.01),
    MetricDef("it_area_m2", "IT white space", "space", "m²", None, _space("it_area"), 0.01),
    MetricDef("mep_indoor_m2", "Indoor mechanical & electrical area", "space", "m²", True, _space("mep_indoor"), 0.01),
    MetricDef("outdoor_yard_m2", "Outdoor equipment yard", "space", "m²", True, _space("outdoor_yard"), 0.01),
    MetricDef("roof_used_m2", "Roof area used", "space", "m²", True, _space("roof_used"), 0.01),
    MetricDef("facade_zone_m2", "External facade projection zone", "space", "m²", True, _space("facade_zone"), 0.01),
    MetricDef("underground_m2", "Underground installation area", "space", "m²", True, _space("underground_field"), 0.01),
    MetricDef("excavation_m3", "Excavation volume", "space", "m³", True, lambda ev: (ev["space"]["excavation_m3"], "derived", ""), 0.01),
    MetricDef("free_heat_mwh", "Heat removed without mechanical refrigeration", "thermal", "MWh_th/yr", False, _free_heat, 1e-3),
    MetricDef("residual_mech_mwh", "Residual mechanical cooling", "thermal", "MWh_th/yr", True,
              _m("free_cooling", "residual_mechanical_cooling_mwh"), 1e-3),
    MetricDef("free_cooling_h", "Full free-cooling hours", "thermal", "h/yr", False, _m("free_cooling", "full_hours"), 0.5),
    MetricDef("peak_coil_kw", "Peak mechanical cooling load", "thermal", "kW_th", True, _m("power_kw", "peak_plant_coil_load"), 1e-3),
    MetricDef("installed_cooling_kw", "Installed mechanical cooling capacity", "thermal", "kW_th", True, _m("sizing", "installed_capacity_kw"), 1e-3),
    MetricDef("natural_sufficient_h", "Hours natural airflow met server demand", "thermal", "h/yr", False, _natural, 0.5),
    MetricDef("unmet_h", "Unmet cooling hours", "thermal", "h/yr", True, _m("thermal", "unmet_hours"), 0.5),
    MetricDef("violation_h", "Server-inlet envelope violation hours", "thermal", "h/yr", True, _m("thermal", "inlet_envelope_violation_hours"), 0.5),
    MetricDef("max_inlet_c", "Maximum server inlet temperature", "thermal", "°C", True, _m("thermal", "max_inlet_c"), 1e-3),
]
BY_KEY = {m.key: m for m in METRICS}


def pct_change(value, base) -> tuple[float | None, str | None]:
    """((value − base) / base) × 100, or (None, reason) when undefined."""
    if value is None or base is None:
        return None, "value unavailable"
    if base == 0:
        return None, "baseline is zero — percentage undefined"
    return 100.0 * (value - base) / base, None


# A percentage of a near-zero baseline is mathematically defined but misleading (7 m³ → 7 000 m³ reads as +100 000 %).
# Below these floors the percentage is withheld and the absolute change is shown instead. Water floors are 1 % of a
# reference 0.5 L/kWh × IT energy (the same reference the scenario objective uses).
def _water_floor(base: dict) -> float:
    return 0.01 * 0.5 * base["summary"]["energy_mwh"]["it"]   # m³ (0.5 L/kWh × MWh = 0.5 m³/MWh)


PCT_FLOORS: dict[str, Callable[[dict], float]] = {
    "water_withdrawal_m3": _water_floor, "water_consumption_m3": _water_floor,
    "wue_withdrawal": lambda base: 0.005, "wue_consumption": lambda base: 0.005,
}


def metric_values(ev: dict) -> dict[str, dict]:
    out = {}
    for m in METRICS:
        if not ev.get("simulated"):
            out[m.key] = {"value": None, "status": "infeasible", "note": "not simulated: " + "; ".join(ev.get("infeasible_reasons", []))}
            continue
        r = m.get(ev)
        rec = {"value": r[0], "status": r[1], "note": r[2]}
        if len(r) == 5:
            rec["low"], rec["high"] = r[3], r[4]
        out[m.key] = rec
    return out


def compare(base: dict, ev: dict, currency: str = "") -> list[dict]:
    """Baseline-relative records for every metric (absolute values, change, % change, status)."""
    bv, sv = metric_values(base), metric_values(ev)
    rows = []
    for m in METRICS:
        b, s = bv[m.key], sv[m.key]
        unit = m.unit.replace("currency", currency) if currency else m.unit
        row = {"key": m.key, "label": m.label, "group": m.group, "unit": unit, "lower_is_better": m.lower_is_better,
               "baseline": b["value"], "value": s["value"], "status": s["status"], "baseline_status": b["status"], "note": s["note"]}
        if "low" in s:
            row["range"] = [s["low"], s["high"]]
        delta = None
        if m.key == "capex" and ev.get("capex_incremental"):
            inc = ev["capex_incremental"]
            if inc["complete"]:
                delta = inc["delta"]["base"]
                row["delta_range"] = [inc["delta"]["low"], inc["delta"]["high"]]
                if s["status"] == "insufficient_data":
                    row["status"] = "estimated"
                    row["note"] = "incremental cost complete; " + (inc.get("pct_note") or "")
            row["pct"], row["pct_note"] = inc.get("pct"), inc.get("pct_note")
            if inc.get("pct_range"):
                row["pct_range"] = inc["pct_range"]
        elif s["value"] is not None and b["value"] is not None:
            delta = s["value"] - b["value"]
            row["pct"], row["pct_note"] = pct_change(s["value"], b["value"])
            floor = PCT_FLOORS.get(m.key)
            if floor and b["value"] != 0 and abs(b["value"]) < floor(base):
                row["pct"] = None
                row["pct_note"] = (f"baseline ({b['value']:.3g} {unit}) is below the materiality floor ({floor(base):.3g} {unit}); "
                                   "a percentage would be misleading — compare absolute values")
            if "low" in s and "low" in b:
                lo, hi = s["low"] - b["low"], s["high"] - b["high"]
                row["delta_range"] = [min(lo, hi), max(lo, hi)]
        else:
            row["pct"], row["pct_note"] = None, ("value unavailable" if s["value"] is None else "baseline unavailable")
        if delta is not None and abs(delta) <= m.abs_tol:
            delta = 0.0
            row["pct"] = 0.0 if row.get("pct") is not None else row.get("pct")
        row["delta"] = delta
        row["direction"] = None if delta is None else ("no change" if delta == 0 else "decrease" if delta < 0 else "increase")
        if delta is None or m.lower_is_better is None or delta == 0:
            row["assessment"] = "neutral" if delta == 0 else None
        else:
            row["assessment"] = "improvement" if (delta < 0) == m.lower_is_better else "trade-off"
        if ev.get("simulated") and not ev.get("feasible") and row["status"] not in ("infeasible",):
            row["scenario_infeasible"] = True
        rows.append(row)
    return rows


INTERACTION_METRICS = [("facility_mwh", 1.0), ("cooling_mwh", 1.0), ("water_withdrawal_m3", 1.0), ("co2e_t", 0.5), ("capex", 1.0),
                       ("site_at_grade_m2", 1.0)]


def interaction(d_a: float | None, d_b: float | None, d_ab: float | None, abs_tol: float) -> dict:
    """Non-additivity diagnostic I = Δ_AB − Δ_A − Δ_B (deltas vs baseline; lower-is-better metrics).

    Classification (diagnostic only, not a causal attribution):
      additive       |I| within tolerance
      complementary  combined change better than the sum of individual changes
      overlapping    both reduce the metric; combined reduction exceeds the better one but is less than the sum
      conflicting    combined result worse than the better individual strategy, or worse than additive otherwise
    """
    if d_a is None or d_b is None or d_ab is None:
        return {"interaction": None, "class": "not evaluated", "note": "a required value is unavailable"}
    i = d_ab - (d_a + d_b)
    tol = max(0.02 * (abs(d_a) + abs(d_b)), abs_tol)
    s_a, s_b, s_ab = -d_a, -d_b, -d_ab
    if abs(i) <= tol:
        cls = "additive"
    elif s_a > tol and s_b > tol:
        if s_ab > s_a + s_b:
            cls = "complementary"
        elif s_ab < max(s_a, s_b):
            cls = "conflicting"
        else:
            cls = "overlapping"
    else:
        cls = "complementary" if i < 0 else "conflicting"
    return {"interaction": i, "sum_individual": d_a + d_b, "combined": d_ab, "class": cls, "tolerance": tol}
