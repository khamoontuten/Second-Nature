"""Biomimetic and low-energy cooling strategy library (A–I) and compatibility rules.

The biological analogy is the *inspiration* for an engineering feature, not evidence that the
feature works. Every performance statement comes from the simulation, never from this text.
"""

from __future__ import annotations

from app.schemas.config import ProjectConfig

STRATEGIES: list[dict] = [
    {
        "id": "A", "key": "stack", "name": "Termite-mound-inspired stack ventilation", "organism": "Termite mound (Macrotermes spp.)",
        "category": "passive airflow with fan assist",
        "biological_inspiration": "Mounds combine tall internal channels, thermal mass and wind/buoyancy pressure differences "
                                  "to ventilate the nest; behaviour varies by species and mound geometry.",
        "physical_mechanism": "Buoyancy (stack) pressure g·H·(ρ_out − ρ_exhaust) plus wind pressure ½ρU²ΔC_p drives outdoor air "
                              "through low inlets, the cold aisle, servers and a tall exhaust chimney.",
        "architectural_translation": ["tall exhaust stacks", "distributed low-level inlets", "separated intake/exhaust paths",
                                      "motorised dampers", "hot-aisle containment feeding the stack", "exhaust fan for assist"],
        "required_climate": "Outdoor air cool and dry enough for direct economisation for many hours; exhaust warmer than outdoors.",
        "required_geometry": "Chimney height (H), chimney and inlet free areas, low pressure-loss paths, containment.",
        "materials": "Concrete or steel shafts, louvres, dampers, filters.",
        "auxiliary_electricity": "Exhaust/assist fans when natural draught is insufficient; damper actuators; controls.",
        "water": "None (humidification water if outdoor air is too dry).",
        "model": "Multizone airflow network (app.physics.airflow, app.cooling.passive.natural_ventilation).",
        "operating_constraints": "Server inlet envelope (T, DP, RH); no recirculation (natural flow ≥ server airflow); air quality.",
        "failure_modes": ["flow reversal in hot weather", "insufficient draught at low ΔT", "damper failure", "filter loading",
                          "hot-air recirculation", "smoke/fire spread through shafts"],
        "maintenance": "Filter replacement, damper and actuator inspection, shaft cleaning, bird/insect screens.",
        "constructability": "Large shafts affect structure, fire compartmentation and roof layout; integrate early.",
        "confidence": "medium-low: lumped network; requires CFD and detailed airflow-network analysis before design.",
        "limitations": "Cannot predict rack-level inlet temperatures or local recirculation; C_p and C_d are assumptions.",
    },
    {
        "id": "B", "key": "envelope", "name": "Mollusc-shell-inspired layered envelope", "organism": "Mollusc shell / nacre",
        "category": "passive architectural feature",
        "biological_inspiration": "Nacre stacks hard and soft layers into a tough, lightweight structure.",
        "physical_mechanism": "Alternating insulation and mass layers lower U-value and add thermal inertia (time lag, decrement); "
                              "a reflective, emissive roof cuts absorbed solar.",
        "architectural_translation": ["alternating mass/insulation layers", "ventilated rainscreen cavity", "cool roof",
                                      "thermal breaks", "spiral (logarithmic) intake ducts — hypothesis"],
        "required_climate": "Benefit only when outdoor/sol-air temperature exceeds the hall temperature for significant periods.",
        "required_geometry": "Wall/roof areas, layer build-up.",
        "materials": "Concrete/rammed earth, mineral wool/PIR, cladding.",
        "auxiliary_electricity": "None.", "water": "None.",
        "model": "Multilayer implicit FD conduction with sol-air and sky exchange (app.physics.envelope).",
        "operating_constraints": "None (passive).",
        "failure_modes": ["insulation traps internal heat in cool climates (blocks free heat loss)", "moisture in layers",
                          "added structural load"],
        "maintenance": "Roof coating cleaning/re-coating (reflectance degrades with soiling).",
        "constructability": "Heavier walls raise structural and seismic demands; embodied carbon increases.",
        "confidence": "medium: 1-D conduction model; thermal bridging as an allowance.",
        "limitations": "Envelope loads are small relative to IT heat in large halls; the spiral-duct benefit is a hypothesis.",
    },
    {
        "id": "C", "key": "facade", "name": "Fish-scale-inspired kinetic facade", "organism": "Overlapping fish scales",
        "category": "low-energy adaptive feature",
        "biological_inspiration": "Overlapping scales cover the body directionally and can change orientation.",
        "physical_mechanism": "Movable overlapping louvres block beam irradiance when the sun loads a facade and open to admit air "
                              "when outdoor conditions are favourable.",
        "architectural_translation": ["overlapping movable louvres", "sun-tracking control", "storm lock position",
                                      "comparison with fixed overhangs/fins"],
        "required_climate": "High facade solar loads (E/W/S), sunny climates.",
        "required_geometry": "Facade areas per orientation, panel size.",
        "materials": "Aluminium/composite panels, actuators, controls.",
        "auxiliary_electricity": "Actuator standby + per-movement energy (included).", "water": "None.",
        "model": "Solar geometry (pvlib) + shading + envelope heat balance (app.physics.solar).",
        "operating_constraints": "Wind/gust lock-out; actuator failure (stuck fraction).",
        "failure_modes": ["stuck actuators", "storm damage", "fouling", "control faults"],
        "maintenance": "Actuator servicing, panel cleaning, control calibration.",
        "constructability": "Wind loads on panels and fixings; façade access for maintenance.",
        "confidence": "medium: geometric shading model; isotropic diffuse.",
        "limitations": "Benefit limited for opaque, well-insulated data-hall walls; must beat fixed shading to justify complexity.",
    },
    {
        "id": "D", "key": "eahe", "name": "Prairie-dog-burrow-inspired earth-air heat exchanger", "organism": "Prairie-dog burrow",
        "category": "low-energy system (fans)",
        "biological_inspiration": "Burrows with entrances at different heights use wind to ventilate, and the surrounding soil "
                                  "damps temperature swings.",
        "physical_mechanism": "Outdoor air passes through buried pipes and exchanges heat with soil near the undisturbed ground "
                              "temperature; wind pressure between a low intake and a raised outlet can assist the fans.",
        "architectural_translation": ["buried intake pipes / plenum", "raised outlet mound", "filtration", "condensate drainage",
                                      "cleanable access"],
        "required_climate": "Large difference between hot-season air and ground temperature; land available; suitable soil.",
        "required_geometry": "Pipe count, length, diameter, depth, spacing.",
        "materials": "HDPE/PVC/concrete pipes, excavation, filters.",
        "auxiliary_electricity": "Fans to overcome friction, bends and filters (minus wind assist).", "water": "Condensate (not a supply).",
        "model": "Segmented air side + radial implicit FD soil with shape-factor far field (app.physics.earth_air).",
        "operating_constraints": "Server inlet humidity (EAHE outlet often near saturation); soil saturation; filtration.",
        "failure_modes": ["soil thermal saturation", "condensation, mould, biofilm", "radon / soil gas ingress",
                          "groundwater infiltration or contamination", "pipe collapse or blockage"],
        "maintenance": "Cleaning access, drainage, filter replacement, microbial monitoring.",
        "constructability": "Large excavation footprint; conflicts with services and foundations.",
        "confidence": "medium-low without site soil data; requires thermal response testing and groundwater survey.",
        "limitations": "No moisture migration, freezing or groundwater advection; homogeneous soil assumed.",
    },
    {
        "id": "E", "key": "stack", "name": "Passive solar chimney", "organism": "Buoyancy-driven ventilation",
        "category": "passive airflow (variant of A)",
        "biological_inspiration": "Density-driven flows in natural structures.",
        "physical_mechanism": "A solar collector face heats the chimney column (η·G·A), raising buoyancy on sunny hot days.",
        "architectural_translation": ["glazed dark collector face", "insulated shaft", "shaded vs exposed surfaces"],
        "required_climate": "High irradiance coinciding with weak natural draught.",
        "required_geometry": "Collector area and chimney geometry.",
        "materials": "Glazing, absorber, insulated shaft.",
        "auxiliary_electricity": "Assist fans when needed.", "water": "None.",
        "model": "Network with collector temperature boost, fixed-point on chimney flow.",
        "operating_constraints": "Material temperatures; unwanted solar gains to occupied/IT spaces.",
        "failure_modes": ["overheating of shaft materials", "reverse flow at night", "glazing breakage"],
        "maintenance": "Glazing cleaning.", "constructability": "Tall glazed elements need wind and fire design.",
        "confidence": "low-medium.", "limitations": "Collector efficiency is an assumption.",
    },
    {
        "id": "F", "key": "storage", "name": "Thermal-mass buffering / thermal energy storage", "organism": "Thermal inertia in organisms",
        "category": "low-energy system (pumps)",
        "biological_inspiration": "Organisms exploit thermal inertia to reduce the rate of temperature change.",
        "physical_mechanism": "Stores cooling when plant efficiency is high (cool hours) and discharges at hot hours; mass also extends "
                              "ride-through after a cooling failure. Heat is shifted in time, not eliminated.",
        "architectural_translation": ["chilled-water or PCM tank", "structural mass exposure", "ground-coupled mass"],
        "required_climate": "Large diurnal swing (night/day plant efficiency difference).",
        "required_geometry": "Tank volume / mass area.", "materials": "Steel/concrete tanks, water or PCM.",
        "auxiliary_electricity": "Transfer pumps.", "water": "Initial fill only.",
        "model": "State-of-charge store with losses (app.physics.storage); two-node ride-through.",
        "operating_constraints": "Chilled-water plant required; never discharge below reserve needed for ride-through.",
        "failure_modes": ["stratification loss", "PCM degradation", "releasing stored heat during critical hours"],
        "maintenance": "Water treatment, tank inspection.", "constructability": "Large tanks need space and structure.",
        "confidence": "medium.", "limitations": "Heuristic daily dispatch; not an optimal controller.",
    },
    {
        "id": "G", "key": "radiative", "name": "Radiative cooling and reflective envelope", "organism": "Radiative exchange of natural surfaces",
        "category": "passive feature / low-energy (panels need pumps)",
        "biological_inspiration": "Natural surfaces lose heat to the cold sky by long-wave radiation.",
        "physical_mechanism": "Cool roofs reduce absorbed solar; purpose-built radiative panels reject heat to the sky through the "
                              "atmospheric window, pre-cooling the chilled-water return.",
        "architectural_translation": ["high-reflectance roof", "radiative sky-cooling panels", "shaded equipment yards"],
        "required_climate": "Clear, dry skies; low dew point.",
        "required_geometry": "Roof/panel area.", "materials": "Coatings, radiative films/panels, piping.",
        "auxiliary_electricity": "Panel loop pumps.", "water": "None.",
        "model": "Sky temperature (IR or Berdahl–Martin), panel heat balance (app.physics.radiative).",
        "operating_constraints": "Panel below dew point → condensation; soiling reduces reflectance.",
        "failure_modes": ["soiling/dust", "UV degradation", "condensation", "hail damage"],
        "maintenance": "Cleaning, re-coating.", "constructability": "Competes with PV and plant for roof area.",
        "confidence": "medium for cool roof; low for panels (material-specific).",
        "limitations": "Ordinary cool roofs are not sub-ambient coolers in daytime.",
    },
    {
        "id": "H", "key": "evaporative", "name": "Evaporation-assisted cooling", "organism": "Evaporative cooling (latent heat)",
        "category": "low-energy system that consumes water",
        "biological_inspiration": "Evaporation removes latent heat from surfaces and air streams.",
        "physical_mechanism": "Direct or indirect evaporative cooling toward the wet-bulb temperature; or pre-cooling of condenser air.",
        "architectural_translation": ["indirect evaporative coolers", "controlled direct evaporative pads", "adiabatic condenser pre-cooling"],
        "required_climate": "Large wet-bulb depression; water available.", "required_geometry": "Unit sizes.",
        "materials": "Heat exchangers, media pads, water treatment.",
        "auxiliary_electricity": "Fans and pumps.", "water": "Explicit: evaporation + bleed/blowdown (+ drift).",
        "model": "Heat-and-mass balances (app.physics.evaporative).",
        "operating_constraints": "Water limits; humidity at server inlet (direct); Legionella/aerosol control.",
        "failure_modes": ["scaling", "biological growth (Legionella)", "water supply interruption"],
        "maintenance": "Water treatment, media replacement, hygiene regime.",
        "constructability": "Water supply and drainage infrastructure.",
        "confidence": "medium.", "limitations": "Not water-free; unsuitable where water is constrained.",
    },
    {
        "id": "I", "key": "controller", "name": "Adaptive hybrid airflow (supervisory control)", "organism": "Adaptive regulation",
        "category": "control strategy",
        "biological_inspiration": "Organisms switch between regulation mechanisms as conditions change.",
        "physical_mechanism": "Each hour selects the safest, lowest-objective feasible mode among economiser, EAHE, indirect/evaporative, "
                              "stack-assisted, combined and mechanical, with hysteresis and minimum run times.",
        "architectural_translation": ["integrated BMS sequences", "sensor redundancy", "fail-safe dampers"],
        "required_climate": "Any.", "required_geometry": "As per installed strategies.", "materials": "Controls.",
        "auxiliary_electricity": "Controls (included).", "water": "Depends on modes.",
        "model": "app.cooling.controls / app.cooling.hybrid.",
        "operating_constraints": "Hard constraints first; safety override of hysteresis.",
        "failure_modes": ["sensor faults", "mode hunting", "control software failure"],
        "maintenance": "Sensor calibration, commissioning.", "constructability": "Commissioning effort.",
        "confidence": "medium: idealised controls (perfect hourly knowledge).",
        "limitations": "Hourly steps; no transient control dynamics.",
    },
]

