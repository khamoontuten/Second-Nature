"""Standardised definitions of the strategies the simulation engine can actually model.

The catalogue is derived from the implemented configuration switches (app.schemas.config.StrategiesConfig and
the S1/hybrid candidate patches in app.simulation.scenarios) and the strategy library text
(app.strategies.library). A strategy appears here only if switching it on changes what run_simulation computes.
Applicability to a given project is decided by the same compatibility rules the scenario engine uses.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field

from app.schemas.config import ProjectConfig
from app.simulation.scenarios import _merge, derive
from app.strategies.library import STRATEGIES, compatibility_issues

LIB = {s["id"]: s for s in STRATEGIES}


@dataclass(frozen=True)
class Param:
    path: str
    label: str
    unit: str
    min: float
    max: float
    step: float


@dataclass(frozen=True)
class Measure:
    id: str
    name: str
    category: str                      # "biomimetic" | "conventional enabling measure"
    library_id: str | None
    description: str
    patch: dict
    physics_model: str
    effects: dict[str, str]           # metric family → how the model represents the effect
    construction: str
    space: str
    parameters: tuple[Param, ...] = ()
    requires: tuple[str, ...] = ()
    exclusive_group: str | None = None
    model_status: str = "implemented"
    assumptions: str = ""
    validation: str = ("Physics sub-models verified against analytical/reference cases (docs/validation.md); "
                       "the coupled strategy has not been validated against a built facility.")
    control_system: str | None = None  # controlled subsystem (for controls-integration counting); None = passive

    @property
    def inspiration(self) -> str:
        return LIB[self.library_id]["biological_inspiration"] if self.library_id else "Conventional engineering measure (not biomimetic)."

    @property
    def mechanism(self) -> str:
        return LIB[self.library_id]["physical_mechanism"] if self.library_id else self.description

    @property
    def uncertainty(self) -> str:
        return LIB[self.library_id]["limitations"] if self.library_id else self.assumptions


_P = Param
ENERGY_MODELLED = "modelled — hourly simulation"
INDIRECT = "modelled indirectly (through the cooling plant and controller)"

MEASURES: list[Measure] = [
    Measure(
        "containment_full", "Full hot/cold-aisle containment", "conventional enabling measure", None,
        "Seals hot and cold aisles so supply air reaches server inlets without bypass or recirculation.",
        {"it": {"containment": "full"}},
        "Hall air balance: bypass and recirculation fractions by containment level (app.simulation.engine.CONTAINMENT).",
        {"energy": ENERGY_MODELLED, "water": INDIRECT, "carbon": "derived from electricity", "capex": "estimated (per rack)",
         "space": "no change modelled"},
        "Aisle doors, roof panels or chimney cabinets, blanking panels; fire-suppression coordination.",
        "Within the existing white space.",
        assumptions="Upgrade from the configured containment level; per-rack cost covers doors/panels only.",
    ),
    Measure(
        "economiser_direct", "Direct air-side economiser", "conventional enabling measure", None,
        "Supplies filtered outdoor air to the hall when it is within the inlet envelope; mechanical trim otherwise.",
        {"strategies": {"economiser": {"enabled": True, "kind": "direct"}}},
        "Hourly mode evaluation with mixing, humidification/dehumidification and fan power (app.cooling.hybrid).",
        {"energy": ENERGY_MODELLED, "water": "modelled (humidification)", "carbon": "derived from electricity",
         "capex": "estimated (outdoor-air intake, controls)", "space": "estimated (intake plenum)"},
        "Intake louvres, dampers, filter banks, relief air path, controls.",
        "Outdoor-air intake plenum and filter bank (shared with other outdoor-air strategies).",
        parameters=(_P("strategies.economiser.path_dp_pa", "Outdoor-air path pressure drop", "Pa", 50, 800, 10),
                    _P("it.economiser_supply_margin_k", "Supply safety margin", "K", 0, 5, 0.5)),
        exclusive_group="economiser", control_system="economiser",
    ),
    Measure(
        "economiser_indirect", "Indirect air-side economiser", "conventional enabling measure", None,
        "Cools recirculated hall air through an air-to-air heat exchanger; outdoor air never enters the hall.",
        {"strategies": {"economiser": {"enabled": True, "kind": "indirect"}}},
        "Air-to-air heat-exchanger effectiveness model within the hourly controller (app.cooling.hybrid).",
        {"energy": ENERGY_MODELLED, "water": INDIRECT, "carbon": "derived from electricity",
         "capex": "estimated (heat-exchanger units)", "space": "estimated (packaged units outdoors)"},
        "Plate or wheel heat exchangers with scavenger fans, ducting, controls.",
        "Packaged heat-exchanger units, usually outdoors or on the roof.",
        parameters=(_P("strategies.economiser.indirect_effectiveness", "Heat-exchanger effectiveness", "–", 0.4, 0.9, 0.05),),
        exclusive_group="economiser", control_system="economiser",
    ),
    Measure(
        "stack", LIB["A"]["name"], "biomimetic", "A",
        "Tall exhaust stacks and low inlets drive outdoor air through the contained hall by buoyancy and wind, with fan assist.",
        {"strategies": {"stack": {"enabled": True}}},
        LIB["A"]["model"],
        {"energy": ENERGY_MODELLED, "water": "modelled (humidification of outdoor air)", "carbon": "derived from electricity",
         "capex": "estimated (shafts, dampers, intake, controls)", "space": "estimated (shafts and intake plenum)",
         "airflow": "modelled — natural flow vs server demand"},
        LIB["A"]["constructability"], "Exhaust shafts add to the building footprint; inlets share the outdoor-air intake.",
        parameters=(_P("strategies.stack.chimney_height_m", "Chimney height above roof", "m", 5, 80, 1),
                    _P("strategies.stack.chimney_area_m2", "Chimney cross-section (each)", "m²", 5, 120, 1),
                    _P("strategies.stack.n_chimneys", "Number of chimneys", "–", 1, 20, 1),
                    _P("strategies.stack.discharge_coefficient", "Discharge coefficient C_d", "–", 0.3, 0.9, 0.05)),
        requires=("containment_full",), control_system="stack",
        assumptions="Lumped zonal airflow network; C_d, C_p and loss coefficients are assumptions until CFD.",
    ),
    Measure(
        "solar_chimney", LIB["E"]["name"], "biomimetic", "E",
        "Adds a glazed solar collector to each stack to raise exhaust temperature and draught.",
        {"strategies": {"stack": {"enabled": True, "solar_chimney": True}}},
        LIB["E"]["model"],
        {"energy": ENERGY_MODELLED, "water": INDIRECT, "carbon": "derived from electricity",
         "capex": "estimated (collector glazing)", "space": "estimated (collector area on roof/facade)"},
        LIB["E"]["constructability"], "Collector glazing on the roof or the chimney faces.",
        parameters=(_P("strategies.stack.collector_area_m2", "Collector area per chimney", "m²", 0, 600, 10),
                    _P("strategies.stack.collector_efficiency", "Collector efficiency", "–", 0.1, 0.8, 0.05)),
        requires=("stack",), control_system="stack",
    ),
    Measure(
        "envelope", LIB["B"]["name"], "biomimetic", "B",
        "Thicker insulation, added thermal mass and a reflective, emissive roof.",
        {"strategies": {"envelope": {"enabled": True}}},
        LIB["B"]["model"],
        {"energy": ENERGY_MODELLED, "water": INDIRECT, "carbon": "derived from electricity",
         "capex": "estimated (insulation, mass, cool roof)", "space": "no footprint change modelled (wall build-up ignored)"},
        LIB["B"]["constructability"], "Wall build-up thickness is not deducted from internal area (small).",
        parameters=(_P("strategies.envelope.insulation_m", "Insulation thickness", "m", 0, 0.5, 0.01),
                    _P("strategies.envelope.thermal_mass_m", "Added thermal-mass thickness", "m", 0, 0.5, 0.01),
                    _P("strategies.envelope.roof_solar_reflectance", "Roof solar reflectance", "–", 0.1, 0.95, 0.05)),
    ),
    Measure(
        "facade_fixed", "Fixed fish-scale shading", "biomimetic", "C",
        "Static overhangs and fins on the selected facades.",
        {"strategies": {"facade": {"enabled": True, "mode": "fixed"}}},
        LIB["C"]["model"],
        {"energy": ENERGY_MODELLED, "water": INDIRECT, "carbon": "derived from electricity",
         "capex": "estimated (shading area)", "space": "facade projection zone only"},
        LIB["C"]["constructability"], "External projection zone along the shaded facades; no ground footprint.",
        parameters=(_P("strategies.facade.overhang_pf", "Overhang projection factor", "–", 0, 2, 0.1),
                    _P("strategies.facade.fin_pf", "Fin projection factor", "–", 0, 2, 0.1)),
        exclusive_group="facade",
    ),
    Measure(
        "facade_kinetic", LIB["C"]["name"], "biomimetic", "C",
        "Motorised overlapping louvres that close against beam sun and open when outdoor conditions are favourable.",
        {"strategies": {"facade": {"enabled": True, "mode": "kinetic"}}},
        LIB["C"]["model"],
        {"energy": ENERGY_MODELLED + " (incl. actuator energy)", "water": INDIRECT, "carbon": "derived from electricity",
         "capex": "estimated (kinetic facade area)", "space": "facade projection zone only"},
        LIB["C"]["constructability"], "External projection zone along the shaded facades; no ground footprint.",
        parameters=(_P("strategies.facade.beam_block_closed", "Beam blocked when closed", "–", 0.3, 1, 0.05),
                    _P("strategies.facade.close_beam_threshold_wm2", "Close above beam irradiance", "W/m²", 0, 600, 10)),
        exclusive_group="facade", control_system="facade",
    ),
    Measure(
        "eahe", LIB["D"]["name"], "biomimetic", "D",
        "Buried pipes pre-condition the outdoor-air intake against near-constant ground temperature.",
        {"strategies": {"eahe": {"enabled": True}}},
        LIB["D"]["model"],
        {"energy": ENERGY_MODELLED + " (incl. EAHE fans)", "water": INDIRECT, "carbon": "derived from electricity",
         "capex": "estimated (pipe, excavation, fans, intake)", "space": "underground field + intake structure"},
        LIB["D"]["constructability"], "Underground pipe field (surface use restricted) and an intake/fan structure.",
        parameters=(_P("strategies.eahe.n_pipes", "Number of pipes", "–", 1, 400, 1),
                    _P("strategies.eahe.length_m", "Pipe length", "m", 10, 300, 5),
                    _P("strategies.eahe.diameter_m", "Pipe diameter", "m", 0.1, 1.5, 0.05),
                    _P("strategies.eahe.depth_m", "Burial depth", "m", 1, 8, 0.25),
                    _P("strategies.eahe.design_flow_m3s", "Design airflow", "m³/s", 1, 400, 1)),
        control_system="eahe",
        assumptions="Soil properties default to generic values unless measured (strong influence on results).",
    ),
    Measure(
        "storage", LIB["F"]["name"], "biomimetic", "F",
        "Chilled-water storage shifts plant operation to hours with better efficiency.",
        {"strategies": {"storage": {"enabled": True}}},
        LIB["F"]["model"],
        {"energy": ENERGY_MODELLED + " (dispatch; idle if no benefit)", "water": INDIRECT, "carbon": "derived from electricity",
         "capex": "estimated (storage capacity)", "space": "estimated (tank footprint)"},
        LIB["F"]["constructability"], "Stratified tank outdoors.",
        parameters=(_P("strategies.storage.capacity_kwh_th", "Storage capacity", "kWh_th", 500, 100000, 500),
                    _P("strategies.storage.max_discharge_kw", "Maximum discharge", "kW_th", 100, 20000, 100)),
        control_system="storage",
    ),
    Measure(
        "radiative", LIB["G"]["name"], "biomimetic", "G",
        "Roof-mounted radiative sky-cooling panels pre-cool the chilled-water return.",
        {"strategies": {"radiative": {"enabled": True}}},
        LIB["G"]["model"],
        {"energy": ENERGY_MODELLED, "water": INDIRECT, "carbon": "derived from electricity",
         "capex": "estimated (panel area)", "space": "roof area"},
        LIB["G"]["constructability"], "Panels occupy roof area (must fit the roof).",
        parameters=(_P("strategies.radiative.panel_area_m2", "Panel area", "m²", 50, 20000, 50),),
        control_system="radiative",
    ),
    Measure(
        "iec", "Indirect evaporative cooling", "biomimetic", "H",
        "Wetted secondary air cools hall air through a heat exchanger; no moisture enters the hall.",
        {"strategies": {"evaporative": {"enabled": True, "kind": "indirect"}}},
        LIB["H"]["model"],
        {"energy": ENERGY_MODELLED, "water": "modelled (evaporation, blowdown)", "carbon": "derived from electricity",
         "capex": "estimated (IEC units, water treatment)", "space": "estimated (packaged units, water treatment)"},
        LIB["H"]["constructability"], "Packaged units outdoors and water treatment.",
        parameters=(_P("strategies.evaporative.effectiveness", "Wet-bulb effectiveness", "–", 0.4, 0.9, 0.05),),
        exclusive_group="evaporative", control_system="evaporative",
    ),
    Measure(
        "evap_direct", "Direct evaporative cooling", "biomimetic", "H",
        "Outdoor air is cooled by evaporation through wetted media and supplied to the hall.",
        {"strategies": {"evaporative": {"enabled": True, "kind": "direct"}}},
        LIB["H"]["model"],
        {"energy": ENERGY_MODELLED, "water": "modelled (evaporation, blowdown)", "carbon": "derived from electricity",
         "capex": "estimated (media sections, intake, water treatment)", "space": "estimated (media sections, intake)"},
        LIB["H"]["constructability"], "Media sections with the outdoor-air intake; water treatment.",
        parameters=(_P("strategies.evaporative.effectiveness", "Saturation effectiveness", "–", 0.4, 0.95, 0.05),),
        exclusive_group="evaporative", control_system="evaporative",
        assumptions="Supply humidity is limited by the inlet dew-point envelope; humid hours fall back to mechanical cooling.",
    ),
    Measure(
        "evap_precool", "Evaporative condenser pre-cooling", "biomimetic", "H",
        "Wetted media lower the air temperature entering air-cooled condensers.",
        {"strategies": {"evaporative": {"enabled": True, "kind": "precool_condenser"}}},
        LIB["H"]["model"],
        {"energy": ENERGY_MODELLED, "water": "modelled (evaporation, blowdown)", "carbon": "derived from electricity",
         "capex": "estimated (media per kW of condenser)", "space": "within the chiller yard"},
        LIB["H"]["constructability"], "Media frames on the existing condensers.",
        parameters=(_P("strategies.evaporative.effectiveness", "Pad effectiveness", "–", 0.4, 0.95, 0.05),),
        exclusive_group="evaporative", control_system="evaporative",
    ),
]

BY_ID = {m.id: m for m in MEASURES}

CONTROLLER_NOTE = ("The adaptive supervisory controller (library strategy I) is part of every evaluation: it chooses the "
                   "operating mode hour by hour with hard thermal constraints, hysteresis and minimum run time.")


class SelectionError(ValueError):
    pass


def resolve(selection: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Resolve dependencies and mutual exclusions.

    Returns (resolved ids in catalogue order, auto-added dependency ids, conflict messages). Unknown ids raise.
    """
    unknown = [s for s in selection if s not in BY_ID]
    if unknown:
        raise SelectionError(f"unknown strategies: {unknown}; supported: {[m.id for m in MEASURES]}")
    chosen = set(selection)
    added: list[str] = []
    changed = True
    while changed:
        changed = False
        for sid in list(chosen):
            for req in BY_ID[sid].requires:
                if req not in chosen:
                    chosen.add(req)
                    added.append(req)
                    changed = True
    conflicts = []
    groups: dict[str, list[str]] = {}
    for sid in chosen:
        g = BY_ID[sid].exclusive_group
        if g:
            groups.setdefault(g, []).append(sid)
    for g, ids in groups.items():
        if len(ids) > 1:
            conflicts.append(f"{' and '.join(sorted(ids))} are alternative {g} options and cannot be combined.")
    ordered = [m.id for m in MEASURES if m.id in chosen]
    return ordered, [a for a in ordered if a in added], conflicts


