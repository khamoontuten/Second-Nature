"""Preliminary space estimate for one simulated configuration.

Categories are kept separate and are never added across boundaries that differ in kind:

* building footprint (at grade, including attached stack shafts)
* IT white space and indoor mechanical/electrical area (floor area inside the building)
* outdoor equipment yard (at grade)
* roof area used (panels/collectors — not a ground footprint)
* facade projection zone (external shading — not a ground footprint)
* underground field (surface above has restricted use) and excavation volume

Geometric quantities from the configuration are exact ("derived"); ratio-based areas use the project's layout
assumptions (``ProjectConfig.layout``) with a ± range ("estimated"). These are planning estimates, not
architectural measurements.
"""

from __future__ import annotations

from app.schemas.config import ProjectConfig


def _item(label: str, value: float, status: str, basis: str, frac: float) -> dict:
    r = frac if status == "estimated" else 0.0
    return {"label": label, "base": value, "low": value * (1 - r), "high": value * (1 + r), "status": status, "basis": basis}


def _cat(items: list[dict]) -> dict:
    items = [i for i in items if i["base"] > 0]
    status = "estimated" if any(i["status"] == "estimated" for i in items) else "derived"
    return {"base": sum(i["base"] for i in items), "low": sum(i["low"] for i in items), "high": sum(i["high"] for i in items),
            "status": status if items else "derived", "items": items}