STRATEGY_BY_KEY = {s["key"]: s for s in STRATEGIES if s["id"] != "E"}

# Pairwise interactions. "conflict": cannot be combined as configured; "interacts": modelled jointly
# (no double counting); "requires": prerequisite.
COMPATIBILITY = [
    ("stack", "eahe", "interacts", "EAHE intake feeds the same outdoor-air path; both are modes of one controller."),
    ("stack", "evaporative", "interacts", "Direct evaporative cooling raises intake humidity, limiting natural economisation."),
    ("envelope", "radiative", "interacts", "Cool-roof properties are part of one envelope heat balance."),
    ("facade", "envelope", "interacts", "Shading changes the incident irradiance used by the envelope model."),
    ("radiative", "baseline_dx", "conflict", "Radiative panels pre-cool chilled water; DX systems have no water loop."),
    ("storage", "baseline_dx", "conflict", "Thermal storage requires a chilled-water plant."),
    ("evaporative_precool", "baseline_water_cooled", "conflict", "Condenser pre-cooling applies to air-cooled condensers only."),
    ("evaporative", "water_cap_zero", "conflict", "Evaporative strategies need water; permitted withdrawal is zero."),
    ("stack", "containment_full", "requires", "Natural ventilation needs full hot/cold-aisle containment to avoid recirculation."),
]

