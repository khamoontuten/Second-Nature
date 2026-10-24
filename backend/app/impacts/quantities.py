"""Quantity take-off for one simulated configuration.

Quantities come from the configuration geometry ("derived") or from the simulation's plant sizing and water
balance ("modelled"). They are computed for the scenario as a whole, so infrastructure used by several strategies
(outdoor-air intake, water treatment, supervisory controls) appears once, sized for the largest need.
"""

from __future__ import annotations

import math

from app.impacts.catalogue import MEASURES
from app.schemas.config import ProjectConfig

OA_MEASURES = ("economiser_direct", "stack", "evap_direct", "eahe")


def active_measures(cfg: ProjectConfig, base: ProjectConfig | None = None) -> list[str]:
    """Catalogue measures switched on in ``cfg`` (containment counts only when it differs from the baseline)."""
    st = cfg.strategies
    on = []
    if cfg.it.containment == "full" and (base is None or base.it.containment != "full"):
        on.append("containment_full")
    if st.economiser.enabled:
        on.append(f"economiser_{st.economiser.kind}")
    if st.stack.enabled:
        on.append("stack")
        if st.stack.solar_chimney:
            on.append("solar_chimney")
    if st.envelope.enabled:
        on.append("envelope")
    if st.facade.enabled:
        on.append(f"facade_{st.facade.mode}")
    if st.eahe.enabled:
        on.append("eahe")
    if st.storage.enabled:
        on.append("storage")
    if st.radiative.enabled:
        on.append("radiative")
    if st.evaporative.enabled:
        on.append({"indirect": "iec", "direct": "evap_direct", "precool_condenser": "evap_precool"}[st.evaporative.kind])
    return on


def _q(value: float, unit: str, status: str, basis: str) -> dict:
    return {"value": float(value), "unit": unit, "status": status, "basis": basis}