def estimate(cfg: ProjectConfig, summary: dict, takeoff: dict[str, dict], base: ProjectConfig | None = None) -> dict:
    lay, b, st, it = cfg.layout, cfg.building, cfg.strategies, cfg.it
    f = lay.range_fraction
    sz = summary["sizing"]
    arch = cfg.baseline.architecture
    q_inst = sz["installed_capacity_kw"]

    building = [_item("Building (length × width)", b.length_m * b.width_m, "derived", "configured building geometry", f)]
    if st.stack.enabled:
        building.append(_item("Exhaust stack shafts", st.stack.n_chimneys * st.stack.chimney_area_m2, "derived",
                              "chimneys × cross-section (assumed attached to the building)", f))
    it_area = [_item("IT white space", it.rack_count * lay.white_space_m2_per_rack, "estimated", f"{it.rack_count} racks × {lay.white_space_m2_per_rack} m²/rack", f)]
    mep = [_item("Electrical rooms (UPS, switchgear)", it.installed_capacity_mw * 1000 * lay.electrical_m2_per_kw_it, "estimated",
                 f"{lay.electrical_m2_per_kw_it} m² per kW IT", f),
           _item("CRAH gallery", sz["crah_installed_airflow_m3s"] * lay.crah_m2_per_m3s, "estimated", f"{lay.crah_m2_per_m3s} m² per m³/s installed", f)]
    if arch == "water_cooled_chiller_tower":
        mep.append(_item("Chiller plant room", q_inst * lay.chiller_room_m2_per_kw, "estimated", f"{lay.chiller_room_m2_per_kw} m² per kW_th", f))
    oa = takeoff["oa_intake"]["value"]
    if oa > 0:
        mep.append(_item("Outdoor-air intake plenum and filters (shared)", oa * lay.oa_intake_m2_per_m3s, "estimated",
                         f"{oa:.0f} m³/s × {lay.oa_intake_m2_per_m3s} m² per m³/s — counted once", f))

    yard = []
    if arch in ("air_cooled_chiller_crah", "dx_crac", "dx_with_airside_economiser"):
        yard.append(_item("Air-cooled chillers / condensers", q_inst * lay.air_cooled_chiller_m2_per_kw, "estimated",
                          f"{lay.air_cooled_chiller_m2_per_kw} m² per kW_th incl. clearances", f))
    rej = takeoff["heat_rejection"]["value"]
    if arch == "water_cooled_chiller_tower":
        yard.append(_item("Cooling towers", rej * lay.cooling_tower_m2_per_kw, "estimated", f"{lay.cooling_tower_m2_per_kw} m² per kW_th", f))
    if arch == "liquid_cooling_dry_cooler":
        yard.append(_item("Dry coolers", rej * lay.dry_cooler_m2_per_kw, "estimated", f"{lay.dry_cooler_m2_per_kw} m² per kW_th", f))
    for key, label, ratio in (("iec_units", "Indirect evaporative units", lay.air_handler_m2_per_m3s),
                              ("indirect_economiser_hx", "Indirect economiser heat exchangers", lay.air_handler_m2_per_m3s),
                              ("dec_units", "Direct evaporative media sections", lay.dec_m2_per_m3s)):
        v = takeoff[key]["value"]
        if v > 0:
            yard.append(_item(label, v * ratio, "estimated", f"{v:.0f} m³/s × {ratio} m² per m³/s", f))
    if st.storage.enabled:
        vol = st.storage.capacity_kwh_th * 3.6e6 / (4186.0 * cfg.baseline.chw_delta_t_k) / 1000.0
        yard.append(_item("Thermal-storage tank", vol / lay.tes_tank_height_m * lay.tes_clearance_factor, "estimated",
                          f"{vol:.0f} m³ water at ΔT {cfg.baseline.chw_delta_t_k} K, {lay.tes_tank_height_m} m tall × {lay.tes_clearance_factor} clearance", f))
    wt = takeoff["water_treatment"]["value"]
    if wt > 0:
        yard.append(_item("Water treatment and storage (shared)", wt * lay.water_treatment_m2_per_m3_day, "estimated",
                          f"{wt:.1f} m³/day peak × {lay.water_treatment_m2_per_m3_day} m² per m³/day — counted once", f))
    if st.eahe.enabled:
        yard.append(_item("EAHE intake structure and fan room", st.eahe.design_flow_m3s * lay.eahe_plant_m2_per_m3s, "estimated",
                          f"{lay.eahe_plant_m2_per_m3s} m² per m³/s", f))

    roof = []
    if st.radiative.enabled:
        roof.append(_item("Radiative panels", st.radiative.panel_area_m2, "derived", "configured panel area", f))
    if st.stack.enabled and st.stack.solar_chimney:
        roof.append(_item("Solar-chimney collectors", st.stack.collector_area_m2 * st.stack.n_chimneys, "derived", "collector area × chimneys", f))
    facade = []
    if st.facade.enabled:
        length = sum(b.length_m if fc in "NS" else b.width_m for fc in st.facade.facades)
        facade.append(_item("External shading zone", length * lay.facade_projection_m, "estimated",
                            f"{length:.0f} m of facade × {lay.facade_projection_m} m projection", f))
    under = []
    if st.eahe.enabled:
        e = st.eahe
        under.append(_item("EAHE pipe field", e.n_pipes * e.spacing_m * e.length_m, "derived", "pipes × spacing × length", f))

    cats = {"building_footprint": _cat(building), "it_area": _cat(it_area), "mep_indoor": _cat(mep), "outdoor_yard": _cat(yard),
            "roof_used": _cat(roof), "facade_zone": _cat(facade), "underground_field": _cat(under)}
    at_grade = {k: cats["building_footprint"][k] + cats["outdoor_yard"][k] for k in ("low", "base", "high")}
    cats["site_at_grade"] = {**at_grade, "status": "estimated", "items": [],
                             "basis": "building footprint + outdoor equipment yard (underground field reported separately)"}
    gfa = b.length_m * b.width_m * b.floors
    roof_area = b.length_m * b.width_m
    need = cats["it_area"]["base"] + cats["mep_indoor"]["base"]
    checks, infeasible = [], []
    if need > gfa:
        checks.append(f"Layout ratios imply {need:,.0f} m² of IT + M&E floor area, more than the building's {gfa:,.0f} m² gross floor area "
                      "(building geometry and layout assumptions are inconsistent; review before relying on space results).")
    if cats["roof_used"]["base"] > roof_area:
        infeasible.append(f"Roof equipment needs {cats['roof_used']['base']:,.0f} m², more than the {roof_area:,.0f} m² roof.")
    site = cfg.site.site_area_m2
    if site:
        if at_grade["base"] > site:
            infeasible.append(f"At-grade footprint {at_grade['base']:,.0f} m² exceeds the site area {site:,.0f} m².")
        elif at_grade["base"] + cats["underground_field"]["base"] > site:
            checks.append("At-grade footprint plus the underground field exceed the site area: the field cannot be under open yard only.")
    land = cfg.site.land_available_for_buried_m2
    if land is not None and cats["underground_field"]["base"] > land:
        infeasible.append(f"Underground field {cats['underground_field']['base']:,.0f} m² exceeds land available for buried infrastructure ({land:,.0f} m²).")
    return {"categories": cats, "excavation_m3": takeoff["eahe_excavation"]["value"], "gross_floor_area_m2": gfa, "roof_area_m2": roof_area,
            "site_area_m2": site, "checks": checks, "infeasible": infeasible,
            "note": "Preliminary planning estimate from layout ratios and configured geometry; not an architectural measurement."}