def patch_for(ids: list[str], parameters: dict | None = None) -> dict:
    """Configuration patch for a resolved selection; parameter overrides apply only to selected strategies' parameters."""
    patch: dict = {}
    for sid in ids:
        patch = _merge(patch, copy.deepcopy(BY_ID[sid].patch))
    for path, value in (parameters or {}).items():
        cur = patch
        keys = path.split(".")
        for k in keys[:-1]:
            cur = cur.setdefault(k, {})
        cur[keys[-1]] = value
    return patch


def allowed_parameters(ids: list[str]) -> dict[str, Param]:
    return {p.path: p for sid in ids for p in BY_ID[sid].parameters}


def applicability(base: ProjectConfig) -> dict[str, dict]:
    """Per strategy: applicable to this project's baseline? (conflicts from the engine's compatibility rules)."""
    out = {}
    for m in MEASURES:
        ids, _, conflicts = resolve([m.id])
        cfg = derive(base, patch_for(ids))
        issues = compatibility_issues(cfg)
        hard = [i["message"] for i in issues if i["level"] == "conflict"] + conflicts
        out[m.id] = {"applicable": not hard, "reasons": hard, "warnings": [i["message"] for i in issues if i["level"] == "warning"]}
    return out


def describe(m: Measure, cfg: ProjectConfig | None = None) -> dict:
    d = asdict(m)
    d.update({"inspiration": m.inspiration, "mechanism": m.mechanism, "uncertainty": m.uncertainty})
    d["parameters"] = [{**asdict(p), "value": _get(cfg, p.path) if cfg else None} for p in m.parameters]
    if m.library_id:
        lib = LIB[m.library_id]
        d["library"] = {k: lib[k] for k in ("organism", "failure_modes", "maintenance", "required_climate", "confidence")}
    return d


def _get(cfg, path: str):
    cur = cfg
    for k in path.split("."):
        cur = getattr(cur, k)
    return cur


@dataclass
class Selection:
    requested: list[str]
    resolved: list[str] = field(default_factory=list)
    auto_added: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    parameters: dict = field(default_factory=dict)
    ignored_parameters: list[str] = field(default_factory=list)


def select(requested: list[str], parameters: dict | None = None) -> Selection:
    resolved, added, conflicts = resolve(requested)
    allowed = allowed_parameters(resolved)
    params, ignored = {}, []
    for path, value in (parameters or {}).items():
        if path in allowed:
            p = allowed[path]
            if not (p.min <= float(value) <= p.max):
                raise SelectionError(f"{path} = {value} outside the supported range {p.min}–{p.max} {p.unit}")
            params[path] = value
        else:
            ignored.append(path)
    return Selection(list(requested), resolved, added, conflicts, params, ignored)