DX_ARCHS = {"dx_crac", "dx_with_airside_economiser", "direct_evaporative", "indirect_evaporative"}


def compatibility_issues(cfg: ProjectConfig) -> list[dict]:
    st = cfg.strategies
    arch = cfg.baseline.architecture
    issues = []
    roof = cfg.building.length_m * cfg.building.width_m
    if st.radiative.enabled and arch in DX_ARCHS:
        issues.append({"level": "conflict", "strategies": ["G", "baseline"], "message": COMPATIBILITY[4][3]})
    if st.storage.enabled and arch in DX_ARCHS:
        issues.append({"level": "conflict", "strategies": ["F", "baseline"], "message": COMPATIBILITY[5][3]})
    if st.evaporative.enabled and st.evaporative.kind == "precool_condenser" and arch == "water_cooled_chiller_tower":
        issues.append({"level": "conflict", "strategies": ["H", "baseline"], "message": COMPATIBILITY[6][3]})
    cap = cfg.site.constraints.permitted_annual_withdrawal_m3
    if st.evaporative.enabled and cap is not None and cap <= 0:
        issues.append({"level": "conflict", "strategies": ["H"], "message": COMPATIBILITY[7][3]})
    if st.stack.enabled and cfg.it.containment != "full":
        issues.append({"level": "warning", "strategies": ["A"], "message": COMPATIBILITY[8][3]})
    if st.radiative.enabled and st.radiative.panel_area_m2 > roof:
        issues.append({"level": "conflict", "strategies": ["G"],
                       "message": f"Radiative panel area {st.radiative.panel_area_m2:.0f} m² exceeds roof area {roof:.0f} m²."})
    land = cfg.site.land_available_for_buried_m2
    if st.eahe.enabled and land is not None:
        fp = st.eahe.n_pipes * st.eahe.spacing_m * st.eahe.length_m
        if fp > land:
            issues.append({"level": "conflict", "strategies": ["D"],
                           "message": f"EAHE footprint ≈ {fp:.0f} m² exceeds land available for buried infrastructure ({land:.0f} m²)."})
    if st.eahe.enabled and cfg.site.constraints.groundwater_depth_m is not None and cfg.site.constraints.groundwater_depth_m < st.eahe.depth_m + 0.5:
        issues.append({"level": "warning", "strategies": ["D"],
                       "message": "Groundwater is near or above the EAHE depth: infiltration, buoyancy and contamination risk."})
    if st.eahe.enabled and cfg.site.constraints.groundwater_protection_zone:
        issues.append({"level": "warning", "strategies": ["D"], "message": "Site is in a groundwater protection zone: buried air paths need permitting review."})
    if st.eahe.enabled and cfg.site.constraints.radon_risk in ("medium", "high"):
        issues.append({"level": "warning", "strategies": ["D"], "message": "Radon/soil-gas risk: sealed pipes and monitoring required."})
    if st.stack.enabled and st.stack.chimney_height_m + cfg.building.height_m > 60:
        issues.append({"level": "warning", "strategies": ["A"], "message": "Very tall stacks: structural, planning and wind-load review required."})
    if st.facade.enabled and st.facade.mode == "kinetic" and cfg.site.constraints.salinity_exposure == "high":
        issues.append({"level": "warning", "strategies": ["C"], "message": "High salinity exposure accelerates actuator corrosion."})
    if (st.economiser.enabled or st.stack.enabled or st.eahe.enabled) and cfg.site.constraints.dust_exposure == "high":
        issues.append({"level": "warning", "strategies": ["A", "D"], "message": "High dust exposure: outdoor-air paths need high-grade filtration and pressure-drop allowance."})
    if (st.economiser.enabled and st.economiser.kind == "direct") or st.stack.enabled:
        if cfg.site.constraints.gaseous_contamination_high or cfg.site.constraints.salinity_exposure == "high":
            issues.append({"level": "warning", "strategies": ["A"],
                           "message": "Corrosive or saline outdoor air: direct outdoor-air cooling of IT equipment needs gas-phase filtration and coupon monitoring; consider indirect economisation."})
    return issues