def takeoff(cfg: ProjectConfig, summary: dict, base: ProjectConfig | None = None) -> dict[str, dict]:
    st, b, it = cfg.strategies, cfg.building, cfg.it
    sz = summary["sizing"]
    arch = cfg.baseline.architecture
    active = active_measures(cfg, base)
    installed_kw = sz["installed_capacity_kw"]
    v_design = sz["design_airflow_m3s"]
    walls = 2 * (b.length_m + b.width_m) * b.height_m
    roof = b.length_m * b.width_m
    q: dict[str, dict] = {}

    # Conventional facility (present in every scenario; changes only if sizing or geometry changes).
    q["building_shell"] = _q(b.length_m * b.width_m * b.floors, "m²", "derived", "building length × width × floors")
    q["it_electrical_fitout"] = _q(it.installed_capacity_mw * 1000, "kW", "derived", "installed IT capacity")
    q["mech_plant"] = _q(installed_kw, "kW_th", "modelled", f"{sz['n_installed']} installed units × unit capacity (design hour + margin, redundancy)")
    rej = installed_kw * (1 + 1 / max(sz.get("rated_cop", 3.0), 1.0)) if arch in ("water_cooled_chiller_tower", "liquid_cooling_dry_cooler") else 0.0
    q["heat_rejection"] = _q(rej, "kW_th", "modelled", "installed capacity × (1 + 1/COP) for towers or dry coolers; integral for air-cooled plant")
    q["crah"] = _q(sz["crah_installed_airflow_m3s"], "m³/s", "modelled", "CRAH design airflow per unit × installed units")

    # Shared infrastructure, sized once for the largest user.
    oa = 0.0
    users = []
    for m in ("economiser_direct", "stack", "evap_direct"):
        if m in active:
            oa = max(oa, v_design)
            users.append(m)
    if "eahe" in active:
        oa = max(oa, st.eahe.design_flow_m3s)
        users.append("eahe")
    q["oa_intake"] = _q(oa, "m³/s", "derived", f"largest outdoor-air demand of {', '.join(users) or 'none'} (counted once)")
    q["water_treatment"] = _q(summary["water"]["peak_daily_withdrawal_m3"], "m³/day", "modelled", "peak daily site water withdrawal")
    controlled = sorted({m.control_system for m in MEASURES if m.id in active and m.control_system})
    q["supervisory_controls"] = _q(1.0 if controlled else 0.0, "system", "derived", "one hybrid supervisory controller if any controlled strategy is present")
    q["controls_integration"] = _q(len(controlled), "subsystem", "derived", f"distinct controlled subsystems: {', '.join(controlled) or 'none'}")

    # Strategy-specific items.
    q["containment"] = _q(it.rack_count if "containment_full" in active else 0, "rack", "derived", "racks upgraded to full containment")
    if st.stack.enabled:
        s = st.stack
        perim = 4 * math.sqrt(s.chimney_area_m2)
        q["stack_shafts"] = _q(s.n_chimneys * perim * s.chimney_height_m * 0.2, "m³", "derived", "n × perimeter × height × 0.2 m wall")
        q["stack_outlet_dampers"] = _q(s.n_chimneys * s.chimney_area_m2, "m²", "derived", "chimney outlet free area")
        q["solar_collectors"] = _q(s.collector_area_m2 * s.n_chimneys if s.solar_chimney else 0, "m²", "derived", "collector area × chimneys")
    else:
        q["stack_shafts"] = _q(0, "m³", "derived", "no stack")
        q["stack_outlet_dampers"] = _q(0, "m²", "derived", "no stack")
        q["solar_collectors"] = _q(0, "m²", "derived", "no solar chimney")
    e = st.eahe
    q["eahe_pipe"] = _q(e.n_pipes * e.length_m if e.enabled else 0, "m", "derived", "pipes × length")
    q["eahe_excavation"] = _q(e.n_pipes * e.spacing_m * e.length_m * (e.depth_m + 0.5) if e.enabled else 0, "m³", "derived",
                              "field area × (depth + 0.5 m)")
    q["eahe_fans"] = _q(e.design_flow_m3s if e.enabled else 0, "m³/s", "derived", "EAHE design airflow")
    env = st.envelope
    q["envelope_insulation"] = _q((walls + roof) * max(env.insulation_m - b.insulation_m, 0) if env.enabled else 0, "m³", "derived",
                                  "(walls + roof) × added insulation thickness")
    q["envelope_mass"] = _q((walls + roof) * max(env.thermal_mass_m - b.thermal_mass_m, 0) if env.enabled else 0, "m³", "derived",
                            "(walls + roof) × added thermal-mass thickness")
    q["cool_roof"] = _q(roof if env.enabled and env.roof_solar_reflectance > b.roof_solar_reflectance else 0, "m²", "derived", "roof area")
    fac_area = sum((b.length_m if f in "NS" else b.width_m) * b.height_m for f in st.facade.facades)
    q["facade_fixed"] = _q(fac_area if st.facade.enabled and st.facade.mode == "fixed" else 0, "m²", "derived", "area of shaded facades")
    q["facade_kinetic"] = _q(fac_area if st.facade.enabled and st.facade.mode == "kinetic" else 0, "m²", "derived", "area of shaded facades")
    q["tes"] = _q(st.storage.capacity_kwh_th if st.storage.enabled else 0, "kWh_th", "derived", "storage capacity")
    q["radiative_panels"] = _q(st.radiative.panel_area_m2 if st.radiative.enabled else 0, "m²", "derived", "panel area")
    q["iec_units"] = _q(v_design if "iec" in active else 0, "m³/s", "modelled", "facility design airflow")
    q["dec_units"] = _q(v_design if "evap_direct" in active else 0, "m³/s", "modelled", "facility design airflow")
    q["indirect_economiser_hx"] = _q(v_design if "economiser_indirect" in active else 0, "m³/s", "modelled", "facility design airflow")
    q["evap_precool_media"] = _q(installed_kw if "evap_precool" in active else 0, "kW_th", "modelled", "installed condenser capacity")
    return q
